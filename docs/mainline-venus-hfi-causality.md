# Venus encoder: ranked first-buffer hang hypotheses

2026-10-24, host only. Hardware encode remains required. This ranks the static
stock/mainline differences by their ability to explain failure at the first
ETB/FTB; it does **not** establish that the retained failed run reached that
boundary or identify a proven hang cause. The earlier evidence does not prove
START completion or first ETB. No phone, firmware execution or power experiment
was performed.

The input identities, packet layouts and stock addresses are in the
[HFI comparison](mainline-venus-hfi-compare.md). This pass covers the complete
47-helper stock encoder setup chain, its relevant nested setters, selected
Bengal/AR50LT callbacks, 86 control defaults, allocation/start and core PM/boot
paths. Defaults and generic helper existence are distinguished from actual
traffic. Firmware internals remain undecoded: the exact locked image is Xtensa
`VIDEO.VE.6.0-00039-PROD-1`, with mainline **HFI4**, not HFI6.

**Integ26 update, 2026-10-25:** the retained trace now establishes init,
requirements and clean deinit responses, with no encoder START, registration,
ETB or FTB. The first real Surface RGBA input fails CPU mapping because its
allocation lacks CPU usage. This supports the userspace input-layout/error
handling branch of rank4; the firmware allocation/count part and other startup
hypotheses remain untested. Mapped EOS was enabled but never reached. The
[line evidence and opt-in CPU-readable input fix](../runs/integ26-hvenc-20261025.md)
take priority for the next qualification; keep the independent kernel variants.
The [real-app follow-up](../runs/hvenc-realpath-20261025.md) makes CPU-readable
input the component default and corrects C2-versus-gralloc usage bits. Camera
and screen-recording surfaces share this contract; the qualification switch
remains available as an explicit0/1 override.

## Ranking and the two landed experiments

The ranking is a mechanistic investigation order, not a measured probability.
Rows 1–2 have independently selectable, default-off module parameters in
`57544b1`. Everything else below remains analysis, without speculative fixes.

