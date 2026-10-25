# A9 composer behavior for the examined binary

Date: 2026-10-12. These notes summarize accepted host evidence for one composer
build. They describe software targets and frame production; optical levels,
ghosting, update completion, latency and phone FPS remain unmeasured here.
This docs-only task leaves the device in its reported Smooth mode and installs
no new app build.

## Identity and evidence strength

The input is `vendor.qti.hardware.display.composer-service`, 2,049,912 bytes,
SHA256 `8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`.
Its GNU build ID is `286c1b0d8c993a17ede02b45d6053b62`. No claim transfers to
another binary merely because its firmware label or symbol names match.
The documentation base is toolkit assembly
`8dae8ad81c2c91921946c2328faad95b223db852`.

All composer offsets below are ELF virtual addresses in that exact input;
the cited code and rodata addresses also equal file offsets. They are not live
ASLR addresses, and this equality does not apply to all data/BSS addresses.
Function names come from its compressed debug data, whose extracted ELF SHA256
is `f4511694cec38c4767e31e056ae090837d0ac6c9640c7d5ffbeb3314ed83debd`.

| Evidence | What it establishes | Boundary |
| --- | --- | --- |
| Stored-byte inventory [I] | Five families, headers, bins, payload statistics and tails | No execution or control-flow proof |
| Static instruction model [P] | Reset-aware request map, final type, lookup paths and regular counts | Bounded interpreter; pixel loops and consumers are not executed |
| Isolated original ARM64 execution [G] | Local tone, dither, luma, packing and neighbor branches on synthetic memory | Host emulation; imported ReadingMode calls and whole service are excluded |
| Saved-log cross-check [L, P] | Selected software counts agree with the decoded lengths and sequences | One device/build, limited modes and temperatures; no optical timing |
| User optical evidence | One older photo matched structural target content in [snapshot notes](epd-panel-screenshot.md#5-verification-and-what-the-panel-showed) | No calibrated gray, ghosting or timing measurement |

Regenerating normalized JSON establishes decoded-data identity, not the request
map or its control flow. The corrected producer review [P] supersedes stale
count/N claims in the earlier private `STATIC-RE-COMPOSER` UPDATE 8.

## Image to software target to output boundary

```text
App image -> Android composition -> composer image copy and luma/rotation
          -> external ReadingMode calls, if detection enables processing
          -> controls + G2/G16 lookup -> selected dither -> new target nibble
Previous software target + new target -> uint16 indices, with Clear exception
          -> request steps then final pass -> table/cache/copy frames
          -> Minui-DRM submission boundary -> electrical drive -> optical image
```

`EinkSwTconThreadHandler` (`0x1a5acc`) copies a composed buffer at
`0x1a6160..70`, then computes `(76*c0 + 150*c1 + 30*c2) >> 8` at
`0x1a61b0..dc`, ignoring channel 3. Equal RGB gives the same byte exactly.
Rotation at `0x1a6210..f8` consumes 824 active columns in an 832-column source
stride. Native conditioning geometry is 824 rows by 1648 columns: 1,357,952
active bytes, or 2,715,904 bytes for uint16 indices [G]. Larger historical
allocation sizes include extra storage; loop geometry does not prove a live
allocator header or pointer origin.

Calls to imported `ReadingModeDetect` and `ReadingModeImageProcess` precede
local conditioning at `0x1a72c0` and `0x1a72e0`; processing runs only when
detection returns at least 1. The exact current `libReadingModeEngine.so`
provider remains unidentified [G, R]. A stock candidate has SHA256
`f6ea5823ade234aefc13a05e8b200998fcee685861b41004e275cd02f044955e`, but its
companion composer SHA256 is
`17f9cf4111c6e156d8ef74d98d7a4ef8dc6891a16dc59e6bde72a0933f31fe19`, which
differs from this input. In that stock library, `ReadingModeDetect`
`0x96dac..df4` returns 0 for selector `(mode >> 12) & 255 == 0`, including
all four service words. Bounded ARM64 execution confirms that stock gate;
applying it to the current provider is conditional on an identity match [R].

The established [snapshot notes](epd-panel-screenshot.md#2-evidence-for-a-userspace-e-ink-engine-in-the-hwc)
place output frames at the Minui-DRM boundary. The accepted producer proof
stops at frame production: it does not prove consumer timing, cancellation,
buffer ownership under races, DRM completion or pigment response. Queue
research outside these accepted inputs is not adopted here.

## Service modes and controls

The [RefreshMode enum](https://github.com/dmayala/hisense-a9-patcher/src/commit/5384a2908d3ca40acd1d5cf254819ab07dbdd02b/app/src/main/java/com/lmqr/ha9_comp_service/RefreshModeManager.kt)
(lines 7–11), [service command definitions](https://github.com/dmayala/hisense-a9-patcher/src/commit/5384a2908d3ca40acd1d5cf254819ab07dbdd02b/app/src/main/java/com/lmqr/ha9_comp_service/command_runners/Commands.kt)
and [daemon dispatch](https://github.com/dmayala/hisense-a9-patcher/src/commit/5384a2908d3ca40acd1d5cf254819ab07dbdd02b/app/external_scripts/root_service/jni/eink_daemon.c)
(`processCommand`, mapping at lines 244–251) map the labels to these mode words.
Local dispatch is `EinkSwTconThreadHandler` `0x1a7338..5c`, table `0x2ac3c` [G].

| Label | Command / decimal / word | Ordinary local conditioning | New target |
| --- | --- | --- | --- |
| Balanced | `b` / 513 / `0x201` | G2 + signed serpentine binary diffusion | 0 or 15 |
| Smooth | `s` / 518 / `0x206` | Same G2 and diffusion as Balanced | 0 or 15 |
| Speed | `p` / 521 / `0x209` | Same G2 + 32 by 32 binary matrix | 0 or 15 |
| Clear | `c` / 515 / `0x203` | G16; bit 8 clear skips optional 16-level dither | 0 through 15 |

Balanced/Smooth test bit 10 at `0x1a73e8`: setting it chooses the matrix.
Speed uses the matrix at `0x1a78c8` unconditionally. Clear tests bit 8 at
`0x1a77f4`; an internal OR of `0x100` at `0x1a6e80..94` can enable
`DitherSStyle16` (`0x1a8300`). Thus the ordinary service-word behavior does
not describe every internal Clear path. Right shift by four at
`0x1a7994..b8` produces the target nibble [G]. Accepted low bytes 2 and 8
are not exposed by these service commands and lack device validation [P].

The binary matrix chooses white when `g > matrix[y & 31][x & 31]` or
`g > 239`. Simple diffusion uses signed threshold `work > 127` and arithmetic
shift weights 5/16, 7/16 and 1/8, with serpentine boundary/parity rules
(`0x1a741c..7640`); these are not standard Floyd-Steinberg weights [G].

The service writes black threshold, white threshold and contrast through
`stb`, `stw` and `sco`. `UEventHandler` (`0x18ce08`, call `0x18d6d4`)
parses corresponding event values, rejects negative parsed integers, and calls
`EpdSetContrast` (`0x1a183c`). The setter stores controls at object offsets
`0x19e7208/720c/7210` and rebuilds both lookups. Its direct-call behavior can
store negative values; the event handler supplies the negative guard [G].

`calcG2Table` (`0x1a49a4`) uses black threshold, white cutoff and contrast
knee. For controls in 0–100, black is B, cutoff is `255 - floor(7*W/20)`,
and knee is `50 + floor(80*(100-C)/100)`. Each out-of-range control independently
falls back to black 30, cutoff 244 or knee 130. `calcG16Table` (`0x1a48c8`) uses contrast
only; contrast zero gives an identity byte lookup, and out-of-range contrast
falls back to zero. These fallbacks are code behavior, not measured current
settings [G]. Knobs can alter tone and binary pixel density without adding
intermediate per-pixel targets to Balanced, Smooth or Speed.

Under synthetic controls B=30, W=30, C=0, with no external preprocessing,
Clear `0x203` maps equal-RGB 255/170/85/0 to targets 15/10/5/0. Balanced and
Smooth instead produce identical endpoint-dithered target images; Speed has
different endpoint densities. Four app shades therefore do not guarantee
four per-pixel targets or four physical luminance levels. Composition, the
unidentified ReadingMode provider, spatial dither, drive history and optics
are separate links in that claim [G, R].

## Request reset, corrected map and final pass

Both request aliases resolve to `S = this + 0x19e7418`, constructed at
`0x1a5b3c`, `0x1a5b7c`, `0x1a5b90`, `0x1a5ba4`, `0x1a5bc4` and `0x1a5bdc`.
Controls at `this + 0x19e7208` are separate.
At `0x1a660c..1c`, two zero 64-bit stores reset kind, flag, N and count
before dispatch. Retained type words are inactive when count is zero.
There is no stale-N/count explanation for this normal producer entry [P].

S fields are kind `+0`, flag `+4`, N `+8`, count `+0xc`, and up to five
step types at `+0x10..20`. For clear input zero, the corrected active steps
are below; each list is followed by a final pass. Empty means zero steps,
not zero output frames. Dispatch tables are `0x2ac22/2ac2b`, with branch/store
proof in `EinkSwTconThreadHandler` `0x1a6670..6ab0` [P].

| Prior low byte | Current 1 | Current 2 | Current 3 | Current 6 | Current 8 | Current 9 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | [] | [1,1] | [] | [1,6,6,6,6] | [] | [1,9,9,9,9] |
| 2 | [] | [] | [] | [3,6,6,6,6] | [] | [3,9,9,9,9] |
| 3 | [] | [] | [] | [3,6,6,6,6] | [] | [3,9,9,9,9] |
| 6 | [6] | [6,6,6,6] | [6] | [] | [] | [6,9,9,9,9] |
| 8 | [] | [] | [] | [] | [] | [] |
| 9 | [9] | [9,9,9,9] | [9] | [9,6,6,6,6] | [] | [] |

Prior 2/3 to current 1 is header-only kind 1, flag 1, N 0, count 0
(`0x1a68b8..d4`). Prior 1 to current 3 is kind 1 with flag/N/count 0
(`0x1a66b0..d8`). The claimed five-step prior 2/3 to current 1 path was
wrong; the five-step `[3,6,6,6,6]` path goes to current 6 (`0x1a6978..90`).

With nonzero clear input, kind 1 transitions retain their lists, including
header-only cases. Other pairs enter kind 2: current 3 gets `[3]`, current
6 gets five 6s, current 9 gets five 9s, and current 1/2/8 gets no steps.
Prior 6/9 to current 1/3 forces clear output to 1 (`0x1a696c`). Otherwise
clear output equals input. At `0x1a7a74..8c`, final type F is current low
byte except current 1/3 with nonzero clear output becomes type 2 [P].

Step calls (`0x1a7ccc`, `0x1a80a4/f0`, `0x1a813c/88`) use their own index
arrays. Final call `0x1a7dd8` uses the final array and F. Completing the last
step forces another iteration at `0x1a81c0`, so it does not replace the final
pass. Buffer/state branches can suppress a request; this map alone does not
prove that every accepted pair reaches a submitted batch [P].

## Temperature, nominal lengths and observed compound counts

In `EinkSwTconThreadHandler`, caps at 50 are applied at `0x1a7a94..9c` and
`0x1a7ac4..c8`. Bit 7 then zeros each affected reading at `0x1a7aa4..a8`
and `0x1a7acc..d0`, before weighting. On signed-byte inputs this clears
negative readings. Weighting/division at `0x1a7ab0..1a7b00` gives
`avg = floor((2*t1 + t2)/3)`; selection at `0x1a7b04..0c` reads the byte
map at `0x2b0d0`. Bin 0 covers avg 0–2; bins 1–10 cover `3*b..3*b+2`;
bins 11/12/13 cover 33–37 / 38–43 / 44–50 [P, I]. The simplified formula applies
to signed-byte inputs, not arbitrary integers. For example,
(-20,50) becomes (0,50), avg 16, bin 5; combining the negative value first
would incorrectly select bin 1. Positive warm logs do not test this branch.

Nominal lengths for bins 0 through 13, from canonical stored headers [I]:

| Lookup family | Lengths |
| --- | --- |
| Type 2 / type 3 | 104, 88, 76, 66, 57, 48, 40, 35, 30, 30, 30, 30, 30, 30 |
| Balanced | 54, 46, 40, 35, 30, 26, 22, 19, 17, 15, 14, 13, 12, 12 |
| Smooth | 28, 25, 23, 20, 17, 14, 12, 10, 8, 8, 8, 8, 8, 8 |
| Speed | 28, 25, 23, 20, 17, 14, 12, 10, 7, 7, 7, 7, 7, 7 |

For a completed regular batch, with bit 0 of gate `0x4c7fb8` equal to 1,
initial lookup counter `0x4c7fbc` equal to 0, well-formed count 0–5 and no
abort/interruption, total equals the sum of active-step lengths plus length(F).
The gate check is at `0x1a7c74`; gate 0 takes the separate `0x2b103` sequence
via `0x1a7d04..dc0`, whose lifecycle remains unresolved. The logged accumulator
increments at `0x1a7e14` and logs at `0x1a7e40/48`: it counts producer
iterations, not optical refreshes or consumer acknowledgements [P].

| Observed class | Active steps; final F | Bin / sum / reported total |
| --- | --- | --- |
| 1 to 6, clear input 0 | [1,6,6,6,6]; 6 | 9: 15+4*8+8 = 55; 10: 14+4*8+8 = 54 |
| 6 to 1, clear output forced 1 | [6]; 2 | 8/9/10: 8+30 = 38 |
| 6 to 6, clear input 1 | [6,6,6,6,6]; 6 | 9: 5*8+8 = 48 |
| 1 to 1, clear input 1 | []; 2 | 9: 30 |

Branches `0x1a671c/6a58`, `0x1a6880/691c/696c`, `0x1a6958/6a70` and
`0x1a6ab0` respectively support these classes. The independent log check [P]
found 13 complete deduplicated chains across two producer PID/TID pairs,
with no count/total mismatches. Equal type 2/3 lengths cannot distinguish
those types by totals alone; the final override identifies type 2.

Separately, [L] verifies 310 initial steady events and 1,409 unique held-out
steady events after removing overlap: held-out modes 1/6 have 916/493 events,
only bins 8/9, with zero length mismatches. These are bounded corpus events,
not statistically independent samples. Initial temperatures (28,34) give avg 30 and bin 10; held-out events cover only bins 8/9.
Invalid r002 stimulus evidence contributes only engine correlation. Speed,
ordinary Clear and colder bins have no comparable saved-log validation.

## Stored columns, caching and sentinel 256

`lookUpTable` (`0x1d1690`) uses the [canonical inventory](a9-composer-tables.md#confirmed-byte-specification):
14 bins of 257 slots per family, with a length header followed by capacity
bytes. Zero codes inside declared payload remain payload. Smooth's two active
rows differ from the corresponding Balanced prefixes in all 14 bins [L];
Smooth is not simply Balanced truncated to fewer stored columns.

Runtime paths [P] do not execute every stored column independently:

| Type / condition | Regular path for nominal length L |
| --- | --- |
| 2/3 | Table lookup on each iteration; 257 scratch entries |
| Balanced 1, S flag nonzero | Table lookup on each iteration; 256 entries |
| Balanced 1, S flag zero | One lookup, L-3 cached repeats, two terminal buffer copies |
| Smooth 6/8 | One lookup, L-2 cached repeats, one terminal copy; 256 entries |
| Speed 9 | One Speed lookup, L-2 cached repeats, one terminal copy; 257 entries |

Speed entry `0x1d1abc`, first-frame branch `0x1d1adc -> 0x1d1b84`, uses
base `0x1313df`. The `0x3838` stride path `0x1d1b38/1d1c50` belongs to
Balanced base `0xe5b2b`, selected at `0x1d18e0`; it is not Speed's first
frame. Smooth joins the cached path at `0x1d16e4 -> 0x1d1ae0` with its own
base `0x116e3b`. A warm Speed L=7 means one lookup, five repeats and one
terminal copy; it is code-derived, with no saved Speed event. Exact copied
buffer content is outside the producer model's stubbed memcpy proof.

Regular packing `0x1a7a08..30` writes `(old << 4) | new` as little-endian
uint16, then copies new to old at `0x1a7a34..44`. Old is previous software
target memory, not pigment feedback [G]. Ordinary indices are 0–255.

Exception branch `0x1a79bc..cc` applies only to low byte 3 with clear flag 0.
It clears the active packed array, then a changed-to-15 pixel can write 256
to the first in-bounds stable old==new==15 neighbor, choosing left, right,
up, then down (first column starts right). The trigger retains its regular
pair; a later stable-neighbor visit preserves the sentinel. Writes at
`0x1a7eec/7f2c/7f54/7f74/7ffc/8044/8074` and independent full-frame ARM64
cases establish boundaries and priority [G]. Slot 256 is active in type 3
and zero in the other stored families [I]. Its physical purpose and routing
through every request case remain unverified; it is neither ordinary level
16 nor universal padding/full-clear behavior.

## Remaining questions and useful experiments

The missing current ReadingMode identity, gate-0 lifecycle, exceptional and
concurrent paths, consumer ownership/timing, drive polarity, DC balance,
physical gray accuracy and ghosting remain open. No safe driver patch or
ghosting elimination follows from this document.

Useful next work can preserve Smooth: review the exact current dependency
when available; validate composed pixels and correctly identified software
targets for a deterministic four-gray scene at unchanged settings; then use
synchronized optical capture to measure visible gray, residue and completion.
Each stage needs its own build identity, valid stimulus/window and sensor.
Mode-switch experiments should be separate from steady Smooth measurements
because their compound producer counts differ. These are proposed experiments,
not device actions performed by this task.

## Reproduction references

The committed [inventory tool](../scripts/a9_composer_tables.py) requires the
full input SHA and can reproduce stored facts. The [saved-log parser](epd-offline-log-report.md)
and [workflow](display-investigation-tools.md) preserve software counter units;
neither verifies the new control-flow assertions above. Evidence remains
private, outside Git. The following receipt hashes pin the synthesis inputs:

- [I] `/tmp/a9-package-review-composer-775657f-20261012.txt`, SHA256
  `31118b62409e70e09141b2cfa00ea339c7ecc4f3e9d8a718af8d2eb79cd5f85d`;
  accepts inventory commit `317692c19cb43b8b852fd25f12e56f0cc45cff8f`.
- [L] `/tmp/a9-user-sol-composer-layout-review-20261012.txt`, SHA256
  `434913cba96f77f1c22a3b057e20c913d88aedb3a1c7f63ede9c1cee3e88b63b`.
- [P] `/tmp/a9-composer-producer-proofreview-20261012.txt`, SHA256
  `fe1eb05737d04f9d4d24e6f80896aee0e2b0d167c192c3a33f2db7e90d9e3030`.
- [G] `/tmp/a9-gray-path-independent-review-20261012.txt`, SHA256
  `0c3d9f6cffc3ef9e40b04195edb3be23db33e1cfc6d95bd300086ee83d7a3be3`.
  Author report under `a9-display-evidence-20261012/GRAY-PATH-USER-SOL-20261012.txt`
  has SHA256 `68eaba562d860529bf1a60419ac798236fbf8651d581562318aac3573af0d2cf`.
- [R] Same private evidence root, `readingmode-user-sol-20261012/READINGMODE-STOCK-FOLLOWUP.txt`,
  SHA256 `c78030a725b8b94104b0c75441ccc9e6dfd681bbe72320893ed939f7530856f9`.

Function/offset references and full input identity above allow claims to be
checked against the original private binary even if temporary receipts move.
No vendor bytes, full waveforms, disassembly, raw logs or user artifacts are
included in this source document.
