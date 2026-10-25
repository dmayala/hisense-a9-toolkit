# AW8695 amplitude and RAM data-rate follow-up — 2026-10-20

Historical candidate: the user now reports integrated-20261021n is stronger
than stock. See [stock-equivalent.md](stock-equivalent.md) for the lower default,
stock REA evidence and configurable comparison.

Coordinator k4 result: F0 detection succeeded, raw `0x065a`, **236.5 Hz**.
The motor's resonance is near the stock nominal 235 Hz. The next candidate
uses a generated **±126** sine (stock bank maximum), reads the hardware reset
gain as stock does, exposes a live `gain` knob, and fixes a separate RAM
upsampling setting. Normal RAM keeps the existing boost configuration.
No phone access or integrated image build was performed by this worker.

## Stock gain and wave evidence

Stock symbolized kernel:
`~/A9-build/mainline/sol-pmic/evidence/vmlinux.elf`, SHA256
`258f157ce827c8997be7e358c06b91fdcb0032d423e978c248d7f5fc858291b5`.
All kernel offsets below are **file byte offsets** using
`offset = VA - 0xffffff8008080000 + 0x1c0`.

| Question | Evidence and conclusion |
| --- | --- |
| What gain does ordinary RAM use? | `aw869x_misc_para_init`, `0x73fa58..0x73fa80`, reads register `0x39` and saves the byte as the base gain. `vibrator_work_routine`, `0x74eaf0..0x74eafc`, loads that saved gain and calls set_gain for RAM mode 1. It does not set a fixed `0xff`. The new module mirrors the reset-gain read and prints its actual result. |
| What gain does long RAM_LOOP use? | `vibrator_work_routine`, `0x74ea34..0x74ea50`, invokes `ram_vbat_comp(1)` before mode 5. `ram_vbat_comp`, `0x751760..0x751770`, computes `saved_gain*4200/VBAT_mV`, capped at **179** at `0x751788`. If compensation is disabled it uses the saved gain unchanged. For base128 this is128 at4200mV,153 at3500mV,179 at3000mV; these are calculations, not phone measurements. |
| Can stock gain be changed? | `gain_store`, `0x74f5cc..0x74f5e0`, saves the supplied value and calls set_gain. `aw869x_set_gain` writes byte register `0x39`. A sysfs gain setting is a raw code, separate from an Android amplitude argument. |
| Is `0x80` necessarily half the motor voltage? | Awinic's AW8697 family datasheet gives `0x80` as DATDBG's reset default (p.34). The stock kernel relies on the hardware default rather than substituting `0xff`. This source does **not** establish a motor-voltage formula of `GAIN/255`, so multiplying the user's normalized codes is not a measured output-voltage calculation. We retain the stock reset baseline and allow explicit live steps through255. |

Stock firmware source:
`~/A9-build/mainline/android/ref/vendor_a.img:/firmware/haptic_ram.bin`,
2311 bytes; extracted copy under
`~/A9-build/mainline/vibration-strength-20261020/stock-firmware/`.
SHA256 `dfbb039adedfc85d797645fc3652ee1631899d2aeb15db51b1f8c4d7c332929f`.
The cache contains `haptic_ram.bin`, not an `aw8695_haptic.bin` name.

| Wave | Header/sample offsets in that file | Signed peaks |
| --- | --- | --- |
| 1, transient,663 samples | table `0x5`; samples `0x19..0x2af` | +126 at `0x31`, -126 at `0x64` |
| 2, transient,510 samples | table `0x9`; samples `0x2b0..0x4ad` | +126 at `0x2c8`, -126 at `0x2fb` |
| 4, steady,102 samples | table `0x11`; samples `0x60a..0x66f` | +78 at `0x621`, -78 at `0x655` |

The new sine is generated as `round(126*sin(2*pi*i/102))`; no proprietary
waveform bytes are copied into source. It uses stock's **maximum transient
peak**, rather than reproducing stock wave4 or its transient envelopes. It
retains F0 resampling. The maximum gain code255 is an explicit experimental
setting, not a claim that stock always uses255 or that voltage stays linear.

## Stock HAL and long effects

The cached vendor `/lib64/vendor.qti.hardware.vibrator.impl.so` (copied into
`vibration-amplitude-20261020/`, SHA256
`7edf57b98332e7fdc287732c71446f7f63312e270397add24423307429d3a7cd`)
has identity-mapped executable/rodata file offsets used here:

- `LedVibratorDevice::on`, `0x4c94..0x4df0`, writes state, optional index,
  duration and activate. Its on/off path does not write a gain attribute.
  Strings: `state` at `0x293e`, `index` at `0x2980`, `duration` at `0x2b7a`,
  `activate` at `0x2b40`, values `1` at `0x2acd`, `4` at `0x2b49`.
- The optional index write at `0x4d18..0x4d58` selects **wave4** when its flag
  is true. Constructor `0x4a78..0x4b18` derives that flag from property
  `ro.vendor.line.vibrator` (string `0x287f`), comparing against `true`.
  The current stock slot-A property is unobserved; this is a conditional static
  path, not a claim that wave4 is selected in every stock boot.
