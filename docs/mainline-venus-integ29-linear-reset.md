# integ29: linear NV12 also resets at the first encoder input

2026-10-25. Host-only analysis; the user ran the phone test. **Removing EGL,
Codec2, gralloc and the converter did not remove the first-input reset.**
Both tested input allocation paths reach the same HFI boundary. This weakens
those userspace paths as necessary causes; it does not exclude a shared Venus
mapping, layout, internal-buffer, power or firmware problem. No retained fault
identifies the reset mechanism.

Stock disassembly establishes a concrete raw-layout difference: for a
320x240 picture, stock linear NV12 uses393216 bytes with UV offset262144;
mainline originally used147456 bytes with UV offset98304. **The subsequent
393216-byte padding comparison also reset.** Raw capacity alone is therefore
less compelling. The next experiment is the bounded packet/stage capture below,
rather than another allocator or padding substitution.

## What the new capture establishes

Evidence root is `/home/user/A9-build/mainline/`:

- `manual-hvenc-linear-integ29/{attempt.log,kernel-live.log,parameters.txt}`.
- `hvenc-linear-reset-integ29-pull/mainline.tar`.
- Earlier Surface comparison: [integ28 reset](mainline-venus-integ28-reset.md)
  and `hvenc-reset-integ28-pull`.

The native probe requested visible320x240 and negotiated linear `NV12`
(`0x3231564e`), stride384, Y scanlines256, sizeimage147456. It allocated **four**
147456-byte MMAP input buffers and four2097152-byte MMAP capture buffers.
Mapped EOS and bidirectional mapping were Y; output-size and stock-work-mode
experiments were N. All eight tuning controls succeeded, including GOP size1.

The retained kernel sequence is:

| Boundary | Observed result |
| --- | --- |
| Firmware requirements | Input147456/min3; output230400/min2 |
| Internal registration | Types6/7/8/4, each count1; sizes below |
| LOAD_RES and START | Responses rc0; START response at329.757935s |
| FTB | Four type2 buffers, allocation2097152, tags0..3, each queued rc0 |
| First ETB | Type1, allocation=filled147456, offset0, flags0, tag0 |
| Last live marker | `hfi_session_process_buf phase=buffer.end rc=0` at329.873539s |
| Completion | No retained EBD/FBD or native result; user observed whole-SoC reset |

The native log ends at `input.qbuf phase=0`; its end marker never arrived.
The kernel's rc0 means the HFI command was queued, rather than completed by
firmware. In the matching driver, input load/clock scaling precedes that HFI
submission; the final marker is beyond the call to `venus_pm_load_scale()`.

Archive boot139 matches the live timestamps. Its saved kernel log ends at
329.849576s, just before the live ETB pair: the live stream retained more than
the persistent logger. **The crashed boot's deadline was disarmed at104.884205s**;
this is not merely a disarm in its successor. Successor boot140 has an empty
`pstore.previous.log` (0bytes); the other two archived previous-pstore copies
are empty too. The retained kernel files contain no panic, SMMU context-fault,
XPU/NoC error, Venus recovery or deadline-expiry signature. This bounded negative
result cannot exclude a fault whose reporting or RAM retention failed.

## Stock Bengal/AR50LT versus the submitted workload

Stock addresses below are unslid VAs in the saved 4.19 Image, SHA256
`2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`.
The address-preserving REA RELA carrier SHA256 is
`040492d327d6737b1d959fe39fb0f3c1a03d2c88e8c0050d9c9f1fc4a8eb17c0`.
Static findings describe code paths, not a captured stock encode. Synthetic
function extents, missing carrier regions and decompiler warnings apply.

