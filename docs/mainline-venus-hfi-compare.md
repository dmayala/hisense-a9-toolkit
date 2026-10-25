# A9 Venus encoder: stock 4.19 HFI versus mainline

2026-10-24. Host-only static investigation and module build. **Hardware encode
remains required and unqualified.** A proved difference is stock's mapped EOS
allocation versus mainline's synthetic address. The default-off experiment below
changes that one path. It is not yet a demonstrated cause of the hang: the saved
surface-input evidence does not prove that the failed session reached EOS.

Userspace analysis remains in [ENC-HANG.md](../scripts/mainline/video/ENC-HANG.md).
agent owns the userspace qualification stage; hand these kernel results to
that owner through the coordinator. No phone operations were performed.

The subsequent [ranked causality audit](mainline-venus-hfi-causality.md) accounts
for the complete setup chain, clocks/PM/handshake and two additional default-off
startup options (`a9_output_size`, `a9_stock_work_mode`, commit `57544b1`).

The [boot132 first-filled-input audit](mainline-venus-first-frame.md) narrows
the later reset to ETB/FTB before any completion, compares stock DMA mappings and
NV12 geometry, and adds an opt-in bidirectional-buffer test plus watchdog recovery.

## Inputs and evidence boundary

The actual saved stock boot kernel is 4.19.157-perf, with builtin `msm_vidc`.
The available stock vendor-module inventory does not supply a different video
module. The compared mainline source is Linux 7.2.10 plus
`sm6115-stock-firmware.patch`, `sm6115-encoder-diag.patch`, and the new
`sm6115-mapped-eos.patch` under `scripts/mainline/video/`. The first two patches
replay exactly onto the source of the Venus modules packaged in integ25. This
comparison concerns the selected Bengal/AR50LT path, rather than every platform
supported by the downstream driver.

| Input | SHA-256 |
| --- | --- |
| Saved stock kernel gzip, 15,047,394 bytes | `b1ac57e388353f045d796c280386819072e3ca80819aa9ed8eb4caed7b3fedc5` |
| Decompressed Image, 34,476,544 bytes | `2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1` |
| Recovered symbol ELF | `258f157ce827c8997be7e358c06b91fdcb0032d423e978c248d7f5fc858291b5` |
| Address-preserving VIDC analysis carrier | `af278b5f14738b3fb38c8352cfc56c9ca24e236ef42c5a9f4c09a43b6b7ed59a` |
| Carrier with retained RELATIVE relocation addends applied | `040492d327d6737b1d959fe39fb0f3c1a03d2c88e8c0050d9c9f1fc4a8eb17c0` |
| Image's joined `venus.mbn`, 1,792,012 bytes | `44c39a0ff5a25433626ae4b21e3267b0713cdf4c5338cdf88763489d72a3da05` |
| Reconstructed `venus.mdt` metadata | `925c3cdb12d494f76fa79bbc4abe6bda49540a24cf72d23eb0017a450f29b219` |

The image firmware matches all 13 entries of
[stock-firmware.lock.json](../scripts/mainline/video/stock-firmware.lock.json),
including metadata. Its embedded version is `VIDEO.VE.6.0-00039-PROD-1`.
Mainline selects HFI4, AR50_LITE, minimum firmware 6.0.39. The existing encoder
EOS length workaround covers this revision (`<=6.0.53`).

The firmware is ELF32, little endian, **EM_XTENSA (94)**, entry `0x0f500000`.
Its principal executable segment has file offset `0x10019c`, virtual address
`0x1000`, physical address `0x0f501000`, and length 686,976 bytes. REA rejected
this architecture as `unsupported_target`; it was not relabeled as ARM or
executed. Firmware-internal HFI parsing, memory dereferences and completion
semantics are therefore **unresolved**. The firmware addresses above describe
ELF layout, not decompiled firmware functions. Stock kernel instructions and
packet stores provide the protocol evidence here.

Stock addresses below are recovered kernel virtual addresses, without a live
KASLR slide. The focused carrier preserves code bytes and original addresses;
it is synthetic and not runnable. A recovery-tool message about applied
relocations was misleading: the first carrier retained zero pointer slots.
The second carrier explicitly applies 1,507 retained `R_AARCH64_RELATIVE`
addends, with a validation record proving that executable bytes are unchanged
and all changes lie in declared relocation slots. Null callback conclusions
below use that second carrier. Ghidra pseudocode is derived evidence; EOS stack
stores, packet lengths and platform callback data were checked against bytes or
assembly. No runtime packet trace or firmware requirement values were captured.

