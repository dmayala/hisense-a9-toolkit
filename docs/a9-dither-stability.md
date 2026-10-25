# A9 dither stability under synthetic motion and edits

**Date:** 2026-10-12. **Status:** reviewed host study of synthetic software targets, controls and fixtures.

Small gray edits and scrolls can expand changed software targets beyond the changed source pixels under
Balanced/Smooth diffusion. Speed's fixed matrix preserves unchanged screen pixels in the tested stage, but its
phase changes when moving content is compared at the same world position. Binary inputs remove both effects in
the tested cases, while collapsing tone and changing the source masks. These findings support host
experiments, not a shipping palette/mode recommendation or a claim of faster or smoother physical display.

## Input identity, execution and measurement

Composer: private `vendor/vendor.qti.hardware.display.composer-service`, SHA256
`8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`. All addresses are ELF virtual addresses.
Unicorn 2.1.4 executes original `calcG16Table` (`0x1a48c8`), `calcG2Table` (`0x1a49a4`) and handler
`EinkSwTconThreadHandler` (`0x1a5acc`) from `0x1a7338` up to stop PC `0x1a79bc`: dispatch, G2 conversion,
diffusion/matrix and target right shift at `0x1a7994..0x1a79b8`. Inputs enter after luma/rotation;
ReadingMode is omitted. Fabricated object/buffers and a logger no-op replace live service context; diffusion is not
replaced by an analytical approximation. Matrix outputs also match an independent coordinate oracle pixel for
pixel. See [behavior notes](a9-composer-behavior.md) for mode gates and conditioning offsets.

Native arrays have **824 rows x 1648 columns**, 1,357,952 active pixels. Portrait dimensions are 824 x 1648,
with `native[y,x] = portrait[source_y=x, source_x=823-y]`; native dx is portrait vertical motion and native dy
reversed horizontal motion. Portrait stride 832 excludes eight padding columns. Guarded rounded allocation
tails are not pixels.

Primary synthetic controls B/W/C are **30/30/0**; neutral sensitivity uses **0/0/0**. Neither is a measured
current setting or asserted device default. Palettes, indexed light to dark: canonical `255/170/85/0`,
historical `255/184/97/31`, binary `255/255/0/0`. Bands, gradients, glyphs, checker edges and a tile-like
scene/text-like panel have exact fixture generation and hashes pinned in the private bundle below.

Translations wrap the full frame by native dx1, dy1 or synthetic tile dx32. For two sources A/B and
corresponding 0/15 targets TA/TB:

| Metric | Definition / comparison domain |
| --- | --- |
| Source changed | Count `A != B` at fixed screen coordinates |
| Target changed | Count `TA != TB` at fixed screen coordinates |
| Outside mask | Target changed AND source unchanged at that screen coordinate |
| Aligned-world changed | Undo translation of TB and compare common nonwrapped content with TA |
| Edge / flat rates | Target changes divided by each region's pixels; edge includes both endpoints of differing four-neighbor pairs in either source, flat is the complement |

Screen comparisons use all 1,357,952 pixels. Aligned dx1, dy1 and dx32 use rectangles of 824x1647, 823x1648
and 824x1616: denominators 1,357,128, 1,356,304 and 1,331,584. Wrapped strips are excluded from world
comparison; their influence on diffusion elsewhere remains included. None is a ghosting score.

## Main results

Canonical palette, primary 30/30/0 controls; diffusion is Balanced `0x201`, matrix is Speed `0x209`. Smooth
`0x206` matches Balanced on the tile scene.

| Fixture / motion | Source changed | Algorithm | Target changed | Outside mask | Aligned-world changed |
| --- | ---: | --- | ---: | ---: | ---: |
| Tile dx1 | 56,014 | Diffusion | 218,463 | 190,463 | 187,657 |
| Tile dx1 | 56,014 | Matrix | 29,425 | 0 | 270,170 |
| Tile dy1 | 58,240 | Diffusion | 215,537 | 185,628 | 198,329 |
| Tile dy1 | 58,240 | Matrix | 34,078 | 0 | 275,595 |
| Tile dx32 | 1,053,548 | Diffusion | 580,588 | 8,776 | 3,207 |
| Tile dx32 | 1,053,548 | Matrix | 536,516 | 0 | 0 |
| Bands dx1 | 3,296 | Diffusion | 249,942 | 248,120 | 241,614 |
| Bands dx1 | 3,296 | Matrix | 1,544 | 0 | 332,210 |
| 8x8 patch at native [100,80], flat 85 changed to 170 | 64 | Diffusion | 64,720 | 64,691 | — |
| Same patch | 64 | Matrix | 16 | 0 | — |