| Area | Stock behavior and address | Meaning for integ29 |
| --- | --- | --- |
| Startup order | `msm_vidc_start_streaming@0xffffff8008ddd750`: properties/requirements, internal registrations, LOAD_RES, START, pending buffers; state transition `0xffffff8008db0cc8` waits for START completion. | Broad ordering matches. Deferred buffers follow list/userspace order; no universal stock ETB-before-FTB rule was established. |
| Nonsecure CB | `msm_smem_get_context_bank@0xffffff8008e23a10` matches secure state and buffer type. Nonsecure input/output/internal buffers select `venus_ns`; raw pixels use `secure_pixel` only for secure encoding. | Stock NS SIDs0x860/0x880 match mainline. Moving this nonsecure test into the secure pixel bank has no supporting evidence. |
| IOVA | Stock DT NS pool `[0x70800000,0xe0000000)`; importer `0xffffff8008e204b8` uses the selected bank's mapped address plus offset. | Mainline upper DMA limit0xdfffffff and protection boundary0x70800000 match. Its generic allocator has no explicit stock lower-pool reservation. Actual failed-run input/output/internal IOVAs and hardware CB/TTBR are unlogged, so valid placement is unresolved. |
| Mapping/cache | The same stock importer maps bidirectionally. Cached-input qbuf calls `msm_smem_cache_operations@0xffffff8008e23478` through `0xffffff8008dcee78`, with partial CPU-access synchronization. | Bidirectional Y is confirmed in this run; changing that flag already failed to discriminate. MMAP uses vb2's allocation/cache path, avoiding the gralloc DMA-BUF exporter/converter contract. Shared mapping defects remain possible. |
| Pixel format | Stock raw-format setter `0xffffff8008dfee78` sends input color property0x1003 and, for NV12, constraints0x201002. Its initial Q128/UBWC default is a different format. | The probe requests NV12; the matching mainline encoder maps it to HFI color2, distinct from UBWC0x8002. No evidence shows an RGBA or UBWC buffer mislabeled as NV12. |
| Stride/scanlines | Stock constraints builder `0xffffff8008dd1700` emits40bytes with NV12 stride multiple512, Y height multiple512 and UV height multiple256. Its raw-input sizing helper uses those alignments too. Mainline instead sends actual-plane property0x1002 with stride384 and heights256/128. | **A newly resolved concrete layout difference.** Both describe two color planes in one V4L2 memory plane. The trace records IDs, not payloads; mainline values follow the matching source and negotiation. |
| Extradata/metadata | `populate_frame_data@0xffffff8008dd4788` attaches an extra address/size and flag0x40 only when an additional V4L2 plane is configured. `msm_venc_set_extradata@0xffffff8008e18d80` has explicit enable/disable properties. | A stock optional second memory plane is extradata, not the NV12 UV plane. No retained requirement proves a mandatory separate metadata buffer for this linear AVC input. UBWC metadata would belong to a different pixel layout. |
| User-buffer registration | Stock import `msm_comm_get_vidc_buffer@0xffffff8008dcf3b0` and smem mapper `0xffffff8008e1fc38` supply addresses through ordinary ETB/FTB, without a separate ordinary encoder REGISTER_BUFFER step in the inspected path. | No missing mandatory user-buffer registration was found; HFI4 dynamic capture uses the same broad approach. |
| Counts/bitstream size | Stock count property0x201001; stock start also sends actual output size0x20100c before internal allocation. | Four inputs exceed firmware min3; four capture allocations2097152 exceed requirement230400. Output-size N omits the stock size notification here, but [boot132](mainline-venus-first-frame.md) already reset with output-size and stock-work-mode enabled together. Neither is a demonstrated remedy. |
| Internal requirements | `msm_comm_try_get_bufreqs@0xffffff8008dbe420`; local calculator initialization `0xffffff8008e71910` selects only resource versions3/4, whereas Bengal is version5. | Both selected paths query firmware requirements. An HFI6 host-size formula or invented extra recon allocation is not justified. |
| Internal lengths | `msm_smem_alloc@0xffffff8008e21d28` page-rounds; `set_internal_buf_on_fw@0xffffff8008dd4a50` advertises that stored rounded size, count1. | Mainline advertises exact requirement sizes, while the DMA backing is page-rounded. This is a concrete remaining length-convention difference. |
| Recon buffers | `msm_comm_set_recon_buffers@0xffffff8008dc5370` allocates0x20-byte tracking nodes per output count; no pixel DMA allocation/SET_BUFFERS occurs in this function. | Its existence does not prove mainline lacks a separate required reconstruction pixel buffer. |
| First-work power votes | `msm_comm_scale_clocks@0xffffff8008e689d8` and bus voter `0xffffff8008e673f8` request startup maxima when the startup counter is below16 and mapped/filled input is present. | Stock can request384MHz and configured DDR maximum2128000kB/s; mainline uses load-based votes. This remains a candidate, not measured underclocking or proof of the reset source. |

## Input size and the out-of-bounds hypothesis

