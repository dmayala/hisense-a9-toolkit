# AW8695 measured F0 and playback readbacks — 2026-10-20

This file describes 3c137fb. The coordinator measured F0=236.5Hz on k4. See [amplitude.md](amplitude.md) for the later gain, waveform and RAM data-rate changes.

The coordinator tested `43eb28f` on build k2: 100/500/3000 ms effects played,
and the user reports a **more distinct buzz, still weak overall**. Keep the
boost change. This follow-up measures resonance and observes actual register
readbacks before making another amplitude or gain change.

## Behavior

Normal RAM effects keep the 8500 mV boost request, ±78 generated wave, 24 kHz
PWM and gain cap 128. Every AW8695 start and stop now logs actual registers.
The initial F0 is explicitly **not measured**; 235 Hz remains the nominal
fallback. `f0_measure` is a root-writable device sysfs attribute:

- Reading it reports the last successful measured F0 in tenths of Hz, current
  RAM sample count, nominal playback frequency in mHz, and RAM readiness.
- Writing `1` while idle runs one bounded stock-style CONT F0 detection, logs
  raw and converted results, then resamples the generated sine to
  `round(240000/F0_tenths)` samples and rewrites SRAM. Normal effects resume in
  boosted RAM mode. No measurement or unexpected buzz runs at module probe.
- The measurement temporarily clears **volatile** TRIM_LRA, as stock does,
  and restores it and the other measurement registers on success or error.
  It does not write efuses, saved calibration, files or partitions.
- Busy/suspended operation is rejected. A new FF request during measurement
  returns EBUSY. Timeout, zero/out-of-range result or immediately idle output
  cannot establish a new F0. Restoration or SRAM failure blocks playback until
  readiness is recovered or the module is reloaded.

The explicit action avoids putting a 104 ms trace plus completion/stop polling
inside a 100 ms framework effect. The 100–400 Hz accepted range is an analyst
buffer bound, **not a stock motor specification**. Out-of-range values are
logged and rejected. Quantization to a whole sample introduces a small frequency
error, reported by the sysfs output; e.g. measured 170.0 Hz gives 141 samples,
170.212 Hz playback. Mock values in the host tests are not phone measurements.

## Stock source, byte offsets and interpretation

The stock DT/ELF/firmware identities and boost/waveform offsets are recorded in
[stock-strength.md](stock-strength.md). ELF offsets below use that same
`vmlinux.elf` SHA256
`258f157ce827c8997be7e358c06b91fdcb0032d423e978c248d7f5fc858291b5`;
`offset = VA - 0xffffff8008080000 + 0x1c0`.
Disassembly with a file offset beside every instruction is saved at
`~/A9-build/mainline/vibration-f0-20261020/stock-file-offset-disassembly.txt`.