## Selected stock platform and session initialization

The saved DT's `qcom,bengal-vidc` match entry at `0xffffff8009745728` selects
`bengal_data` at `0xffffff800a0cdee8`. Its version field at `+0x68` is **5**, which
selects `core_ops_ar50lt` at `0xffffff800a0d9c58`. This number is the downstream
resource version, not HFI version 5. Its work-mode callback is
`decide_work_mode_ar50` at `0xffffff8008e61c40`; its work-route callback is null.
Generic Iris route setters are not evidence that this Bengal configuration
sends a route property.

`msm_comm_try_state` at `0xffffff8008db0cc8` opens the session through the HFI
callback. For encoder instance type 0, `get_hal_domain` at
`0xffffff8008d9f998` returns HAL domain 2 (table at `0xffffff8009745f18`).
The packet builder translates that to wire domain 1, matching mainline's
`VIDC_SESSION_TYPE_ENC=1`. AVC's wire codec is 2. Session cookies/tags differ
between drivers and are opaque host bookkeeping, not addresses to copy.

All command words below are little-endian u32. Common session header:
size at byte 0, command at 4, session ID at 8.

| Operation | Wire command / bytes | Stock builder or sender VA | Mainline implementation |
| --- | --- | --- | --- |
| Session init | `0x10007`, 20; domain at 12, codec at 16 | `0xffffff8008e5f5c8` | `hfi_cmds.c:pkt_session_init` |
| Set property | `0x11001`, 20 + payload; count 1 at 12, ID at 16, payload at 20 | `0xffffff8008e60788`; wrapper `0xffffff8008e4c640` | `pkt_session_set_property` and version-specific packetizers |
| Set buffers | `0x11002`, 32 + 4N, or 32 + 8N for output pairs | `0xffffff8008e5f8a0` | `pkt_session_set_buffers` |
| Load resources | `0x211001`, 12 | `0xffffff8008e4b648` | `pkt_session_cmd` / `venus_session_load_res` |
| Start | `0x211002`, 12 | `0xffffff8008e4b6e8` | `venus_session_start` |
| Input ETB | `0x211004`, 64 for encoder raw input | `0xffffff8008e60218` | `pkt_session_etb` |
| Output FTB | `0x211005`, 44 | `0xffffff8008e60390` | `pkt_session_ftb` |
| Get requirements | `0x211009`, 20; count 1, property `0x202001` | `0xffffff8008e60510` | `pkt_session_get_property` |
| Stop | `0x211003`, 12 | `0xffffff8008e4b788` | `venus_session_stop` |
| Release buffers | `0x21100b`, 32 + 4N or 32 + 8N | `0xffffff8008e5fc18` | `pkt_session_release_buffers` |
| Release resources | `0x21100c`, 12 | `0xffffff8008e4b698` | `venus_session_release_res` |
| End / abort | `0x10008` / `0x210001`, 12 | `0xffffff8008e49e00` / `0xffffff8008e4a378` | `venus_session_end` / `venus_session_abort` |

The stock set-property builder copies the caller's numeric ID and payload
unchanged. Mainline's version-specific packetizer can suppress a property even
though its caller returns success. Count **wire packets**, not setter calls or
pre-packetizer diagnostic lines.

## Properties, requirements and registration before start

Stock `msm_vidc_start_streaming` at `0xffffff8008ddd750` waits for both queues,
sets encoder properties, checks support and obtains requirements. It then sends
`BUFFER_SIZE_ACTUAL`, allocates/registers scratch and persist, creates recon
tracking records, and transitions through load/start to state 9. Deferred
buffers and pending EOS are submitted only after that transition succeeds.

Mainline `venc_init_session` initializes the session, sends input/output geometry
and raw format, then encoder properties. On the second STREAMON,
`venc_start_streaming` repeats the encoder properties, verifies output/input
requirements, sends actual counts, allocates internal buffers, registers output
where applicable, then synchronously loads and starts. The major differences
below are specific properties and host allocation behavior, not a reversed
load/start order.