| Rank | Difference and first-buffer mechanism | Evidence / practical discriminator |
| --- | --- | --- |
| **1** | **Output size notification missing.** Stock sends `0x20100c [output=2,sizeimage]` after requirements and before internal buffers/load. Mainline encoder never called the existing size setter. This is the most direct selected-path difference in capture-buffer configuration. | Stock start `0xffffff8008ddd750`; mainline `venc_start_streaming`. `a9_output_size=1` sends the negotiated capture allocation size, not a new allocation or guessed size formula. FTB already carries allocation length, so absence alone does not prove a defect. |
| **2** | **CBR work mode, low latency and their placement differ.** Mainline AVC/HEVC uses mode 2 before other properties. Selected stock CBR/OFF uses mode 1 plus low latency 1, after rate control/other properties and before requirements. This changes the firmware processing pipeline selected for the first input. | Stock mode callback `0xffffff8008e61c40`, RC setter `0xffffff8008e03d48`. The checked Codec2 default is CONST → V4L2 CBR. `a9_stock_work_mode=1` reproduces this selected policy. Actual ioctl acceptance remains unobserved; the new `_cbr`, `_off` or `_2` trace identifies the kernel branch. A successful case implicates the coupled mode/latency/order policy, not one isolated field. |
| **3** | **Startup clock/bandwidth boost missing.** Stock's startup counter below 16 requests maximum clock and bus votes; mainline computes load immediately. An initial firmware/memory burst could behave differently. | Stock `msm_comm_scale_clocks` `0xffffff8008e689d8`, bus vote `0xffffff8008e673f8`. Stock requests 384 MHz and configured DDR maximum 2,128,000 kB/s; low-resolution mainline uses about 133 MHz and 121,000 kB/s. The override requires mapped input with positive filled length. The steady low-resolution formulas both round to the lowest OPP, so this is not proof of underclocking. No clock/vote changes landed. |
| **4** | **Input layout, registration size/count and error handling.** Stock uses format constraints while mainline explicitly sends actual stride. Stock internal allocation sizes are page-rounded in registrations; mainline registers exact firmware sizes. Mainline can skip an internal type after a requirements-query failure. | Format constraints `0x201002` versus actual-plane information `0x1002`; allocator `0xffffff8008e21d28`, registration `0xffffff8008dd4a50`. Differences need actual format, requirements, sizes/counts, query errors or IOMMU evidence. Neither side is proved to underallocate; see the buffer section. |
| **5** | **Other applicable CBR/firmware defaults.** Stock sends CBR VBV 500 ms at the qualifier size, real-time scheduling 1, explicit extradata disables, timestamp-RC-disable 0, single-slice configuration and bitrate-savings enable. Mainline omits these sends. | Exact IDs/conditions are in the complete property table below. These could affect buffering, scheduling or first-output defaults; no observed firmware default contradicts mainline yet. They rank below the direct size and selected-mode differences. |
| **6** | **QP presence/values and layer/default disable semantics.** Stock sends QP bounds only after explicit vendor flags; mainline sends them during setup. LTR, hierarchy and target-layer handling also differ. | Both QP payloads are **64 bytes**, with **84-byte wire packets**, and min/max offsets match. There is no proved packet-length defect. Explicit-versus-absent settings can matter, but unsupported ABI reconstruction is unwarranted. |
| **7** | **Bitstream choices.** VUI enable, IDR period, CABAC model, NAL mode, signal/rotation/blur defaults and header handling differ. | Mostly valid alternate settings or explicit default versus absence. They are more likely to change output syntax/metadata than hang the SoC at first input; that is an inference, not firmware proof. |
| **8** | **Conditional feature paths.** CQ/grid, Main10/HDR, VP8 resilience, CSC, chroma offset, lossless, hierarchy priority and CVP extras differ or are missing. | Their enabling conditions do not describe the ordinary 8-bit CBR AVC/HEVC qualifier. Do not enable a bundle of unrelated properties to make the sequence look like stock. |
| **9** | **PM/completion/firmware-handshake differences.** Timer/framework and version-completion policy differ. | Mainline autosuspend is 2000 ms versus stock 1500 ms. Both require WFI/video idle and PC_READY before collapse. Mainline verifies image version before exposing the encoder. No busy-poweroff, wrong-image or bootstrap defect was found on the inspected paths. |

EOS is outside this first-buffer ranking. Stock's real mapped EOS allocation
versus `0xdeadb000` remains a concrete **drain** difference, tested separately
by the existing `a9_mapped_eos` flag.

## Complete setup-property accounting

Payload lengths below exclude the 20-byte one-property wrapper. These are the
identified differences across the complete inspected setup chain, including
conditional families and shared families whose ABI matches. Stock addresses
refer to the saved 4.19 kernel, without a live KASLR slide. The retained full
inventory supplies each condition's branches and control-table provenance.