| Value/action | File and offset evidence |
| --- | --- |
| Nominal F0 235.0 Hz; coefficient 260 | Stock `merged.dtb`, `f0_pre=2350` at `0x30408`, `aw869x_f0_coeff=260` at `0x30388`. No unit's measured F0 follows from these nominal values. |
| Preset registers `0x49/0x4a = 0x06/0x64` | Stock ELF `aw869x_misc_para_init`, `0x73fb18..0x73fb5c`: `floor(1e9/(2350*260))=1636=0x0664`. |
| Detect in CONT, bypass, with HW VBAT compensation | ELF `aw869x_get_f0`, `0x73d8d0..0x73d8d8` calls play_mode 4. `aw869x_play_mode`, `0x73edb0..0x73ee54`, uses SYSCTRL low nibble `0x08`, ADCTEST `0x66` bit 6 set. Normal boosted RAM restores the earlier configuration. |
| Detect/open-loop flags | `aw869x_get_f0`, `0x73d8dc..0x73d8ec` updates register `0x48`, clear mask `0x0c`, value `0x04`; normal tail `0x73db90..0x73dba0` switches these bits back. We restore the saved full register. |
| DATCTRL filter bits | `0x73d8f0..0x73d900` passes register `0x2b`, inverse mask `0x9f`, value `0xe0`. The helper at `0x74cf74..0x74cf8c` computes `(old & mask) | (value & ~mask)`, so the actual changed field is **bits 6:5 = 0x60**, preserving bit 7. This distinction is retained in regmap_update_bits. |
| Detection drive `0x7b = 0x7d` | DT `cont_drv_lev` at `0x303d8`; ELF `0x73d904..0x73d914`. No change to normal RAM gain or amplitude. |
| Trace `0x7d..0x7f = 0x53,0x02,0x0f` | DT four bytes `05 03 02 0f` at `0x30368` mean pre=5, wait=3, repeat=2, trace=15. ELF `0x73d918..0x73d94c` packs `(pre<<4)|wait`, repeat, trace. |
| Trace wait and completion bound | ELF `0x73d9d4..0x73da0c` computes `(10000/2350)*(5+3+(15+3)*(2-1))=104 ms`. `0x73da10..0x73da58` polls GLB_STATE `0x46` low nibble up to 100 times with 2000..2500 µs sleeps. We use a 200 ms regmap poll after the trace wait, then force stop; the stop itself is also bounded. |
| Measured F0 raw `0x68/0x69`, big endian | ELF `0x73da7c..0x73db00` computes `F0_tenths = floor(1e9/(raw*260))`. Zero raw returns an error. We additionally require observed active state before waiting for completion, so an immediately idle chip cannot claim a fresh measurement. |
| Volatile trim `0x5b`, low six bits | `aw869x_get_trim_lra`, `0x740380..0x7403b8`; `f0_show`, `0x750370..0x750390`, requests upload type zero then measurement. `aw869x_upload_lra`, `0x73fd4c..0x73fd7c`, writes zero into `0x5b`. Our diagnostic saves and restores it; it does not calculate or persist a new trim code. |

### Where stock gets saved calibration

This is distinct from the nominal frequency:

- Offline stock vendor `/etc/init/hw/init.target.rc` contains
  `mkdir /kdebuginfo/haptic` at file `0x1c20` and a chmod for
  `/kdebuginfo/haptic/aw_cali_lra.bin` at `0x1c4e`. These are permissions and
  directory setup; they do not prove that the running kernel reads that file.
- The symbolized kernel's actual `read_cali_data` resolves partition name
  **`diag`**, ELF string at `0x19b5b04`, then reads **one byte at offset
  `0x130c`**: ELF `0x752d5c..0x752da8`. This source finding does not
  require reading any partition image or device storage, and neither was read.
- `hs_cali_delayed_work`, `0x75293c..0x75294c`, accepts a saved byte
  only when the calibration flag is appropriate and its value is at most
  `0x3f`; `0x752af4..0x752b08` sends it through upload type 1.
  `aw869x_upload_lra` then writes register `0x5b`. This is a **trim code**, not
  a frequency in Hz. The unit's actual stored code remains unknown.
- In the extracted stock vendor `/etc/init` search, the haptic matches were the
  service declaration and the init.target.rc permissions above. No haptic
  persist/F0 sysfs write was found in that bounded source set. This is not a
  claim to have audited every stock userspace binary or partition.

The existing 102-sample signed waveform and 24 kHz setting remain consistent
with stock's nominal 235 Hz. The new code changes only period after a successful
measurement; ±78 peak and gain 128 are deliberately held fixed to distinguish
resonance from output scaling. Stock RAM includes ±126 transient waves, but
this experiment does not substitute them or increase gain.

## Readbacks and how to use them

`play` logs after GO and a 2..2.5 ms settle. `stop` logs after GO clear and forced
standby. Both include cached measured F0 and actual reads of:

- SYSST `0x01`, SYSCTRL `0x04`, GO `0x05`, PWM `0x2e`, DBGSTAT `0x30`;
- BSTCFG `0x34`, GAIN `0x39`, BSTVOL `0x3a`, STATE `0x46`, AUTO `0x47`;
- CONT `0x48`, volatile TRIM `0x5b`, raw VBAT `0x62`, ADCTEST `0x66`;
- raw F0 `0x68/0x69`, alternate/CONT raw F0 `0x6a/0x6b`, DATCTRL `0x2b`,
  preset `0x49/0x4a`.