| Property or storage | Stock selected path | Current mainline HFI4 |
| --- | --- | --- |
| Geometry `0x1001`, format `0x1003`, profile/level `0x1005` | Payloads 12, 8, 8 bytes respectively; encoder property sequence precedes requirements | Same IDs/layouts; `venc_init_session`, `venc_set_properties` |
| Rate `0x2001`, mode `0x1015` | FPS Q16 with buffer type 2; work mode depends on rate control/low latency | FPS same shape; AVC/HEVC mode always 2 |
| Actual count `0x201001` | 12 bytes: type, actual count, minimum host count | Same HFI4 layout; sends input 1 then output 2, host minimum = actual |
| Requirements `0x202001` | Firmware reply, fixed 32-byte records | Firmware reply on this platform, same 32-byte shape |
| Actual output size `0x20100c` | **Sent** before internal allocation: type 2, configured capture `sizeimage`, 8 bytes | **Absent** from encoder start; existing `venus_helper_set_bufsize` is not called |
| Scratch / persist | HFI types 6/7/8 and 4/5; separately register every nonzero allocation | Same types/order and count-1 registrations |
| Extradata index `0x201006` | 8-byte enable/index; additional controls `0x2205003`, `0x2205008`, and HEVC `0x220500a` can explicitly disable metadata | No equivalent encoder setup; no separate encoder extra-plane allocation |
| NAL format `0x100b`, plane constraints `0x201002` | Stock helpers support these, payloads 4 and 40 bytes; actual sends depend on format/control path | Absent from current encoder sequence |
| Operating rate `0x2003` | Conditional on platform CVP configuration | Absent from current encoder sequence; not established as required here |
| Low latency `0x2005022` | Sent as 1 after selected work mode 1 | Absent |
| Work route `0x1017` | **No selected AR50LT callback** | Absent; adding the generic stock route setter would not match selected stock |
| Max bitrate `0x2006007`, session QP `0x2005006`, allocation mode `0x201008` | Stock has corresponding control/property machinery; active requests require control evidence | Mainline HFI4 returns `-ENOTSUPP`, normalized to success with **no packet**, for these IDs |

Stock's encoder property helper call order is frame size, frame rate, secure,
priority, color, sequence header, profile/level, transform, entropy, rate control,
VBV, bitrate savings, timestamp rate control, frame QP, QP range, image controls,
AUD, VUI, HDR, VPX, NAL, slice, filter, refresh, LTR, hierarchy, base priority,
IDR/intra period, aspect/signals, layer/target bitrate, CSC/chroma/blur, extradata,
CVP/operating rate, counts, rotation and lossless. Many helpers return without
sending anything for the selected codec/controls; this is static call order,
not a claim that all those properties reach firmware in an ordinary AVC run.

Mainline's actual AVC sequence in `venc_set_properties` is mode, rate, VUI,
entropy, deblock, transform, optional hierarchy, IDR period, optional B-frame
maximum, intra period, rate control, optional CQ quality, target bitrate,
sequence-header mode, QP range, LTR, profile/level, AUD and conditional refresh.
Its `MAX_BITRATE` and `SESSION_QP` calls do not transmit on HFI4. The detailed
numeric map and payload conditions are retained in the source report cited
below. No missing property in this table is yet proved to cause the hang.

Selected stock mode details: `msm_venc_set_rate_control` at
`0xffffff8008e03d48` maps internal RC 0 to VBR_CFR `0x1000003`, 1 to CBR_CFR
`0x1000005`, 3 to CBR_VFR `0x1000004`, 5 to CQ `0x1000008`, and 6/7 to OFF
`0x1000001`; 2/4 use vendor value `0x1000006`. CBR sets the low-latency byte.
`decide_work_mode_ar50` selects mode 1 if low latency is set; otherwise mode 2
for RC {0,2,4,5} and mode 1 for the others. Thus ordinary VBR_CFR agrees with
mainline mode 2; CBR, OFF or explicit low latency can differ. Do not treat this
as a universal mode mismatch without the qualifier's active control values.

Stock's local internal-size calculator is installed only for resource versions
3/4 (`msm_vidc_init_buffer_size_calculators`, `0xffffff8008e71910`). Selected
Bengal is version 5, so `msm_comm_try_get_bufreqs` at `0xffffff8008dbe420`
queries firmware. Mainline's HFI4 platform also has no host bufreq calculator.
**Neither selected path uses the HFI6 host scratch-size formula.**

