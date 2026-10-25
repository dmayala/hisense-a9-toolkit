# B6 stock input-current control, round 3

Host built; coordinator phone test pending. Extend the existing PMI632 sampled
battery module, already selected by the integrated builder's default B6 step.
PM6125 is not the primary charger. Stock uses `qcom,qpnp-smb5` on PMI632 USID 2;
its parallel SMB1355 remains disabled by the existing B6 policy.

## Stock evidence

Use the saved stock Image SHA256
`2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`
and DTBO entry SHA256
`74138d6535db5897db0c80ae093dc5840e8c51cf578b5d395499ded185069931`.
The merged DT has `qcom,usb-icl-ua=2000000`, `qcom,float-icl-ua=900000`,
`qcom,float-option=1` (FLOAT_DCP) and a separate HVDCP limit of 3000000.
The latter is not used by this BC1.2 fallback. Earlier FCC/FV/JEITA/QG evidence
is in [README.md](README.md) and [mainline-pmic.md](../../../../docs/mainline-pmic.md).

Stock `update_sw_icl_max` at `0xffffff8008a323a8` uses a jump table at
`0xffffff8009700504`: supply types 4/5/6/16 select code at
`0xffffff8008a32470` / `0xffffff8008a324e4` / `0xffffff8008a324b8` /
`0xffffff8008a32520`. These vote SDP **500000**, board DCP current, CDP
**1500000**, and board float current respectively. This stock SDP starting
vote differs from the cached public SMB5 source's 100000. USB reset/configuration
can subsequently vote a lower/higher USB51 limit. Type-C Rp and QC have other
branches; this implementation does not port them.

REA/Ghidra 12.1.4 analyzed exact ARM64 excerpts preserving the Image addresses:

| Procedure | Excerpt SHA256 | Evidence |
|---|---|---|
| `smblib_icl_override`, `0xffffff8008a2bae0`, 0x170 bytes | `262aec35b1a24491ad844f7c082978807611454d0e28f710564ba908a78ae633` | `ev_b8af669597473159d382e8f64d82f3d8a018cd49c7597a21df9ca2bb9f600d63` |
| `smblib_set_icl_current`, `0xffffff8008a2ce58`, 0x5b0 bytes including local helpers | `95b1e0749ca6edf94f740c626cd97154817dad57ae1a223616adac02b9ebc59e` | `ev_f4126fca75826a03429155cc40a41eceb290d895a00270b7c7017170582c9373` |
| `set_sdp_current`, `0xffffff8008a2d190`, within preceding excerpt | same | `ev_03847bf67b136b02c93bd1eab7c44aa1c1ad193cca6782d2d89e6978ffd6e07d` |

External callees/data are outside these excerpts. Symbol identities and the
update_sw_icl_max jump-table targets were checked against the saved symbolized
stock ELF and original Image bytes; these are static findings, not execution.
Unlike an older public SMB5 boolean helper, this stock override has three modes:

| Mode | 0x1366 bit0 | 0x1342 bit0 | 0x1365 bit4 |
|---|---:|---:|---:|
| APSD auto (0) | 1 | 0 | 0 |
| USB51 (1) | 0 | 1 | 0 |
| software HC (2) | 1 | 0 | 1 |

USB51's 100/150/500/900 mA encodings are 0/4/2/6 in 0x1366 bits2:1.
Stock's SDP path selects USB51 and then restores auto. HC writes 0x1370 at
50 mA/code before selecting mode 2. At <=25 mA stock suspends USB input using
0x1340 bit0. `smblib_run_aicl` at `0xffffff8008a2d2f0` kicks 0x1644 bit0 when
0x110b bit6 is clear; this fallback retains that non-HC transition behavior.

Stock vendor init runs `init.qti.chg_policy.sh`, which grants sysfs access and
starts `hvdcp_opti`. It does not replace the kernel's SMB5 charge policy.
REA also analyzed the saved 66016-byte health service, SHA256
`d9941eef1530542df8c536c490a14549962f6ea4eb8812de2f4669c295ae4882`:
`main.cfi` at ELF VA 0x506c (REA 0x10506c) loads the default passthrough IHealth
and wraps it in BinderHealth; `getCurrentNow` at ELF 0x8c38 (REA 0x108c38)
forwards to it. Evidence `ev_c1f3c01bccc1552273f780da61b9371f5ba741660429dbf308c527f3e6e7298f`
and `ev_c7127c851f1a5bd3d9547de63ea3dd85940105e420bc4852c802647a20d911ab`.
This is the wrapper, not a complete analysis of its separately loaded implementation
or of hvdcp_opti. Mainline already has the sysfs-backed health HAL.

Complete REA exports and stock assembly remain local under
`~/A9-build/mainline/charging-20261020/`; stock bytes are not committed.

## Mainline change and coordinator check

The staged DT sets the two stock current properties and DWC3's existing
`usb-psy-name="a9-usb"` interface. No DWC3/kernel Image rebuild is needed.
The battery loader moves to `early-init`, before gadget setup, because DWC3 now
defers until this supply registers. Cold-boot both boot and vendor changes
together; changing only the module in an already running image is insufficient.

The module accepts DWC3 `vbus_draw` input-current votes and selects SDP, CDP,
DCP or float policy. A float source that the USB stack enumerates uses its USB
vote. It preserves BC1.2/AICL and applies limits before input resume. Existing
battery thermal/FCC/FV/enable behavior remains. `a9-usb/current_max` now supplies
Android's maximum charging current, `input_current_limit` decodes the selected
USB51/HC mode, and `voltage_now` supplies measured VBUS. Battery `current_now`
continues to be net cell current; it is distinct from input-current limit.

Use the usual integrated build; B6 is on by default. For a small host-only
staging build over an existing integrated boot:

```sh
A9_BATTERY_OUT=$HOME/A9-build/mainline/charging-20261020 \
  python3 -I scripts/mainline/pmic/battery/build-images.py \
  --boot "$HOME/A9-build/mainline/integrated-20261020f/boot.img" --stage-only
```

Coordinator, in the next owned boot, check USB still enumerates and read:

```sh
dmesg | grep -E 'pmi632-battery|couldn.t get usb power supply'
cat /sys/class/udc/*/state
cat /sys/class/power_supply/a9-usb/uevent
cat /sys/class/power_supply/battery/uevent
dumpsys battery
```

Configured SDP should follow the gadget's advertised MaxPower (normally 500 mA);
USB reset requests 100 mA, USB suspend 2 mA maps to input suspend, and resume
restores the configured request. A physical CDP/DCP should select 1.5/2 A, not
the arbitrary inherited selector. Compare source-change log/current_max with
input_current_limit and battery status/current. Actual current can be lower
because of load, AICL, temperature or termination. A full cell need not charge.
Use a real VBUS cycle for unplug evidence; framework battery simulation is not
a physical charger test.

No phone access, full image build or hardware validation was performed by this
worker. This is BC1.2 control, not full stock QC/PD/Rp/parallel-charger parity.
Polling remains 1 Hz; the existing suspend path still stops battery charging
and invalidates sampled SOC across a long gap. Long-term charging and Android
USB resume behavior remain coordinator tests.
