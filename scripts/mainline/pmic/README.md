# PMI632 power proposal for Hisense A9

2026-10-15 — **observe telemetry permits one probe-only stock FV write.**
`fv_stock=1` writes 0x55 (4.450 V) to 0x1070 once and verifies readback.
Unexpected values/errors latch health failure without retries or other writes.
Pure observe defaults to `fv_stock=0`; the telemetry proposal defaults it to 1.
Boot 3 supplied valid telemetry; the follow-up health fix now qualifies the
hardware OV bit with stock's FV+40mV margin. Constant quantized QG codes do
not prove staleness in observe. See the boot comparison in doc section 9.
The optional upstream ADC5 conversion path writes ADC registers;
`observe_adc=0` omits ADC entirely and returns ENODATA for TEMP.
Firmware charging is otherwise inherited. Stock boot logs show 4.500 V before
Linux lowers it to the profile’s 4.450 V; this opt-in repeats that correction.
See [the evidence and validation gates](../../../docs/mainline-pmic.md).
No phone command was run. This is a bring-up proposal, not daily-use battery
protection. The driver cannot promise positive battery balance at Android load.

Files:

- `pmi632-a9.c`: combined primary charger and read-only QG telemetry consumer;
  publishes `battery` and `a9-usb` power supplies. All sysfs properties read-only.
- `pmi632-policy.h`: control-mode 4.10 V / 500 mA FCC / 500 mA ICL ceilings,
  stock 10k NTC curve, narrower 10–40 C charging window, voltage-based capacity.
- `pmi632-fv.h`, `tests/fv.c`: one-shot FV sequence and injected-failure tests.
- `a9-pmi632.dtsi`: disabled-by-default DT fragment, no validation flag.
- `patches/0001-*`: ADC5 battery thermistor **voltage** channel support.
- `patches/0002-*`: driver, policy, Kconfig and Makefile additions.
- `patches/0003-*`: disabled power fragment; adds the board include if absent.
- `inspect-stock.py`, `stock-evidence.json`: reproducible stock numerical facts.
- `stock-boot-charger-evidence.json`: sanitized UEFI/handoff/profile log lines.
- `offline-build.sh`: private O= module builds and sanitizer-backed policy tests.
- `tests/policy.c`: temperature interpolation/open/short, source classification,
  fault vetoes, charge boundaries, voltage-estimate monotonicity/bounds.
- `make-proposals.py`: refresh patches 0002/0003 from these canonical sources.

## Reproduce the offline build

From `/home/user/A9`:

```sh
scripts/mainline/pmic/offline-build.sh
```

Uses the existing `scripts/mainline/kernel/shell.nix` unchanged. Reads the
existing kernel tree/config/Module.symvers; writes only
`~/A9-build/mainline/sol-pmic/`. Modules:

```
~/A9-build/mainline/sol-pmic/module/pmi632-a9.ko
~/A9-build/mainline/sol-pmic/adc/qcom-spmi-adc5.ko
```

The ADC module is a compile artifact: the current board has ADC5 built in.
Its code must be incorporated in the kernel; do not load a second ADC driver.
The private prepared O= tree uses a copy of the shared Module.symvers; it
has not linked a new Image. No shared source, build output, shell.nix or
build.sh is changed. Keep the original build's config/symvers aligned when
repeating this check. Do not copy this private preparation's generated
headers into the shared build.

## Standalone control-mode alternative (not the telemetry default)

For the current observe telemetry image, use the final section below.
The following describes the older control-mode alternative, whose OFF writes
must not be used to implement observe.