Tile dx1 diffusion changes 19.732% of edge pixels and 15.230% of flat pixels; matrix changes 11.369% and 0%.
Lower screen-mask churn does not imply better world-pattern stability: the aligned dx1 counts favor diffusion
here. Matrix dx32 equality reflects its 32-pixel period, not a useful physical scrolling speed. All ten
identical-image repeat checks have zero target changes.

For the patch, no changed target precedes the first edit in serpentine scan order. Diffusion changes reach row
823 and span columns 0..671 in the changed bounding box. Other patch positions give different counts: row
parity, matrix phase, column and remaining rows matter. This is a conditioned fixture result, not a universal
locality rule for diffusion or the complete display pipeline.

Primary binary tile dx1/dy1/dx32 gives respectively 37,408/39,536/546,424 source AND target changes, with zero
outside-mask and aligned-world differences in both algorithms. Neutral binary sensitivity tests **dx1 only**,
also 37,408/0/0 (target/outside/aligned). Binary collapses the two light and two dark palette entries, so
these source masks differ from canonical gray masks. Zero incidental changes is limited to these endpoint
inputs and tested controls.

## Palette and control sensitivity

| Tile dx1 controls / palette | Diffusion targets / outside | Matrix targets / outside |
| --- | ---: | ---: |
| Primary 30/30/0, canonical | 218,463 / 190,463 | 29,425 / 0 |
| Primary 30/30/0, historical | 233,859 / 204,186 | 30,025 / 0 |
| Neutral 0/0/0, canonical | 239,599 / 210,398 | 29,407 / 0 |
| Neutral 0/0/0, historical | 263,881 / 229,874 | 26,027 / 0 |

Canonical lowers diffusion changes in this scene at both controls; matrix palette ranking reverses. Controls
change G2 densities/quantization and the observed ranking has no universal palette or optical interpretation.

## Implications and unresolved boundaries

Host experimentation can compare app-owned endpoint presentation and deterministic spatial dither while
measuring tone/detail loss, translated pattern stability and intermediate pixels introduced by
scaling/composition. These results justify such tests; they do not establish benefits for an unreviewed
endpoint prototype. Use explicit controls, palettes, source masks and both comparison domains. Keep target
metrics separate from [scheduling milestones](a9-composer-scheduling.md) and [saved-log
observations](epd-offline-log-report.md).

Current ReadingMode identity/behavior, live controls, full Android composition, overlays, coalescing and
request handling are unverified here. App endpoint pixels may be altered before this isolated stage. Synthetic
wrap translations are not measured game frames, and target bytes are not pigment feedback. Optical ghosting,
FPS, latency, energy, readability and durability were not measured.

## Private evidence and reproduction

Root: `/home/user/source/a9-display-evidence-20261012` (outside Git). Bundle:
`evidence/dither-stability-20261012/`, 179 manifest entries plus manifest. `REPORT.txt` SHA256
`53538eed2f7b7acf7455454cbbcb8169e3ebcca85a2f144f59db7e6bb3b36073`; `SHA256SUMS` SHA256
`1cef7886036a147814d31d96ed9fab46d3c2d89199e0f7b8d93a5ece9ff0f832`. `investigate.py` SHA256
`f20049e82f8b81b373a6dae3b0c65e05d26fa8efeb2870ae2ed2e59d5372e8fa`; `neutral-controls.py` SHA256
`dde208fc221b92a83ce4d369096ab03f874bc8fda93b9219d3932f530465b522`. `results.json` / `neutral-results.json`
pin fixtures, target hashes and metrics; `verification.json` recounts 96 cases and 167 saved arrays across 128
isolated calls (116 primary, 12 neutral). Host execution durations are not phone FPS.

Independent review receipt `/tmp/a9-dither-stability-independent-review-20261012.txt` SHA256
`55041aa88a7d5bd41f65e318f4692f84e39ece7ec0f01e88f216fabd48886d6b`; durable harness is
`derived/dither-stability-independent-review/rev.py`, SHA256
`b245e673a112ad287fab01c9516916b0ce2c1fcfa4bba5bd5879cc342e2e0998`. The reviewer recreated tile dx1 for both
algorithms and the [100,80] patch with randomized buffers/guards; figures matched. Other cases and the full
128-call recount are author-verified, not independently rerun. Final receipt hashes and all 179 manifest
entries were independently checked.

Verify the bundle with `sha256sum -c SHA256SUMS` from its directory. To regenerate, copy it to a separate
scratch evidence tree with the same relative composer path; follow `REPORT.txt` commands using numpy, Unicorn
and pyelftools (matplotlib for the optional figure). Preserve accepted outputs. Scripts, arrays, figures,
vendor bytes and raw execution logs stay outside Git.