- The InputFF backend's `setAmplitude` and 255 scaling are a different backend;
  finding that code does not prove a direct `GAIN=255` write to this chip.

Stock DT `mode=5` at `merged.dtb` byte `0x303e8` is the default timed mode.
`activate_store`, ELF `0x74f168..0x74f170`, copies the configured mode into
playback state. `vibrator_work_routine`, `0x74ea20..0x74ea78`, dispatches
mode5 to **RAM_LOOP** and mode4 to CONT. Thus the provided stock default for
long timed effects is RAM_LOOP, **not automatically CONT**. CONT is available
when explicitly selected. Its DT drive level125/overdrive155 are at
`0x303d8`/`0x303c8`; `aw869x_cont_config` maps them to `0x7b/0x7c` at ELF
`0x73e608..0x73e624`. The earlier stock-strength.md records the remaining
CONT/brake settings. This candidate keeps normal playback in boosted RAM.

## Independent RAM-rate correction

The old driver wrote SYSCTRL=`0x01` at probe and `0x21` while initializing
SRAM, clearing bits7:6. Stock `aw869x_play_mode` changes only the low mode bits
(e.g. ELF `0x73ecc8..0x73ecd8` and `0x73ed68..0x73ed74`), preserving those
upper bits. The [Awinic AW8697 family datasheet, p.29](https://datasheet.lcsc.com/datasheet/pdf/d50972553dddf4a0c0b1af6ecfb9ac75.pdf?productCode=C5125864#page=29)
defines SYSCTRL bits7:6 as the waveform upsampling selector: field1 means1×,
field0 means2×. This is a family-register reference; the new AW8695 probe also
logs the actual reset SYSCTRL for confirmation.

Clearing that selector could halve RAM waveform playback frequency despite
correct F0 detection and PWM rate. This is a static inference, not a measured
motor waveform. The candidate sets field1 explicitly at probe and every SRAM
rewrite, including F0 retuning. Expected active SYSCTRL=`0x42`, stopped=`0x41`.
The 102-sample period now corresponds to the intended 24kHz/102≈235.3Hz data
stream. The earlier frequency log was an arithmetic expectation that omitted
this selector; it did not measure RAM output frequency.

The user's other readbacks are consistent with the requested fields:
BSTVOL=`0x62` masked by`0x3e` gives`0x22`; PWM=`0xc1` masked by`0x60` gives`0x40`
(24kHz). The upper bits do not indicate a different boost or PWM request.
The family datasheet is [Awinic AW8697 V2.2, p.34](https://datasheet.lcsc.com/datasheet/pdf/d50972553dddf4a0c0b1af6ecfb9ac75.pdf?productCode=C5125864#page=34)
for the DATDBG default and boost encoding. No analog voltage is inferred solely
from a register code.

## Gain knob and coordinator commands

`/sys/bus/i2c/devices/0-005a/gain` reads the configured raw gain limit. Write
an integer0..255 (decimal or `0x` notation). It applies to the running effect
immediately, without restarting GO, and to subsequent effects. At full FF
magnitude it is the exact GAIN register byte; smaller FF magnitudes scale it.
Zero mutes the effect while its timer continues. Invalid values are rejected.
It is serialized with playback; suspended/measuring/other-chip writes are
rejected. An I/O error stops output and leaves the prior configured value.
F0 measurement temporarily uses its original detection gain128, then restores
the prior register and configured knob. No setting persists across module
reload or boot. The Android HAL still advertises on/off, not amplitude control.

After the coordinator loads the module in its existing owned boot/root shell:

```sh
H=/sys/bus/i2c/devices/0-005a
cat "$H/gain"
# Optional existing F0 measurement, before the comparison effects:
printf '1\n' > "$H/f0_measure"
cat "$H/f0_measure"
# Separate, short gain comparisons:
for g in 80 128 160 192 224 255; do
    printf '%s\n' "$g" > "$H/gain"
    cmd vibrator_manager synced oneshot -a 300 255
    sleep 1
done
# Start a 3s effect in this shell:
cmd vibrator_manager synced oneshot -a 3000 255
# While it plays, in the existing second root shell:
# H=/sys/bus/i2c/devices/0-005a
# for g in 128 160 192 224 255; do echo "$g" > "$H/gain"; sleep 0.3; done
cmd vibrator_manager cancel
printf '128\n' > "$H/gain"
dmesg | grep -E 'a9-aw86xx|F0 detection|FF playback|standby wait'
```

Capture the reset-gain/reset-SYSCTRL line, new peak126/1× probe line, all
`gain configured=... effective=...` lines and actual GAIN/SYSCTRL readbacks.
The first gain80 approximately matches the old peak78×gain128 numeric product,
allowing a useful comparison of the data-rate fix; it is not a voltage estimate.
Confirm strength, duration, mute and cancel by touch. Stock slot-A comparison
and this candidate's physical strength remain for the user/coordinator.