For the **mainline negotiated linear layout**, the arithmetic is:

```text
visible bytes = 320 * 240 * 3/2          = 115200
Y plane       = 384 * 256              =  98304
UV plane      = 384 * 128              =  49152
total         = 384 * 256 * 3/2        = 147456 = 0x24000 = 36 pages
UV offset     = 98304
```

Thus147456 is not merely256x384: that product is the Y plane alone. It includes
the UV plane and agrees exactly with the firmware's reported input requirement.
The probe fills that padded extent; its crop keeps the coded picture320x240.
The bitstream allocation is separately2097152 bytes. These facts give no
evidence of a shortage relative to the **mainline negotiated contract**.

However, new stock REA resolves a gap left in the earlier reports:

- `msm_comm_convert_color_fmt@0xffffff8008db0078`: assembly maps fourcc NV12
  to internal format0 (NV12 branch`0xffffff8008db00f0..00fc`, return0 at
  `0xffffff8008db0268`). `msm_comm_get_hfi_uncompressed@0xffffff8008d9ff40`
  separately maps it to HFI wire color2. Internal0 does not mean wire0.
- `msm_vidc_calculate_enc_input_frame_size@0xffffff8008e73840` selects its
  format0 branch using that conversion. It computes stride`ALIGN(W,512)`,
  Y height`ALIGN(H,512)`, UV height`ALIGN(ceil(H/2),256)`, then page-rounds
  the sum of the Y/UV extents. `msm_venc_s_fmt@0xffffff8008ded7e8` calls
  this raw-input calculator. The inspected path has no secure-session or
  platform-version condition that would exclude ordinary Bengal encoding.
- For320x240 that is Y`512*512=262144`, UV`512*256=131072`, total**393216**,
  UV offset**262144**. No UBWC metadata or appended tail is added in this
  selected linear branch. This raw-input calculator is distinct from the
  optional internal-buffer calculator restricted to platform versions3/4.
- The stock NV12 constraint table at`0xffffff800a0d5d38`, through builder
  `0xffffff8008dd1700`, gives property0x201002 words
  `[1,2,512,16384,512,512,512,16384,256,256]`:
  input1/two planes, then each plane's stride multiple, maximum stride,
  height multiple and buffer alignment. Mainline HFI4 sends actual dimensions
  through0x1002 and does not send these stock constraints.

Therefore **147456 matches mainline and its firmware reply, but does not match
stock's default linear NV12 allocation/layout convention**. The new result
does not establish that firmware requires the stock convention after mainline
explicitly supplies a different actual stride/height. If firmware nevertheless
used the stock UV offset262144, its first chroma access would already be beyond
the147456-byte allocation. That is a concrete, testable way this layout mismatch
could cause an out-of-bounds access, rather than an inference from visible
resolution alone.

Neither comparison proves firmware's actual address calculations. A wrong color/plane
property, firmware requirement bug, invalid IOVA, or access beyond an internal
buffer could still fault despite an apparently consistent input size. An
out-of-bounds DMA access causing SMMU/XPU/NoC failure and then a whole-system
reset is a **possible mechanism**, not an observed chain. The retained evidence
has no fault IOVA, SID, WNR/FSYNR or secure error that selects it.

## Internal buffers in this run

Type4 is **persist**, not the only encoder internal allocation. The new trace
has all four nonzero registrations below, each count1. Type5 has no recorded
nonzero registration; do not invent a size for it. These are integ29 values,
rather than the different scratch requirements seen in boot132.

| HFI type | Purpose | Requested and mainline-advertised bytes | Stock-style 4096-byte rounding | Difference |
| --- | --- | ---: | ---: | ---: |
| 6 | Scratch | 1175296 | 1175552 | 256 |
| 7 | Scratch1 | 366640 | 368640 | 2000 |
| 8 | Scratch2 | 147712 | 151552 | 3840 |
| 4 | Persist | 16392 | 20480 | 4088 |

Stock setters `0xffffff8008dc4040` / `0xffffff8008dc58f8` select scratch6/7/8
and persist4/5 through their HAL-to-HFI mapping. `set_internal_buffers`
at`0xffffff8008dc43a0` uses each firmware record's size/count. Absolute stock
sizes for a matching live session were not captured: the last two columns
apply the proven allocator convention to **this run's** requirements.

