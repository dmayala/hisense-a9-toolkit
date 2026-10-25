# Hisense A9 PMI632 charger and QG bring-up

**Date:** 2026-10-15. **Status:** boot 3 supplied numeric observe/FV telemetry;
the updated health fix is offline validated only. No agent phone operations,
no full charging validation. **Not ready for unattended charging
or daily Android use.** Sources and integration instructions:
[`scripts/mainline/pmic/`](../scripts/mainline/pmic/README.md).

**MEASURED** below means inspected from the phone's saved firmware/logs,
disassembled, checked offline, or explicitly attributed to the supplied mainline
captures. The coordinator supplied the boot 3 results; this agent only read files.
**INFERRED** means hardware behavior still needs coordinator measurements.
Only the coordinator may operate the phone. This work ran no adb, fastboot,
edl, health, flashing or first-boot command and wrote nothing to the backup.

**Current telemetry (follow-up 5): stock-qualified OV health.** Section 9
analyses boot 3 and fixes the omitted stock FV+40mV qualification for the
hardware OV bit. Equal quantized QG codes no longer cause a false stale error
in observe. Section 8's one-shot stock FV write, default parameters and all
write guards remain unchanged. Inherited JEITA/watchdog policy is unvalidated.

## 1. Closest upstream work

**MEASURED:** local Linux 7.2.10 (`a316467911dc`, v7.2.10 plus existing A9 patches)
has `drivers/power/supply/qcom_smbx.c`, Kconfig `CHARGER_QCOM_SMB2`, with
`qcom,pmi8998-charger` and `qcom,pm660-charger` matches. It is the nearest
primary charger, but its float encoding, temperature/OV status bits, Type-C
registers and initialization are not a PMI632 variant table. In particular,
SMB2 0x1007/0x100d bit definitions cannot be reused blindly.

