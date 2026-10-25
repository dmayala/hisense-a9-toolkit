# PMI632 standby measurement and stock evidence

The opt-in drain stage uses a separate, relative charge integral built from QG
hardware batches. It does not change Android's existing sampled charge counter
or treat its cached percentage as a live sleep counter. No stage runs by default.
All work and validation here are host only; physical charging control, suspend
coverage, current calibration and the battery floor still need an owned sweep.

## Exact stock image

The saved stock kernel ELF is
`A9-build/mainline/venus-hfi-host-20261024/stock/vmlinux.elf`, SHA256
`258f157ce827c8997be7e358c06b91fdcb0032d423e978c248d7f5fc858291b5`.
Addresses below are its original virtual addresses, not offsets into a rebased
binary. REA used a derived ELF containing byte-verified text, jump tables and
strings; its SHA256 is
`022782cec6b15f004c9184212c88d4e6cde86cd3076d464c75b51f103a811a91`.
The local segment manifest, disassembly and REA snapshot are retained under
`A9-build/mainline/drain-gauge-host/` (`stock-power-with-tables.json`,
`stock-power-disassembly.txt`, `stock-power-tables-analysis.json`).

| Stock address | Observed behavior | Mainline counterpart |
| --- | --- | --- |
| `0xffffff8008a45928`, charge-counter case `0xffffff8008a45aa8..0xffffff8008a45e50` | `qg_psy_get_property`: FCC from SDAM offset0x68 or nominal FCC, multiplied by rounded/clamped cached `cc_soc` at offset0x6f4, divided by100. This is a coarse software SOC estimate, not a free-running hardware coulomb register. | Keep legacy Android properties unchanged; expose a dedicated relative drain snapshot. |
| `0xffffff8008a473e0` | `get_sample_count`: `1 << ((reg4851 & 7)+1)`. | Decode actual ADC samples represented by each FIFO average. |
| `0xffffff8008a47480` | `get_sample_interval`: reg4852 times10ms on PMI632. The clock-adjust workaround is for PM6150. | Decode actual sample duration, without extrapolating instantaneous current over elapsed sleep. |
| `0xffffff8008a42a88` | `qg_process_fifo`: current FIFO at48a0, signed16 averages; 0x8000 is invalid. | Sum each average times its actual ADC count and interval. |
| `0xffffff8008a427f8` | `qg_process_rt_fifo`: STATUS3 at480a low nibble is completed FIFO count; signed24 current accumulator at488b and partial count at488e. | Include partial ADC accumulation, without filling incomplete intervals. |
| `0xffffff8008a44780` | FIFO interrupt processing compares actual elapsed time with expected sampling duration. Hardware can stop sampling in S3. | RTC wake before FIFO/S3 limits; reject overflow, config changes and missing coverage. |
| `0xffffff8008a468d0`, `0xffffff8008a46cb8` | Suspend/resume harvests partial data and can use good sleep OCV at4874 when4809 bit1 is set. | Do not assume an arbitrary long suspend is fully sampled. Report measured coverage and monitoring wakeups. |
| `0xffffff8008a2eaf0`, `0xffffff8008a2db88` | Stock input-suspend setter/getter uses a separate USER vote, independent of USB stack current votes. | A dedicated USER pause survives DWC3 current/reset/resume votes. |
| `0xffffff8008a2ae44..0xffffff8008a2ae8c` | OEM `charging_enabled` branch inverts the input boolean and calls input-suspend. | Drain pauses USB input, rather than only turning off battery charging while USB powers the system. |
| `0xffffff8008a2c318`, writes at `0xffffff8008a2c354/368` | `smblib_set_usb_suspend` updates USBIN command1340 bit0. | Dedicated root-only `a9-usb/input_suspend`. |
| `0xffffff8008a3a9f8` | Ordinary charging-disable vote updates1042 bit0. | Existing normal charging stop and every fault/JEITA/voltage restriction remain active. |

Primary source cross-checks:
[Qualcomm QG property and suspend logic](https://android.googlesource.com/kernel/msm/+/60e67638d95e7fd48f602b15729ea13c13c478d5/drivers/power/supply/qcom/qpnp-qg.c),
[QG FIFO and sample helpers](https://android.googlesource.com/kernel/msm/+/b12578d7731ada3b0477db98b1257e4fa537b97b/drivers/power/supply/qcom/qg-util.c).
The exact saved ELF controls the address-specific conclusions above.

## Driver behavior

`drain-qg.h` decodes the inherited S2 configuration and S3 qualification length.
An ALARM_BOOTTIME timer wakes at half the smaller FIFO/S3 window, capped at30s.
It holds a wakeup source only until freezable work can run after bus resume.
Sub-second configurations are unsupported. Neither sampling configuration nor
IRQ masking is changed. Master-hold4841 bit0 brackets each coherent FIFO read;
release restarts the FIFO. There are no SDAM, calibration or persistent writes.

`drain_snapshot` returns capacity, relative charge in uAh, design FCC, hardware
sampled milliseconds, elapsed milliseconds and collection wakeups together.
The charge baseline is zero at stage start; subtraction yields discharged uAh.
Current scale and stock profile come from the existing driver. Master-hold gaps
are not filled. Each batch rejects missing/replayed data and FIFO overflow;
the stage requires99–101% aggregate hardware coverage and records it. The rate
uses actual sampled time, and suspend residency uses BOOTTIME minus MONOTONIC.
Periodic wakeups and USB traffic are included in the measured overhead.

`drain_capacity` uses fresh loaded VBAT against the stock temperature-dependent
profile, bounded downward by the relative integral while active. This is a
conservative stage floor estimate, not a replacement for the full stock QG SOC
algorithm or learned FCC. Start must exceed35%; the driver, parent and separate
host watchdog restore at35%, leaving margin above30%. Missing measurements end
the stage. The legacy stale percentage does not protect this stage's floor.

USER pause and pending restoration have separate state. Restoration explicitly
clears/readbacks1340, releases QG, takes a fresh sample, then reapplies the normal
DWC3 and charging policy. It never clears a fault or overrides JEITA. Failed
USER/QG release stays visible and retries on a one-second alarm independently
of ordinary polling. A paused poll error also ends collection. Unplug, low SOC,
read failure, invalid coverage or a70-minute kernel bound ends collection.
The host's finally block and independent watchdog additionally restore charging
and check readback. Restoring input never means charging an unsafe battery.

## Host validation and image integration

`test-drain-kernel.py` compiles the actual input/charging/drain/PM functions with
an in-memory regmap. Five groups exercise USER vote isolation and signed batch
accounting; fault/thermal restoration; start-write/FIFO/release failures and
pending retry; fresh floor/overflow/unplug; suspend alarm and module cleanup.
`test-drain-qg.c` covers step currents, signed partial sums and missing hardware
data across a long CPU sleep. Existing notification and legacy suspend-counter
checks remain applicable. The five stage test groups include a simulated hour
with complete/incomplete hardware coverage and fresh watchdog reads.

`module-payload.py` builds only the module in private output against exact image
config/exports, checks every import CRC and vermagic, and pins production source
hashes. Incremental `build14.py` replaces the inherited battery module with this
fresh payload and records it. The old battery image builder copies the new
headers too. This needs no second kernel build and changes no device tree.
