# HVENC Surface qualification for the next integrated image

Hardware AVC is required for daily use. The software preference from 4e868cf
remains a fallback and supplies no hardware qualification. The 94f70e7 negotiated
input-size repair is on main; its host checks do not prove the historical hang
fixed. Integ26 ran the Surface qualification: both decoders passed, but encode
failed before kernel START because the first RGBA input lacked CPU-read usage.
The [integ26 analysis](../../../runs/integ26-hvenc-20261025.md) records the exact
kernel/logcat boundary and the default-off allocation fix prepared for integ27.

Enable the new stage for the owning coordinator's next freshly built image:
`A9_SWEEP_HVENC_QUALIFY=1 /path/to/new-integrated-image/run.sh`. Default is 0;
invalid values refuse before transport. Do not inject this into a live sweep or
modify an existing frozen image. build14 (and its later-generation wrappers)
builds `hvenc-qualification/` and includes its complete source/hash closure in
the frozen runtime. The bundle binds the encoder receipt, Venus modules,
Codec2 patches and unchanged stock firmware lock. No new firmware/kernel packet
behavior is introduced. The original ByteBuffer probe is labelled
`qualification=0`; only the enabled Surface stage can supply `qualification=1`.

For integ27, CPU-readable input is now the **component default**, for real
camera/MediaRecorder/MediaCodec/recorder surfaces as well as the synthetic test.
The [real-app follow-up](../../../runs/hvenc-realpath-20261025.md) traces the
framework consumer usage, stock OMX/C2D addresses and corrected C2 flag mapping.
Patch0014 requests C2 `VIDEO_ENCODER | CPU_READ` (65537), which the framework
converts to gralloc `VIDEO_ENCODER | CPU_READ_OFTEN` (65539). Raw gralloc3 must
not be used as C2 usage: it includes C2 READ_PROTECTED. The initial opt-in in
762033c made that enum mistake; the follow-up repairs it before integ27.
`A9_ENC_TRACE op=input_usage phase=end cpu_read=1 usage=65537` proves the fresh
encoder interface consumed the default. Decoder usage and mapper checks remain.

For A/B, `A9_SWEEP_HVENC_CPU_READ=0` disables the request for that qualification;
`=1` forces it on. Unset/empty uses the default without changing a property.
Explicit overrides save/set/read back `debug.stagefright.a9.enc_cpu_read`, then
restore the previous value in finally within the same owner deadline.
`cpu_read_requested/applied/restored` and `cpu_read_value` retain the override.
This works alongside `A9_SWEEP_HVENC_VARIANTS=1`; hold CPU-read policy constant
across the A/B/C/D kernel cases. The fresh default encoder stage is
`A9-build/mainline/agent-hvenc-realpath-20261025/encoder-stage/`.
Hardware encoding remains unqualified until real frames, EOS and the playable
MP4 checks pass. Removing the first observed blocker may expose a later failure.

The enabled stage skips that earlier ByteBuffer probe, reserves 240 seconds for terminal evidence/closure and qualification,
and runs LAST, after normal feature rows, USB, optional warm reboot and health,
credential/E Ink closure, ANR extraction and persisted results. MTP failure does
not prevent this independent hardware qualification when closure and slot B
are proved. The original owner hold deadline is propagated through prerequisite
hooks. Normal commands and polling stop 240 seconds before that deadline; terminal evidence and cleanup stop 155 seconds before it. The qualifier still needs 145 seconds before the original qualification deadline;
finally's extra cleanup allowance grants no test time. There is one attempt,
no automatic replay and no feature check/reboot after it. Only scoped temporary
file cleanup, host result saving and the owner's existing return path follow.