| Family / stock setter VA | Stock selected/default behavior | Mainline difference or matching boundary | Rank |
| --- | --- | --- | --- |
| Frame size/rate `0xffffff8008dfe3a0` / `0xffffff8008df56b0` | `0x1001` input/output, 12 B each; `0x2001 [2,fpsQ16]`, 8 B. Stock initializes 30 fps. | Same ABI. Compare negotiated dimensions/rate, not stock defaults against the qualifier's requested 320×240/10 fps. | 4 if actual values differ |
| Raw format `0xffffff8008dfee78` | `0x1003`, 8 B, plus `0x201002`, 40 B, for supported constraint-table formats including NV12. Initial format is Q128 UBWC. | Mainline negotiates linear NV12 and sends `0x1002` actual-plane/stride information; HFI4 encoder lacks the constraint call. An unrequested stock UBWC default is not a matched-format comparison. Full constraint enum conversion remains unresolved. | 4 |
| Secure mode `0xffffff8008dff538` | `0x1011`, 4 B, explicitly 0 for default nonsecure. Secure 1 restricted to AVC/HEVC. | No corresponding encoder send. No evidence that firmware defaults to secure. | 5 |
| Scheduling/priority helper `0xffffff8008dffe80` | Default control 1 sends **REALTIME `0x202002=1`**, 4 B. | Omitted. Mainline has a REALTIME packetizer. **PRIORITY `0x202003` is a different ID** and is not what this stock helper sends. | 5 |
| Sequence headers `0xffffff8008e0ed88` | AVC/HEVC `0x2006008`, 4 B; default prepend control 0 is sent. | Same ABI, value follows header-mode control; C2 asks joined headers. Actual stock userspace may override its default. | 7 |
| Profile/level and transform `0xffffff8008e00620` / `0xffffff8008e14760` | `0x1005`, 8 B. `0x2005025`, 4 B, only AVC High/Constrained High. | Profile/level ABI matches. Mainline sends explicit transform 0 for other AVC profiles where stock skips it. | 7 |
| Entropy `0xffffff8008e0ab50` | AVC `0x2005002`, 8 B, CABAC-model literal 3; host normalization coerces baseline to CAVLC. | Mainline uses CABAC-model literal 1. The model is irrelevant when CAVLC is selected. Host capability/bitrate normalization also differs. | 7 |
| Rate control `0xffffff8008e03d48` | `0x2005004`, 4 B; stock normalizes RC and forces low latency for CBR. | Common RC wire values; selected work-mode/latency/order difference is flag 2. Stock's vendor control enumeration is not the modern V4L2 enumeration. | 2 |
| VBV `0xffffff8008e04448` | CBR only, not VP8: **`0x200600d`**, 4 B; 500 ms below 1280×720, 1000 otherwise. | Omitted. Do not substitute the different `VBV_HRD_BUF_SIZE` ID. | 5 |
| Bitrate savings `0xffffff8008e0c710` | `0x2005038`, 4 B; default control 3 derives enable 1, with feature exceptions. | Omitted. | 5 |
| Timestamp RC `0xffffff8008e04cb0` | `0x2005027`, 4 B; default disable control 0 explicitly sent. | Omitted despite a corresponding packetizer. | 5 |
| Frame QP `0xffffff8008e05450` | `0x2006012`, 24 B, only explicit I/P/B flags or OFF mode, with additional OFF requirements. | Mainline calls different legacy SESSION_QP `0x2005006`; HFI4 suppresses it. Not an ordinary untouched CBR send. The mode flag alone does not make arbitrary OFF sessions stock-equivalent. | 6 / conditional |
| QP bounds `0xffffff8008e063c0` | `0x2005009`, **64 B**, only packed min/max flags at instance `+0x1e68 & 0x18`. | Mainline sends 64 B during setup, with enable masks/values that may differ. C2 AVC requests standard MAX_QP 51; that standard CID is not stock's packed vendor CID. No length or min/max-offset mismatch. | 6 |
| HEVC image controls `0xffffff8008e07790` | CQ paths call quality `0x2006014` (16 B) and grid `0x2006015` (4 B); otherwise grid normally absent. | Mainline's CQ quality call is not supported by its HFI4 packetizer; no grid counterpart. Both declarations' quality size includes reserved words. Qualifier is CBR, not CQ/grid. | 8 |
| AUD / VUI `0xffffff8008e0f550` / `0xffffff8008e15238` | AUD `0x2005016`, 4 B, explicit 0 normally matches. VUI `0x200501e`, 12 B, skipped at default control 0. | Mainline AVC always sends VUI `[1,1,1e9]`; stock enabled VUI uses `[1,RC<3,1e9]`. | 7 |
| HDR / VPX `0xffffff8008e185c0` / `0xffffff8008e11240` | HDR `0x2005036`, 48 B, HEVC Main10 only. VPX `0x2005029`, 4 B, VP8 only. | Mainline has conditional Main10 HDR; no encoder VPX-resilience send. Neither adds an ordinary 8-bit AVC/HEVC discrepancy. | 8 |
| NAL / slices `0xffffff8008e15a28` / `0xffffff8008e0b040` | AVC/HEVC: `0x100b=1` start-code mode, 4 B; `0x200500e [single=1,size=0]`, 8 B, at defaults. | Both sends omitted; packetizers already exist. Framing/default effects remain unobserved. | 5 / 7 |
| Loop filter `0xffffff8008e0e000` | AVC/HEVC `0x2005003`, 12 B, converted mode/offset controls. | Mainline AVC has the counterpart; its encoder setup does not send this block for HEVC. | 7 |
| Intra refresh `0xffffff8008e0bc40` | CBR `0x200500d`, 8 B, default NONE 1 / count 0. | Mainline CBR default matches; nondefault MB-count policy needs actual controls. | No default mismatch |
| LTR `0xffffff8008e16510` | `0x200501c`, 12 B, only count >0 and permitted codec/RC; default count 0 skips. | Mainline explicitly sends count 0/manual 1/trust 1. No missing recon/LTR allocation is established. | 6 |
| Hierarchy `0xffffff8008e10778` / `0xffffff8008df7dd0` | Default max/layers 0 still sends `0x2005026=0` and `0x200600b=0`; hybrid may use `0x200502f`. | Mainline sends the normal properties only with layer bitrate configured; no comparable hybrid path. | 6 / 8 |
| Base-layer priority `0xffffff8008df8ce8` | `0x200600f`, 4 B, only max layers >0. | No corresponding encoder call; inactive in the qualifier's ordinary no-layer case. | 8 |
| IDR / intra period `0xffffff8008e00c68` / `0xffffff8008df3848` | `0x2006002=1`, 4 B; `0x2006003`, 8 B, current P/B counts; adaptive B `0x2005037=1` if B frames become enabled. | Mainline IDR period is 0; P/B packet ABI matches but control adjustment differs; no adaptive-B send. Qualifier asks B=0. | 7 / 8 |
| Aspect / video signal `0xffffff8008e171c0` / `0xffffff8008e11a00` | SAR `0x2005017`, 8 B, only nonzero width/height. Video signal `0x200501d`, 28 B, derived/default-disabled fields. | Corresponding encoder calls absent. Do not infer a scalar alias has the stock signal payload. | 7 / 8 |
| Layer/target bitrate `0xffffff8008df98d0` / `0xffffff8008df4d90` | Layer fractions use `0x2005031`; no-layer case explicitly disables it, then sends `0x2006001 [bitrate,layer=0xff]`, 8 B. | Mainline omits the fraction-disable and uses target layer 0. With one layer these may be equivalent, but that firmware interpretation was not proved. Mainline MAX_BITRATE `0x2006007` is suppressed on HFI4. | 6 / 7 |
| CSC / chroma offset `0xffffff8008e13938` / `0xffffff8008e0d518` | CSC `0x3007002`, 80 B, requires nondefault enable. Chroma `0x2005040`, 8 B, requires special HEVC/RC/color/offset conditions. | No corresponding encoder setup; inactive at normal defaults. | 8 |
| Blur `0xffffff8008dfbfa8` | `0x2006010 [input=1,width=0,height=0]`, 12 B, even default disabled. | Omitted. | 7 |
| Extradata `0xffffff8008e18d80` | Default mask 0 explicitly disables `0x2205003`, `0x2205008`, plus `0x220500a` for HEVC; each 8 B `[enable=0,same ID]`. Nonzero masks can enable other extra/statistics. | No corresponding sends or extra-plane allocation. **INDEX_EXTRADATA `0x201006` is a separate `[enable,index]` ABI**, not a substitute. | 5 |
| CVP / operating rate `0xffffff8008dfd338` / `0xffffff8008df7268` | CVP-index extras require mask bit 4; operating rate `0x2003` requires CVP-internal enabled, which selected Bengal does not enable. | No corresponding calls, but not an established ordinary selected-path omission. Full CVP index semantics remain unresolved. | 8 |
| Buffer counts `0xffffff8008dff098` | `0x201001`, 12 B each for input/output, actual and minimum counts from queue/capability state. | Same wire ABI; mainline sets host minimum equal to actual. A difference depends on actual/minimum counts, not the stock control default 1. | 4 if values differ |
| Rotation / lossless `0xffffff8008e12aa0` / `0xffffff8008e19658` | Rotation/flip `0x3007001`, 8 B, identity `[1,1]` at defaults. Lossless `0x2005039=1`, 4 B, only special RC 7. | Omitted; identity versus absence is a default difference, lossless is inactive in the qualifier. | 7 / 8 |