Review/apply the three proposal patches to an **isolated kernel copy** after
copying `docs/mainline/sm6115-hisense-hlte556n.dts` into its qcom DT directory.
Patch 0003 supplies the fragment and adds the include to that copy only if
the project board does not already contain it. Enable
`CONFIG_CHARGER_PMI632_A9=m` (or `y`) and retain `CONFIG_MFD_SPMI_PMIC`,
`CONFIG_IIO`, `CONFIG_QCOM_SPMI_ADC5`, `CONFIG_POWER_SUPPLY`, and
`CONFIG_REGMAP_SPMI`. Do not bind `qcom_smbx`, another QG driver or a secondary
SMB1355 charging driver to these blocks.

The kernel build script currently recopies the project board DTS on every
build. Thus a coordinator integrating here must add the include to that input
DTS or a separate board copy, and explicitly arrange the proposal patch/config
stage. Merely copying patch 0003 into its automatic patch directory will lose
the include at the later DTS-copy step. The project kernel files were left
untouched because other agents own them.

For initial telemetry testing, set only `&a9_pmi632_power { status = "okay"; };`
in the private board. Leave `allow_charging=0` and omit
`hisense,charging-validated`. A built-in driver disables inherited charging at
probe; a module does so only on load. Both also suspend USB input and assert
the secondary-charger OFF override. **OFF is not a zero-write mode.** ADC
reads trigger ordinary ADC conversion writes; the QG block itself is read only.
Keep the existing diskless boot/fallback guards. Adding the node to a DT alone
is not a reason to boot or flash anything.

The module and dependencies would have to be explicitly included in the
initramfs module closure; the existing build.sh closure includes E Ink modules
only. If built in, no module packaging is needed. No new boot image is built
by this proposal. A binding schema/DT schema validation is still outstanding;
this custom compatible is intentionally board-local.

Charging requires BOTH load-time `allow_charging=1` (built-in:
`pmi632_a9.allow_charging=1`) AND `hisense,charging-validated;` on the enabled
node. Neither is supplied by default. Read the document's hardware gates
before setting either. Parameters cannot raise the fixed limits at runtime.
Only BC1.2 CDP/DCP sources are accepted; a USB data host/SDP or a PD-only source
can remain OFF. Fault lockout requires reload/reboot after diagnosis.

## Reproduce stock evidence (offline)

```sh
W=$HOME/A9-build/mainline/sol-pmic/evidence
B=$A9_DATA/Backups/edl_2026-10-14_110759/lun4
python3 -I scripts/mainline/pmic/inspect-stock.py \
  --boot "$B/boot_a.bin" --dtbo "$B/dtbo_a.bin" --out "$W"
nix-shell -p vmlinux-to-elf --run \
  "vmlinux-to-elf '$W/Image.raw' '$W/vmlinux.elf'"
nix-shell -p 'python3.withPackages (p: [p.pyelftools])' --run \
  "python3 -I scripts/mainline/pmic/inspect-stock.py --boot '$B/boot_a.bin' --dtbo '$B/dtbo_a.bin' --out '$W' --elf '$W/vmlinux.elf'"
nix-shell scripts/mainline/kernel/shell.nix --run \
  "dtc -q -I dtb -O dts -o '$W/base_0.dts' '$W/base_0.dtb'; dtc -q -I dtb -O dts -o '$W/dtbo_0.dts' '$W/dtbo_0.dtb'"
nix-shell -p 'python3.withPackages (p: [p.capstone p.pyelftools])' --run \
  "python3 -I scripts/mainline/kern-dis.py '$W/vmlinux.elf' qg_get_battery_voltage qg_get_battery_current qg_get_battery_temp smblib_get_prop_usb_present smblib_get_prop_batt_present smblib_get_prop_batt_health smblib_update_jeita adc_probe adc_read_raw qcom_vadc_hw_scale"
```

The relocation offsets in inspect-stock.py are pinned to the SHA-identified
phone Image. Do not use them for a different firmware. The JSON records
numerical hardware tables and addresses, not the stock kernel binaries.

## Current telemetry proposal: observe firmware charging

