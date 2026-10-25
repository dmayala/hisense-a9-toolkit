# AW8695 stock steady strength — 2026-10-21

Status: host implementation/build checked; user feel comparison pending.
The user reports `integrated-20261021n` is **stronger than stock**. This
supersedes the earlier weak-buzz reports. The default now reproduces stock's
steady-wave peak, RAM_LOOP bypass and software battery compensation. The
previous boosted profile remains selectable, and base gain remains adjustable.

## Before and after

These register values are hexadecimal. In particular, dmesg `GAIN=80` means
`0x80` = decimal128; it does not mean gain80/255. `BSTVOL=62` means `0x62`,
whose voltage field is `0x22`. That field requests the same nominal8500 mV
in stock and in our previous build, but only matters while boost is selected.
The encoded quantized request is8414 mV, not a measured motor voltage.

| Setting | integrated-20261021n / previous profile | New stock steady default |
| --- | --- | --- |
| Active SYSCTRL04 | `0x42`: 1x RAM, **boost** | `0x40`: 1x RAM, **battery bypass** |
| Stopped SYSCTRL04 / GO05 | `0x41` / `0x00` | `0x41` / `0x00` |
| BSTVOL3a | Observed `0x62`, voltage field`0x22` | `0x62` retained; boost inactive |
| DATDBG39 baseline | Observed `0x80` (128), read from reset | Same reset baseline`0x80` |
| DATDBG39 during full effect | Fixed`0x80` at baseline128 | `min(floor(128*4200/VBAT_mV),179)` |
| Signed RAM peak | ±126 continuously | **±78**, stock steady wave4 peak |
| PWM2e / data rate | Observed`0xc1`; field`0x40` =24 kHz; 1x | Same |
| BSTCFG34 current field | Code1 (2 A boost limit) | Same dormant boost configuration |
| ADCTEST66 compensation bit6 | Clear | Clear; compensation is software |

For example, stock's ADC raw176 gives4193 mV and gain`0x80`; raw168 gives4003 mV
and gain`0x86`; raw126 gives3002 mV and capped gain`0xb3`. These are formula
examples verified on the host, not new phone measurements. The measured F0
and eight-cycle resonance tuning from [resonance.md](resonance.md) remain.

The sine sample peak is78/126 of the previous peak. Bypass also changes the
drive supply. Neither that ratio nor a gain code alone establishes a force,
motor voltage or subjective-strength ratio.

## Stock evidence

Private artifacts: `~/A9-build/mainline/vibration-stock-20261021/`.
Selected files were freshly dumped with read-only `debugfs` from the cached
stock vendor image `../android/ref/vendor_a.img`. The HAL, firmware and build
properties were also extracted from the golden-derived
`../android/golden/vendor_b.img` and matched byte for byte
(`vendor-extraction.json`, `golden-comparison.json`). The golden vendor carrier
is SHA256`2a7d5d8605181615d47ddc9ac992c4b06daec11eadb740d626a9fd0868678e2a`;
its existing extraction receipt identifies the2026-10-14 golden super source.
This is the phone's December2022 vendor, rather than substituting an older
HAL from the July2022 HLTE556N package. No firmware or device data is committed.

**HAL.** `lib64/vendor.qti.hardware.vibrator.impl.so`, SHA256
`7edf57b98332e7fdc287732c71446f7f63312e270397add24423307429d3a7cd`.
REA Ghidra12.1.4, `rea-hal.json`, successfully decompiled:

| File offset / REA address | Function / Evidence |
| --- | --- |
| `0x4c94` / `0x104c94` | `LedVibratorDevice::on`: `ev_53014b333d7b486feae84d2654196a15258df4a7955fd49864ec641073700664` |
| `0x49b0` / `0x1049b0` | LED constructor: `ev_4ef4ec3669fb6324bd447342efd417c607639b7e6ebcee37a9c73143df3179a4` |
| `0x54c4` / `0x1054c4` | `Vibrator::perform`: `ev_87ce36056f5d7d7fa83452961ed4054abb3c89ea448db10b86cb7d3e044fb5a7` |
| `0x5ab0` / `0x105ab0` | `Vibrator::setAmplitude`: `ev_faea67978c97fe6ab3876701036110fd506249fbad795fb95dd5c3934bc32dc5` |

The LED on path writes `state=1`, optional `index=4`, the requested duration,
then `activate=1`; it does **not** set gain. The constructor enables the index4
write only for `ro.vendor.line.vibrator=true`, with fallback string`false`.
LED setAmplitude/perform are unsupported. The other InputFF backend has
effect-strength magnitudes`0x3fff/0x5fff/0x7fff` and float-amplitude scaling
through255. Those paths do not establish a half-gain write to AW8695's DATDBG.
Our HAL continues to send full FF magnitude for on/off and reject amplitude
control; there is no new per-effect Android amplitude multiplier.

**Driver and config.** Same stock symbolized kernel and merged DT identities as
[stock-strength.md](stock-strength.md). Kernel file offsets equal VA minus
`0xffffff8008080000` plus`0x1c0`. DT `mode=5` at file`0x303e8` selects timed
RAM_LOOP; `aw869x_play_mode` at file`0x73ee78..0x73ee90` uses bypass for that
mode. Boosted RAM and RTP are separate paths. DT boost8500 mV is at`0x302f8`.
`aw869x_misc_para_init` at`0x73fa58..0x73fa80` saves reset gain39, rather than
hard-coding255. These original `.asm` records remain under
`vibration-strength-20261020/`.