The chain's host-only preparation/normalization helpers — entropy update,
bitrate update, B-frame decision and all-intra restrictions — also differ in
policy, but have no independent property packet. Their dependent effects are
accounted for above. Nested adaptive-B, hybrid, image quality/grid and CVP
helpers are included; a helper name alone is not treated as a wire send.

## Buffers, scratch/persist and queue mechanics

Both selected drivers query firmware requirements. Stock platform version 5
AR50LT does not use the version-3/4 local size calculator; mainline HFI4 also
has no host calculator. Both use 32-byte requirement records, types scratch
6/7/8 and persist 4/5, and separately register each allocation with count 1.
There is no justification for an HFI6 scratch-size patch.

There are narrower differences worth recording:

- Stock `msm_smem_alloc` at `0xffffff8008e21d28` page-rounds normal requests,
  stores the rounded size, and `set_internal_buf_on_fw` at
  `0xffffff8008dd4a50` registers it. Mainline registers exact `bufreq.size`,
  though its backing allocation uses pages. Registered sizes differ only if
  the requirement is not page-aligned; both cover the requested size. Secure
  stock alignment can be larger, outside this nonsecure qualifier.
- Mainline queries requirements repeatedly by type and skips an internal type
  after a failed query/zero size. Stock first obtains requirements in the
  start chain, whose error aborts setup, then skips absent/zero internal types.
  A real query failure could therefore leave a more consequential difference;
  the existing HFI timeout/error records must establish that premise.