`video/qualification/qualify.c` uses exact `c2.v4l2.avc.decoder` and
`c2.v4l2.hevc.decoder` identities to decode two fixed 20-frame 320x240/10fps
synthetic MP4s through EOS. It then requires `c2.v4l2.avc.encoder`, creates a
codec InputSurface, draws 30 moving GLES/EGL frames with explicit 100ms PTS,
and muxes AVC into a one-track, video-only MP4. It requires 30 submitted and
encoded frames, positive bytes, increasing PTS, EOS and successful muxer/codec
stop and delete. MediaExtractor requires 30 samples, duration 2.8–4.0s and
PTS span 2.8–3.1s; the resulting file must also hardware-decode 30 AVC frames
through EOS. Host ffprobe checks one AVC track, 320x240, 30 frames, 3s ±250ms,
and ffmpeg decodes the entire file with errors fatal. Only this synthetic
<=4MiB output is eligible for export. No camera, microphone or user media is
read, played or exported. This checks codec decode with CPU-visible output;
display presentation and long-running camera recording remain separate rows.

Bounds: decoder API loops 10s each; encode/drain 20s; native alarm 50s;
device timeout TERM at 55s, KILL at 57s; host command timeout 60s. Staging,
stat/pull and remote cleanup have separate bounded calls within a 140s stage
budget. A userspace timeout cannot stop an all-CPU/interconnect hang; the
existing watchdog, panic settings, logger and original owner restore remain.
A timeout, missing hardware name/EOS/track/duration or incomplete result FAILS.

`HVENC_QUAL` records numeric begin/end boundaries: 1 AVC fixture decode,
2 HEVC decode, 3 encoder create/configure, 4 InputSurface, 5 EGL, 6 first swap,
7 signal EOS, 8 first output, 9 muxer stop, 10 encoder stop, 11 encoder delete,
12 MP4 inspect, 13 AVC roundtrip, 14 encoder start, 15 EOS drain, 99 alarm.
Decoder stop/delete use op+20/op+40. Phase 0 begins, 1 ends, 2 observes.
The final numeric `HVENC_RESULT` includes exact-name flags and every count.
Both records are immediately synced in the boot log, alongside existing
`A9_ENC_TRACE` allocator/QBUF/STREAMON and `A9_VENUS_ENC` HFI send, queued,
response, bufreq, set-buffer/property and input/output completion records.

The original owner's post-return pull runs `qualification/extract.py` on each
saved boot independently. Each boot gets `hvenc-diagnostics.log` (typed numeric
whitelist) and `hvenc-diagnostics.json` (firmware response/error/timeout,
buffer completion and kernel splat counts, last native boundary). Full kernel
splats remain private in `kernel.early.log`, `kernel.log[.prev]` and the owner's
pstore archive. Previous-boot pstore never supplies this boot's completion or
error counters. Host numeric `hvenc-qualification.json` and playable synthetic
`hvenc-output.mp4` are copied to the final boot only when its probe is present.
No identifiers/raw errors are emitted in the console summary. Missing/truncated
firmware diagnostics, any observed firmware error/timeout or kernel splat, or
incomplete native result prevents overall PASS when enabled. Missing file
completion is evidence of a failed capture, never a hardware success.

Host validation: five qualification groups (real MP4 decode and source-pin
checks; fake transport deadline/one-attempt/cleanup/refusal; real sweep LAST
ordering; partial/duplicate/nonnumeric result rejection; per-boot privacy and
old-pstore isolation), existing five acceptance and two legacy encoder groups,
fake b-try two-boot integration, NDK API30 compile with warnings as errors,
regular-file logger fsync checks and static ARM64 logger compile. Native target
entry points and real transports were never run. Evidence:
`A9-build/mainline/hvenc-qualification-host-20261024/`. Actual HW PASS remains
unqualified until the coordinator executes the new stage on the integrated image.

---

# ENC-HANG host findings and encoder input sizing repair

Current result: the takeover below repairs a demonstrated pre-firmware input
length defect and supplies a new checked stage. Hardware hang/PASS remains
unqualified. Earlier intake/stage descriptions are historical.