Mainline `helpers.c:intbufs_set_buffer()` assigns `buf->size=bufreq.size`, uses
that for DMA allocation, and copies it to `bd.buffer_size`; the packet builder
sets both advertised size and minimum size from it. Release/free reuse the
same stored size. Page-rounded backing does not establish that advertising a
non-page length is incorrect, but the stock convention differs.

## Completed padding experiment: same reset, no ETB packet retained

The user kept the existing integ29 kernel, parameters, counts, controls and
visible320x240 picture, changing only raw S_FMT from320x240 to512x512.
The separate capture-format request and crop remain320x240. In the matching
driver the crop restores `inst->width/height` to320x240 while raw
`out_width/out_height` remain512x512. Thus this tests input padding rather than
encoding a different picture size. No kernel/firmware change or secure-bank
switch is involved.

The existing dynamic filler then uses the negotiated stride512/Y height512,
UV offset262144 and total393216. It fills neutral padding, writes the same
visible320x240 pattern, and submits one frame. Expected raw MMAP allocation
and ETB allocation/filled length are393216; record the actual returned firmware
requirement rather than assuming it must change. Capture allocations remain
2097152. All four parameters stay mapped_eos=Y, bidir=Y, output_size=N,
stock_work_mode=N; the wrapper preserves its original cleanup and log capture.

The consumed host-built candidate is at
`/home/user/A9-build/mainline/venus29-linear-analysis-20261025/padded-input`.
Its `source.diff` changes only the raw request's two dimensions on one line,
starting from source whose SHA matches the frozen integ29 probe manifest.
The static AArch64 NDK build passed warnings-as-errors. The production syscall
model, with modeled negotiated geometry/length assertions adapted to512 padding,
passes layout, queue/output, rejected controls, heap sync/denial and bounds
cases. This is host verification, not an observation of actual firmware behavior.

Retained evidence is `manual-hvenc-linear-integ29-padding512/` and
`hvenc-pad512-reset-integ29-pull/mainline.tar` under the build root. The probe
confirms NV12 stride512, scanlines512, UV offset262144, filled=sizeimage393216,
four393216-byte input allocations, the320x240 crop, successful controls and both
STREAMONs. Firmware still reports input147456/count3 and the same scratch/persist
sizes as the preceding linear run. START responds0 at174.237938s; four FTBs end
successfully, the last at174.333348s. Native `input.qbuf phase=0` is present,
but **no ETB begin, exact packet, queue result, EBD/FBD or native result is
retained in this padded capture**. The user observed the same whole-SoC reset.
Do not borrow the earlier run's ETB-queued marker to fill this gap.

Archive boot145 matches the setup timestamps, but its persistent kernel log
ends earlier at173.330851s. Its deadline was disarmed at161.074089s. Successor
boot146's previous-pstore copy is empty; all three archived copies are0bytes.
No retained panic, fault, Venus-recovery or deadline-expiry signature identifies
the cause. Again the live stream contains more than the persistent logger.

An unchanged buffer requirement does **not** establish that the padded plane
properties failed to change. The matching source sends frame-size0x1001 for
the cropped320x240 picture separately from actual-plane0x1002. In the padded
probe, the latter derives `[input1,planes2,512,512,512,256]`. The HFI4 packetizer
forwards0x1002 through its3xx/1xx implementation; it is not one of the suppressed
properties. The expected packet is44bytes, with that24-byte payload. Those are
source-derived expectations, not captured runtime bytes. Firmware may report a
minimum using coded dimensions; the retained requirement alone cannot tell us
which plane information it applied. Dump the actual packets before concluding.

The393216-byte allocation covers both known linear layouts. Merely adding more
raw capacity did not yield encoding; a simple147456-byte-end overrun is weakened.
Shared packet construction, DMA addresses, internal buffers and the PM/clock
path remain common. The padded trace does not prove where its reset occurred
within that path.

## Common first-input path and exact encoder packet

The shared source path is `venc_vb2_buf_queue()` (including
`venc_pm_get_put()`), `venus_helper_vb2_buf_queue()`, `session_process_buf()`
(input load scaling), `hfi_session_process_buf()`, `venus_session_etb()`,
`pkt_session_etb_encoder()`, then the shared command queue and optional doorbell.
Surface and both CPU-filled probes converge here despite different allocation
paths. Earlier ETB-queued evidence places those earlier runs beyond load scaling;
the padded run's missing ETB marker leaves that placement unresolved.

