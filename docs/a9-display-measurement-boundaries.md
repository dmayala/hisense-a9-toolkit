# A9 display measurement boundaries

**Date:** 2026-10-12. **Status:** docs synthesis of three independently accepted
private reports; this source change awaits independent review.

Saved logs, emitted software frames, DRM submissions, worker return and optical
output describe separate milestones. The accepted findings establish bounded
software behavior, with no measured user-visible FPS, speed improvement,
installation recommendation or drive-sequence safety result.

## Counts and milestones

| Layer | Quantity or milestone | Boundary |
| --- | --- | --- |
| [Stored inventory](a9-composer-tables.md) | Five families, 14 bins, 257 slots each: 17,990 stored slots; nominal length and payload-code counts | Stored bytes, including separately inventoried slot 256; payload nonzero count is not a frame count |
| [Request model](a9-composer-requests.md) | Conditional sum of active-step lengths plus the final pass | Requires regular gate, counter zero, stable request and uninterrupted unsuppressed production |
| [Target/dither study](a9-dither-stability.md) | Changed software pixels in explicit screen/world domains | Software targets and old-target history do not measure pigment state |
| [Scheduling](a9-composer-scheduling.md) | Pending snapshot replacement, production and ring consumption | Targets coalesce; producer done precedes ring drain; seven outstanding slots is a normalized ring assumption |
| Effective subframes | Actual bytes returned by isolated `lookUpTable` calls | A generated frame can be a fresh table result, cached copy or terminal copy |
| Submission | Consumer copy, `Flip`, atomic ioctl attempt/return | Surface rotation also occurs on errors or without an ioctl |
| Kernel completion | Conditional worker/inline software path finishes | Successful return can occur without a new completion event or counter recheck |
| Optical output | Visible transitions, settled image, optical FPS | Unmeasured by these investigations |

An update can contain multiple passes. A stored length, a pass length, a logged
batch's producer count and a count of successful submissions need distinct names.
Producer `total frame num` at ELF VA `0x1a7e48` follows final production;
it does not wait for consumer drain or optical completion. The log's `clear`
marker at `0x1a7b2c` follows preparation and precedes production.

## Emitted software bytes

The accepted effective-subframes report executes original ARM64 snippets in
isolated host emulation: 36 primary sequences / 411 frames plus 90 supplementary
frames, totaling 501 successful lookup calls. Scope is types 1 (Balanced,
flags 0/1), 6 (Smooth), 9 (Speed), bins 8/9/10 and ordinary indices 0..255.
Supplementary nonzero flags test the branch, not ordinary request reachability.
Here L is the nominal pass length, including terminal copies where applicable.

| Pass | Length at bins 8 / 9 / 10 | Emission phases |
| --- | --- | --- |
| Balanced flag 0 | 17 / 15 / 14 | One generation, L−3 copies of its first frame, two terminal copies |
| Balanced flag nonzero | 17 / 15 / 14 | L table generations |
| Smooth | 8 / 8 / 8 | One generation, L−2 cached copies, one terminal copy |
| Speed | 7 / 7 / 7 | One generation, L−2 cached copies, one terminal copy |

The bounded `SetPowerMode` setup constructs a deterministic terminal template
whose active packed-code bytes are zero. It installs the cache pointer without
filling the cache; first generation replaces the entire cache, and middle
copies retain that first output even when subsequently supplied indices change.
The 829×448 four-byte-word output is 1,485,568 bytes, including five leading
rows and padding; 824×1648 active indices occupy 2,715,904 input bytes.

For the four binary endpoint rows, effective and stored sequences coincide in
these bins. Synthetic gray indices expose 28 differing Balanced flag-0 rows
per bin. This does not establish arbitrary intermediate-input reachability
through normal G2 conditioning, electrical code meaning or optical behavior.
The independent reviewer executes two differently poisoned setups and five
passes / 52 frames, checking every output byte with a separate oracle; the
remaining author cases receive the receipt's stated metadata/static checks.
No mismatch requires changes to the accepted request/count model.