`kernel-telemetry-PROPOSAL.patch` is an **unapplied A9-project patch** against
the coordinator's integrated 0007–0009 baseline. It now only adds a Linux patch
with the next free number and the health/freshness fixes; it leaves applied
0009, build scripts,
enabled board node and configuration untouched. That baseline already uses:

```
CONFIG_CHARGER_PMI632_A9=y
CONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY=y
CONFIG_CHARGER_PMI632_A9_OBSERVE=y
CONFIG_CHARGER_PMI632_A9_FV_STOCK=y
```

OBSERVE defaults to y for TELEMETRY_ONLY and forces observe even if the module
parameter is false. A normal build can opt in with `pmi632_a9.observe=1` or
`observe=1` when loading the module; parameters are read-only after load.
Observe overrides allow_charging and validation flags. The telemetry image
defaults `fv_stock=1` via FV_STOCK=y; omit that config for pure observe's
default 0. `pmi632_a9.fv_stock=0` selects pure observe even in this image.
The load-time parameter cannot be changed after binding.

After all probe dependencies and supply registration, fv_stock=1 reads
0x1070, accepts only known stock handoff 0x5a or profile 0x55, writes 0x55
once, and verifies 0x55 by readback. It never raises a lower or unknown target.
Initial-read failure/unexpected value causes no write. Write failure causes
no retry; readback failure/mismatch causes no corrective write. Failure is
latched as HEALTH_UNSPEC_FAILURE while telemetry remains available. Later
selector drift also latches failure; it never triggers another write. There
is no probe failure/deferral return after the attempt, preventing automatic
probe retry. A deliberate new bind/reboot is a new probe, with a new budget.

Polling, errors, suspend, shutdown and unbind never write charger/QG
registers. Charging and USB ONLINE report hardware state; sysfs reports
FV/FCC/ICL selectors. Firmware charging is never disabled by this mode.

Temperature uses only upstream `qcom-spmi-adc5`'s normal IIO API, which performs
conversion writes to 0x3142–0x3147 and ordinary IRQ acknowledgement. No private
ADC commands or QG hold/clear/ESR/SDAM writes are introduced. For zero writes
including this ADC exception, use both `pmi632_a9.fv_stock=0` and
`pmi632_a9.observe_adc=0`: no ADC requests,
no TEMP or sampled VBUS/die temperature; voltage/current/capacity still work
if QG data is valid. Health remains unknown without temperature unless a
known fault/limit violation is visible. The compiled observe object is checked
for exactly one regmap_write call site and no bulk-write/RMW imports; tests
exercise the actual shared one-shot code with injected failures and a one-write
budget. The system's other upstream drivers
still perform their own normal initialization.

**Inherited is not proven within stock limits:** saved UEFI logs request
FV=4.500 V / FCC=1 A; initial stock-kernel reads are 4.500 V / 2 A / ICL=1.5 A.
Stock Linux subsequently programs FV=4.450 V. Pure observe can preserve a
50 mV higher float target; fv_stock=1 corrects it only if verified. Hardware POR defaults,
exact current mainline handoff settings, JEITA thresholds and watchdog
survival over hours are not established. See docs/mainline-pmic.md sections 7–9
and `stock-boot-charger-evidence.json`. A configured FV/FCC above the normal
stock profile reports a health failure; no periodic correction is attempted.

Coordinator review and offline checks:

```sh
git apply --check scripts/mainline/pmic/kernel-telemetry-PROPOSAL.patch
scripts/mainline/pmic/offline-build.sh
python3 -I scripts/mainline/pmic/tests/integration.py
```

Do not also apply standalone 0001–0003 over the existing kernel 0007/0008.
`make-kernel-proposal.py` handles both an original 0007/0008-only baseline and
the integrated 0009 baseline; it regenerates the appropriate nested delta.
Its inner patch is mail-formatted for the build's git-am loop. No kernel/ file,
shared Linux source, boot image or phone state is changed by this proposal.
The hard-OFF instructions from follow-up 2 are superseded for telemetry.