`vibrator_work_routine` calls `ram_vbat_comp(1)` for RAM_LOOP. Probe enables that
flag at VA`0xffffff80087ce1a8` (`w22=1` at`0xffffff80087ce08c`). REA separately
decompiled the complete264-byte `ram_vbat_comp` at
VA`0xffffff80087d1570`, file`0x751730`:
Evidence`ev_6d944bed35e999c53aa49219c10cb963b2a205f146f6625790a52ed1f5f89d4c`,
`rea-comp.json`, exact-byte extraction identity in
`stock-comp-extraction-complete.json`. It computes saved_gain*4200/VBAT and
caps179. Our implementation uses that formula. Existing instruction evidence
`vibration-f0-20261020/aw869x_get_vbat.asm` establishes detection via RAMINIT,
DETCTRL5f bit1, VBATDET62, `6100*raw/256` and3000..4500 mV clamping. Our poll
has a10 ms bound and checked I/O; timeout or cleanup errors cannot start output.
Live gain changes reuse the latest detected VBAT without restarting playback.

**RAM bank.** `/firmware/haptic_ram.bin`,2311 bytes, SHA256
`dfbb039adedfc85d797645fc3652ee1631899d2aeb15db51b1f8c4d7c332929f`.
Checksum`dacb`, base`1000`; the independently parsed table is
`vibration-f0-20261021/stock-ram.json`. Wave4 has102 samples, offsets`0x60a..0x66f`,
peaks±78. Transient waves1/2/3 peak±126 and have envelopes. Our steady sine
uses the stock steady peak, not a transient peak for the whole effect. It is
generated/resampled code, not a copy of vendor waveform bytes.

**Props/settings limits.** Bounded searches of vendor `build.prop`,
`default.prop`, `system_properties.xml`, extracted stock vendor init,
golden product `build.prop` and retained stock system_ext `build.prop` found
no vibration gain override or definition of `ro.vendor.line.vibrator`.
The HAL's property check is observed; its value in every original stock boot
is not established by those files. No current settings database or saved
calibration partition was read. Stock frameworks/apps can vary duration and
effect choice, so no universal effective strength exists for every effect.
This default matches the documented **steady RAM_LOOP** electrical settings
and waveform peak; it does not reproduce every stock transient envelope or
claim an already measured feel match.

REA's HAL loader adds image base`0x100000`; raw kernel addresses are original
VAs. HAL perform and the raw driver slice have partial external/indirect-call
coverage. Firmware, symbolized instructions and DT provide the other bindings.
All conclusions above are static observations or explicitly qualified
interpretations. No live target was opened.

## Configuration and one-minute user comparison

Device sysfs attribute `stock_profile`: `1` (default) selects peak78, bypass
and software VBAT compensation; `0` selects previous peak126, boost and fixed
gain. Switch while idle; it rewrites RAM and preserves measured F0 and base
gain. SRAM failure leaves `ready=0` and playback blocked. `gain` remains a live
0..255 **baseline** knob; stock mode caps the compensated register value179.
Neither setting persists across driver reload. The gain baseline defaults to
the hardware reset read, expected128 (`0x80`).

After agent includes the module in the next integrated build, use the
coordinator's existing root shell, with the user holding the phone. First feel
the earlier strong profile; then the new default at exactly the same baseline
gain and duration. These are coordinator instructions, not commands run by
this host-only worker.

```sh
H=/sys/bus/i2c/devices/0-005a
cmd vibrator_manager cancel
sleep 1
printf '128\n' > "$H/gain"
printf '0\n' > "$H/stock_profile"
cmd vibrator_manager synced oneshot -a 500 255
sleep 2
printf '1\n' > "$H/stock_profile"
cmd vibrator_manager synced oneshot -a 500 255
sleep 2
cmd vibrator_manager synced oneshot -a 100 255
sleep 1
cat "$H/stock_profile" "$H/f0_measure"
dmesg | grep -E 'a9-aw86xx|stock_profile|FF playback|standby wait'
```

Use the rest of the minute for a few keyboard taps and the user's comparison
with stock. Expected new active SYSCTRL40, lower peak78, and battery-dependent
GAIN around80..b3; BSTVOL62 alone is unchanged and does not mean boost is on.
Leave `stock_profile=1`. If a small subjective correction is still needed,
adjust base gain around128 while retaining the stock profile and record the
chosen value and actual GAIN/VBAT. User feel is the final acceptance check.

## Host build

`~/A9-build/mainline/vibration-stock-20261021/a9-aw86xx.ko` builds with `W=1`
against the existing power-kernel. All36 import CRCs and vermagic match
`integrated-20261021n`; borrowed kernel files are unchanged. Five focused
source-extracted checks cover profiles, ADC/gain compensation, switching
failures, retained F0 and ADC cleanup/legacy-chip behavior. Commands and logs
are in the artifact directory. No new HAL, DT, boot or vendor image was built.
Rebuild the vibration module when integrating; retaining the old module in a
vendor-only refresh will retain the stronger settings. Preserve the integrating
branch's separate reset-across-suspend change when merging this driver.