**MEASURED, fetched 2026-10-15:** linux-next `next-20261014`, commit
`d419d682257d813f6cb7a28e42b35d0edf4ad038`, still has those two matches and no
QG driver in the power-supply Kconfig. Its source is available from the
[linux-next mirror](https://kernel.googlesource.com/pub/scm/linux/kernel/git/next/linux-next/+/d419d682257d813f6cb7a28e42b35d0edf4ad038/drivers/power/supply/qcom_smbx.c).
The [SMB5 v4 cover letter](https://lore.kernel.org/r/20260820-submit-qcom-smbx-send-v1-v4-0-818dabb2771e@snyders.xyz/)
(2026-08-20; also read via the LKML archive) covers PM8150B/PM7250B. It depends
on SMBx watchdog/health/FV fixes. It does not establish PMI632 compatibility.
Casey Connolly's earlier
[SMB5 series](https://lore.kernel.org/r/20250619-smb2-smb5-support-v1-0-ac5dec51b6e1@linaro.org/)
is useful prior art for separating generation-specific registers.

Searches for PMI632, PM6150, SMB5, QG and qgauge did not find a PMI632 charger
or QG driver ready for mainline. Direct lore search endpoints were inaccessible;
this is **not** a claim of an exhaustive review of every mailing-list message.
A [Fairphone battery-temperature series](https://lore.kernel.org/r/20260220-bat-temp-adc-v2-5-fe34ed4ea851@fairphone.com/)
explicitly describes a future QGauge consumer and a 10k NTC / 30k pull-up
conversion. Downstream community PM6150 QG/charger experiments exist, including
[asidko's sources](https://github.com/asidko/pm6150-charger-mainline); their board
limits, current-sign assumptions and SOC corrections are not A9 evidence.

Other local drivers (MEASURED):

| Driver | Applicability |
|---|---|
| `qcom_battmgr` | Remote battery service over PMIC GLINK; not a direct SPMI driver. No established ADSP battmgr service for the A9; not a substitute. |
| `qcom-spmi-rradc` | PMI8998/PM660 round-robin ADC. A9 uses PMI632 ADC5-lite at 0x3100 plus QG at 0x4800; not register-compatible ADC plumbing. |
| `qcom-spmi-adc5` | Suitable existing PMI632 ADC provider; lacks the 0x2a battery-thermistor voltage entry in this tree. Proposal 0001 adds it. |
| `qcom,qpnp-smb5`, `qcom,qpnp-qg` downstream | Reference implementation, with PMI632-specific limits and workarounds. Not upstream drivers. |

Public downstream cross-checks:
[PMI632 support commit](https://android.googlesource.com/kernel/msm/+/a9e10914e5fe315942622e6f9333d458fd219197),
[PMI632 PD/AICL exclusions](https://android.googlesource.com/kernel/msm/+/81e5cadccf145d72e1cd5e9491c56a42856037b9),
[SMB5 parameters](https://android.googlesource.com/kernel/msm/+/60e67638d95e7fd48f602b15729ea13c13c478d5/drivers/power/supply/qcom/qpnp-smb5.c),
[QG conversions](https://android.googlesource.com/kernel/msm/+/60e67638d95e7fd48f602b15729ea13c13c478d5/drivers/power/supply/qcom/qg-util.c).
The phone's binary/DT takes precedence wherever these differ.

## 2. Stock evidence and compatibility

Inputs are the read-only `$A9_DATA/Backups/edl_2026-10-14_110759/lun4/boot_a.bin`
and `dtbo_a.bin`, not the different stock-package kernel. The raw Image is
34,476,544 bytes, SHA256
`2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`.
The 88,658-byte overlay entry SHA256 is
`74138d6535db5897db0c80ae093dc5840e8c51cf578b5d395499ded185069931`.
Reproduction and numeric extracts are in
[`inspect-stock.py`](../scripts/mainline/pmic/inspect-stock.py) and
[`stock-evidence.json`](../scripts/mainline/pmic/stock-evidence.json).

**MEASURED, stock base DT:** primary SPMI USID 2, CHGR 0x1000, DCDC 0x1100,
BATIF 0x1200, USBIN 0x1300, Type-C 0x1500, MISC 0x1600, QG 0x4800, QG SDAM
0xb100. The secondary SMB1355 is on i2c1 at 0x0c. The proposal leaves Type-C,
OTG, SDAM, ESR and the QG state machine untouched and asserts secondary OFF.

**MEASURED, overlay fragments 13/15/16:**

| Stock property | Value |
|---|---|
| Battery type | `Hisense_HLTE556N_Highpower_4000mAH_PMI632_averaged_MasterSlave_Feb16th2022` |
| Profile version / ID | 100 / 100 kΩ batt-ID, 15% ID selection range |
| Max voltage / CC-CV threshold | 4,450,000 / 4,430,000 µV |
| Fast-charge current | 3000 mA |
| USB ICL / float-source ICL | 2,000,000 / 900,000 µA |
| HVDCP2 maximum ICL | 3,000,000 µA (not permission to use it on mainline) |
| JEITA 0–10.0 C | 800,000 µA, 4,450,000 µV |
| JEITA 10.1–45.0 C | 3,000,000 µA, 4,450,000 µV |
| JEITA 45.1–53.0 C | 1,200,000 µA, 4,100,000 µV |
| Soft thresholds, cold/hot | raw 0x2aac / 0x0fdc; hysteresis 0x2767 / 0x1149 |
| Hard thresholds, cold/hot | raw 0x36b9 / 0x0ca0; do not treat these as Celsius |
| Charger / SMB temperature setting | 800 in downstream deci-C convention (80 C); die mitigation enabled |
| Software JEITA / step charging | both enabled; secondary charger config = 2 |
| Thermal mitigation current table (mA) | 3000, 2600, 1100, 800, 1500, 1200, 500, 500, 500, 500 |
| QG cutoff / low / cold-low / empty | 3350 / 3450 / 3750 / 3250 mV |
| QG iterm / auto recharge | 500 mA / 98% |

The thermal table is nonmonotonic; its policy is not copied. The profile has
large temperature/OCV/impedance tables and capacity-learning data. The proposal
neither runs Qualcomm's QG userspace model nor writes an apparently authoritative
SOC into SDAM. The nominal 4000 mAh is a profile label, not measured capacity.

**MEASURED, `smb5_pmi632_params` at 0xffffff80097000d0:** entries are **40 bytes**.

| Register | Stock encoding | Relevant difference |
|---|---|---|
| 0x1061 FCC | 0–3 A, 50 mA/code | Same address as SMB2; scale must be selected per PMIC |
| 0x1070 FV | 3.6–4.8 V, 10 mV/code | A9 4.45 V = 0x55; proposal 4.10 V = 0x32 |
| 0x1370 USB ICL | 0–2.2 A, 50 mA/code | This binary's maximum is 2.2 A, unlike public 3 A tables |
| 0x1106 max ICL / 0x1107 ICL status | 50 mA/code | Public SMB5 source uses 0x1108 for the latter; do not assume compatibility |
| 0x1092/0x1093 JEITA FCC reduction | 25 mA/code | Reduction, not an independent total FCC target |

**MEASURED in stock functions:**

- USB presence: `smblib_get_prop_usb_present` 0xffffff8008a2cc18 reads 0x1310 bit4.
- Battery presence: `smblib_get_prop_batt_present` 0xffffff8008a2dc98 rejects
  0x1210 bits4/5 (thermistor/ID or terminal missing).
- Health: `smblib_get_prop_batt_health` 0xffffff8008a2e288 reads battery OV
  at 0x1007 bit1 and temperature at **0x100d bits2–5**, unlike SMB2.
- `smblib_update_jeita` 0xffffff8008a3a3e8 writes thresholds hot-first,
  big endian: hard 0x1098–0x109b = `0c a0 36 b9`.
- Voltage: `qg_get_battery_voltage` 0xffffff8008a47f20 reads LE16 at 0x48c0;
  µV = raw × 194637 / 1000.
- Current: `qg_get_battery_current` 0xffffff8008a47cf8 holds burst data using
  0x4842 bit0, then reads signed LE16 at **0x48c6** and releases the hold.
  Scale = 152588/1000 µA/code for QG subtype3, 305176/1000 for subtype4.
- Temperature: `qg_get_battery_temp` 0xffffff8008a47b98 calls the IIO provider;
  Hisense also adds a hot-temperature wake/count path. Overlay 15 replaces
  the base 0x4a input with **0x2a, 30k pull-up**.
- Extracted IKCONFIG has `CONFIG_QPNP_SMB5=y`, `CONFIG_QPNP_QG=y`,
  `CONFIG_HISENSE_CHARGE_FG_FUNCTION=y`, and
  `CONFIG_HISENSE_BATTERY_THERM_NTC_10K=y`.

The NTC table is `adcmap_batt_therm_30k_NCP_XH103` at 0xffffff8009731278:
70 (mV, deci-C) pairs. At 25 C interpolation gives approximately 469 mV,
consistent with 10k / (30k+10k) × 1.875 V. `adc_probe` defaults absent
`qcom,lut-index` to zero; `lut_table_30[0]` points to this table (pointer
recovered from the original Image's RELATIVE relocation). `adc_read_raw`
passes that index to `qcom_vadc_hw_scale`. **The profile's old 100k thermistor
metadata is not the ADC curve actually selected by the firmware.** Copying
the generic mainline 100k curve would give the wrong temperature.

**INFERRED in this proposal:** read-only instantaneous current uses signed
LE16 at 0x48c2 (QG LAST_ADC_I), not stock's held burst average. Its scale/sign
and physical sense path must be checked. The driver reverses raw QG sign to
Linux's positive-charge/negative-discharge convention. It performs two matching
four-byte samples to reject tearing, but that does not prove ADC freshness.
It fails after 30 seconds with no raw voltage/current change. It does not
initialize QG; firmware may leave it stopped. This is a deliberate validation
gate, not a reason to fabricate readings or use a latched boot OCV as live data.

## 3. Original control-mode behavior and risks (historical telemetry default)

The DT node is disabled. When deliberately bound, the driver immediately clears
0x1042 bit0, sets 0x1340 bit0, and asserts 0x1648 secondary OFF before dependencies
can defer probe. `allow_charging` defaults false and is read-only after load.
**A disabled/unloaded driver cannot stop firmware-autonomous charging before
probe.** The first coordinator step is to inspect that inherited state.

Even with the parameter true, an enabled node must carry
`hisense,charging-validated;`. It is absent from the supplied fragment. Neither
compilation nor a successful register read qualifies as that validation.

When both gates are supplied, the proposal programs/read-verifies 4.10 V,
500 mA FCC, 500 mA ICL, stock hard JEITA thresholds, shortest 192-minute fast
charge timer, and watchdog charge-disable-on-bite. It disables HVDCP, accepts
only 5 V and detected BC1.2 CDP/DCP, sets USB2 500 mA mode, and leaves APSD
negotiation/Type-C control to existing hardware. SDP, unknown, floating, QC and
PD-only sources can remain OFF. There is no high-current override or parallel
charging. All configured limits are below stock's relevant JEITA/current limits.

Once per second it checks presence, USB UV/OV/collapse, battery OV, all known
hot/cold hardware bits, PMIC die and connector flags, an independent fresh NTC
conversion, battery voltage, source classification and OTG OFF. It only permits
10 C < battery < 40 C, battery >3.350 V and <4.100 V, VBUS 4.5–5.5 V, die <70 C.
This narrower all-or-off thermal policy replaces the stock soft-JEITA current
bands for initial bring-up. It preserves hardware soft reductions, which may
further reduce output. It does not guess Celsius equivalents for hard raw codes.

Every settings readback is checked before petting the watchdog and enabling
input/charging. Source/OTG are checked again after ADC conversion. Measured
charging current above 600 mA locks off. I/O failures, mismatches or stale QG
samples latch charging OFF until reload/reboot. Both OFF writes are attempted
on failure even if one fails. It cancels work and stops charging for suspend,
unbind, shutdown and probe failure after configuration. No charging during
suspend is supported. Cached values older than five seconds return ENODATA.

The watchdog timeout request is 16 s bark + 8 s bite, with disable-charge on
bite. **INFERRED:** immediate-start 0x1651 bit0 follows the SMB-family layout;
stock PMI632 enables-on-plugin bit1, but does not prove bit0 behavior. Prove
actual charging stop on missed pets and no AFP/reset behavior before enabling
normal use. There is no software recovery that can guarantee OFF if SPMI/TZ
access fails or the CPU hangs; physically disconnect the charger on any OFF
write failure. Independent hardware JEITA and watchdog are mandatory backstops.

The module exposes `battery` (Battery) and `a9-usb` (USB) using standard
power_supply properties/uevents, suitable for normal Android health discovery.
Android's actual Health HAL policy remains untested. CAPACITY is a clamped
linear **terminal-voltage estimate** (3.35 V = 0%, 4.45 V = 100%); MODEL_NAME is
`A9-PMI632-voltage-estimate`. It is load/temperature dependent and **not**
coulomb counting or measured OCV. The reduced 4.10 V charging ceiling means it
will not normally display 100%. No artificial FULL status is emitted. No
battery-backed shutdown policy is implemented: Android must react to real
voltage/critical conditions; this prototype does not protect against deep
discharge while left running. A 500 mA input may be below Android's load, and
charging OFF/suspend intentionally removes input power. **Positive energy
balance and battery safety have not been established.**

## 4. Original control-mode checks — coordinator only, NOT RUN

Historical control-mode procedure. For the current observe telemetry image,
use section 8 instead; do not expect OFF readbacks or write those values.

Use the coordinator's established phone transport/serial selection. The
commands below are examples of exact commands to run as root on the mainline
phone; they do not mount debugfs, load modules, write registers or flash.
If debugfs is unavailable, stop and arrange it in the next offline image.
Do not enable this proposal to obtain the initial register baseline.

First, discover supplies and the regmap name:

```sh
adb shell 'uname -a; cat /proc/cmdline; ls -d /sys/class/power_supply/* /sys/kernel/debug/regmap/*; dmesg | tail -120'
adb shell 'for f in /sys/kernel/debug/regmap/*/name; do echo "$f"; cat "$f"; done'
```

On this kernel `qcom-spmi-pmic` uses 16-bit addresses, 8-bit values, max 0xffff,
stride1 and no hidden/precious ranges. Debugfs register lines are **9 bytes**.
For the discovered **USID 2** map (normally `0-02`), use lseek-based `dd` to
read only specific registers. Verify the first two returned addresses are
`0104`, `0105`, values `51`, `25`. Stop on any format/address/identity mismatch.
The command's arithmetic is small enough for the phone shell.

```sh
adb shell 'R=/sys/kernel/debug/regmap/0-02/registers; dd if="$R" bs=9 skip=$((0x104)) count=2'
```

Only after that identity check, record this allowlist. If the discovered name
is different, substitute that exact path. Do **not** pipe a full `cat registers`
through grep: that still reads every hardware register, including clear-on-read
QG status2 at 0x4809. `dd` must be a version that seeks on skip for this file;
stop if its output addresses do not match the requested offsets.

```sh
adb shell 'R=/sys/kernel/debug/regmap/0-02/registers; for a in 0x1006 0x1007 0x100d 0x1042 0x1061 0x1070 0x1090 0x1094 0x1095 0x1096 0x1097 0x1098 0x1099 0x109a 0x109b 0x10a2 0x1140 0x1210 0x1307 0x1308 0x1310 0x1340 0x1360 0x1362 0x1365 0x1366 0x1370 0x1607 0x1609 0x1648 0x1651 0x1653 0x4804 0x4805 0x4808 0x4843 0x48c0 0x48c1 0x48c2 0x48c3 0x48c6 0x48c7; do dd if="$R" bs=9 skip=$((a)) count=1; done'
adb shell 'R=/sys/kernel/debug/regmap/0-02/registers; i=0; while [ "$i" -lt 10 ]; do date; dd if="$R" bs=9 skip=$((0x48c0)) count=4; dd if="$R" bs=9 skip=$((0x48c6)) count=2; sleep 1; i=$((i+1)); done'
```

Expected/required interpretation before proceeding:

- QG 0x4804 = 0x0d, subtype 0x4805 = 3 or 4; QG_OK and present in 0x4808.
  Compare changing 0x48c0/2 with stock conversions; do not use 0x4870 boot OCV
  as live voltage. Compare 0x48c2 to burst 0x48c6, allow for sampling differences.
- Check 0x4843 bit7 (parallel sense). Do not enable with an unexplained sense
  mode; the software does not reinitialize QG and vetoes charging when bit7 is set. Establish actual current sign
  under a known discharge load and verify physical voltage independently.
- Record 0x1042/0x1340 inherited states; 0x1140 bit0 must be off. Validate
  0x1648 override and **independent SMB1355 disabled state** using the board's
  known I2C mapping/datasheet before treating primary FCC as total battery FCC.
  No guessed I2C bus transaction is provided.
- Temperature must agree with stock/external measurement, including the 10k
  curve rather than profile metadata. Verify raw hard thresholds and hardware
  comparator behavior, not just software Celsius. Check USB source/voltage,
  thermal flags and absence of SPMI errors.

After the coordinator has deliberately integrated and bound OFF mode, these
are further read-only observations (the binding itself writes OFF and ADC
conversion registers; it is a separate coordinator action):

```sh
adb shell 'cat /sys/module/pmi632_a9/parameters/allow_charging; for p in /sys/class/power_supply/battery /sys/class/power_supply/a9-usb; do echo "$p"; cat "$p/uevent"; done'
adb shell 'for d in /sys/bus/iio/devices/iio:device*; do echo "$d"; cat "$d/name"; ls "$d"/*label "$d"/*input 2>/dev/null; done'
```

Record labelled ADC inputs for battery-NTC voltage, VBUS and die temperature
using the discovered paths. A sysfs ADC read requests an ordinary conversion;
it is observational but not a zero-write PMIC transaction. Verify battery
`present=1`, realistic µV/µA/deci-C, expected negative discharge current, OFF
status and explicit voltage-estimate model. Stock hard thresholds must be
`0c a0 36 b9`; enabled proposal selectors would be FV=0x32, FCC=0x0a,
ICL=0x0a, USB mode=0x02. OFF mode does not program these selectors.

**Stop here and return the measurements to the coordinator.** No charging
has been authorized by these checks. Before setting the validation flag,
coordinator-controlled bench tests must demonstrate independent hard thermal
cutoffs, watchdog charge stop/no reset, USB removal, suspend, failed/stale
telemetry, secondary isolation, and actual current/voltage limits. Those tests
require controlled writes and physical measurements; this agent did not run
or invent their results.

## 5. Offline validation performed

- Separate O= directory `~/A9-build/mainline/sol-pmic/build-7.2.10`; copied existing
  config and Module.symvers, prepared private headers. Shared source/build files
  and kernel build scripts were not changed.
- Cross compiled ARM64 `pmi632-a9.ko` and the patched ADC5 object/module with
  `W=1`, no compiler/modpost warnings. These are not a linked boot image.
- Host policy tests under ASan/UBSan cover every thermistor knot and monotonic
  interpolation over 1,598,001 µV points, open/short/out-of-table rejection,
  all 128 source classifications, independent fault vetoes, cutoff boundaries,
  stock-limit inequalities, and 3,000,001 capacity-estimate points.
- All three proposals apply in an isolated file copy; generated driver/policy
  patch contents match canonical sources. Board DTS + fragment preprocesses
  and compiles with dtc, and fdtget reports charger status `disabled`.
- Driver checkpatch: no errors/warnings. Portable policy header has one
  ARRAY_SIZE style warning because it is also built outside Linux headers.
  No DT schema validation, hardware tests, watchdog/JEITA validation, healthd
  integration test or battery-runtime measurement has been performed.

## 6. Boot capture follow-up and hard-OFF telemetry proposal (2026-10-15)

The coordinator supplied `runs/20261015T041539Z-mainline-first-boot/capture`.
Only sanitized findings are recorded here; none of the capture files or the
command line was copied into git. These observations supersede the earlier
absence of any mainline run. They do not validate the power proposal itself.

| Dependency | Capture evidence | Conclusion |
|---|---|---|
| SPMI arbiter | dmesg line 241; devices line 10; drivers 406–407; iomem 10–13 | **MEASURED:** v5 arbiter bound at 1c40000, controller resource windows registered |
| PMI632 USID 2 parent | devices 18–22 show populated children; its ADC5 and GPIO children are bound | **INFERRED from successful children:** `pmic@2` parent probed; the capture enumerates platform devices, not the SPMI parent driver symlink |
| PMI632 regmap | ADC5 probe requires `dev_get_regmap(dev->parent)` before registration | **INFERRED, necessary dependency:** the USID2 parent regmap exists; its debugfs filename and charger/QG access permissions were not captured |
| ADC5, USID 2 | devices line 18; drivers 264–266; IRQs 26/27 labelled pm-adc5 | **MEASURED:** PMI632 ADC5 bound; two ADC5 IRQ handlers exist with zero interrupts in this capture. No battery conversion result is demonstrated |
| PON | devices 13–15; dmesg 246 and 303–306 | **MEASURED:** PON is on **pm6125 USID 0**, not PMI632 USID 2; beacon readback was 2. No additional PMI632 PON node is needed |
| Charger / QG consumer | no charger@1000, QG consumer, or pmi632-a9 binding in devices/drivers | **MEASURED:** absent from this captured run; not evidence that the hardware blocks are inaccessible |

The current IIO provider can provide channels 0x08 (VBUS) and 0x06 (die
thermometer). The captured image predates the battery 0x2a channel/consumer.
Need the ADC5 voltage-channel addition, the 0x2a ratiometric DT child, and the
battery power node/driver binding. A separate QG DT driver is unnecessary for
this proposal: the combined consumer uses the parent's regmap at fixed 0x4800.
QG type/subtype, live data, NTC calibration and charger OFF state remain unknown.

The regulator summary's `0mA` columns are regulator constraints/state, **not a
battery-current measurement**. Neither that summary nor the clock summary
identifies a missing external supply/clock needed by this already-bound ADC5
provider. Battery SPMI peripherals do not get separate CPU iomem windows.
The capture ran the diskless shell image; it does not establish Android healthd
or a Health HAL running, and contains no battery power_supply measurement.

### Probe access audit — the OFF mode is not zero-write

Normal successful `a9_probe`, with charging disabled, executes this exact
primary-USID2 hardware sequence (early failures stop the sequence):

| Order | Read address | Other effect |
|---|---|---|
| 1 | 0x1042, then 0x1042 readback | read/modify/write bit0 = 0; the write is skipped if already correct |
| 2 | 0x1340, then 0x1340 readback | read/modify/write bit0 = 1: suspend USB input |
| 3 | 0x1648, then 0x1648 readback | read/modify/write mask0x1f = 0x08: secondary OFF override |
| 4 | 0x4804 | require QG peripheral type0x0d |
| 5 | 0x4805 | require QG ADC subtype3 or4 |

`qcom_pmic_get()` reads the parent's **cached** identity structure; DT reg and
compatible checks and regmap lookup read kernel memory, not hardware. The PMIC
parent previously obtained identity from **0x0104/0x0105**, not 0x0004/0x0005.
This corrects the first report's identity-check command; the earlier command
must not be used. IIO channel acquisition resolves provider metadata without
requesting conversions. Supply registration can invoke cached getters, which
do no I/O and return ENODATA before the first sample.

A scheduled worker can start before probe returns. Its exact read sequence is:
0x1310, 0x1210, 0x4808, 0x1006, 0x1007, 0x100d, 0x1607, 0x1609, 0x1140,
0x1307, 0x1308, 0x4843, then two bulk reads of 0x48c0–0x48c3. The latter pair
can repeat up to three times to reject inconsistent reads. It never reads
clear-on-read QG status2 at 0x4809 and never writes the QG block.

It then requests IIO conversions, in order, for 0x2a battery-NTC voltage,
0x08 VBUS voltage, and 0x06 die temperature. Each ADC5 request reads **six**
bytes 0x3142–0x3147, modifies channel/calibration/settling/enable/request fields,
and **writes those six bytes back**. It waits for IRQ or polls 0x3108 on
timeout, then reads 0x3150 and 0x3151. The SPMI IRQ controller can acknowledge
EOC at 0x3114. Thus even a standard IIO sysfs "read" is not a zero-write bus
transaction. On every OFF-mode poll, the two primary OFF RMW/readbacks repeat.
Probe cleanup, suspend and shutdown also attempt those primary OFF operations.
No FV/FCC/ICL, JEITA, watchdog or charging-enable-ON write is made in hard-OFF
mode. Failed reads/disable readbacks prevent valid telemetry or fail the probe;
no fabricated 25 C/50% readings are returned.

The follow-up 2 proposal retained explicit OFF writes; **only its QG access
and power_supply getters were read-only**. Follow-up 3 supersedes that decision:
section 7 leaves the inherited charging configuration untouched.

### Hard-OFF integration from follow-up 2 (superseded for telemetry)

[`kernel-telemetry-PROPOSAL.patch`](../scripts/mainline/pmic/kernel-telemetry-PROPOSAL.patch)
is an **unapplied A9-project patch**, based on the coordinator's current working
integration (kernel patches 0007/0008, disabled board include, module packaging).
It adds a small Linux patch 0009 and changes only proposed kernel build/config:

- `CONFIG_CHARGER_PMI632_A9=y`: built in, so no initramfs module load is needed.
- `CONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY=y`: compiles out charging
  configuration **and** the worker's enable path, even if a future command line
  or DT supplies the normal charging gates.
- Appends an enabled `&a9_pmi632_power` override to the **private copied DTS**,
  after build.sh's board copy, with validation property deleted. The existing
  include is reused, not duplicated; the project board DTS is untouched.
- Removes pmi632-a9 from the module closure because it is now built in. Adds
  config and enabled-DT checks to build.sh's proposed `check` function.

Do not additionally apply the standalone PMIC patches 0001–0003 when using
this integration: the coordinator's 0007/0008 already supply their code.
Standalone patch 0002 is refreshed to contain the hard-OFF option as well.
`make-kernel-proposal.py` regenerates the delta against the working baseline;
regenerate/review if another agent changes that baseline. The generator never
edits kernel/ files. The automatic source loop will apply the proposed 0009
only after the coordinator applies the A9-project patch and rebuilds.

Offline checks cross-compile both original and hard-OFF variants; the hard-OFF
module is a compile artifact at `sol-pmic/module-telemetry/pmi632-a9.ko`.
`tests/integration.py` applies the nested patch only in temporary copies,
checks shell syntax and the actual Kconfig parser with stub platform
dependencies, compares the resulting C to the canonical source, and compiles
an enabled DT with no validation flag. This does not rebuild a full Image or
prove on-device telemetry. No capture content is input to those tests.

Coordinator's next observations, using the existing mainline console/transport
(the captured image has ACM/NCM, not an established adb daemon), **NOT RUN**:

```sh
readlink /sys/bus/spmi/devices/0-02/driver
ls /sys/kernel/debug/regmap
R=/sys/kernel/debug/regmap/0-02/registers
dd if="$R" bs=9 skip=$((0x104)) count=2
# Require addresses 0104/0105, values 51/25; stop on mismatch.
dd if="$R" bs=9 skip=$((0x4804)) count=2
# After the coordinator has deliberately booted the integrated hard-OFF image:
cat /sys/class/power_supply/battery/uevent
cat /sys/class/power_supply/a9-usb/uevent
cat /sys/module/pmi632_a9/parameters/allow_charging
```

Use the discovered regmap name instead of 0-02 if different. The original
allowlisted checks still apply, with the corrected identity offset. Stop for
coordinator review on QG rejection, ADC/permission errors, ENODATA or failed
OFF readback. A successful rebuild exposes the standard battery interface to
Android health discovery **when Android is later running**; neither numeric
telemetry nor healthd behavior is established by the supplied capture.

## 7. Follow-up 3: pure observe firmware charging (2026-10-15)

Historical default; section 8 adds the coordinator-authorized FV exception.
This section's no-charger-write contract still describes fv_stock=0.

**Status: offline compiled proposal; no mainline register measurement or
long-duration charging validation.** The coordinator explicitly selected
observe to avoid actively stopping inherited USB charging during Android
sessions. This supersedes the hard-OFF telemetry procedure in section 6.
Observe guarantees no charger/QG register writes by this driver; it does not
guarantee that charging is enabled, safe, or sufficient to power Android.

### Zero-write contract and ADC exception

`CONFIG_CHARGER_PMI632_A9_OBSERVE=y` forces observe for the driver's entire
lifetime, including probe deferral/failure, poll errors, suspend, shutdown
and unbind. It defaults to y when TELEMETRY_ONLY=y. Neither `observe=0`,
`allow_charging=1` nor a validation DT flag can override the compiled mode.
A normal build can select it with the read-only load-time parameter
`pmi632_a9.observe=1` (module: `observe=1`). A normal control build without
this option still defaults to actively OFF; do not confuse the two modes.

In observe, `a9_off()` returns before any bus access, settings programming
is forbidden, and the worker returns before every control/enable path.
There is no RMW of 0x1042, 0x1340 or 0x1648, and no FV/FCC/ICL, JEITA,
watchdog-pet, OTG, QG hold/clear/ESR or SDAM write. In particular, read
failure or over-limit telemetry only reports an error; it cannot stop the
firmware's charger. Power-supply getters use cached samples, and there are
no writable power-supply properties. The compiled observe object's undefined
imports contain `regmap_read` and `regmap_bulk_read`, with **no register-write
or update-bits API**. This also covers compiler elimination of cleanup paths.

The explicit permitted exception is **the existing upstream ADC5 driver's
normal conversion path**, requested with `iio_read_channel_processed()`:
battery NTC 0x2a, VBUS 0x08, die temperature 0x06. The consumer has no ADC
register-write code. The provider reads/writes six bytes 0x3142–0x3147 per
conversion; its normal EOC interrupt path can acknowledge 0x3114. Thus default
observe with temperature is not literally zero writes across all PMIC bus
transactions. The telemetry proposal uses this upstream path only, as the
coordinator allowed. Other existing upstream drivers retain their own probe
and interrupt operations; this is not a system-wide zero-write guarantee.

For no ADC requests by this consumer either, use
`pmi632_a9.observe_adc=0` (module: `observe_adc=0`). It skips IIO acquisition
and all three conversions; QG voltage/current/estimated capacity remain,
TEMP returns ENODATA, and health is UNKNOWN unless a known fault or limit
violation is seen. It does not disable other consumers of the ADC provider.
The default `observe_adc=1` supplies the requested temperature telemetry.

### Exact observe probe and worker reads

Probe uses cached parent identity, DT metadata and the existing parent
regmap; it directly reads only **0x4804 then 0x4805**. QG type must be 0x0d,
subtype 3 or 4. IIO acquisition and power-supply registration request no
conversion. An immediately scheduled worker can run before probe returns.
Every successful sample reads in this exact order:

```
1310 1210 4808 1006 1007 100d 1607 1609 1140 1307 1308 4843
1042 1340 110b 1070 1061 1370
48c0..48c3 twice (up to three pairs if changing)
```

It then uses the three upstream IIO conversions above unless observe_adc=0.
There are no later driver register operations in observe. A read failure
stops that sample at the failing access. No 0x4809 clear-on-read access is
made. Identical QG data for 30 seconds is reported stale, with no restart or
hold write. Current scaling and freshness still require live validation.

Observe reports CHARGING from USB presence, hardware enable/state and
positive battery current; it never synthesizes FULL. USB ONLINE reads
**DCDC power-path status 0x110b**, VALID_INPUT bit0 and USE_USBIN bit4 with
suspend vetoes. Stock `smblib_get_prop_usb_online` disassembly confirms this
address, rather than 0x160b. FV/FCC are exposed as
`constant_charge_voltage_max`/`constant_charge_current_max`, and the USB
`input_current_limit` reports the inherited selector. Capacity remains an
uncalibrated terminal-voltage estimate, labelled in model_name. A selector
above normal-profile FV=4.45 V or FCC=3 A produces HEALTH_UNSPEC_FAILURE
without changing it; this warning is not a complete JEITA safety check.

### What firmware leaves, and what hardware can do without Linux

**MEASURED from saved stock boot logs**, not the mainline capture:
read-only inspection of golden `lun0/kdebuginfo.bin`, directory
`/kmon_log/kexmlog_0`, found the same sequence in three stock boots.
[`stock-boot-charger-evidence.json`](../scripts/mainline/pmic/stock-boot-charger-evidence.json)
contains only selected charger lines and their source line numbers, with no
raw capture or identifiers. For example, kmonlog_2026_0811_105015:

| Stage | Evidence | Interpretation |
|---|---|---|
| UEFI, line 189 | `EFI_PmicSchgInit fv=4500, fcc=1000` | Firmware requests 4.500 V / 1 A |
| UEFI, line 190 | `EFI_PmicSchgEnableHWJeita`, hot/cold FV values 105 and FCC values 500 | Hardware JEITA setup is attempted; exact units/threshold programming not established by this line |
| UEFI, line 191 | `ChargerPlatform_Init Enabled WDOG` | Firmware enables a watchdog; its handoff timeout/action is not established |
| Early stock Linux, lines 1234/1235/1237 | FCC=2,000,000 (0x28), FV=4,500,000 (0x5a), USB ICL=1,500,000 (0x1e) | Actual initial driver register reads; the 1 A request is not the observed FCC handoff value |
| Stock profile applied, line 1472 | FV=4,450,000 (0x55) | Linux lowers float voltage to the battery's stock profile |

These are **firmware-initialized settings**, not silicon power-on/reset
defaults. No trustworthy hardware POR FV/FCC/ICL defaults were recovered.
The stock driver's 3.6–4.8 V supported encoding range is not a default.
The latest mainline handoff values must be read; historical boots cannot
prove they match after a warm boot or different prior firmware state.

**INFERRED hardware behavior:** PMI632's charger state machine, USB detection
and already-configured hardware JEITA/thermal logic can continue running
without this consumer. Observe leaves all such configuration and any
secondary charger untouched. It supplies no Linux software JEITA/profile
updates, no new hardware safety limits, and no watchdog service. The saved
stock boots proceed into a Linux driver that does change configuration and
service policy; they do not demonstrate unattended bootloader-only charging
for hours. An inherited watchdog may eventually stop charging or have another
effect. Neither continued charge nor a positive battery power balance is proven.

**Within stock limits? No such guarantee is supported.** The observed 4.500 V
handoff target is 50 mV above the stock battery profile's 4.450 V. Initial
FCC=2 A is below the 3 A normal-temperature profile, but exceeds its 0.8 A
cold and 1.2 A warm ceilings unless firmware's JEITA applies appropriate
reductions. ICL=1.5 A is below the 2 A normal DT setting but above the 0.9 A
float-source DT limit, and source detection matters. Log values 105/500
alone do not validate this policy. The narrower control-mode limits from
section 3 are **not** applied in observe. This proposal preserves the state
requested by the coordinator while explicitly reporting this unresolved risk.

### Updated integration and coordinator's next read-only checks

The **unapplied** A9-project `kernel-telemetry-PROPOSAL.patch` replaces the
previous proposal. On top of existing kernel patches 0007/0008 it adds a
mail-formatted 0009 for the current git-am build loop, compiles the driver
built in with TELEMETRY_ONLY=y and OBSERVE=y, ensures an active fragment
include only in the private DTS copy, enables that node with validation
removed, and checks the final config/DT. A commented include is handled;
an active include is not duplicated. No kernel/ or shared Linux file was
edited. Standalone 0002/0003 were regenerated for separate pristine-tree use.
Do not stack them over the existing 0007/0008 integration.

Coordinator only, through the existing mainline console/transport; commands
below were **NOT RUN**. Before relying on inherited charging, inspect the
existing mainline map, then repeat on the reviewed observe image. The saved
capture does not contain these register values. Require 4-hex-address,
2-hex-value, 9-byte lines before using the targeted offsets; substitute the
discovered map name if needed. Do not dump the whole map: 0x4809 clears on read.

```sh
readlink /sys/bus/spmi/devices/0-02/driver
ls /sys/kernel/debug/regmap
R=/sys/kernel/debug/regmap/0-02/registers
dd if="$R" bs=9 skip=$((0x104)) count=2
# Require 0104: 51 and 0105: 25; stop on identity/format/address mismatch.
for a in 0x1042 0x1340 0x1648 0x1070 0x1061 0x1370 0x110b 0x1006 0x1007 0x100d 0x1090 0x1092 0x1093 0x1094 0x1095 0x1096 0x1097 0x1098 0x1099 0x109a 0x109b 0x1651 0x1653 0x4804 0x4805 0x4808 0x4843 0x48c0 0x48c1 0x48c2 0x48c3; do
    dd if="$R" bs=9 skip=$((a)) count=1
done
# Reviewed observe image only (cached consumer properties):
cat /sys/module/pmi632_a9/parameters/observe
cat /sys/module/pmi632_a9/parameters/observe_adc
cat /sys/module/pmi632_a9/parameters/allow_charging
cat /sys/class/power_supply/battery/uevent
cat /sys/class/power_supply/a9-usb/uevent
dmesg | grep -E 'pmi632-a9|a9-usb|battery'
```

Decode FV as `3,600,000 + raw*10,000` µV, FCC/ICL as `raw*50,000` µA.
FV=0x5a means 4.500 V, not the profile's 0x55. Record charger enable and
input-suspend states, actual voltage/current/temperature and repeated samples
for watchdog/charge behavior; return these to the coordinator before any
long session. These commands neither change limits nor enable/disable charge.
No corrective write command is provided or authorized here.

### Offline checks for this revision

ARM64 W=1 normal/control and forced-observe modules and the upstream ADC5
channel patch compile against 7.2.10 in the private O= directory. ASan/UBSan
policy checks now also cover observed charging/online state and the 4.500 V
limit warning. An import audit rejects register-write/RMW symbols in the
forced-observe object. Nested proposal application with actual git am, shell
syntax, actual Kconfig parsing (including the default-y relationship),
canonical C match and enabled-DT compilation pass in temporary copies.
No boot Image was linked and no hardware or Android healthd test was run.

## 8. Follow-up 4: one probe-only stock FV write (2026-10-15)

**Status: offline proposal, coordinator-authorized exception to pure observe.**
The telemetry image now defaults the load-time `fv_stock` parameter to 1 via
`CONFIG_CHARGER_PMI632_A9_FV_STOCK=y`. The new Kconfig option otherwise
defaults off, so pure observe defaults to fv_stock=0. The read-only parameter
`pmi632_a9.fv_stock=0` also restores pure observe in the telemetry image.
`observe=1 fv_stock=1` selects the exception in an ordinary module build.
FV_STOCK depends on OBSERVE; the exception is ignored in control mode.

The single permitted charger operation is the stock profile correction:
**one full-byte write of 0x55 to 0x1070**, decoded by the measured stock
encoding as `3,600,000 + 85*10,000 = 4,450,000` µV. It uses `regmap_write`,
not an RMW, a bulk write, an unlock sequence or any other register write.
The existing parent uses an uncached 16-bit-address/8-bit-value SPMI regmap,
so verification reads the register rather than a software cache.
This is the FV change demonstrated in the saved stock logs in section 7.

### Sequence, guards and failure behavior

The existing board/PMIC/QG identity checks, IIO acquisition, supply registration
and cleanup registration happen first. Those probe failures/deferrals occur
before the exception. With observe and fv_stock=1, the probe then executes:

1. Read 0x1070 once. Accept only observed stock handoff 0x5a (4.500 V) or
   stock profile 0x55 (4.450 V). A lower or unknown selector is unexpected;
   it is never raised. Failed/unexpected initial read latches a health error
   with **zero writes**.
2. Write 0x55 to 0x1070 **exactly once**, including if it was already 0x55.
   If that bus operation fails, latch its error with no retry or compensating
   write. Its actual hardware outcome can be uncertain.
3. After a successful write, read 0x1070 once. Anything other than 0x55,
   or an I/O error, latches a health error. There is no second write.

The attempt is after the last possible error/deferral return in probe. On
failure the driver stays bound and continues read-only telemetry so healthd
can see `HEALTH_UNSPEC_FAILURE`. `fv_error` is persistent and separate from
ordinary sample errors; a later successful sample or resume cannot clear it.
If the selector later drifts from 0x55, polling latches the same health failure
and never repairs it. The initial exception helper is called only once by
probe; polling, error handling, suspend, shutdown and cleanup do not call it.
A deliberate new bind or reboot is a new probe and therefore a new budget.

With fv_stock=0 the helper returns before all I/O, including the additional
probe FV read. The original observe sample reads still include 0x1070.
The existing OFF RMW guards at 0x1042/0x1340/0x1648 remain unchanged; no
charger enable/disable, FCC/ICL, JEITA, watchdog, OTG or QG write is allowed.
The existing upstream ADC5 conversion exception is unchanged. For no writes
by this consumer including ADC requests, set **both** fv_stock=0 and
observe_adc=0; TEMP then returns ENODATA as documented in section 7.

### Integration, validation and remaining limits

The unapplied `kernel-telemetry-PROPOSAL.patch` now adds FV_STOCK=y to the
three existing driver/telemetry/observe symbols and checks that configuration.
Its nested mail patch includes the new shared `pmi632-fv.h` sequence, and the
standalone 0002 proposal includes it too. Private-copy DTS enablement and the
missing validation property are unchanged. No kernel/ or shared source/build
file was edited; this does not apply or boot the proposal.

The previous no-write-import audit is superseded: the forced-observe object
now intentionally imports `regmap_write`, with exactly one call site and no
bulk-write/raw-write/update-bits imports. The actual sequence in pmi632-fv.h
is also exercised by host fault-injection tests: pure observe zero I/O,
every byte-valued initial selector plus an out-of-range value, every byte-
valued readback, and errors on initial read/write/readback. All paths enforce
at most one attempt at the exact address/value and no retries. ARM64 W=1
control and observe/FV modules, sanitizer policy/FV tests, actual nested git am,
Kconfig pure-default/telemetry-opt-in checks, DTS compilation and checkpatch
are offline checks; no hardware or Android healthd result is claimed.

A verified 4.450 V setting corrects the observed normal-profile FV excess;
it does not establish cold/warm policy, current limits, watchdog behavior,
secondary-charger behavior or hours of safe autonomous charging. It is not
the stock driver's full JEITA/charging policy. Failed verification deliberately
leaves the inherited hardware state untouched after that one attempt.

Coordinator-only next read-only observations, **NOT RUN** here, in the reviewed
observe/FV image through the existing mainline console. Use the same discovery
and identity/format prerequisites and targeted allowlist from section 7; never
read the whole register map or 0x4809. These extra cached-property checks make
the requested mode and outcome reviewable:

```sh
cat /sys/module/pmi632_a9/parameters/observe
cat /sys/module/pmi632_a9/parameters/fv_stock
cat /sys/module/pmi632_a9/parameters/observe_adc
cat /sys/class/power_supply/battery/health
cat /sys/class/power_supply/battery/constant_charge_voltage_max
cat /sys/class/power_supply/battery/uevent
cat /sys/class/power_supply/a9-usb/uevent
dmesg | grep -E 'pmi632-a9|stock FV'
# Only after confirming the map name, 9-byte line format and 0104/0105 identity:
R=/sys/kernel/debug/regmap/0-02/registers
dd if="$R" bs=9 skip=$((0x1070)) count=1
```

Expected successful readback is `1070: 55`, with the probe verification log
and `constant_charge_voltage_max=4450000`. Any latched FV error remains a
health failure even if later reads happen to show 0x55; report it to the
coordinator, with no rebind/reload or corrective write as part of these checks.

## 9. Boot 3: false OV health and live telemetry (2026-10-15)

**MEASURED:** coordinator capture
`runs/20261015T052915Z-mainline-first-boot/capture`, including late/ and e1/.
Only derived measurements are recorded here; no capture file, command line or
identifier was copied into git. The capture's probe log confirms the one-shot
0x1070=0x55 readback. This agent performed no phone operations. The health fix
below is compiled/tested offline and has not been tested on the phone.

| Derived measurement | Base (~4 s) | late (~306 s) | e1 (~369 s) |
|---|---:|---:|---:|
| QG battery voltage (V) | 4.469644 | 4.448428 | 4.448623 |
| Reported battery current (mA) | +104.522 | 0 | 0 |
| Battery temperature (C) | 26.8 | 27.7 | 27.7 |
| Voltage-estimated capacity (%) | 100 | 99 | 99 |
| Reported status | Charging | Not charging | Not charging |
| Old reported health | Over voltage | Over voltage | Over voltage |
| Configured FV (V) | 4.450 | 4.450 | 4.450 |
| Configured FCC (A) | 2 | 2 | 2 |
| USB ONLINE / ICL (A) | 1 / 1.5 | 1 / 1.5 | 1 / 1.5 |
| Independent ADC5 VBAT-sense (V) | 4.469829 | 4.447251 | 4.447836 |
| NTC voltage (V) | 0.444485 | 0.433650 | 0.433715 |

Approximate times are the phone-generated file mtimes, consistent with capture
completion markers at 4.55/306.88 seconds and E Ink core loading at 307.95 s.
The e1 power-supply snapshot is about 63 seconds after late, rather than a
simultaneous second copy. Last dmesg event time is not capture uptime.

### Terminal voltage and the actual stock OV policy

The initial reading is **19.644 mV above FV**, then drops **21.216 mV** by late
to **1.572 mV below FV**. The cell started full under stock Android and mainline
had just lowered firmware's 4.500 V target to 4.450 V. That small, settling
excess is compatible with near-full relaxation, control/sense-point tolerances
and transient charging behavior. The independent ADC5 battery-sense value is
only 0.185 mV above QG initially and within 1.2 mV in later snapshots, so a
large QG-only voltage offset is not supported by these measurements.

**INFERRED physical explanation, not a measurement of impedance:** positive
charging current raises terminal voltage above electrochemical open-circuit
voltage through resistance/polarization. Dividing the initial FV excess by
reported current gives about 0.188 ohm, but FV is a regulator target, not a
measured OCV, so that ratio cannot establish cell resistance or explain a
regulator overshoot by itself. The snapshots cannot isolate IR, calibration,
relaxation or sense-path effects. ADC5 VPH rising to about 4.52 V later is a
system power rail; it is not the battery's separately sampled VBAT voltage.

**The health alarm was a driver bug:** the proposal reported OV whenever
0x1007 bit1 was asserted. It omitted stock's second qualification. In the
phone's own `smblib_get_prop_batt_health` at 0xffffff8008a2e288, instructions
0xffffff8008a2e2fc–0xffffff8008a2e308 load **0x9c40 = 40,000 µV**, add it to
the effective FV vote, compare the BMS voltage and skip OV reporting below
that sum. Public downstream source agrees, but the phone binary is the
reference. This is a reporting margin, not permission to raise programmed FV.

The corrected driver requires **both hardware BAT_OV and voltage >= decoded
FV + 40 mV**. The FV used for this threshold is capped at the battery's stock
4.450 V profile, so an unexpectedly high selector cannot raise the alarm
ceiling. Lower configured FV, including control mode's 4.10 V, uses its lower
threshold. At FV=4.45 V the OV reporting boundary is **4.490 V**, inclusive.
All three captured voltages fall below it, so this particular alarm is false
by the stock health policy. Raw bit1 assertion is inferred from the old code's
reported result; no direct 0x1007 dump was supplied. It may persist after a
handoff transient, so it must not be interpreted alone.

No arbitrary time delay or extra hysteresis was added: the measured stock
40 mV margin addresses the observed fault. Hardware protection, FV=4.450 V,
the one-shot write/readback budget and latched FV errors are unchanged. Genuine
OV at the boundary still reports OV; implausible/failed samples, unsafe FV/FCC
and other known faults retain their existing failure paths. Other health flags
were not captured raw, so replaying this predicate does not prove the entire
health result will be GOOD on a new boot.

### Current sign, status and freshness

Positive battery `current_now` means current **entering** the battery in both
the [Linux power-supply ABI](https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-class-power)
and [Android BatteryManager](https://developer.android.com/reference/android/os/BatteryManager#BATTERY_PROPERTY_CURRENT_NOW).
The proposal negates the QG's native signed convention once, retaining
+104,522 µA = +104.522 mA charging, with negative values for discharge. Stock
QG's signed current scaling is confirmed in its binary; this consumer uses
instantaneous LAST_ADC_I rather than stock's held burst average. No sign flip
is needed for the public ABI. Physical polarity/scale still lacks an independent
discharge/current-reference measurement: STATUS=Charging is partly computed
from the same current value, so those two fields are not independent proof.
The IIO USB_IN_I_UV channel measures a USB sense voltage, not battery µA;
its numerically similar initial value cannot validate battery current.

Zero current and Not charging with USB ONLINE are compatible with a full cell
at/near its CV target. They do not identify termination versus an inherited
watchdog/inhibit state; CHG_STATUS and watchdog register observations are
needed to distinguish those. Estimated capacity 100→99 follows the linear
voltage estimate, not measured coulomb loss or a calibrated 1% discharge.

Voltage, temperature, NTC ADC and status changes demonstrate the snapshots
are not a single frozen sample. late→e1 QG voltage increases by **195 µV**, one
194.637-µV raw voltage step, while current stays zero. This supports continued
voltage updates at the sampled times, not an uninterrupted conversion-age
guarantee or proof that all zero-current readings are freshly converted.

In observe, unchanged quantized voltage/current codes for 30 seconds no longer
cause ESTALE/health failure: a quiet, full battery can legitimately repeat
codes, and no conversion sequence counter is measured. I/O failure and the
five-second successful-sample age check remain; the 30-second fail-closed
heuristic is retained for experimental active control. Read success proves
bus/sample acquisition, not physical ADC conversion age. No QG restart/hold,
fault clear or watchdog pet was introduced.

### Integration and next coordinator observations

The coordinator has integrated 0007–0009 and enabled observe/FV. Therefore
the refreshed **unapplied** `kernel-telemetry-PROPOSAL.patch` only adds a Linux
**NNNN-power-supply-pmi632-stock-health.patch** at the next free number in the
coordinator's patch series. It does not
rewrite applied 0009, change kernel build/config files or change the board.
Standalone 0002 is regenerated for pristine-tree use; 0003 now reuses the
coordinator's active board include. The generator/test handle either baseline.

Offline checks: ARM64 W=1 control and forced-observe/FV builds, ASan/UBSan
policy and FV fault-injection tests, boundary regression cases for all three
captured voltages and 4.489999/4.490000 V, lower FV and capped high-FV cases,
single-write call-site audit, nested git am/Kconfig/DTS checks and standalone
proposal application. Driver checkpatch has no errors/warnings; the portable
policy header retains its pre-existing ARRAY_SIZE style warning. The build
explicitly disables observe options only in its private O= config for the
control artifact, then separately compiles the forced-observe variant.

The coordinator's concurrent commit `78f8103` included these initially staged
driver, test and analysis changes before this agent's commit ran. That commit
is preserved. The separate PMIC follow-up commit updates proposal numbering
to avoid the coordinator's new E Ink patches; it touches only PMIC files and
this document. Regenerate the proposal if that series gains further patches.

Coordinator only, **NOT RUN** by this agent. In the reviewed next image,
collect cached battery/USB telemetry at early and late times. After the usual
map-name, nine-byte format and 0104/0105 identity checks in section 7, these
additional reads distinguish persistent BAT_OV and charger state without
clearing QG status or changing charging:

```sh
cat /sys/class/power_supply/battery/uevent
cat /sys/class/power_supply/a9-usb/uevent
R=/sys/kernel/debug/regmap/0-02/registers
for a in 0x1006 0x1007 0x100d 0x1042 0x1070 0x110b 0x1310 0x1340 0x1651 0x1653 0x4808 0x48c0 0x48c1 0x48c2 0x48c3; do
    dd if="$R" bs=9 skip=$((a)) count=1
done
```

Return results to the coordinator; no charge-enable, fault-clear, retry or
corrective-write command is supplied. A separately observed discharge sample
with known power conditions would validate current polarity independently.