- Stock can reuse sufficiently sized scratch and retain persist allocations;
  mainline reallocates through its stream/session lifecycle. This is more
  relevant to repeated configuration than the first fresh instance.
- Stock's USERPTR/vendor-plane metadata is converted to imported DMA storage;
  mainline uses the standard DMABUF/vb2 path. No allocation-bank/IOVA defect was
  established. Input padding is not a proved frame-size mismatch: mainline
  sends normalized capture dimensions for both HFI frame-size properties and
  OUTPUT dimensions for stride. The userspace `0013` sizeimage/bytesused fix
  must remain present. Actual DMA addresses/cache behavior remain unknown.
- Mainline's initial mem2mem batch queues FTB before ETB. Stock queues pending
  buffers in userspace/list order; both wait for load/start first. No universal
  stock ETB-first requirement was found. Ordinary user buffers need no separate
  stock SET_BUFFERS/REGISTER, matching mainline dynamic capture behavior.
- Stock's inspected recon setup allocates tracking nodes, not missing pixel
  DMA registrations. ETB/FTB command IDs, sizes and length offsets match.

Thus actual requirements, registered sizes/counts, format/stride, failed-query
records and IOMMU faults are discriminators; static table names alone are not.

## Clocks, votes, PM and firmware handshake

The allowed clocks closely match: stock 133.33/240/300/384 MHz, mainline
133.333333/240/300/384 MHz. Stock startup requests the maximum while its startup
counter is below 16; exact counter increment semantics were not traced, so
“first 16 frames” is not asserted. DDR startup is clamped to 2,128,000 kB/s;
configuration/ARM9 paths request 1000 kB/s.

