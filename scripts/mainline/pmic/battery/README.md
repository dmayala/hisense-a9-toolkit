# B6 battery/charging POC

**Host built; phone untested. B6 parity is still partial.** A new module replaces
R4's observe driver through one DT `compatible` change. R4's kernel Image,
configuration, module CRCs and other DT properties are preserved. The vendor
is rebuilt by current main's `build-vendor.sh --refresh-integrated`, with this
module and its `post-fs-data` loader overlaid. All images remain local.

The kernel now integrates signed QG current at 1 Hz instead of recomputing SOC
from terminal voltage. It exports `capacity`, `charge_counter` (uAh), profile
`charge_full[_design]`, actual charging state, voltage/current/temperature,
FCC/FV selectors, USB present/online and BC1.2 `usb_type`. The existing patched
health HAL reads these sysfs values; the recipe verifies its established hash.

The initial SOC is still an **estimate** from QG S7 boot OCV and the stock
`pc-temp-v2-lut`; FCC comes from `fcc2-temp-lut` at the initial temperature
(3,971,000 uAh at 25 C). These are discharge tables: stock
`lookup_soc_ocv(..., false)` selects OCV2. `initial_soc=0..100` can instead
supply a measured starting percentage when loading the module. A new 4.45 V-band
hardware termination anchors 100% once per charge cycle. Merely
plugging in, or terminating in the warm 4.10 V band, never anchors 100%.
No QG SOC byte, SDAM, learned capacity or persistent state is written.

Charging uses the stock primary FCC/FV limits:

| Battery temperature | FCC ceiling | FV |
|---|---:|---:|
| Below 0 C / above 53 C | charging off | — |
| 0–10 C | 800 mA | 4.45 V |
| 10.1–45 C | 3000 mA | 4.45 V |
| 45.1–53 C | 1200 mA | 4.10 V |

The driver writes stock hard/soft comparator thresholds, disables the parallel
charger using the existing PMI632 override, uses stock's 768-minute safety
timer, configures watchdog charge-disable-on-bite without AFP, and pets while
USB is present. It preserves the inherited termination-current settings and
USB APSD/AICL/Type-C rather than interpreting QG's **500 mA
iterm** as the charger's termination register encoding. Input current now follows
stock BC1.2 source limits and DWC3's USB enumeration votes; see
[charging-control.md](charging-control.md) for stock REA and register evidence.
Temperature, qualified battery
OV, PMIC/connector faults and missing battery stop charging. Transport/control
errors latch control off until reload; USB input stays available to the system.
Stock's 98% recharge decision is implemented in software with a charge-enable
edge after 4.45 V-band termination; hardware SOC-based auto-recharge is disabled because
its inherited monotonic SOC byte would otherwise be stale.

## Evidence

The numerical profile and thresholds are from the phone's saved stock DTBO
entry SHA256 `74138d6535db5897db0c80ae093dc5840e8c51cf578b5d395499ded185069931`.
`../stock-evidence.json` and [mainline-pmic.md](../../../../docs/mainline-pmic.md)
contain the earlier verified register table and thermistor conversions. Saved
raw stock Image SHA256 is
`2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`.

REA/Ghidra 12.1.4 analyzed exact, address-preserving excerpts from that Image:

- `smblib_update_jeita`, `0xffffff8008a3a3e8`, 0x170 bytes, excerpt SHA256
  `2c715aa330ca3d5017a9349bd366094e8958f69517e544251e9a0a51c9d7b880`.
  Evidence `ev_589f21fe382627c9d38a1479149aade3829e99e806d094da3cba02c2286d0cb7`. Confirms threshold register base 0x1094 + kind*4 and
  hot-before-cold big-endian writes. External callees/data are outside the
  excerpt; its stack-failure path has a decompiler warning.
- `lookup_soc_ocv`, `0xffffff8008a49ff8`, 0x90 bytes, excerpt SHA256
  `f33c21a190189bf0306311d76a18e8ec847a06d0566eda376ef46f2b51bb3279`.
  Evidence `ev_948670607fc08489cdaeeb08c570ed09ed7e7945302253ea18e18664fd042017`. Confirms the charging flag selects adjacent 0x28-byte profile tables and
  voltage is divided by 100 before interpolation. Profile table names are
  cross-checked with the primary downstream source below.

