# FP9929A PG: polarity, stock use and the E3 confirmation

**Date: 2026-10-15. Status: offline primary-evidence review; no phone commands run.**
Inputs: the read-only golden dump `edl_2026-10-14_110759`, saved stock-driver and
mainline captures, regenerated stock vmlinux, stock DT/DTBO, current mainline
sources, and manufacturer datasheets. No driver, DTS, image or gate was changed.

## Verdict and confidence

**INFERRED, moderate-to-high confidence: PG is active-high at the A9's GPIO82:
raw 1 is the expected settled power-good level; raw 0 is not good. Keep
`GPIO_ACTIVE_HIGH` as the working DT description.** The strongest A9 evidence is
the saved mainline observation of PG=1 with the bootloader's PWRUP/VCOM_EN high,
followed by GPIO82 low after power-down. The related FP9931 manufacturer timing
diagram also explicitly shows active-high PG. The public **FP9929A** datasheet
identifies PG but does **not** explicitly specify its polarity or timing.

**MEASURED, high confidence:** stock logs **raw** GPIO82, immediately after changing
the power controls. It does **not** wait for PG, test a good level, install a PG IRQ,
or reject an enable when PG is wrong. The statement “45/45 stock sessions prove
PG=0 means rails good” overstates the evidence: these are edge samples, and the
two matching saved captures contain **23 unique active samples**, not 45 independent
sessions. They cannot establish the settled level.

The settling explanation below is **INFERRED**, not an oscilloscope measurement.
There is still no saved trace of GPIO82 continuously spanning a mainline enable,
settled on interval and disable. Therefore this verdict is **not sufficient to
authorize drive frames using either level without the E3 confirmation below**.
In particular, neither silently changing DT polarity nor merely flipping `== 0`
to `== 1` supplies the missing power-good wait.

## Evidence summary

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Golden stock kernel disassembly | `state=` is raw GPIO82, sampled at control transitions; no PG wait/check | Stable PG=0 means good |
| Golden stock DTBO | State pin is TLMM82, flags **0**; input, GPIO mux, 2 mA, bias disabled | Stock enforces active-high semantics: it discards flags and reads raw |
| Mainline boot capture `20261015T052915Z` | Logical PG=1 with both controls high; later raw GPIO82 low with controls low; DT flags active-high | Analog voltage/current correctness or a complete transition trace |
| Golden eight UEFI logs | All report `Epd Pmic Status = 1` after PMIC initialization | That “Status” is literally raw GPIO82; its UEFI implementation was not disassembled |
| Golden stock kernel boot logs | Six early sleep samples are 1; no active samples in this early capture set | Sustained PG after discharge |
| Two stock-driver rendering captures | 22 + 23 active-edge samples at 0; 22 rows are identical between files | 45 independent sessions or steady state |
| FP9929A manufacturer PDF | PG pin 15; separate EN/ENOP; power sequencing | An explicit PG polarity/timing specification |
| FP9931 manufacturer PDF and local upstream driver | Related part PG rises after rails settle; driver requires logical 1 and a rising IRQ | A pin-compatible or timing-identical FP9929A specification |

## Stock kernel: what the logged state actually means

**MEASURED:** regenerated the **phone's own** golden `lun4/boot_a.bin` kernel,
not the different factory-package kernel. `Image.gz` is 15,047,394 bytes, SHA-256
`b1ac57e388353f045d796c280386819072e3ca80819aa9ed8eb4caed7b3fedc5`.
Raw Image is 34,476,544 bytes, SHA-256
`2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`.
`vmlinux-to-elf` recovers the symbols; all following addresses are kernel VAs.