Steady calculations differ: stock encoder cycles are VPP 675, VSP 0,
low-power 320, plus firmware floors 733003 cycles/frame and 225975 VPP
cycles/frame. Mainline lite low-power uses 675 and omits those floors. For a
bounded 320×240/30 fps example, stock requests at least 21,990,090 Hz and
mainline 6,378,750 Hz; both round up to the lowest roughly 133 MHz OPP.
Mainline core acquisition can also send VIDEOCORES_USAGE `0x2002` and
conditional PERF_MODE `0x200600e`. Stock
`msm_vidc_decide_core_and_power_mode_ar50lt` at `0xffffff8008e62058` only
stores core ID 1; no equivalent send was established in that callback. This is
a bounded caller comparison, not proof that stock never sends either property
elsewhere. No generic core/performance property was added.

At low positive load, mainline selects encoder average BW 121,000 kB/s from
rows 396000/275000/242000/121000, peak 0, aggregated across active sessions.
Stock uses a formula with minimum 1280×720 geometry and a separate ARM9 DDR
performance path; mainline uses video/configuration ICC paths. Matching vote
units do not establish physical path equivalence. Runtime delivered clocks
and bandwidth were not observed; no adequacy or underclocking conclusion follows.

Stock rearms a 1500 ms delayed power-collapse worker after commands; mainline
encoder autosuspend is 2000 ms. Stock AR50LT preparation
`0xffffff8008e52720` and mainline HFI4 suspension both require CPU WFI/video idle,
issue SYS_PC_PREP and require PC_READY before power-off. Poll/retry timing and
frameworks differ, but no static busy-poweroff defect was found. Stock's
never-unload-firmware setting does not mean never power down.

The inspected bootstrap register offsets/3 MiB shared-region programming match.
Stock SYS_INIT builder `0xffffff8008e5e960` and mainline send architecture 1;
image-version builder `0xffffff8008e60c48` and mainline request property 6;
debug mask 0x18 also matches. Mainline additionally sends system power-plane
control property 5; a stock builder exists, but selected-path invocation was
not established, so this remains an unknown rather than a proved omission.

Stock completes the traced core-init wait on SYS_INIT_DONE. Mainline with
SM6115 minimum firmware set waits for the image-version response, parses
6.0.39, verifies it, and only then exposes the encoder device. The image bytes
match stock. This completion-policy distinction matters before usable session
creation, not specifically at first ETB after a successful probe. No wrong
firmware, HFI6 selection or version-gate race was found.

## Running several variants in one coordinator-owned boot

Both new parameters and existing `a9_mapped_eos` live under
`/sys/module/venus_enc/parameters/`, are writable 0644, default false and are
**sampled at encoder open**. Close/destroy the previous codec and create a
fresh instance after changing them, including any component/device pre-open.
Readback alone is insufficient for an already-open fd. No module reload or
userspace source change is required.
The new options apply only to AVC/HEVC, exact SM6115/HFI4/AR50_LITE/FW6.0.39.

For first-buffer comparisons, keep `a9_mapped_eos=1` constant so a later drain
failure does not confound the startup result. An informative order is:

| Case | `a9_output_size` | `a9_stock_work_mode` | `a9_mapped_eos` | Purpose |
| --- | ---: | ---: | ---: | --- |
| A | 1 | 1 | 1 | Try both startup candidates first. |
| B | 1 | 0 | 1 | Isolate output-size notification. |
| C | 0 | 1 | 1 | Isolate stock RC mode/latency/order. |
| D | 0 | 0 | 1 | Startup baseline with the same EOS handling. |

The image's all-zero defaults remain available. After a case reaches and
completes normal encoding, an additional otherwise-identical EOS-off case can
isolate drain behavior. A hard hang can still terminate the boot before later
cases; writable parameters do not guarantee all cases finish.