Stock encoder builder `create_pkt_cmd_session_etb_encoder@0xffffff8008e60218`
uses the same64-byte uncompressed-plane0 shape as mainline. The compressed
decoder packet is60bytes, rather than an encoder packet variant. Mainline's
encoder/decoder branch is selected by session type; the ETB builder has no
Iris2-versus-AR50LT switch. HFI4/6 property packetization and hardware doorbell
selection are separate decisions. Never decode a raw encoder ETB as a compressed
decoder or uncompressed-plane1/2 extension.

| Byte offset | Encoder ETB field | Mainline derivation for these CPU probes |
| ---: | --- | --- |
| 0x00 | Packet bytes | 64 (`0x40`) |
| 0x04 | Command | `0x211004`, SESSION_EMPTY_BUFFER |
| 0x08 | Session ID | Opaque hash of this instance; reuse its own INIT session ID |
| 0x0c | view_id | 0 |
| 0x10 | timestamp high32 | Input timestamp in microseconds, high word |
| 0x14 | timestamp low32 | Input timestamp in microseconds, low word |
| 0x18 | flags | 0 for the first nonempty input here; EOS is not reached |
| 0x1c | mark_target | 0 from the initialized frame descriptor |
| 0x20 | mark_data | 0 from the initialized frame descriptor |
| 0x24 | alloc_len | Queried input allocation:147456 or393216 |
| 0x28 | filled_len | Probe payload:147456 or393216 |
| 0x2c | offset | V4L2 data_offset,0 here |
| 0x30 | input_tag | Input buffer index,0 for the first frame |
| 0x34 | packet_buffer | Mapped input IOVA; actual value was not logged |
| 0x38 | extradata_buffer | 0 with this single memory-plane input |
| 0x3c | data | Builder omits this store; matching compiled caller initializes it to0 |

The host definition has **no separately named UV address/offset or
extradata-size member in this64-byte encoder packet**. Its single offset is the base payload offset, not the UV
offset. UV placement comes from the format/plane properties and base IOVA.
The optional extradata pointer is not a second NV12 color-plane pointer. The
44-byte FTB has its own final `data` word, populated from extradata_size; do not
apply that FTB meaning to the ETB without firmware evidence.

New stock caller assembly at`__session_etb@0xffffff8008e51cc8` selects the
encoder builder through packet-ops slot0x88 at`0xffffff8008e51eac..1eb4`.
The selected table is`0xffffff800a0d9b78`, whose slot0x88 is the64-byte builder
`0xffffff8008e60218`; slot0x80 is compressed-decoder builder
`0xffffff8008e600a8`. This is the actual stock selected route, rather than a
similarly named Iris2 function. The stock caller does not initialize its packet
before this call, and the builder omits mark_target/mark_data/data stores.
Their actual stock wire values are unknown on this path; do not assume zero
or deliberately reproduce unspecified stack values. This establishes no
missing required metadata initialization in mainline, whose packet is zeroed.

The CPU probe does not set a timestamp, so its expected ETB timestamp is0; a
Surface producer supplies presentation timestamps. A first input tag0 is valid
host bookkeeping. `filled_len==alloc_len` here describes a full padded image;
it is not the decoder compressed-payload convention and does not itself imply
an invalid packet. Stock copies the same length/offset/tag/timestamp fields.

The matching `hfi_venus.o` explicitly calls `memset(packet,0,64)` before the
encoder builder. Both config and compiler command enable automatic stack
initialization. Therefore the source's omitted final-word assignment is **not
proof of transmitted stack garbage in integ29**. A host test of the actual
matching packet builders/structs passes all offsets, timestamp halves, tag,
extradata, differing allocation/filled lengths, encoder64/decoder60/FTB44 and
null-address rejection. It is not a dump of the failed runtime packet.

Other common candidates retain the following evidence and limits:

| Shared mechanism | Stock difference or unresolved observation |
| --- | --- |
| Startup/session identity | Same stock firmware6.0.39, HFI4, AR50_LITE and AVC wire codec2; no observed INIT/LOAD/START failure. Session IDs must remain internally consistent, not equal stock's pointer/hash values. |
| Raw geometry properties | Stock NV12 constraints0x201002 differ from mainline actual-plane0x1002; the padded buffer changed host layout but no runtime payload was captured. |
| Capture descriptors | Four FTBs use the same44-byte ABI, type2,2MiB allocation and zero payload/offset. Their actual IOVAs were not captured either. A delayed failure from pending FTB work cannot be excluded just because input QBUF was next. |
| Address space/cache | All nonsecure types share venus_ns; configured SIDs match. Neither heap substitution nor bidirectional mapping establishes valid input/output/internal IOVA intervals or complete cache/ownership equivalence. |
| Internal buffers | All scratch6/7/8 and persist4 nonzero requirements were registered; no missing scratch1/2 is evident. Mainline exact lengths versus stock page-rounded registrations remain different. No nonzero type5 requirement or separate recon DMA allocation was established. |
| Clock/bus changes | Input `venus_pm_load_scale()` calls rate and ICC voting before ETB. Stock startup requests maximum rate/bus; mainline calculates load-based votes. No captured actual rate/ICC result proves adequacy or failure. |
| Power collapse/resume | Stock command writes rearm1500ms PM work; mainline encoder autosuspend is2000ms and its collapse path checks idle/WFI/PC_READY. Cached flags and last returned PM calls are unlogged at the failure boundary; neither premature collapse nor its absence is proved. |
| Queue/interrupt | Ring copy, write-index publication, barriers and optional rx_req-driven doorbell are shared across commands. START success exercises them, but not every first-input concurrency/ownership condition. |

The firmware ELF is little-endian Xtensa (`EM_XTENSA=94`), joined
`venus.mbn` SHA256`44c39a0ff5a25433626ae4b21e3267b0713cdf4c5338cdf88763489d72a3da05`,
version`VIDEO.VE.6.0-00039-PROD-1`. The earlier REA attempt returned
`unsupported_target`; no supported new Xtensa provider was established here.
It was not relabeled as ARM or executed. Firmware ETB parsing, tolerance of
reserved fields and the actual faulting access remain unresolved.

## One next experiment: record the first packet before queue publication

Propose **one diagnostic module variant**, retaining the same kernel settings,
padded probe and all codec/mapping/power behavior. Capture the first encoder
input's packet and adjacent stages; do not combine this with another proposed
fix. This follow-up specifies the variant; no new instrumented module or
ready-to-run module command is claimed.

1. Emit bounded `pr_emerg` entry/exit markers around the first input's
   `venc_pm_get_put()` and `venus_pm_load_scale()`. Record returned status,
   requested rate/ICC values at their existing call sites, and cached
   power_enabled/suspended flags. Do not add speculative register reads,
   rate forcing, autosuspend disabling or firmware-policy changes.
2. Record exact on-wire setup packets for this session's0x1001/0x1002/0x1003
   properties and nonzero internal SET_BUFFERS, plus its four initial44-byte
   FTBs. Keep the record bounded to this single encoder session, including
   lengths, session ID and IOVAs. This resolves the padded-property question
   and binds the ETB address to its allocations; dump descriptors, not pixels.
3. After the encoder ETB builder succeeds, while holding the normal command
   queue **mutex**, dump exactly64bytes with a fixed prefix and byte offsets:
   `print_hex_dump(KERN_EMERG, "A9_VENUS_ETB_PRE ", DUMP_PREFIX_OFFSET, 16, 1, packet, 64, false)`.
   Also record packet size/type, command-queue read/write indices, rx_req and
   cached state. Gate this diagnostic once for the first nonempty encoder ETB;
   do not clear or rewrite any packet word, including reserved fields.
4. **Sleep200ms before calling `venus_write_queue()`**, then mark the queue
   call and its result, and the subsequent optional doorbell begin/end. In
   this source the writer copies bytes, executes`wmb()`, publishes
   `qhdr->write_idx`, executes`mb()`, and returns. Only then does
   `venus_iface_cmdq_write_nolock()` ring the doorbell when rx_req is set.
   Waiting after publication but before the MMIO doorbell is too late: firmware
   can already poll the new index. Do not sleep under a spinlock or IRQ-disabled
   section, and retain the existing mutex/barrier/doorbell order.

The existing `venus_dump_packet()` sits before queue publication but is
KERN_DEBUG behind a private static false flag, without an exposed toggle in
this build; an invented sysfs/module-parameter command will not enable it.
The variant needs an explicit first-input emergency-level hook at that point
or an equivalent encoder-only call path before any ring copy/publication.