INTEG16 boot112 stops at kernel time224.274188 during allocator/mapper
service discovery. Encoder OUTPUT/CAPTURE REQBUFS both returned four buffers;
no retained encoder completion, HFI timeout, SMMU fault, watchdog panic or Oops
establishes a cause. Earlier E Ink rail-fault latching is independent evidence,
not a demonstrated cause. Stage4 only means the input/output loop was entered;
it does not prove first input, streaming, or EOS reached firmware.

Stock libOmxVenc.so SHA256
6d7847173f2652a253d5bb0fe4392e693da59d4dc90c8164ac7631e2b007be42:
REA/Ghidra identifies venc_open0x14b158, venc_set_buf_req0x14bce0,
venc_start0x14cab0, venc_empty_buf0x14d588, venc_fill_buf0x1504bc,
venc_stop0x14c584, venc_handle_empty_eos_buffer0x14c418. Start streams
CAPTURE(type9); ordinary input uses OUTPUT(type10), and FTB queues CAPTURE.
The empty-input path explicitly rejects zero-length EOS; its separate handler
ensures OUTPUT streaming then issues VIDIOC_ENCODER_CMD(STOP=1), ioctl0xc028564d.
Our Codec2 also uses encoder STOP; the downstream kernel's exact HFI packet
is not established by the library. Mainline currently sends the synthetic
0xdeadb000 EOS address with allocation1 for old firmware. This remains a
compatibility question, not a proven cause of boot112's loss. HFI4 has no
platform bufreq calculator, so mainline queries firmware buffer requirements.
Do not change scratch allocation based on a supposed HFI6 sizing mismatch.

Merged diagnostics preserve the early /dev/kmsg backlog in a bounded3MiB
RAM ring while UFS becomes available, save kernel.early.log separately from
rotating kernel.log, and snapshot previous ramoops records into
pstore.previous.log without erasing them. Existing console/ramoops config,
stock1MiB region and record offsets remain. The logger sets and reads back
hung_task_timeout_secs60, hung_task_panic1, softlockup_panic1,
hardlockup_panic1, watchdog_thresh10, watchdog1 and panic5 as A9_HANG_DIAG
records. Missing knobs/errors are visible. Existing watchdog ownership/reset
routes remain. Firmware hard resets can still erase all ramoops RAM; neither
logging nor software lockup detectors can guarantee evidence through an
all-CPU/interconnect/storage hang.

The native synthetic probe now logs first input/output, EOS and stop/delete
boundaries to stdout and Android logcat (A9Encode). The logger immediately
syncs A9_VENUS_ENC, A9_HANG_DIAG, HFI timeout/session-error and selected
SMMU/lockup/panic records. No encoder success is inferred from host checks.

Host checks: native regular-file logger test (write-before-sync, marker scope,
error continuation and early-ring wrap), ARM64-musl logger and Android NDK
probe compile with warnings as errors, and existing five acceptance groups
pass. The actual logger entry point was never run on the host. No phone access.

The kernel trace now brackets session commands, properties, buffer requirements,
stream/PM stages, initial buffers, completions and EOS. Its final three modules
compile; replayed sources match those compiled, and all Venus exported symbol
CRCs remain identical. No structure layout or packet behavior changes. Earlier
intermediate modules remain unsuitable for packaging. The next image must
rebuild the logger and native probe; the old encoder stage receipt deliberately
rejects changed probe/build sources and must be refreshed. integ17 was left
untouched. Evidence and REA records: A9-build/mainline/enc-hang-host/.


INTEG17 adds the same failure during automatic camera H264 selection with a
Surface input, near299s. Its last provider marker, like INTEG16's synthetic
ByteBuffer trial, is `Using unoptimized linear block pool`. Both queues already
allocated four slots. The next call is GetCodec2BlockPool(BASIC_LINEAR); subsequent
first-frame mapping, conversion allocation and submission are silent in the
current build. These logs do not prove STREAMON, first HFI ETB, EOS, or a hard
SoC lockup. Native final closure in SWEEP17 also prevents treating every failed
command as a permanent kernel/root failure. Add userspace boundaries before
narrowing the diagnosis further. Software encoder selection protects unrelated
camera trials but does not qualify or fix the hardware encoder.