Valid initialized rings/terminal, first generation at counter zero and stable
sequential state are prerequisites, including eight successful distinct ring
allocations. Setup resets gate zero; direct LUT calls
do not prove startup reaches the assumed regular gate-one caller. Full power
lifecycle, concurrency, sentinel 256, types 2/3 and other bins remain excluded.
Harness precondition refusals are not protections in the vendor code.

## Submission and conditional kernel waits

`Flip` appends only plane `FB_ID` properties per subframe. Its embedded wrapper
uses `DRM_IOCTL_MODE_ATOMIC` (`0xc03864bc`), flags `0x400` (ALLOW_MODESET),
zero user data, and retries EINTR/EAGAIN. NONBLOCK, TEST_ONLY and event flags
are absent. No explicit user-space completion-event/fence wait occurs here;
the separate blank/unblank path supplies additional setup properties.
An empty request returns zero without ioctl. `Flip` rotates draw surfaces after
errors and allocation failure too; returning a surface does not certify submission.
The author's 34 mocked cases were recorded but not independently rerun;
the reviewer independently checks the selected static path and saved-log fits.

In the exact saved kernel Image, flags `0x400` select nonblock zero. Pending
CRTC/plane masks can cause an interruptible wait before state swap; its raw
signal return is −512. Suitable dispatch queues a CRTC worker and blocks in
`kthread_flush_work`; absent a suitable worker it runs completion inline.
The fence-wait return is ignored before prepare/commit. Video wait errors also
do not become ioctl errors; successful worker return establishes this software
path's completion with its conditional waits, without certifying a physical latch.

For the inspected video path, the wait predicate is counter `phys+0x328 == 0`,
with queue `phys+0x330`. IRQ decrement requires the filtered flush-register
result to be zero; normal callback wake alone does not satisfy the predicate.
The filter clears bit 27 of register `0x18` when register `0xc0 & 3` is zero.
Already-zero counters, negative IRQ indices, skipped traversal and hardware-status
fallback can return success without observing a new event; the fallback calls
the callback and returns without rechecking the counter. Flush is conditional;
live CRTC flags, encoder selection and hardware meanings remain unproved.

The helper's 84 ms argument becomes up to two 42 ms slices. Conversion rounds
each slice to five ticks; a still-positive deadline can permit another full
five-tick wait. No universal 84–100 ms bound follows, nor a whole-ioctl latency
bound given pending commits and fences. These are static timer semantics.
Plane cleanup, state destruction and framebuffer reference puts are distinct
conditional steps. `msm_gem_put_iova` is a no-op in this Image; this does not
make all framebuffer cleanup a no-op. No actual reuse, lifetime, lost-frame or
tearing result follows from these paths.

## Saved producer timings and the 85 Hz candidate

Two host overlay candidates describe 448×829 video transport at 85 Hz; saved
Android logical metadata reports 824×1648 at about 60 Hz. Neither proves an
active DRM mode, link cadence, one-subframe-per-vblank or optical FPS.
The candidates are the stock-package DTB (SHA256
`3cdff3d0c8437b2d778f6305319afff78fc8573be6e619de90d0e9dd44708bd2`)
and existing recovery-scratch DTB (SHA256
`74138d6535db5897db0c80ae093dc5840e8c51cf578b5d395499ded185069931`).

Main saved log `a9-qualification-20261011/logcat-epd-full.txt` has 313 complete
producer-marker pairs: N=8/14/15/38/54 with 6/240/64/2/1 samples respectively.
Fits below use clear-to-total wall-clock gaps with millisecond timestamp precision.