The AW8695 cached primary posting in stock-strength.md defines the common
SYSINT/SYSCTRL/GO/PWM/DBGSTAT/BSTCFG/STATE/AUTO registers. Its exact definition
byte offsets are saved in `vibration-f0-20261020/register-source-offsets.json`;
SYSINT definitions start at cached-file `0x14af`. Gain and state are
also confirmed by the stock ELF. VBAT register `0x62` is read by
`aw869x_get_vbat`, ELF `0x73e430..0x73e440`; our raw read does not
trigger a battery ADC conversion and must not be treated as measured mV.
F0 detection above establishes the measured-result registers; the extra raw
registers are diagnostic bytes, not a promise of valid measurements.

SYSINT `0x02` is read-to-clear, so active logging leaves it unread; the stop
snapshot consumes its latched value once. AW8695 posting fault bits are
BSTERR `0x80`, overvoltage `0x40`, undervoltage `0x20`, overcurrent `0x04`,
overtemperature `0x02`, done `0x01`. A read failure logs the register and errno
rather than printing invented zeros. Reading the SYSCTRL boost bit confirms
selection readback; it does **not** measure the motor's analog voltage.

Normal playback should read SYSCTRL low nibble `0x02`, GAIN `0x80`, PWM bits
6:5=`0x40`, BSTVOL bits 5:1=`0x22`; stop should read GO=0, STATE low nibble=0,
and SYSCTRL low bits=1 (standby, boost clear). Other bits and diagnostic bytes
are reported without guessed expectations. Capture STATE/SYSST/DBGSTAT/AUTO
and faults if output is unexpectedly idle or still weak.

## Coordinator commands — host worker must not run these

Use the coordinator's existing root debug shell and owned test boot. Load the
new module through that same workflow; its additional probe line says
`F0 measurement available via f0_measure; not run at probe`.

```sh
H=/sys/bus/i2c/devices/0-005a
cat "$H/f0_measure"
cmd vibrator_manager synced oneshot -a 500 255
# Let the baseline effect finish before measuring.
sleep 1
printf '1\n' > "$H/f0_measure"
cat "$H/f0_measure"
dmesg | grep -E 'a9-aw86xx|F0 detection'
cmd vibrator_manager synced oneshot -a 100 255
sleep 1
cmd vibrator_manager synced oneshot -a 500 255
sleep 1
cmd vibrator_manager synced oneshot -a 3000 255
# During the long effect, from the existing second shell:
cmd vibrator_manager cancel
dmesg | grep -E 'a9-aw86xx|F0 detection|standby wait|FF playback'
```

A successful measurement logs `F0 detection result=0 raw=.... measured=... Hz`;
`cat` then reports nonzero `measured_tenths_hz` and `ready=1`. The subsequent
`play`/`stop` lines include the measured F0 and the new period. An error returns
nonzero from the write and is logged; do not call a nominal/default value a
measurement. Keep the first failed result and its `f0-start`/`f0-stop` registers.
Record whether the brief measurement buzz differs in strength from RAM playback,
and whether retuning changes the user's 100/500/3000 ms and cancel experience.
No persistent storage should be read or written for this diagnostic.

## Host validation and limits

External module only, existing power-kernel objects; no full images or phone
access. `~/A9-build/mainline/vibration-f0-20261020/` contains the module,
command/build log, source-extracted mock tests (five groups), and ABI checks.
Mocks verify software ordering, retuning, standby/boost clear and errors; they
cannot validate physical F0, the analog output stage or perceived strength.
REA objdump inspection was unavailable because its configured executable was
missing (ENOENT). No setup changes or duplicate Ghidra attempt were made;
all instruction evidence here comes from ordinary AArch64 GNU objdump.
