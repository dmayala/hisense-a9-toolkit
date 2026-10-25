# Row 23: automatic AW8695 resonance measurement

This describes `e871566`. The subsequent [stock steady default](stock-equivalent.md)
reduces the peak to78 and selects bypass with battery compensation; automatic
F0 and eight-cycle tuning remain.

Based on main `e42070f`. AW8695 now measures F0 once during probe, before input
registration, and programs a generated RAM loop at that measured frequency.
This produces a brief calibration buzz at driver load. Subsequent framework
effects start normally without another detection delay. `auto_f0=0` disables
the automatic step for a nominal-frequency comparison; idle `f0_measure` still
runs detection and tunes playback. Other chip IDs keep their original behavior.

The base already implemented **explicit** detection, but did not invoke it at
probe. Thus ordinary playback could indefinitely log `F0=0.0 Hz (not measured)`.
It also rounded the period to one integer cycle. The new eight-cycle loop
reduces that rounding: measured 236.5 Hz gives 812 samples at 24 kHz, or
236.453 Hz, compared with 237.624 Hz for the previous 101-sample loop.
The generated samples retain peak126 and the existing 1x data setting.
The longest supported loop is1920 samples plus a five-byte header at0x0800;
its final address0x0f84 stays within4 KiB. No firmware is needed at runtime.

`f0_measure` retains its existing fields and appends `cycles` and `last_error`.
Successful detection reports `last_error=0`, nonzero `measured_tenths_hz`,
`cycles=8` and `ready=1`. An ordinary failed measurement leaves the prior RAM
period available and reports the error. Restoration, SRAM or stop failures
invalidate readiness; probe fails if configuration cannot be restored. The
stop path now preserves the original polling error even if forcing standby
succeeds. An explicit idle retry can recover a failed detection. Calibration
settings, including volatile trim and gain, are restored. Nothing is persisted.

## Stock findings and evidence

Evidence is under `~/A9-build/mainline/vibration-f0-20261021/`. The original
symbolized stock kernel is `../sol-pmic/evidence/vmlinux.elf`, SHA256
`258f157ce827c8997be7e358c06b91fdcb0032d423e978c248d7f5fc858291b5`.
File offsets below equal VA minus`0xffffff8008080000` plus`0x1c0`.
Stock vendor firmware provenance is recorded in [stock-strength.md](stock-strength.md).

REA Ghidra12.1.4 successfully decompiled two exact stock function slices:

- `aw869x_get_f0`, VA`0xffffff80087bd6d8`, file`0x73d898`,832 bytes:
  Evidence`ev_4ec29a65fe85ef35da988c2013f6c268d2b2cb4c4dafabc31669da6dfef6fc4a`,
  `rea-f0-evidence.json`, extraction identity in `stock-extraction.json`.
- `aw869x_cont_config`, VA`0xffffff80087be368`, file`0x73e528`,360 bytes:
  Evidence`ev_eebce1f6ec20ee73f7f0473a976ea29605f44f60ee0d2742d354a78474c61651`,
  `rea-cont-evidence.json`, identity in `stock-cont-extraction.json`.

The full kernel decompilation timed out; slicing kept the stock bytes and
original load addresses. The slices exclude callees, globals and strings.
Their call coverage is partial and pseudocode warns about the absent
stack-check-failure tail. Function names, DT fields and external helpers below
come from the original symbolized ELF and existing instruction evidence,
not from inferred names in the slices. This is static evidence, not execution.