Userspace tracing now spans output-pool acquisition, first-frame C2 mapping,
secondary GraphicBuffer YCbCr locking, converter pool/allocation/maps, first
input QBUF and each STREAMON. A failed C2 map is returned as an error before
its layout/data is accessed. Markers carry sizes/statuses, never pointers,
fd numbers or media bytes. The logger immediately syncs A9_ENC_TRACE records
from logcat as well as kernel trace records. Capturing only dmesg would omit
the allocator boundary and cannot distinguish the two layers.

Saved logs positively show the provider forcing DMA-BUF heaps before decoding
in both boots. The mere presence of the QSEE-only /dev/ion proxy is therefore
not evidence that these trials selected the wrong ION allocator. No heap,
SELinux, PM/clock or firmware change is justified by that hypothesis. Likewise,
HFI4 uses firmware buffer requirements; no HFI6 scratch-size workaround was
applied. USB and Venus have distinct local GCC GDSCs/clocks but share the apps
SMMU/interconnect infrastructure; a fabric/SoC failure remains possible without
being demonstrated. The retained logs contain no qualifying SMMU fault, HFI
error, panic or watchdog stack. Firmware DDR clearing can explain missing pstore
but cannot establish that a panic happened.

| Last begin without end | Next failure boundary to inspect |
| --- | --- |
| output_pool | platform linear allocator/store acquisition |
| layout.map or layout.lock_ycbcr | C2/gralloc map or secondary lock |
| converter.pool/allocate or convert.*_map | converter/gralloc allocation or mapping |
| qbuf.input | DMA-buf import/prepare/QBUF (before firmware start) |
| streamon.output with kernel PM marker | PM/core/clock acquisition |
| HFI send with queued but no response | firmware reply/IRQ path; retain numeric timeout/error |
| buffer.begin without buffer.end/done | ETB/FTB submission or firmware completion |
| EOS marker only | old-firmware EOS; do not blame it before it is reached |

Validation adds clean twelve-patch replay, compilation of all four changed
Android translation units, a complete Codec2 library link against the retained
GSI ABI and the regular-file logger test including non-kernel marker syncing.
These boundaries are ready for the next diagnostic image. They are not proof
that either original trial reached firmware or that encoding is repaired.

## HVENC follow-up, 2026-10-24

STAB was parked on main8802f17. This follow-up remains host-only; it does not
touch the live integ18 worktree/sweep or request a phone operation.

The saved integ18 onb verdict confirms AVC command_rc255, stage4, completed0;
HEVC command_rc1, stage0, completed0; cleanup0. ADB's255 is a command/transport
failure, not a native encoder errno, a firmware response, or proof of a panic.
At the last host intake check the returned `mainline.tar` and boot directories
were still absent from `sweep-integ18-f1-20261024/`. Do not reconstruct missing
kernel/allocator evidence from this status. Inspect that archive when the
coordinator's original return to A finishes; never duplicate its phone operation.

The actual integ18 encoder receipt is based on4e868cf and lacks the subsequent
68ac010 HFI tracing and da2011e allocator/converter tracing. Its codec library
SHA256 starts8a996b02, while the compiled traced library starts5619dc8f.
Its Venus modules likewise differ from the final traced hashes. Consequently,
integ18 cannot supply those new boundaries even if all its logs are recovered.

### Additional stock REA

New REA/Ghidra12.1.4 analysis binds the same libOmxVenc SHA256 shown above.
These are binary virtual addresses, not source line numbers. Selected evidence
is retained under `A9-build/mainline/hvenc-host-20261024/rea-*.json`; earlier
start/empty/fill/EOS records remain under `enc-hang-host/rea/`.