Use the existing direct host kernel stream and persistent boot logger during
the single user-run attempt. The200ms pause gives readers time to save the
pre-publication record; neither printk severity nor a delay guarantees retention
through every whole-SoC reset. A useful result is the complete16-word packet,
setup/IOVA records and last stage marker, even if encoding fails again. A reset
before packet construction localizes the host PM/queue-preparation branch;
a reset during the pre-publication pause cannot be caused by consumption of
that not-yet-published ETB. Reset only after publication localizes further but
does not alone identify the firmware access. If the pause makes encoding work,
report timing sensitivity rather than declaring a packet correction.

Stock packet evidence for this follow-up is
`venus29-etb-analysis-20261025/stock-etb-rea/evidence.json`, with its full
address/Evidence index in`stock-etb.md`:

| Claim | REA Evidence ID |
| --- | --- |
| Stock caller and reserved-word initialization boundary | `ev_0e4b2fcb0d551ac1277786ffb209b0b7fc32867d48a8889adca38b3fde58469b` |
| Compressed-decoder packet size60 | `ev_5e47e527d793a803168229aac08a34d733061e4ec385e8cddeaa4271c7c06f08` |
| Selected stock packet-ops table | `ev_28e8095deda6fea3f504bb98eb386611db3981a2eb1e779b511f12ba11c90087` |

The raw stock table reads and matching mainline object disassembly are retained
with that report. The follow-up's `retained-summary.json` binds the padded-run
evidence, and its `packet-model.c` executes the matching mainline builders on
the host. The isolated stock REA session was exported and closed.

## Scope and retained sources

The mainline comparison uses the **matching integ29 build**, under
`integ29-host/encoder-trace-build/module/drivers/media/platform/qcom/venus/`.
Its build receipt hash matches the predecessor hash in integ29's
`integrated-build.json`; compiled `venus-core.ko`/`venus-enc.ko` hashes match
that package's encoder payload receipt. The source is not inferred from an
unrelated current kernel checkout.

Private analysis root:
`/home/user/A9-build/mainline/venus29-linear-analysis-20261025/`.
`input-manifest.json` and `retained-summary.json` bind the supplied captures
and archive files. Existing static sources are
`venus132-20261025/stock-mapping-rea/evidence.json`,
`venus-hfi-host-20261024/stock-buffers-rea/{evidence.json,user-buffer-evidence.json}`,
and `venus-causality-20261024/{stock-properties,power-firmware}/` under the
same mainline build root. [HFI comparison](mainline-venus-hfi-compare.md),
[causality](mainline-venus-hfi-causality.md), and
[first-frame analysis](mainline-venus-first-frame.md) index their Evidence IDs.

New static layout evidence is in `stock-layout-rea/evidence.json` and
`stock-layout-validation/evidence.json` under the private analysis root:

| Claim | REA Evidence ID |
| --- | --- |
| NV12 to internal format0, assembly | `ev_7b176b2b0df055967e8dba89152b090bc906d49f6e36e8c02695ec7efeeb6749` |
| NV12 to HFI color2, assembly | `ev_d366f16ab075bf4b5753ae27ca4552f0629eaff85dcde613ac8c7d713be7c418` |
| Raw input size formula, assembly | `ev_3b9e2f318e92b68c1276f770c1a977237121912a101566cc95542495883a4484` |
| Raw input size formula, pseudocode | `ev_e627892fb5fa6014700add07686e169ef4ff304fd3c49d582e584decbbc86a33` |
| S_FMT uses the raw-input calculator | `ev_4dd8ae45973a0d0d3a48d84892fb5ceac31c37c84f76b5fb160e2c14c60583a5` |
| Selected-format height tables | `ev_91abb054053ffe37a14b0e730f2d500d0e1fff36b0afaaf834821ff03a3d0f01` |

`stock-layout.md` indexes the S_FMT call and constraints evidence, raw table
bytes, prior buffer evidence and limitations. Both new isolated REA sessions
were exported and closed. The Xtensa firmware was not decompiled or executed;
actual stock runtime layout and the failed mainline DMA addresses remain unknown.

This is static/retained-evidence analysis, not a new phone run or hardware
qualification. It identifies remaining differences without claiming the reset
cause. No phone tools, firmware execution, or phone policy/runtime changes
were used by this analysis task.