Reply parser `0xffffff8008e5bd30` copies 32-byte records with type translation.
Record words: type, size, region size, minimum, host minimum, actual count,
contiguous, alignment. Stock scratch setter `0xffffff8008dc4040` uses HAL
0x40/0x80/0x100, translated by `0xffffff8008e5f990` to HFI 6/7/8; persist setter
`0xffffff8008dc58f8` uses HAL 0x200/0x400, translated to HFI 4/5.
`set_internal_buffers` at `0xffffff8008dc43a0` uses record size and actual count,
skips absent/zero-size entries and calls `set_internal_buf_on_fw` at
`0xffffff8008dd4a50`. Each registration has count 1, minimum size = buffer
size, no extradata for internal types. Mainline `helpers.c:intbufs_alloc` and
`intbufs_set_buffer` match this shape. Absolute sizes/counts need runtime replies.

Stock `msm_comm_set_recon_buffers` at `0xffffff8008dc5370` allocates 0x20-byte
tracking objects per output count, not pixel DMA storage or separate HFI recon
registrations in that function. Copying an invented recon allocation into
mainline is not justified. Stock extradata attaches address/size only when a
second plane is configured (`populate_frame_data`, `0xffffff8008dd4788`), also
setting flag 0x40. Mainline uses zero extra address/size. Extradata differences
need enabled-control and requirement evidence before changing plane allocation.

For ordinary encoder user buffers, stock `msm_comm_get_vidc_buffer` at
`0xffffff8008dcf3b0` imports/maps planes through `msm_vidc_smem_ops` at
`0xffffff800a0d4c08` (`msm_smem_map_dma_buf`, `0xffffff8008e1fc38`). That mapper
does DMA-buf/device-address work, with no HFI registration. `msm_vidc_reqbufs`
at `0xffffff8008dd69e8` calls vb2 allocation; `msm_vidc_release_buffer` at
`0xffffff8008dd6d78` unmaps/drops references rather than sending user-buffer
RELEASE_BUFFERS. The inspected ordinary queue path supplies addresses in
ETB/FTB, without separate user-buffer SET_BUFFERS/REGISTER. The encoder's
stream-output-mode helper returns 1, excluding the DPB-only mode-2 registration
branch. Mainline HFI4 likewise skips separate capture registration because its
output is dynamic; input addresses arrive with ETB. This is a match for the
inspected ordinary path, not a claim about decoder DPB or CVP registration.

## First ETB/FTB and EOS

Stock `msm_comm_qbuf` at `0xffffff8008dbb288` defers user buffers until state 9.
`msm_comm_qbuf_to_hfi` at `0xffffff8008dbcd18` routes V4L2 capture type 9 to
FTB and input/output type 10 to ETB. The pending list follows queue order; it
does not prove a universal first-FTB ordering. Mainline's initial mem2mem batch
walks queued capture buffers first, then input buffers
(`helpers.c:venus_helper_m2m_device_run`).
Later QBUF submissions can interleave. Both submit after load/start.

Encoder ETB has 64 bytes in both drivers:

| Wire byte offset | Field | Ordinary input | Stock drain EOS | Existing mainline drain EOS |
| --- | --- | --- | --- | --- |
| `0x0c` | view | 0 | 0 | 0 |
| `0x10` / `0x14` | timestamp high / low | input timestamp in us | 0 / 0 | 0 / 0 |
| `0x18` | flags | input flags | 1 (EOS) | 1 (EOS) |
| `0x24` | allocation length | allocated plane size | **4096** on normal path | **1** on this FW |
| `0x28` | filled length | bytes used | 0 | 0 |
| `0x2c` | offset | data offset | 0 | 0 |
| `0x30` | client tag | host bookkeeping | 0 | 0 |
| `0x34` | packet buffer | real mapped input | **real mapped EOS input** | **`0xdeadb000`** |
| `0x38` | extradata | conditional extra plane / zero | 0 | 0 |

Stock's builder does not itself write mark-target, mark-data and final data
words at `0x1c`, `0x20`, `0x3c`; this does not establish their caller values or
uninitialized transmission. Mainline copies the mark fields from the frame
descriptor (zero for this EOS); its builder also has no explicit final-data
store. The candidate's compiler enables automatic variable initialization to
zero, but that is not evidence about stock's caller. FTB is 44 bytes:
stream ID at 12, offset 16, allocation 20, filled 24, tag 28, packet address 32,
extra address 36, extra size/data 40. Stock's builder does not itself assign the
output tag. No packet-size/layout mismatch was found for these commands.