| Stock entry | Observed behavior | Comparison / limit |
|---|---|---|
|venc_open0x14b158|Opens video33; chooses H264/HEVC capture fourcc, NV12 output fourcc; both REQBUFS use memory2/USERPTR.|Our discovered Venus node and DMABUF input/MMAP capture use the mainline kernel ABI. The stock USERPTR planes also carry downstream fd/offset information, so copying this userspace ioctl layout is not an upstream buffer-import fix. Evidence ev_893c9f6b0171a0f391d3fe83f262f47a4bd7278cd98a5804d40637aaaac5f03f.|
|venc_set_color_format0x148968|OMX color0x15 selects NV12 fourcc0x3231564e and sends S_FMT for OUTPUT type10.|The shared NV12 choice does not identify the hang as an unsupported input format. Stock has other RGB/UBWC formats; their existence does not show this mainline trial used one. Evidence ev_a366e1fb99c37bcfc9c5f76ea2b5a88894720e7cd6b481b57a1910e2b7b69c44.|
|venc_get_buf_req0x14be98|Reads driver format/sizeimage and minimum-buffer controls0x980928/0x980927; validates counts<=64; rounds the input allocation to4KiB.|Our HFI4 path queries firmware buffer requirements. The320x240 NV12 mainline calculation is384x256/147456bytes, exactly page aligned. Neither this comparison nor stock's rounding establishes a size failure in the observed trial. Evidence ev_6c5a6eff6e42ab02b758a1b5c7a4bc142959a9f6fd9daa15bf0cfc9a5f035ad2.|
|venc_start0x14cab0; venc_empty_buf0x14d588; venc_fill_buf0x1504bc|Start streams CAPTURE type9; input starts OUTPUT type10 and QBUFs ETB; FTB queues CAPTURE buffers.|Our vb2 scheduling differs, but both queues require buffers and streaming before the kernel starts the session. No saved boundary demonstrates a failure at this difference.|
|venc_handle_empty_eos_buffer0x14c418|Ensures OUTPUT STREAMON, then sends VIDIOC_ENCODER_CMD STOP1/0xc028564d.|Our Codec2 also uses STOP. This does not establish the downstream driver's HFI packet or justify another EOS quirk before evidence reaches EOS.|

Firmware usage is through the kernel, not a direct libOmxVenc firmware loader.
The port retains the OEM-signed VIDEO.VE.6.0-00039-PROD-1 payload, joined SHA256
44c39a0ff5a25433626ae4b21e3267b0713cdf4c5338cdf88763489d72a3da05,
and HFI_VERSION_4XX/AR50_LITE. No firmware/signature/PAS change is made.
Successful decoding and stock encoding do not establish upstream encoder
compatibility. The library decompilation does not recover the firmware's
internal execution or prove a particular HFI incompatibility.

### Concrete repairs and build consumption

`stage-encoder-trace.py` assembles a new checked stage from the already compiled
final modules/library, current init/XML and a freshly compiled NDK probe.
It replays the kernel compatibility/diagnostic patches and all twelve Codec2
patches and compares them to the compiled source. It checks actual module
hashes, vermagic/imports, the existing nine payloads and all83 unchanged private
dependencies against the immutable vendor baseline. The existing production
helper test executes with the assembled library set under Android QEMU.
Old stages and images are never rewritten.

The final stage is
`/home/user/A9-build/mainline/hvenc-host-20261024/encoder-stage-r2/`.
The updated incremental recipe selects it by default; an explicit override
still uses the existing source/payload/ABI verifier. The stage recipe is:

```sh
python3 -B scripts/mainline/video/stage-encoder-trace.py --out NEW_ABSOLUTE_DIR
```

The default inputs are the old encoder-host baseline, enc-hang-host final
compile evidence, and integ17's immutable vendor/Module.symvers. This is stage
assembly, not a kernel/image build or a device encoder test. Current image
build14 already rebuilds the logger from source, so it also consumes the logger
changes below. The coordinator must rebuild the acceptance assets for the
changed onb source as usual.

