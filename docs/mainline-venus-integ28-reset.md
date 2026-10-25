# integ28 manual Surface encoder reset

2026-10-25, host-only. The decoder tests passed. Encoder setup also passed;
the retained log ends after the first Surface submission. **No encoded output
or reset cause is established.** The next isolated candidate is the stock-style
bidirectional mapping already shipped in integ28, enabled before codec creation.
The user owns all device operations.

## What the operations actually count

The frozen image is `/home/user/A9-build/mainline/integ-20261024-28`, source
`1e8e81d`, including watchdog repair `470f412`. Its bundled
`runtime/video/qualification/qualify.c` and the current
[qualifier](../scripts/mainline/video/qualification/qualify.c) have identical
SHA256 `21e97c79bba715577977f855e7ab9e347ba8e5c018a553e9ae9eb9408890f533`.
`phase=0` begins an operation, `phase=1` ends it; `rc=0` is API success.
`frames` has operation-specific meaning:

| Op | Operation | Meaning of the retained result |
| --- | --- | --- |
| 1 | Decode synthetic AVC through `c2.v4l2.avc.decoder` | 20 decoded frames and successful stop/delete. |
| 21 / 41 | Stop / delete that AVC decoder | Both succeeded; 20 is the existing decoded count. |
| 2 | Decode synthetic HEVC through `c2.v4l2.hevc.decoder` | 20 decoded frames and successful stop/delete. |
| 22 / 42 | Stop / delete that HEVC decoder | Both succeeded; 20 is the existing decoded count. |
| 3 | Create exact-name AVC encoder and configure 320x240, 10fps, Surface input | Final rc0 means configure succeeded. `frames=0` is hard-coded. |
| 4 | `AMediaCodec_createInputSurface` | rc0 means success. `frames=0` is hard-coded. |
| 14 | `AMediaCodec_start` for the encoder | rc0 means success. `frames=0` is hard-coded. It does not prove firmware encoded anything. |
| 5 | Initialize EGL, choose config, create context/window Surface, make current, resolve presentation-time function | rc0 means success. `frames=0` is hard-coded. |
| 6 | First `eglSwapBuffers` | End rc0/frames1 means one successful Surface swap/input submission. It does not count encoded frames. |
| 8, phase2 | First non-config compressed output successfully written to the MP4 muxer | Absent. This is the first positive encoded-output marker. |
| 7 | Signal encoder input EOS after all 30 Surface submissions | Absent. |
| 15 | Final encoder output/EOS drain | Absent. |
| 9 / 10 / 11 | Stop muxer / stop encoder / delete encoder | Absent. |
| 12 | Inspect muxed MP4 sample count, duration and timestamps | Not reached. |
| 13, then 33 / 53 | Round-trip decode output through AVC decoder, then stop / delete | Not reached. |
| 99 | Native process alarm/deadline | Absent; its absence does not prove a watchdog type. |

Thus zero for ops3/4/14/5 is expected success before any frame submission, while
op2 counts completed decoder output. These are different counters and workloads.
The first swap is the only per-input swap marker: later swaps and ordinary
drain polling are silent. The last line therefore does **not** prove the reset
occurred inside the first swap, immediately after it, or before a second input.
EGL/GPU work and codec/firmware work can both be asynchronous at that boundary.

Private evidence is `manual-hvenc-integ28-20261025/{N.log,N.rc,N-parameters.txt,
reset.log}` beneath `/home/user/A9-build/mainline/`. The transport returned255;
cleanup could not reach a device. CPU_READ was1; mapped EOS Y, bidirectional N,
output-size N, stock-work-mode N. **Y was not attempted.** No `HVENC_RESULT`
means the qualification did not complete.

## Reset diagnosis and stock comparison

The leading actionable hypothesis remains an encoder DMA/ownership or firmware
failure during early frame processing, possibly stalling the wider fabric or
secure-world path and ending in an APSS watchdog reset. A kernel panic followed
by its reset policy is another possibility. This is a hypothesis chain, not an
observed fault. The current log cannot select SMMU fault, Venus exception,
recovery stall, GPU fault or SoC watchdog. Decoder success does not exercise the
encoder's raw-input/reconstruction path or its Surface/GPU synchronization.

Stock imports buffers with `DMA_BIDIRECTIONAL` at
`msm_dma_get_device_address@0xffffff8008e204b8`; mainline HFI4 normally maps
input TO_DEVICE and capture FROM_DEVICE. Nonsecure SIDs0x860/0x880 match;
input geometry147456 bytes also matches the firmware requirement. No retained
fault IOVA/access direction establishes that firmware wrote to an input mapping.
[Earlier mapping evidence](mainline-venus-first-frame.md) also preserves the
cache, page-rounded internal length, IOVA placement and startup-vote alternatives.
Mapped EOS was not reached and is not a supported explanation for this boundary.

The stock recovery comparison uses the unslid 4.19 kernel Image SHA256
`2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`, through the
existing address-preserving REA carrier SHA256
`040492d327d6737b1d959fe39fb0f3c1a03d2c88e8c0050d9c9f1fc4a8eb17c0`.
Decompiler bounds/data omissions remain; this is static analysis, not execution.