| Function / address | Relevant instructions and result |
|---|---|
| `fp9929a_probe`, `0xffffff8008f1f2dc..0xffffff8008f1f2f8` | Calls `of_get_named_gpio_flags(np, "fp9929a,pm-state-gpio", 0, NULL)`, stores returned GPIO at `fp_data+0x0c`. Flags are not retained. |
| Probe `0xffffff8008f1f428..0xffffff8008f1f44c` | Requests that GPIO as `fp9929a pm_state`, calls `gpio_to_desc` then `gpiod_direction_input`. No PG interrupt setup. |
| `fp9929a_get_power_state`, `0xffffff8008f1ea18..0xffffff8008f1ea64` | Loads `fp_data+0x0c`, `gpio_to_desc` → **`gpiod_get_raw_value`**, returns it unchanged. Invalid/missing state can return 0, another reason 0 alone is not proof of good rails. |
| `fp9929a_active_mode`, `0xffffff8008f1ed50..0xffffff8008f1eef4` | Sets PWRUP=1, waits 5000–5100 µs, writes VCOM, reads temperature, waits 20000–20100 µs, sets PWRCOM=1. |
| Active `0xffffff8008f1ee64..0xffffff8008f1eecc` | `gpiod_direction_output_raw(PWRCOM,1)`; sets software active byte at `+0x1c`; reads raw state at `0xffffff8008f1eea8`; prints `epd fp9929a active end, state=%d`. **No delay/poll after PWRCOM=1.** |
| Active return `0xffffff8008f1eee4` | Returns `w19`, the result of `fp9929a_read_temp`; PG does not determine success. |
| `fp9929a_sleep_mode`, `0xffffff8008f1ef00..0xffffff8008f1f028` | Waits 1000–1100 µs, PWRCOM=0, waits 50000–50100 µs, lowers an unused/unparsed GPIO field and PWRUP=0. |
| Sleep `0xffffff8008f1efc0..0xffffff8008f1f018` | Lowers PWRUP, clears software active, reads raw state at `0xffffff8008f1f000`, prints `enter sleep end, state=%d`. **No discharge wait after PWRUP=0.** |

The active/sleep functions inline the same raw-state operation as
`fp9929a_get_power_state`; there is no polarity inversion hidden in the log.
The stock `msm_gpio_get` (`0xffffff8008595408`) shifts the pad input bit and masks
with 1; it does not invert it either. The helper's `--xref` scan finds no direct
callers of the standalone get-state symbol; the two logging paths are inlined.

**INFERRED reconciliation:** an enable-edge reading can be 0 before a power-good
output has asserted; an immediate disable-edge reading can still be 1 before its
deassertion/discharge. This explains why stock's active=0/sleep=1 pair can coexist
with mainline's settled bootloader-on=1/later-off=0. It is a plausible timing
explanation, not a proven FP9929A PG delay or proof that every rail was in spec.
Stock logs alone do not justify adding an inverted safety predicate.

This supersedes the polarity inference in [mainline-plan §2.2](mainline-plan.md),
the old G8 row in [mainline-eink-steps](mainline-eink-steps.md), and the “45/45”
interpretation in [mainline-eink-driver](mainline-eink-driver.md). Their recorded
raw samples remain valid; the inference from them is the problem.

## Stock DT, interrupt flags, pinctrl and physical pad

**MEASURED:** freshly extracted golden `lun4/dtbo_a.bin`, single DTBO entry
88,658 bytes at offset 64. Its decompiled DTS is byte-identical to the previously
reviewed `kre/dt/dtbo_0.dts` (SHA-256
`1f88f1b119980b7535f78a542dcc78b495cf0eb2d42048f2ee377e50a0740e68`).
Resolve overlay `__fixups__` with `scripts/mainline/dtbo-resolve.py`:

```dts
/* fragment@20, fp9929a@48 */
compatible = "fiti,fp9929a";
reg = <0x48>;
pinctrl-names = "fp9929a_default", "fp9929a_sleep";
pinctrl-0 = <0x22 0x23>; /* power pins active, state pin active */
pinctrl-1 = <0x24 0x25>; /* power pins sleep, state pin sleep */
fp9929a,pm-pwrup-gpio = <&tlmm 86 0>;
fp9929a,pm-pwrcom-gpio = <&tlmm 36 0>;
fp9929a,pm-state-gpio = <&tlmm 82 0>;
```

The last cell is a **GPIO flag**, not an interrupt trigger flag. Zero has no
`GPIO_ACTIVE_LOW` bit. There is no `pg-gpio`, `interrupts` or `interrupt-parent`
property on this stock PMIC node. The TLMM controller itself has an interrupt
parent, which does not turn the PMIC state property into a PG interrupt.

Fragment@12 defines both `fp9929a_state_active` (phandle 0x23) and
`fp9929a_state_suspend` (0x25): `pins="gpio82"`, `function="gpio"`,
`drive-strength=<2>`, `bias-disable=<0>`. Neither asks for an input inversion,
pull-up, pull-down, or output drive. Stock requests input explicitly in probe.
Power pins 36/86 use GPIO mux, 8 mA/no bias in active, 2 mA/pull-down in sleep.