Two further evidence-loss bugs are repaired. The logger previously synced C2
boundaries immediately but left the NDK probe's ENCODE_STAGE/ENCODE_IO records
on its ordinary two-second sync cadence. It now syncs those numeric records
and the terminal ENCODE record; the probe also writes its terminal result to
logcat, independently of its ADB stdout. No raw frames or pointers are logged.
These writes cannot guarantee persistence through a storage/fabric hard hang.

The host runner also discarded received stdout on subprocess timeout. Only
the public synthetic encoder now opts into124 with partial text kept privately;
credential/UI commands retain their existing discard behavior. Row18 saves only the last
recognized numeric IO boundary/counters. io_point codes are first_input.begin1,
first_input.end2, first_output3, eos.begin4, eos.end5, stop.begin6, stop.end7,
delete.begin8, delete.end9. Unknown operations and other-codec records cannot
overwrite the boundary. Missing completion or any nonzero command status still
fails; the success predicates,30-second outer timeout and cleanup remain.

Host evidence is under `hvenc-host-20261024/`: new stage receipt/replay logs,
packaged production helper groups, regular-file logger test, ARM64 logger/NDK
probe compiles, focused timeout/partial-boundary tests and acceptance regression.
No actual logger entry point or encoder component is executed on the host.

**Root cause is still unresolved.** The current evidence cannot distinguish
linear allocator acquisition, graphic mapping, vb2 import, stream/PM setup,
firmware/IRQ or later EOS. No speculative driver, allocator, power, policy or
firmware workaround is applied. The next useful phone run is the coordinator's
**integ19 with agent's row29 fix**, consuming this traced stage and current
logger/assets. Retain the first unmatched begin/end boundary using the table
above; require real AVC and HEVC output+EOS and the existing decoder regression
before claiming the hardware encoder is repaired.

HVENC HOST DONE — traced intake prepared; hardware root cause/PASS pending.

## agent takeover: negotiated input length, 2026-10-24

Host-only analysis transferred from agent while its builds remain owned by
agent. No phone operation, live sweep, existing stage or image was changed.
Integ19 row18 was NOT_RUN: cleanup reported wifi_disabled0 and blocked MTP,
which blocked hardware encoding. Its two decoder cases each produced60 frames
and EOS. This is not a traced encoder failure. Evidence:
`A9-build/mainline/hvenc-f1-host-20261024/` and agent's checkpoint.

The saved stock REA above provides a concrete comparison: `venc_get_buf_req`
at **0x14be98**, in libOmxVenc SHA2566d784717…be42, takes its input allocation
size from returned V4L2 format `sizeimage` and rounds it to4KiB. Evidence
ev_6c5a6eff6e42ab02b758a1b5c7a4bc142959a9f6fd9daa15bf0cfc9a5f035ad2
is retained in `hvenc-host-20261024/rea-buffer-requirements.json`. The stock
USERPTR setup at **0x14b158**, NV12 choice at **0x148968**, queue start/input
at **0x14cab0/0x14d588**, and STOP at **0x14c418** establish the comparisons
in the table; they do not justify importing its downstream ABI into upstream.
The existing OEM firmware VIDEO.VE.6.0-00039-PROD-1, HFI4 and AR50_LITE
selection remain appropriate to the known payload; no new firmware was needed.
Mainline `venc_queue_setup` establishes OUTPUT sizes with
`venus_helper_get_framesz`, obtains firmware input buffer counts through
`venus_helper_get_bufreq`, and `venc_verify_conf` checks the negotiated counts.
This task changes no session, buffer-count, firmware, EOS or power behavior.