| Stock location | Recovered behavior |
| --- | --- |
| `__core_clear_interrupt_ar50_lt@0xffffff8008e52dd8` | Reads wrapper IRQ status at0xb000c, latches relevant bits, acknowledges via0xa001c. |
| `__watchdog_common@0xffffff8008e30078` | Tests wrapper bit0x10, the **Venus-local** watchdog. |
| `venus_hfi_core_work_handler@0xffffff8008e40b38` | Dispatches watchdog response6 or firmware SYS_ERROR response7; watchdog prints SFR and leaves the IRQ disabled. |
| `handle_sys_error@0xffffff8008dab7e0` | Calls `subsystem_crashed("venus")`, prints NoC/session diagnostics, invalidates sessions, then follows conditional BUG or core-release handling. External subsystem framework behavior is not established by this function alone. |
| `handle_cmd_response@0xffffff8008da38f0` | Routes both response6 (watchdog) and response7 (SYS_ERROR) to `handle_sys_error`. |
| BRK instructions `0xffffff8008dac558` / `0xffffff8008dac458` | Debug-timeout path or configured recovery-disable policy can deliberately trap. Bengal driver-data defaults debug-timeout false; recovery-disable runtime value is unknown. Assembly at0xffffff8008dabe64..80 tests disable bit0 with status0x80000013, or bit1 regardless. A BUG's eventual reset depends on kernel panic/watchdog policy. |
| `msm_vidc_smmu_fault_handler@0xffffff8008e2cd98` | Logs the fault address and instance state; repeated nonfatal faults are rate limited. No reset/SSR/BUG appears in this callback. Wider SMMU handling remains outside that bounded result. |

New REA evidence IDs S1–S6 and exact branches/defaults are indexed in
`/home/user/A9-build/mainline/venus-integ28-20261025/stock-reset.md` and its
`stock-reset-rea/evidence.json` bundle. Xtensa Venus firmware was not decompiled.

In the exact retained mainline build, `hfi_venus.c` now dispatches local watchdog
bit0x10 through `hfi_process_watchdog_timeout`, as fixed by470f412.
`venus_event_notify` and `venus_sys_error_handler` in `core.c` notify sessions,
disable the IRQ, then deinit/shutdown/coredump/PM-cycle/reinit/boot Venus. Failure
logs and retries recovery; **there is no explicit whole-system panic/reboot
fallback in this handler**. PAS operations address Venus PAS9. A call that
stalls or fails elsewhere can still prevent local recovery.

Consequently the already-fixed missing watchdog dispatch cannot be claimed as
this reset's cause, and normal Venus-local SSR cannot be asserted to have
escalated deliberately into an APSS reset. A global watchdog bite remains a
plausible final mechanism. Boot reason `reboot`, automatic return to B about a
minute later, and deadline disarm at106s of the successor do not identify it.
Check the **crashed boot's** original deadline disarm/EXPIRED markers separately.

## Pull now, then make one isolated mapping comparison

The early logger stores logs under `kdebuginfo:/mainline`, unlike the Magisk
`/a9` path read by `scripts/a9-bootlog.sh`. The B diagnostic domain cannot mount
sda9 or read ordinary pstore. The new user-run pull first tries the existing
logger mount via `/proc/<pid>/root`, without mounting or changing policy:

```sh
bash scripts/mainline/manual/hvenc-pull.sh /home/user/A9-build/mainline/hvenc-reset-integ28-pull
```

That proc access is not yet device-verified. On refusal, after the sweep owner
releases the phone, enter TWRP and run the established recovery mount path:

```sh
bash scripts/mainline/manual/hvenc-pull.sh /home/user/A9-build/mainline/hvenc-reset-integ28-recovery --recovery
```

The script never reboots. Preserve `mainline.tar` and its file listing: the last
three boots' `kernel.log`/`.prev`, `kernel.early.log`, `logcat.log`/`.prev`,
`info.txt` and `pstore.previous.log`. The successor's early pstore copy describes
the preceding boot. `pstore.tar` saves current `/sys/fs/pstore` files if readable,
including console/dmesg/pmsg ramoops records; recovery is needed if B denies it.
The separate current-dmesg and current-boot files describe the running boot.
No records are erased. Empty retained RAM is not proof of no panic.

Look for these discriminators in the crashed boot and its successor's pstore:

- `A9_VENUS_RECOVERY event=1`, IRQ0x10, SFR/firmware error, and recovery success or failure.
- Complete SMMU context fault: FSR, IOVA, FSYNR/WNR, SID/CBFRSYNRA, context-bank index; compare input/output/internal mappings if available.
- Kernel panic/BUG/backtrace, soft/hard lockup, RCU stall, NoC/interconnect, SCM/PAS, GPU faults and watchdog bark/bite.
- `A9_BOOT_DEADLINE EXPIRED`, original disarm, and any explicit reboot/shutdown request. Keep boot numbers and elapsed times intact.