Stock encoder STOP is an EOS injection, **not** HFI session STOP.
`msm_v4l2_encoder_cmd` at `0xffffff8008d95288` calls `msm_vidc_comm_cmd` at
`0xffffff8008db7f30`. In command 1/state 9, it allocates an EOS record and calls
`msm_comm_smem_alloc` with size `0x1000`. `msm_smem_alloc` at
`0xffffff8008e21d28` obtains DMA storage and a mapped device address. Normal
nonsecure allocation stays 4 KiB; secure alignment can enlarge it.
`msm_vidc_send_pending_eos_buffers` at `0xffffff8008db7b58` builds the EOS fields
above from that allocation, submits ETB and marks the record sent. The assembly
stores at `0xffffff8008db7c14..0xffffff8008db7c30` and allocation arguments at
`0xffffff8008db83a0..0xffffff8008db83c4` establish size/address provenance.

On STREAMOFF, stock `msm_vidc_stop_streaming` at `0xffffff8008de2ad0` requests
state 0xd: STOP, wait, RELEASE_RESOURCES, wait. Close at
`0xffffff8008ddb098` releases scratch/persist/user/EOS storage, then END and
session cleanup; `msm_comm_release_eos_buffers` at `0xffffff8008dc1540` frees
EOS mappings. Mainline similarly STOPs/unloads, releases internal registrations,
ends the session and aborts on error through `venus_helper_vb2_stop_streaming`.
These are broader lifecycle matches, not proof of firmware quiescence after a
failed STOP/abort.

## Landed experiment and next qualification

[sm6115-mapped-eos.patch](../scripts/mainline/video/sm6115-mapped-eos.patch)
adds module parameter **`venus_enc.a9_mapped_eos`**, default false. Its sysfs
path is `/sys/module/venus_enc/parameters/a9_mapped_eos` (bool readback `N`/`Y`).
It is writable by the existing privileged owner; the patch adds no SELinux
permission. The value is sampled at each encoder open. Change it before a
**new** encoder instance, including any Codec2 pre-open, rather than during a
live instance. Other platforms/revisions retain the baseline even when true:
the new path requires `qcom,sm6115-venus`, HFI4, AR50_LITE, firmware exactly 6.0.39.

When enabled, STOP lazily allocates coherent 4 KiB through the same Venus DMA
device, checks for a nonzero 32-bit device address, zeroes once and sends that
address with allocation 4096. Allocation/address errors return before submission
or entering DRAIN. The mapping survives input callbacks, repeated drains and
STREAMOFF/reinitialization; it is freed only after `venus_close_common` tears
down the session. Per-instance storage is private to `venc.c`, with
`venus_inst` first, so exported structs and symbol CRCs remain unchanged.

The experiment uses **client tag `4294967295`**, rather than stock tag 0.
Mainline treats input tags as vb2 indices: a synthetic EBD tagged 0 could remove
a real queued input 0. The reserved tag goes through a list-equality search and
matches no vb2 buffer; there is no tag-based array access in this callback path.
This is a host ownership adaptation, not a firmware address or a new buffer
registration. The existing baseline tag and packet path are unchanged when off.

The current build and trace-stage assembler both consume/bind the new patch.
Use a fresh stage, preserving the userspace owner's approved Codec2 inputs, with
explicit `--trace-build`:

```
/home/user/A9-build/mainline/venus-hfi-host-20261024/mapped-eos
```

That directory contains compatible compiled modules and
`kernel-trace-checks.json`; `stage-encoder-trace.py` independently replays all
three patches before accepting them. An old trace build cannot be relabeled
by refreshing receipt hashes. No frozen image or userspace stage was modified.

For the coordinator's next admitted qualification, keep
**`A9_SWEEP_HVENC_QUALIFY=1`** and the same userspace stage/input for both cases.
That flag selects the existing ByteBuffer/surface qualification; it does not
enable this driver option. Record parameter readback and create a fresh encoder
instance with option 0 for baseline, then option 1 for mapped EOS in a separately
recorded case. The coordinator retains phone ownership and run policy.

Expected evidence with option 1: ordinary ETB/FTB/start behavior stays the same;
on encoder STOP, existing `A9_VENUS_ENC op=buffer.begin` should show input type 1,
`alloc=4096`, `filled=0`, `offset=0`, EOS flag 1, tag `4294967295`, followed by
EOS/LAST and a completed drain/teardown. With option 0, EOS remains alloc 1/tag 0.
If failure precedes EOS, this experiment cannot explain or repair that boundary.
Successful initialization or one queued input alone does not qualify HVENC.