Keep `A9_SWEEP_HVENC_QUALIFY=1` for the existing exact-name ByteBuffer/surface
qualification; it does not enable the driver options. The coordinator and
userspace owner retain their run policy and stage ownership. Record parameter
readback, case boundaries and fresh codec creation through that existing flow.

Expected retained markers use the existing strict diagnostic grammar:
`op=a9_output_size` begin/end and property `0x20100c`; or
`op=a9_stock_work_mode_cbr`/`_off`/`_2` begin/end. CBR/OFF should additionally
show property `0x2005022`, after the normal rate-control setup and before
requirements/load/start. `_2` means the CBR/OFF hypothesis was not exercised.
Look for START response, initial buffer.begin/queue records, EBD/FBD and first
encoded output before interpreting EOS/LAST. The current collector accepts
these fixed operation names without editing the userspace stage. A setter
end with rc 0 establishes host acceptance/queueing, not a per-property firmware
acknowledgement.

A successful isolated variant supports a causal contribution from that policy;
it does not prove the exact firmware failure mechanism. If the failure remains
in mapper/converter code before input submission, these kernel hypotheses have
not been exercised. A new IOMMU/panic/timeout trace can reprioritize the other
rows without assuming every difference is a bug.

## Build and host verification

Use a fresh encoder stage with the userspace owner's approved Codec2 inputs and
`stage-encoder-trace.py --trace-build` pointing to:

```
/home/user/A9-build/mainline/venus-causality-20261024/variants
```

Both `venus-core` (new low-latency packetizer) and `venus-enc` are required; use
the three-module set together. Build/stage replay now binds eight originals and
four patches, including `sm6115-encoder-variants.patch`. It rejects a stale trace
build instead of refreshing hashes around old modules. Firmware is unchanged.

All three modules compiled with ARM64 GCC 14.3.0. Export CRCs are unchanged;
imports match integ25 exports; vermagic is
`7.2.10-a9+ SMP preempt mod_unload modversions aarch64`. Clean-source replay
with fuzz 0 matches the compiled source. Eight host groups pass: the existing
four EOS groups and four new groups covering scope/default/order, actual HFI4
mode/latency packets, independent flag combinations/output-size payload and
property errors/suppression. The new packet test includes the actual HFI
header/whole HFI4 packetizer and proves QP payload 64/wire 84. The earlier
24-byte QP statement was wrong because it omitted reserved arrays; it was
corrected before integration and no QP-skip option was landed.

Native module SHA-256:

| Module | SHA-256 |
| --- | --- |
| `venus-core.ko` | `d92525d991a81680a9f6db757a7ddc1aeae7ed1585bb16f5ad253dd7c777bbde` |
| `venus-dec.ko` | `ca2f235c96e7a014013a230d00626c2f4cb8ff9a2574bfac2fb4c6538cacffa2` |
| `venus-enc.ko` | `3b52f3d8753b582646d5cd7d2a1f710b0ef16efe7dc0111b4619d69efe524e13` |

Retained evidence root: `A9-build/mainline/venus-causality-20261024/`:
`stock-properties.md` accounts for all 47 chain helpers and 86 control defaults;
`stock-properties/evidence.json` has new batches
`ev_2633cb2156e504bec5f7340542757b636da9f32778af0f3b5e0298c3cc58e87e`,
`ev_f589a29f53be277910dbb85eb5512a4698a07f50dbd2538063b32a47f8ab8bcb`
and `ev_392f9f490d2b345efba1748ae18da23d4069b01adf80d5b0c08715e64d53ff38`.
`power-firmware/power-firmware.md` indexes 15 REA records plus clock-sort proof;
`mainline/source-options.md` binds source/Codec2 defaults and the corrected
layout assertions. Native logs, module/import hashes and test results are in
`variants/kernel-trace-checks.json`, `module-build.log`, `eos-host-test.log` and
`variants-host-test.log`. All REA sessions are exported and closed. No hardware
qualification, firmware-internal decompilation or hang-root-cause proof is claimed.