| Descriptive model | Selected result |
| --- | --- |
| Fixed `(N−7)/85` seconds, all 313 pairs | RMS residual 8.35 ms; MAE 5.43 ms |
| Fixed `(N−7)/60` seconds, same pairs | RMS residual 41.10 ms; MAE 37.83 ms |
| Free linear fit, all pairs | 11.697 ms/iteration; inverse slope 85.49 iterations/s; fitted credit 7.05 |
| Free fit, 310 pairs with N=8/14/15 only | Inverse slope 94.02 iterations/s; fitted credit 6.35 |

The three long updates strongly influence the free fit. Seven is candidate
initial credit, not measured occupancy. Backpressure, backlog, preparation and
scheduling can affect producer intervals; numerical agreement cannot locate
the wait in ioctl or certify scanout. N=14/15 occur only in the main log;
engine-log N=8 does not distinguish 60 from 85 Hz well. Across three logs there
are 752 pairs with 148 shared, hence 604 unique pairs. The second engine log
(`r002`) has invalid planned stimulus and is only incidental labeled coverage.

## Exact provenance and review anchors

Composer: `vendor.qti.hardware.display.composer-service`, 2,049,912 bytes,
SHA256 `8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`.
Composer anchors are ELF virtual addresses, not live ASLR addresses:
`SetPowerMode` `0x19e964` (author setup `0x19ee98..0x19f118`; reviewer includes
allocation sites from `0x19edf4`), LUT `0x1d1690`, terminal `0x1f29a8`, cache
`0x35d4b0`, counter `0x4c7fbc`; full cache fill `0x1d1d1c..0x1d1d30`.
`Flip` `0x1caee4`, commit call `0x1cb018`, wrapper `0x1dcb10`, ioctl construction
`0x1dce04..0x1dce28`, surface rotation `0x1cb0c4..0x1cb0dc`.

Saved documented-current Image: 34,476,544 bytes, SHA256
`2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`,
4.19.157-perf, Dec 28 2022; not proven running. Kernel file offsets below equal
linked VA minus `0xffffff8008080000`: atomic ioctl `0x6649e0`,
`msm_atomic_commit` `0xe94a78`, completion `0xe955a0`, video wait `0xe0dab8`,
IRQ `0xe0bfb8`, flush filter `0xe4f4a0`. Callback identities come from RELATIVE
relocation addends; raw Image and derived ELF callback words are zero.
No kernel execution, command-mode alternative or physical completion is proved.

Private evidence root: `/home/user/source/a9-display-evidence-20261012`.
New findings use the following frozen reports and final independent receipts;
superseded kernel drafts and the failed development LUT run are excluded.

| Investigation | Report beneath `evidence/` and SHA256 | Final independent receipt in `/tmp/` and SHA256 |
| --- | --- | --- |
| Cadence | `driver-cadence-20261012/REPORT.txt` — `72a053b10e9d011ef17ca6fddc2c6bda8ece778daae46aa509c38fa252456ebf` | `a9-driver-cadence-independent-review-20261012.txt` — `5acf1530108db7f1c9e32fd8b02828c3fa884bb11cc2e43adab83f9ac9833585` |
| Effective subframes | `effective-subframes-20261012/REPORT.txt` — `d1da8e7c701c9f459f9cde7660bc82f66c412e34abcef6078beadd5e31ca178b` | `a9-effective-subframes-independent-review-20261012.txt` — `c13871e016a4b2c16c7232f2fce922554a649a7e08d03fcbeed89b01e78ee596` |
| Kernel FINAL | `kernel-atomic-boundary-20261012/REPORT.txt` — `d410b362a689c1f8d711b1adde71107b08bd547d6ebc585354f2a0e12ad822c9` | `a9-kernel-atomic-boundary-independent-review-20261012.txt` — `d55d5a7670c82333f6dd74e9fac898618be7f7c3cac8b6fe61882bd1cfa28e25` |

This docs task reads those accepted reports and receipts without rerunning
analyzers, emulation or device work. Raw vendor bytes, disassembly and logs stay
outside Git. Further optical conclusions need separately synchronized submission,
hardware-completion and optical observations with proven running identities.