The demonstrated discrepancy is in the C2 vendor component's first input
submission. Production `v4L2FormatToVideoFrameLayout` retains picture width320,
height256 and plane0 size147456 from a stride384 NV12 format. Production
`allocatedSizeFromV4L2Format` returns384x256, so the normalized graphic
allocation has enough storage. But `enqueueInputBuffer` calculated QBUF length
from tightly packed320x256, obtaining122880. `queueDMABuf` forwards that length.
Kernel `videobuf2-core.c::__prepare_dmabuf` rejects a nonzero length below the
plane's negotiated minimum with EINVAL before attach or firmware submission.
Stock's driver-derived sizing avoids this particular discrepancy.

Patch0013 now uses `mInputLayout->mPlanes[0].mSize` for the single memory
plane, retaining existing base-offset addition. Both length and bytesused
include the negotiated padding. Multi-memory-plane handling is unchanged;
kernel DMA-BUF import still checks actual backing storage. Existing QBUF
failure logs retain errno, length, bytesused, offset and DMA-BUF extent.

Host evidence is isolated under
`/home/user/A9-build/mainline/agent-hvenc-20261024/`:

- All13 patches replay with fuzz0; only V4L2Encoder.cpp differs from the
  previously compiled traced tree. That TU compiles and the complete library
  links against the existing GSI ABI. `build-recipe.py` records the command.
- `queue-test-before/` runs the original production enqueue method: aligned
  input passes, stride-padded input fails the minimum-length contract.
- `encoder-stage-r2/input-queue/` runs the repaired method and production format
  helpers with the assembled library: aligned147456, stride-padded147456,
  tail-padded151552, offset151552 and multi-plane98304/49152 all pass. The
  synthetic transport checks minimum length, actual allocation extent, offsets,
  timestamp and successful frame ownership transfer. It executes no video ioctl.
- The three existing production encoder helper groups pass with the assembled
  runtime. NDK probe compilation, nine payload hashes, module vermagic/imports,
  all83 unchanged dependencies, current source pins, software-first ranking and
  timeout-boundary tests pass. Previous stages correctly fail source-pin intake.

The new library SHA256 is
`8731047dee1c2484aafaa211a258b430ccb4f1f99dbc10261156768bfeb757e7`.
The new immutable stage is
`/home/user/A9-build/mainline/agent-hvenc-20261024/encoder-stage-r2/`;
`reboot/build14.py` selects it by default. The stage assembler accepts a separate
`--codec-build` for the repaired library and retains the verified kernel trace
build. A clean full Codec2 build also consumes0013 automatically.

This repairs a proven sizing defect; it does **not** prove why the old phone
stopped responding. Those trials did not retain a first-QBUF boundary, and
this length error normally returns EINVAL. The next coordinator-owned image
must use the new stage and current logger. Its existing bounded30-second,
exact-name AVC/HEVC probes retain these decision boundaries: allocator/map
begin without end is userspace; QBUF failure with numeric length/errno is
import/prepare; STREAMON with unmatched PM is kernel setup; queued HFI command
without reply is firmware/IRQ; completed input submission without output/done
is buffer processing. No extra hardware trial is authorized by this host task.
Keep software selection preferred. Require actual AVC **and** HEVC output/EOS
and the decoder regression before calling row18 PASS.

HVENC INPUT SIZE HOST DONE — repaired, staged; device qualification pending.

### Integ24 admission correction

The enabled qualification never reached payload verification: onb.log152 recorded
cleanup PASS,153 had budget_ok0/attempted0/bundle_verified0. The old180-second
wait reserve was consumed by snapshots, forced ANR collection and final closure.
All ordinary subprocesses and polling now share the protected deadline above;
terminal work has its own85-second allowance. Completed sweeps reuse the recent
ANR capture rather than forcing a duplicate. Neither the owner deadline nor PIN
window is extended. `remaining_ms` and `skip_reason` retain numeric admission
evidence (1 context,2 closure,3 budget). Hardware remains unqualified until an
actual next-image run completes; no failed or skipped row receives PASS.
