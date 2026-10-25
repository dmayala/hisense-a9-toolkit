# Offline E Ink quality fixture

Run `bash tests/mainline-eink-quality/run.sh [COMPOSER]` with a host C compiler.
The optional composer must be the identity-checked stock binary (SHA-256
`8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`).
`PYTHON` can select an existing Python with numpy and unicorn. No downloads,
phone access or output images; vendor bytes remain in memory and temporary
host build products. This is a host script, never a phone script.

Without a composer, compare production conversion and an experimental 32x32
luma/transpose candidate against the original reference on RGBX, BGRX and R8,
tight/padded pitches, unaligned source/destination, clipped/partial/empty/reversed
rectangles, unchanged and changed pixels, a preexisting damage accumulator and
NULL `changed`. Compare entire buffers (including canaries) and damage bounds.
Report warm host medians and p90 (31 samples, 3 warmups), alternating reference
and candidate order within each sample, then conditioning cost.
The candidate remains **test-only**: the measured timings do not justify
replacing production conversion. Cached host RAM cannot establish phone WC,
fence, import or optical latency. Timing differences on a busy host are noisy.

With a composer, link the actual C engine/TCON and reuse the existing stock
Unicorn oracle. Each of Smooth/Clear/Balanced starts from **the same** all-white
old levels, previous Clear mode, zeroed step arrays, 30/30/0 controls, t1=30,
t2=34 (bin 10), and the oracle-built control template. The landscape scene has
white255, grey235, grey230, black0 and integer-box-filtered synthetic E glyphs
(no host font dependency). Compare complete target levels, request fields,
all active step arrays, packed old/new history and every byte of every frame.
The targets of Smooth and Balanced must match; Clear retains intermediate
levels. Assert the pure white/black endpoints explicitly.

Then change exactly one source pixel in grey235, with a one-pixel damage hint;
require diffusion changes beyond the hint and a target rectangle containing
all changed levels. Each mode's oracle and engine follow identical complete
history through that update and an explicit manual clear. Cross-mode history
naturally diverges after the initial common reset. Require final neutral,
committed history/mode and suppression of a following identical ordinary input.
Expected bin-10 frames: entry 70/30/14, ordinary 8/30/14, manual 48/60/30 for
Smooth/Clear/Balanced. These are software target/transport checks, not proof
of optical quality or validated power delivery.

Related checks: `tests/mainline-a9eink-clear/run.sh`, the engine selftest/apitest,
compat integration tests, and `tests/mainline-a9eink-kunit/run.sh` (arm64 QEMU).
No changes to waveform or E Ink electrical settings are exercised here.

`shadow.c` (run first by `run.sh`, no composer needed): Clear UI shadow curve
properties, composition with C=0..100, every mode low byte OFF/ON (only 3 differs),
uniform fields. See docs/mainline-eink-quality.md section 5.