The separate output-size notification and selected CBR/OFF mode/latency options
are now implemented, default off, in `57544b1`. See the
[ranked audit and one-boot variant plan](mainline-venus-hfi-causality.md). No
WORK_ROUTE, HFI6 scratch formula, speculative allocation or clock change is
bundled in. QP payload accounting was checked against the complete structures:
stock and mainline both send 64-byte payloads / 84-byte property packets; an
earlier private report's 24-byte claim omitted reserved words and is corrected.

## Host validation and retained evidence

All three Venus modules compiled with ARM64 GCC 14.3.0 against private compatible
headers/configuration. Export symbol CRCs are identical to the previous modules;
all imports match integ25 exports, including Venus internal exports. Vermagic is
`7.2.10-a9+ SMP preempt mod_unload modversions aarch64`. Clean-source replay of
all three patches with fuzz 0 equals the compiled source. Python consumers parse.
The production EOS helper/command/close were exercised with mocked DMA/HFI in
four host groups: scope/default behavior; mapped fields, repeated drains and
teardown order; allocation/address errors; queue guards and unallocated close.
The source is [tests/mapped-eos.py](../scripts/mainline/video/tests/mapped-eos.py).
An independent read-only buffer-path check found no concrete lifetime/tag issue.
None of these tests executes the firmware or proves hardware encode.

Compiled candidate hashes:

| Module | SHA-256 |
| --- | --- |
| `venus-core.ko` | `960c163d9d5a85a936f89f0729e56475320196bfc7fe04ae9def79d700eeed28` |
| `venus-dec.ko` | `c5773034c88bc687a93aba0b0cfc05a961ded02941ff2b71d1fc0c5f721c8fc1` |
| `venus-enc.ko` | `4d5ce69a928ca4b4b4bb1001ee517df0b9bce0f2a680d65187d801aed907acd7` |

Retained host evidence root:
`/home/user/A9-build/mainline/venus-hfi-host-20261024/`.

| Evidence | Content / selected REA evidence IDs |
| --- | --- |
| `stock-packets-report.md`, `stock-packets-rea/evidence-original-carrier.json` | Packet builders `ev_ec92085b535c55834efe7380249de2027193c668b7e3f8c150e6f487d50eb684`; command wrappers/order `ev_ca7a6fcd7562fb100edca630691e35306e333a0fb343f5827be6901197a43014`; property/requirements `ev_d3054df4f461f98ab4acae955467c921ed3e44f39c42b142024255c79650d2bf` |
| `stock-packets-rea/evidence-final.json`, `stock/validation.json` | Relocation-grounded platform callbacks `ev_f36bb963906fabafd8057cf3c5c4b6f051b67c71ee8c54f27f207b3ec8d6d3ef`; selected RC/mode `ev_1738274d170cf0c6938a2cdb8bbf7ba27101130ea1797bbb1855ba7040c02ade` |
| `stock-domain-followup.md` | ENC 0 → HAL 2 → wire 1, exact disassembly/table bytes |
| `stock-buffers-report.md`, `stock-buffers-rea/evidence.json` | EOS command `ev_1e208491a419a559db6321dc704ba8ff642ca7134b200ab1b4390e1fd424aebb`; EOS assembly `ev_f00bee48215ead7b84f7489a7b210794b86b683878940722ae479d6b539a501a`; allocator/lifecycle `ev_378626602f827f4838033227a3c76049279104f1ad53d9e62ac3e577af5af906` |
| `stock-buffers-rea/user-buffer-evidence.json` | Six focused records: ordinary encoder DMA import, smem ops table, vb2 request/release and exclusion of the DPB-only mode-2 branch |
| `mainline/mainline-report.md`, `source-image-binding.json`, `numeric-property-map.json` in that directory | Exact source/image modules, firmware inventory and full mainline property/payload map |
| `firmware-rea-open.json` | Unsupported Xtensa route, no successful firmware decompilation |
| `mapped-eos/kernel-trace-checks.json`, `module-build.log`, `eos-host-test.log` | Source replay, module/import/export hashes and host results |
| `stock-buffers-eos-review.md` | Independent static check of default-off gating, allocation lifetime and reserved tag |

The answered questions are packet layouts, selected platform, static setup,
requirements, internal registration, start/queue/EOS/stop sequence and candidate
host behavior. Remaining questions are firmware-internal semantics, actual
runtime control/requirement values, exact failure boundary and successful
hardware qualification. No kernel lockup or USB root cause is proved by this
static comparison.
