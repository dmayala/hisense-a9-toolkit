# A9 diagnostic contrast-pattern host test

Run `tests/mainline-a9eink-pattern/run.sh`. This compiles the exact pure-C
`a9_eink_pattern.h` helper used by the driver's normal update path. It uses no
phone, sequencer, kernel tree, or waveform blob.

The helper writes a complete 1648 x 824 landscape grey buffer. On the portrait
824 x 1648 glass its rectangles are:

| Portrait rows (inclusive) | Portrait columns (inclusive) | Input grey | Clear level |
| --- | --- | --- | --- |
| 0–823 | 0–411 | 0 | 0 |
| 0–823 | 412–823 | 255 | 15 |
| 824–1235 | band `n = floor(column * 16 / 824)` | `17 * n` | `n`, 0–15 |
| 1236–1647 | 0–274 | 0 | 0 |
| 1236–1647 | 275–548 | 27 | 1 |
| 1236–1647 | 549–823 | 255 | 15 |

The listed Clear levels assume `contrast=0`, the measured default; the driver
still applies its normal control mapping to this diagnostic input.

The large upper rectangles show true black and white references. The middle
ramp shows all sixteen quantised levels, and the lower three rectangles compare
true black with the setup wizard's approximate grey 27 and white. Input values
and digital target levels are exact; this test does not prove physical optical
levels, waveform effectiveness, temperature calibration, or HV timing.

Tests construct an independent portrait image from rectangles, then compare
every pixel after rotation into landscape engine coordinates. They check buffer
canaries, full coverage, all sixteen level populations, the grey-27 boundaries,
and deterministic replacement of any previous grey-buffer contents.

A second fixture extracts the production `a9_ctl_store`, `test_pattern_store`,
`a9_take_job`, `a9_plane_atomic_update`, and `a9_fmt` functions verbatim. It links
the real engine and TCON implementation, including framebuffer conversion and
the stock control curve; only kernel/DRM plumbing is mocked. It checks:

- Invalid pattern/control writes and a missing worker refuse without queuing.
- Pattern start and repeat produce full Clear jobs; disabled operation stays
  dry and the selected steady mode remains unchanged.
- Android commits update the newest UI shadow while the pattern is held,
  without queuing a job or replacing the pattern. `off` queues a full Clear
  of the newest UI and subsequent KMS commits resume normally.
- A changed control queues a full conditioned redraw for a static UI, applies
  its actual transfer curve, and unchanged writes queue nothing.
- Neither diagnostic operation nor control updates enable drive/PMIC gates or
  clear a fault latch, whether `enable` starts false or true.

These fixtures do not execute HV or transport operations. Frame-progress,
emergency-off, worker admission and abort/interlock behaviour belongs to the
stall suite; a mocked mutex does not establish concurrency ordering.