| Area | Observed stock behavior and implementation choice |
| --- | --- |
| Detection | Stop, CONT mode4, register48 bits3:2=`04`, DATCTRL bits6:5=`60`, drive7b=`7d`, trace7d..7f=`53 02 0f`, nominal235.0 Hz/coefficient260. Wait104 ms, poll GLB_STATE46, read big-endian68/69. `F0_tenths=1e9/(raw*260)`. REA confirms the code path and formula; DT values and bit-helper semantics are in [f0-diagnostics.md](f0-diagnostics.md). Our existing detection adds checked I/O, active-state observation and timeout rejection. |
| Calibration | Stock `hs_cali_delayed_work` loads an accepted six-bit saved trim or measures F0, calculates a rounded trim from fractional frequency error, uploads type1 to volatile5b and may save it. `hs_cali_delayed_work.asm` and `aw869x_upload_lra.asm` in `vibration-f0-20261020/` show this. Saved trim is a code, not a frequency. Our driver measures with zero trim, restores it, then changes sample period; it does not also apply a stock clock correction or read/write the saved calibration store. |
| RAM firmware | Independently parsed the cached vendor `haptic_ram.bin`:2311 bytes, SHA256`dfbb039adedfc85d797645fc3652ee1631899d2aeb15db51b1f8c4d7c332929f`, valid checksum`dacb`, base1000. Waves1/2/3 have663/510/348 samples and peaks126; steady wave4 has102 samples and peaks78; wave5 is silent. Exact headers in `stock-ram.json`. We generate a sine; stock transient envelopes are not reproduced. |
| Gain | Stock reads reset DATDBG39 as its baseline. Its RAM_LOOP software compensation uses saved_gain*4200/VBAT, capped179. Gain255 is writable but not established as stock's normal setting. Existing reset-gain baseline and live `gain` knob remain. See [amplitude.md](amplitude.md), `ram_vbat_comp.asm` in `vibration-strength-20261020/`, and this task's `aw869x_set_gain.asm`. |
| CONT | REA confirms mode4 plus CONT48=`89`, time4b/4c, TSET4d, zero-cross72/73, brake78 low nibble,7a=`23`, drive7b/7c, then GO. DT drive125/overdrive155,time006c,zero-cross0ff1,brake3 are in [stock-strength.md](stock-strength.md). Detection's open-loop flags differ from this normal CONT path. Normal CONT is not selected by our FF implementation. |
| RAM and RTP modes | Stock DT timed default5 is RAM_LOOP in bypass, while RAM1/RTP2 use their boosted paths. `aw869x_play_mode.asm` and `vibrator_work_routine.asm` in `vibration-strength-20261020/` establish that distinction. `rtp_work_routine.asm` here loads a selected firmware via request_firmware at VA`0xffffff80087ceabc`; `aw869x_set_rtp_data` at VA`0xffffff80087befd0` writes streaming data to FIFO06. `upload_lra` distinguishes type2 RTP oscillator trim from type1 F0 trim. RTP is not evidence that normal long effects track F0. We retain the prior boosted RAM experiment; no RTP feeder or CONT playback is added. |

The older coordinator report in [amplitude.md](amplitude.md) recorded a
successful **236.5 Hz** measurement. This is historical evidence, not a new
measurement of the currently loaded module. Since it is near nominal235 Hz,
automatic F0 and finer period matching may provide little subjective strength
gain. Weak output at correctly measured F0 leaves analog drive, waveform
envelope and playback mode unresolved. Host checks cannot establish feel parity.

## Host build and two-minute tactile test

External module: `~/A9-build/mainline/vibration-f0-20261021/a9-aw86xx.ko`.
Build uses the existing `video-20261020/power-kernel/{kernel,obj}` with `W=1`.
`module-command.json`, `module-build.log`, `test-functions.py/.log` and
`host-verification.json` record the build, five focused checks and ABI comparison
with `integrated-20261021n`. No image or HAL rebuild was done. Integration must
rebuild the vibration module from this branch; a vendor-only refresh retaining
an older module will not activate automatic F0. Preserve any integrating
branch's separate suspend fix when resolving the driver cherry-pick.

The coordinator loads the candidate in its existing owned boot. Then, in its
existing root shell, with the user holding the phone:

**0:00–0:20:** inspect calibration. Expected: nonzero measured frequency,
`cycles=8 last_error=0 ready=1`. The load-time detection pulse is separate from
the following effects. If detection failed, run the explicit retry while idle
and retain its exact error/readbacks; do not label fallback as measured.

```sh
H=/sys/bus/i2c/devices/0-005a
cmd vibrator_manager cancel
cat "$H/f0_measure"
G=$(cat "$H/gain")
dmesg | grep -E 'a9-aw86xx|F0 detection'
# Only if detection failed:
# printf '1\n' > "$H/f0_measure"; cat "$H/f0_measure"
```

**0:20–1:00:** feel short, medium and sustained effects at the baseline gain.
Compare with the previous10–20 strength rating and note sharpness and steadiness.

```sh
for ms in 100 500 3000; do
    cmd vibrator_manager synced oneshot -a "$ms" 255
    sleep 4
done
```

**1:00–1:30:** compare raw gain codes at the same measured frequency. These
are register codes; the HAL's Android amplitude argument does not select them.

```sh
for g in 128 192 255; do
    printf '%s\n' "$g" > "$H/gain"
    cmd vibrator_manager synced oneshot -a 500 255
    sleep 2
done
printf '%s\n' "$G" > "$H/gain"
```

**1:30–2:00:** cancel a long effect, type briefly with keyboard haptics, and
record the user's strength rating at each gain plus any fault/error.

```sh
cmd vibrator_manager synced oneshot -a 3000 255
sleep 1
cmd vibrator_manager cancel
cat "$H/f0_measure"
dmesg | grep -E 'a9-aw86xx|F0 detection|standby wait|FF playback'
```

Success requires the user to feel the effects and confirm cancel/off, plus a
fresh successful detection. Readbacks alone do not qualify strength. For an
optional direct nominal-versus-tuned comparison, load with `auto_f0=0`, feel one
500 ms effect, write1 to `f0_measure`, then feel the same effect at the same gain.
No phone, adb, fastboot, edl or camera access was performed for this delivery.