The prepared candidate uses the **existing** integ28 module; no flash is needed:

```sh
bash scripts/mainline/manual/hvenc.sh /home/user/A9-build/mainline/integ-20261024-28 /home/user/A9-build/mainline/manual-hvenc-integ28-Y Y
```

This holds CPU_READ1, mapped EOS Y, output-size/work-mode N, and enables
`a9_bidir_buffers=Y` before fresh encoder creation. New bounded kernel/logcat
streams write directly to host files during the attempt; post-failure dmesg
alone could miss the fault. The nc kernel reader releases its endpoint before
parameter reset. Transport loss stops further cases; a clean codec error can
still proceed to Y in the explicitly selected NY mode. No watchdog is disabled
and no speculative kernel default is changed. A positive result needs actual
encoded output, EOS, MP4 inspection and round-trip decode, not op6 or QBUF alone.

## Host verification

### CPU-filled linear input comparison

The standalone `scripts/mainline/video/qualification/linear.c` bypasses EGL,
gralloc, the format converter and Codec2. It requests linear NV12, fills one
320x240 frame using the negotiated padded stride/height, and submits it directly
to the Venus V4L2 encoder. `mmap` uses driver-allocated input; optional `system`
uses `/dev/dma_heap/system` with explicit CPU-write synchronization. A denied
system heap stops before opening the codec. Both use MMAP capture buffers.
The runner preserves live kernel/logcat output, restores the four original
Venus parameters after a completed transport, and independently decodes the
returned H.264 on the host. Transport loss stops the attempt without retry.

Build on the host into a new directory:

```sh
python3 -B scripts/mainline/video/qualification/build-linear.py --out /home/user/A9-build/mainline/venus-linear-20261025/bundle-final
```

That bundle is already built locally. With the existing integ28 B boot and its
root nc endpoint available, the user can run this host command once:

```sh
bash scripts/mainline/manual/hvenc-linear.sh /home/user/A9-build/mainline/venus-linear-20261025/bundle-final /home/user/A9-build/mainline/manual-hvenc-linear-integ28-mmap mmap
```

This enables bidirectional mappings, keeps output-size/work-mode experiments
off, and does not reboot. A pass requires a real decoded 320x240 frame, rather
than QBUF acceptance alone. No hardware result is established yet.

Saved stock analysis at
`/home/user/A9-build/mainline/venus-linear-20261025/stock-surface-heap.md`
finds ordinary unprotected gralloc uses system ION mask `0x02000000`, with
flags0/1 according to cache usage; OMX's separate allocator requests mask
`0x02000200`. Stock OMX defaults to linear NV12, while private gralloc usage
can select UBWC. These are static paths: the actual stock Surface allocation
and final negotiated format were not measured. Ordinary encoder input does
not require a secure heap or secure VMID reassignment.

The two focused runner groups pass through fake adb and Android mksh under
QEMU: upload, one MMAP attempt, host decode, parameter restore, heap denial
and transport-loss retention/no retry. The native syscall model covers layout,
VCL detection, MMAP, system-heap sync/denial and capture bounds. The static
AArch64 build matches its source manifest. All checks are host-only.

### Surface runner and log pull

Three focused fake-transport rehearsals execute the real pull payload through
Android mksh under qemu: private logger path, denial/recovery instruction, and
already-running TWRP. They verify exact previous-boot content, raw pstore archive,
and absence of B mount/reboot calls. Two encoder groups cover the existing
clean-error N-to-Y behavior, Y-only selection, concurrent captures, transport-loss
stop, and original parameter/property cleanup. Shell syntax and diff checks pass.
No device operation or hardware recovery/encode qualification occurred here.

### First linear run: optional control rejection

The user-run MMAP attempt ended before buffer allocation or submission, with
`HVENC_LINEAR_RESULT rc=-22 vcl=0 input_done=0 bytes=0`; the phone did not reset.
`0x9909cb` is `V4L2_CID_MPEG_VIDEO_GOP_SIZE`. Venus registers this control
with range0..65535/default30, but `venc_calc_bpframes()` explicitly rejects
zero with `-EINVAL` in the GOP setter. This is a rejected value, rather than
an unsupported control. The probe now requests1 (one-frame GOP).

All codec controls and the framerate request log their return status and
continue on rejection, retaining driver defaults. Format/layout, allocation,
buffer I/O and streaming failures remain fatal. The host syscall model rejects
every tuning request and verifies that all eight controls are attempted,
one input is queued, and compressed output is collected successfully.
This fixes a setup failure; the first run did not exercise encoding.

The updated static bundle is `venus-linear-20261025/bundle-controls`, beneath
`/home/user/A9-build/mainline`. From the host, use a fresh output directory:

```sh
bash scripts/mainline/manual/hvenc-linear.sh /home/user/A9-build/mainline/venus-linear-20261025/bundle-controls /home/user/A9-build/mainline/manual-hvenc-linear-integ28-mmap-controls mmap
```