**MEASURED pad-mapping cross-check:** stock TLMM is `/soc/pinctrl@400000`, base
0x00400000; `bengal_groups` at `0xffffff800968fab8` has 0x50-byte entries.
GPIO82's entry at `0xffffff8009691458` contains IO offset **0x00152004** at +0x28.
Its getter selects input bit 0. The resulting physical input register is
**0x00552004 bit 0**. Mainline `pinctrl-sm6115.c` maps GPIO82 to WEST, IO offset
`0x4 + 82*0x1000`, input bit 0; `sm6115.dtsi` WEST base is 0x00500000. Thus it
reads **the same register/bit** despite the different controller base/node name.
The discrepancy is not explained by a different numeric GPIO bank or an inverted
mainline debugfs display. This is a source comparison, not a live MMIO experiment.

## Saved logs: provenance, counts and limitations

**MEASURED golden backup:** extracted only `/kmon_log` from the read-only
`lun0/kdebuginfo.bin` using `debugfs -R 'rdump ...'` (no `-w`). The eight
`kexmlog_0/kmonlog_*` files contain the UEFI log followed by early kernel output.
They span the stock July/August/October/December 2022 kernel OTA chain, as recorded
in [stock-boot-logs](stock-boot-logs.md). They are not eight new mainline tests.

- All **8/8** UEFI logs print `Epd Pmic Status = 1` after FP9929A initialization.
  Last boot `kmonlog_2026_0811_105015`, lines 198–204: TC358767 init starts, PMIC
  initialization reports VCOM 2730/code126, TMST30, then status1; bridge ID follows.
  The log does not label the status as a raw GPIO. Do not count it as independent
  proof of PG polarity without tracing the UEFI implementation.
- **6** kernel sleep-end samples are all state1; two logs end without that sample.
  There are **0** active-end samples in these early boot logs. The last boot's
  lines 4431–4432 show sleep start at 14.236551 s and state1 at 14.287831 s.
  They establish what stock logged immediately after shutdown, not after settling.

**MEASURED later stock-driver captures**, under
`/home/user/source/a9-display-evidence-20261012/runs/`:

| File | Active end | Sleep end | Independence |
|---|---|---|---|
| `r001-S0-smooth-launcher/dmesg-epd.txt` | 22 × state0 | 22 × state1 | Active timestamps 100.130209 … 630.863188 |
| `r002-INVALID-shade-pulled-by-launcher-swipe/dmesg-epd.txt` | 23 × state0 | 23 × state1 | Includes **all 22 identical active lines** from r001, plus 685.607712 |

Thus these files reproduce a numerical “45/45” count but contain **23 unique
active events**. The INVALID marker concerns the gesture experiment; no claim is
made that the kernel's raw log lines are fabricated. These are stock-kernel driver
logs under a GSI, distinct from the golden stock-Android boot-log set.

A separate previously saved `scratchpad/blpull/boot-00006/kmsg.txt` has 10 active
state0 lines and 11 sleep lines: ten state1 and **one state0** at 99.664444 s.
So even “off always reads 1” is not universal in the available records.

**MEASURED mainline captures already on disk:**

- `runs/20261015T041539Z-mainline-first-boot/capture/dmesg.txt`: at 1.481818 s,
  `at probe: PWRUP=1 VCOM_EN=1 PG=1`.
- `runs/20261015T052915Z-mainline-first-boot/capture/dmesg.txt:288`: at 1.506249 s,
  the same 1/1/1 values; probe then powers down.
- That run's `capture/gpio.txt` and `capture/e1/gpio.txt`: GPIO36/86 output low,
  GPIO82 **input low, func0, 2mA, no pull**. This is a later observation, not an
  immediate post-PWRUP transition reading.

These samples favor active-high and agree with the boot-3 interpretation in
mainline-eink-steps, but no analog rail measurements or steady enable trace are
present here. PWRUP/VCOM_EN high alone is a command, not a voltmeter reading.

## Manufacturer documents and FP9931 comparison

Primary PDFs retrieved 2026-10-15; downloaded only into `/tmp/sol-fp9929a-pg/`:

- [Fitipower FP9929A, revision 1.1, September 2017](https://www.fitipower.com/dl/file/h5Kjw7xPjy2BteF0):
  pin table p3 identifies PG pin15. Figure4 p10 shows EN, EN(OP) and rail timing,
  including a VCOM ramp after EN(OP), but **no PG trace**. The reviewed 15 pages
  do not explicitly define PG high/low semantics. It cannot alone settle polarity.
  SHA-256 `19aba90b73750d558033349502f0ffe1dff6f19d863e363777fd3f77be1fa90d`.
- [Fitipower FP9931, revision 1.0, October 2021](https://www.fitipower.com/dl/file/flXa6hIchVeu0W3K):
  pin table p3 identifies PG pin17; electrical table p8 calls it an open-drain
  N-MOS output. **Figure3 p10 explicitly shows PG low before startup, high 1 ms
  after the final VPOS ramp, and low at EN falling**. Open-drain does not mean
  active-low. This supports active-high for that related part, not identical
  FP9929A timing. SHA-256
  `55ccba7cf1962e97cc29c377be358708c8dedd2df643b0f33b4a36fb4ea20c9b`.

**MEASURED local upstream code:**
`~/A9-build/mainline/linux-7.2.10/drivers/regulator/fp9931.c`:
`fp9931_set_enable()` lines 307–329 waits up to 200 ms for a completion and then
requires `gpiod_get_value_cansleep(pgood_gpio) == 1`; `fp9931_check_powergood()`
lines 259–267 returns that **logical** value. Probe lines 424–453 obtains
`pg-gpios` as input and requests **IRQF_TRIGGER_RISING | IRQF_ONESHOT**.
This agrees with Figure3. Its 200 ms timeout is an implementation choice, not
a measured FP9929A timing bound to copy blindly.

## Mainline audit and consequences for E3

**MEASURED files at review time:**

| Location | PG handling |
|---|---|
| `scripts/mainline/kernel/patches/0001-regulator-add-FITI-FP9929A-E-Ink-PMIC-driver-Hisense.patch` | Acquires optional `pg` input, logs `gpiod_get_value_cansleep`; does not wait for PG. |
| `scripts/mainline/eink/patches/0006-regulator-fp9929a-export-rail-state-PG-and-the-last-.patch:64` | `st->pg_good = fp->pg && gpiod_get_raw_value_cansleep(fp->pg) == 0`. Header comment claims raw0 good. |
| Built tree `drivers/regulator/fp9929a.c:400–407, 498` | Same logical probe log and raw `==0` status predicate after the subsequent patches. Power-up log is also logical; `fp->on` means sequence completed, not measured PG good. |
| Built tree `arch/arm64/boot/dts/qcom/sm6115-hisense-hlte556n.dts:556` | `pg-gpios = <&tlmm 82 GPIO_ACTIVE_HIGH>`; the adjacent “reads 0 when on” comment is an unsupported interpretation. |
| `scripts/mainline/eink/driver/a9_eink_drv.c:502–510` and its patch0007 | Authorizes frames when `st.on` and either PG is missing or `st.pg_good`; repeats the “45 of 45” comment. Later updates recheck the same predicate. |

With current DT flags, logical and raw PG are equal. With ACTIVE_LOW they would
be complements, **but the raw `==0` predicate would not change**. Changing DT
polarity alone only changes logging; it cannot fix that predicate.

**RECOMMENDATION, not implemented:** after the coordinator confirms the steady
level, use one convention consistently: ACTIVE_HIGH DT and logical `pg == 1`,
or explicit documented raw-high semantics. Poll/wait for the settled PG assertion
with a bounded timeout **before marking the rails ready or allowing frames**.
Propagate negative GPIO errors and require the known-wired PG on this board;
the current `!pg_valid` bypass is not a substitute for confirmation. On failure,
use the existing ordered VCOM-EN/PWRUP shutdown, preserve the hard on-time limit,
and do not bypass the gate to get a picture. A confirmed power-on trace should
determine the settle margin; stock's lack of waiting is not a safety specification.

## Exact read-only confirmation for the coordinator

**Not executed. Run in the mainline initramfs shell**, through the coordinator's
existing transport, while a separately approved E3 procedure holds rails on with
no nonzero drive frames, and again across its normal power-down. This command
only reads uptime and the TLMM debugfs report; it does not request/export GPIOs,
change directions/pulls, touch I2C or enable power:

```sh
i=0
while [ "$i" -lt 40 ]; do
    printf 'PG-sample '
    cat /proc/uptime
    awk '
        /^gpiochip/ { tlmm = ($0 ~ /500000[.]pinctrl/) }
        tlmm && /gpio(36|82|86)[[:space:]]*:/ { print }
    ' /sys/kernel/debug/gpio
    i=$((i + 1))
    sleep 0.05
done
```

The saved kernel's TLMM debug printer reads the hardware input bit for an input
pin, not the descriptor's ACTIVE_LOW-adjusted value (`pinctrl-msm.c`,
`msm_gpio_dbg_show_one`). Expected **settled** observations for the active-high
verdict: GPIO36/86 high and GPIO82 **in high**, then GPIO36/86 low and GPIO82
**in low** after power-down/discharge. Capture several consecutive stable samples,
not just the first read after changing controls. The two-second loop is only an
observation window; do not extend E3's existing power-on budget to satisfy it.

If the rails cannot be held for an observation under the existing safe procedure,
or PG remains low with both controls high, **stop before frame sending and return
the capture to the coordinator**. Do not infer analog-good rails just from two
output GPIOs, invert the assertion to force a pass, or read the stock PMIC
`temperature` attribute (that can power-cycle it). No additional phone operation
was performed or proposed here to create that on interval.

## Offline reproduction

Scratch is `/tmp/sol-fp9929a-pg/`; reference tools are
`scripts/mainline/kern-dis.py` and `scripts/mainline/dtbo-resolve.py`.
The kernel extraction is the header-v2 method in
[kernel-re-assessment appendix](kernel-re-assessment.md#appendix-reproduction-offline-session-scratchpad-kre).
This compact reproduction extracts only host-side files:

```sh
mkdir -p /tmp/sol-fp9929a-pg
python3 -I - <<'PY'
from pathlib import Path
import struct, zlib
b = Path('$A9_DATA/Backups/edl_2026-10-14_110759/lun4/boot_a.bin').read_bytes()
assert b[:8] == b'ANDROID!'
n = struct.unpack_from('<I', b, 8)[0]
page = struct.unpack_from('<I', b, 36)[0]
assert page == 4096
Path('/tmp/sol-fp9929a-pg/Image.raw').write_bytes(zlib.decompress(b[page:page+n], 31))
d = Path('$A9_DATA/Backups/edl_2026-10-14_110759/lun4/dtbo_a.bin').read_bytes()
magic, total, header, entry_size, count, entries, page, version = struct.unpack_from('>8I', d)
assert magic == 0xd7b7ab1e and count == 1
size, offset = struct.unpack_from('>2I', d, entries)
Path('/tmp/sol-fp9929a-pg/stock-dtbo.dtb').write_bytes(d[offset:offset+size])
PY
/nix/store/jf7fb17d6lmxz90s6fc90nymj7lg57an-vmlinux-to-elf-0-unstable-2024-07-20/bin/vmlinux-to-elf \
  /tmp/sol-fp9929a-pg/Image.raw /tmp/sol-fp9929a-pg/vmlinux.elf
nix-shell -p 'python3.withPackages(p:[p.capstone p.pyelftools])' --run \
  'python3 -I scripts/mainline/kern-dis.py /tmp/sol-fp9929a-pg/vmlinux.elf fp9929a_probe fp9929a_active_mode fp9929a_sleep_mode fp9929a_get_power_state msm_gpio_get'
nix-shell -p dtc --run \
  'dtc -I dtb -O dts -q -o /tmp/sol-fp9929a-pg/stock-dtbo.dts /tmp/sol-fp9929a-pg/stock-dtbo.dtb'
python3 -I scripts/mainline/dtbo-resolve.py /tmp/sol-fp9929a-pg/stock-dtbo.dts fragment@12 fragment@20
nix-shell -p e2fsprogs --run \
  'debugfs -R "rdump /kmon_log /tmp/sol-fp9929a-pg" $A9_DATA/Backups/edl_2026-10-14_110759/lun0/kdebuginfo.bin'
```

`debugfs rdump` emitted ownership-change warnings on the temporary destination;
the eight files were extracted and inspected. The input image was never opened
writable. The Python-with-packages form is intentional: `python3 -I` does not
load packages supplied solely through `PYTHONPATH`.