Primary source references for the shared PMI632/QG approach (phone DT/binary
values take precedence):
[QG profile table selection](https://android.googlesource.com/kernel/msm/+/9bc512676061161105fa95ccf40532f625c05b7e%5E2..9bc512676061161105fa95ccf40532f625c05b7e/),
[QG boot SOC and software model](https://android.googlesource.com/kernel/msm/+/60e67638d95e7fd48f602b15729ea13c13c478d5/drivers/power/supply/qcom/qpnp-qg.c),
[SMB5 configuration](https://android.googlesource.com/kernel/msm/+/60e67638d95e7fd48f602b15729ea13c13c478d5/drivers/power/supply/qcom/qpnp-smb5.c).

## Build

```sh
python3 -I scripts/mainline/pmic/battery/build-images.py
```

Default output: `~/A9-build/mainline/battery-20261020` (override
`A9_BATTERY_OUT`). A prepared private header/tool tree is about 6 MiB;
there is no full kernel rebuild or shared build mutation. Native Kbuild command
is saved in `module-command.json`. Image hashes are `out/SHA256SUMS`, with
recipe/main identity and ABI hashes in `artifacts.json`. Output budget is 1 GiB
plus a retained 15 GiB free floor. No firmware/binaries belong in git.

The five focused host groups in `test-policy.c` cover stock JEITA boundaries,
FCC interpolation, every OCV knot, boot estimate saturation/interpolation and
signed current integration. ARM64 module build uses `W=1` and native modpost.
The image recipe verifies its payload readback through the existing refresher,
health identity and ext4 filesystem. These checks do not prove charging.

## Coordinator phone test (worker has run none)

Use `out/boot.img` and `out/vendor.img` in the next owned integrated slot-B POC.
If combining with another boot derivative, change **only**
`/soc@0/spmi@1c40000/pmic@2/charger@1000` compatible to
`hisense,hlte556n-pmi632-battery` and overlay this module/rc onto the refreshed
vendor. Do not apply both battery drivers to the same node. The supplied boot
uses public R4 as input; any runtime/private radio-address integration remains
with the coordinator's existing POC path.

Read on the phone as root:

```sh
dmesg | grep 'pmi632-battery'
ls /sys/module/pmi632_battery
dumpsys battery
cat /sys/class/power_supply/battery/uevent
cat /sys/class/power_supply/a9-usb/uevent
for n in 1 2 3 4 5; do
    cat /sys/class/power_supply/battery/charge_counter
    cat /sys/class/power_supply/battery/current_now
    cat /sys/class/power_supply/battery/capacity
    sleep 10
done
```

Expect model `A9-PMI632-sampled-CC-POC`, realistic temperature, FCC/FV appropriate
to that temperature, Android level equal to sysfs and Android charge counter
tracking sysfs. Compare counter delta with the measured current integral:
100 mA for 1 minute is about 1667 uAh. Percentage may stay unchanged across a
short sample because 1% is roughly 40,000 uAh. Record USB `present`, `online`,
`usb_type`, battery `status` and current while the coordinator performs a real
USB VBUS off/on cycle through the existing USB setup. Android `dumpsys battery
unplug` is a simulation and supplies no evidence of physical removal. If the
host cannot switch VBUS while retaining custody, report that observation as
untested rather than inventing a plug/unplug result.

## Remaining limits

This is a sampled software fallback, not Qualcomm's FIFO/model/capacity-learning
implementation. Initial OCV accuracy, QG freshness/current scale under actual
load, net energy balance, the secondary override, hardware termination and
watchdog/comparator behavior still need phone evidence. Termination thresholds
and Type-C/QC/PD negotiation remain inherited; the BC1.2 input policy is not a
full SMB5 port.
USB `usb_type` reports SDP/CDP/DCP; the generic supply type remains `USB`.

The fallback does **not** count through suspend or missing samples longer than
5 seconds. It reports CAPACITY/CHARGE_COUNTER unavailable after that gap until
a new full anchor (or a reload/reboot with a new seed). Charging stops in
suspend and resumes polling on wake. This is an explicit B3/B6 integration gap,
not stock suspend-charging parity. No daily-use qualification is claimed.

Optional standby drain now has a separate hardware-batch measurement and USER
input pause; see [stock evidence and limits](drain-stock.md). It is off by
default and does not change the ordinary sampled counter across suspend.
