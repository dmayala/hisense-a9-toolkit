# eink-probe: SurfaceFlinger vs glass, latency and quality

Status: 2026-10-17. Tool built and tested offline (fake camera, fake adb, two real
downscaled camera frames). **Not yet run against the phone**; every number below
that says "measured" was measured on the webcam stream or in the offline tests, not
on a live scenario. Code: `scripts/eink-probe/`, tests: `tests/eink-probe/`.

Purpose: iterate on mainline E Ink quality and latency with numbers. The host
compares what SurfaceFlinger rendered (`adb screencap`) with what the glass shows
(fixed webcam at `http://127.0.0.1:8093`), and times each input against the first
and last visible change.

## Safety (read first)

* The tool never contacts the phone by itself. Only `act` runs commands, and only
  through the prefix you pass with `--adb` (default `adb`). The library has no
  `subprocess` import (a test enforces this, and that fastboot/edl/nc are absent).
  The coordinator runs it; agents without phone rights run only
  `patterns calibrate verify warp capture watch report` (camera/HTTP only).
* `act` sends `input`/`am start` and `screencap` over adb. It does not touch
  sysfs, modules, partitions or the gate parameters. Whatever gate state the
  coordinator put the phone in stays as it was.
* **Gates-open runs remain attended-only (review 66b).** That rule is unchanged by
  this tool. A camera does not make an unattended run safe by itself. Unattended,
  camera-watched iteration would need a new review that admits it, with the
  automatic stop conditions below implemented and tested first.

### Proposed stop conditions for a future "camera-watched unattended" review

Proposal only (nothing here is implemented in `act` yet; the coordinator's
harness would own them because they need phone state the probe does not read).
Abort the session, close the gates and return to the known-safe state when any of:

1. `rail_fault` != 0 or `rail_faults` increases (eink-state.txt line `rail_fault ...`).
2. `hv_on` stays 1 (or `rail_on`/`rail_energized`) for longer than the longest
   expected waveform plus margin with no frame activity (stuck high voltage).
3. `stall_fault` != 0, `commit_fail` or `aborts` increases, `late_drive` > 0 growth.
4. **No glass change** for N consecutive actions (suggest 2) although `updates`
   advanced: the camera says the panel is dead or the picture path is broken.
   Equally: glass changes with `updates` flat (unexpected drive).
5. A glass change that never settles (`settled=false` / `max_wait`) twice in a row.
6. Max session length (suggest 10 min) and max actions (suggest 40), whichever first.
7. Camera health: stream stalls > 3 s, or fps below half of the measured 15 fps
   (the probe is blind, so the run must stop).
8. adb vanishes, any adb rc != 0 on two consecutive steps, or screencap is not a PNG.
9. Whole-frame brightness jump / loss of calibration (quad mismatch via a periodic
   `verify`): stop, because the metrics are no longer trustworthy.
10. Operator override file (e.g. `touch STOP`) checked between steps.

The first five are state the existing `eink-state.txt` sample already exposes; a
new review should say who samples it between steps (the coordinator, over the
channel it already uses).

## Setup (once per camera position)

Work in a nix shell or let the wrapper do it (`scripts/eink-probe/eink-probe` uses
system python if it has numpy+cv2, else `nix-shell -p python3.withPackages(numpy opencv4 pillow)`).

```
P=scripts/eink-probe/eink-probe
$P patterns --out /tmp/probe/pat         # writes pattern-white/black/checker.png (824x1648)
```

Coordinator (touches the phone): push and show each pattern full screen, then
let the E Ink settle (a full refresh, about 2 s) before capturing. For example:

```
adb push /tmp/probe/pat/pattern-white.png /sdcard/Pictures/
adb shell am start -a android.intent.action.VIEW \
    -d file:///sdcard/Pictures/pattern-white.png -t image/png
```

The viewer's chrome and the status/nav bars will show; that is fine, the
detector only needs the panel to change between white and black, but cleaner is
better (an immersive image viewer). After each pattern, capture 2 s:

```
$P capture --seconds 2 --out /tmp/probe/cal/white
# show black ...
$P capture --seconds 2 --out /tmp/probe/cal/black
$P calibrate --white /tmp/probe/cal/white/frames --black /tmp/probe/cal/black/frames \
    --out /tmp/probe/calib.json --preview /tmp/probe/quad.jpg
```

Look at `quad.jpg` (green quad over the white frame). `calibrate` also saves
`calib.white.png`/`calib.black.png`: the flat-field references (16-bit, warped)
used to normalise illumination: normalised pixel = (cam - black) / (white - black).
If detection is wrong or the aspect is off, pass `--corners x1,y1,x2,y2,x3,y3,x4,y4`
(TL, TR, BR, BL in camera pixels, any order). Optional registration check: show
`pattern-checker.png`, capture one frame, `$P verify --calib ... --image f.jpg
--pattern /tmp/probe/pat/pattern-checker.png` (exit 3 and "POOR" if shifted more
than 12 panel px or NCC < 0.5).

Camera facts: do not touch the camera, phone or lighting between calibration and
the run, and leave the frontlight and ambient light where they were during
calibration (the refs encode them). If either moved, recalibrate.

## Scenario runs (`act`)

```
$P act --adb 'adb -s SERIAL' --calib /tmp/probe/calib.json \
       --scenario scripts/eink-probe/scenarios/default.json --out /tmp/probe/runs/<name>
```

* Starts camera capture, waits 1.5 s pre-roll, takes `screencap` 0 (initial),
  then per step: records host monotonic `t_send`, runs `<adb prefix> <step args>`,
  records `t_ret`, waits until the glass is quiet (no change for `quiet_ms`, not
  before `min_wait_ms`, give up at `max_wait_ms`), then `screencap -p`.
* Scenario JSON: steps with `name` and `adb` (argument vector appended to the
  prefix); per-step or global `min_wait_ms`, `quiet_ms`, `max_wait_ms`. Default:
  home, drawer swipe up, back, open Settings, two scrolls, home. Copy and edit it.
* `--screencap-cmd` defaults to `exec-out screencap -p`; the PNG signature is
  checked and CRLF damage repaired, otherwise the run fails loudly.
* `--cam-latency-ms N` subtracts a known camera pipeline latency from the
  timings (default 0, see Limits). `--stop-on-error` ends at the first adb failure.
* Output dir: `frames/` (every camera JPEG + `index.jsonl`), `screencap/`,
  `session.json` (raw timestamps), then `report.json`, `report.md`, `sheets/NN-name.png`.
* Re-analyse any time without the phone: `$P report <session> --calib calib.json`.
* Dry run against a fake: `--adb 'bash tests/eink-probe/fake-adb.sh'` with `FAKE_DIR`
  set (see `tests/eink-probe/test_probe.py::EndToEnd`).

## Attended demo mode (`watch`)

```
$P watch --seconds 30 --calib /tmp/probe/calib.json --out /tmp/probe/runs/demo1
```

Captures N s while a human uses the phone, then writes `watch.json`/`watch.md`
and `activity.png`: a frame-diff timeline and a list of activity segments
(start, end, duration, peak fraction of panel pixels changing). Calibration is
optional (without it the whole frame is used, so room movement counts too).
`--analyse-only` re-runs the analysis on an existing `OUT/frames`.

## What is computed

Per action (`report.json` -> `actions[]`):

| field | meaning |
|---|---|
| `first_change_ms` | `t_send` to the first camera frame that differs from the pre-input baseline (mean of last 3 pre-frames) for 2 consecutive frames. Threshold: fraction of panel pixels changed > max(median + 6 MAD of pre-input frame diffs, 0.2%); a pixel "changed" if its normalised value moved > 0.08 after removing the global gain shift (auto-exposure). `*_since_adb_return` gives the same from `t_ret`. |
| `settle_ms` | `t_send` to the last frame that still differs from the final picture (mean of the last 3 frames of the window). `settled=false` if less than 500 ms of unchanged frames followed. Compared with the final picture, not frame to frame, because slow E Ink waveforms change little between 15 fps frames. |
| `contrast_michelson`, `white_level`, `black_level` | median normalised glass value over regions the screencap says are white (>0.88) / black (<0.12): (W-B)/(W+B). Normalised, so 1.0 = the white reference. |
| `edge_sharpness_ratio` | mean gradient magnitude of the glass over the screencap's edge pixels divided by the same for the screencap blurred to camera resolution. 1.0 = as sharp as the camera can see the render; low = blurred/soft glass. |
| `ncc_vs_screencap`, `rmse_vs_screencap` | similarity after phase-correlation registration (shift reported). |
| `ghost_residual` | where previous and current screencap differ strongly (|P-S| > 0.5), projection of (glass - S) on (P - S): 0 = clean, 1 = glass still shows the old image. `ghost_region_frac` tells how much area supports it; `unchanged_region_rmse` is the error where nothing should have changed. |
| `speckle_std*` | std of the high-passed glass in flat regions of the screencap (white / black / both); measured on the mean of up to 6 settled frames so camera sensor noise averages down but persistent panel speckle does not. |

Contact sheet per action: previous screencap | screencap | glass (normalised) |
|glass - screencap| x2, all at 412x824 scaled 0.6.

Metrics are computed at 412x824 (half the panel): the camera sees about 415
pixels across the panel, so finer comparison would measure nothing.

## Measured vs inferred

Measured (this session, 2026-10-17):

* The webcam MJPEG stream at 127.0.0.1:8093/stream delivers 1920x1080 JPEGs of
  about 160 KB with `Content-Length` and `X-Timestamp` per part. A 6 s clip: 90
  frames, **14.9 fps** (median frame spacing 67 ms, p95 100 ms, 2 gaps over 1.5x).
  So the latency resolution floor is **~67 ms** (one frame), jitter +-33 ms. About
  2.4 MB/s, 150 MB per minute: keep sessions short, delete frames after reports.
* From today's frames (t2-smooth white vs t2-home dark) the detector found the
  glass at x 704-1105, y 75-889, aspect 2.05 (expected 2.00). Corners are inset a
  little by the rounded screen corners (about 10-15 camera px at the top left).
  Detection on the pair `ugly.jpg` vs `t2-home.jpg` was wrong (the phone had moved
  and the frontlit stand changed brightness): it returns a tilted quad that still
  passes the aspect check, so **always look at the preview**, and use `--corners`
  if in doubt.
* Offline tests: 17 pass in ~40 s (`tests/eink-probe/run-tests.sh`): synthetic
  calibration within 5 px of 1920, parser with/without Content-Length, fps
  measurement, timing on a synthetic progressive change, ghost estimate 0.35 +- 0.12
  for a 35% blend, speckle and blur sensitivity, `act` end to end against the fake
  adb and fake camera (first change 250-520 ms for a 300 ms fake, settle 500-900 ms
  for 600 ms), watch timeline.

Inferred (not yet measured on the phone):

* `input` via adb adds roughly 100-300 ms (adb transport plus the `input` Java
  tool start) before the event exists. `first_change_ms` therefore overstates the
  device's own latency by that amount; compare builds with the same prefix and
  scenario, and use `*_since_adb_return` only as a different reference, not as truth.
  A faster injector (a persistent `sendevent` shell, or `input` via an already
  running `adb shell` session) is a possible refinement for the coordinator.
* The camera's own pipeline latency (exposure + USB + mjpg server) is unknown and is
  not subtracted. In a dark scene the 67 ms frame spacing suggests long exposure.
  Calibrate it once with a known event (for example `screencap` host time vs a
  full-screen black/white flip started by the same host command) and pass it as
  `--cam-latency-ms`.
* Contrast/ghost values depend on the frontlight and exposure at calibration time.
  The flat-field refs remove gradients, not clipped highlights or exposure changes
  during the run (auto-exposure is a risk; if the camera allows, lock it).

## Limits

* Time resolution is one camera frame (67 ms at 14.9 fps, +-33 ms), so changes
  shorter than that (a single-frame flash) can be missed; timings of a single
  action are +-1 frame, so use several runs and compare medians.
* The E Ink waveform itself (234 ms for 7 frames in the last sample, longer for
  full refresh) sets the lower bound for `settle_ms`; first change can be earlier.
* Geometry is a plane homography from the detected quad (no lens correction), so
  residual registration errors of a few panel pixels are absorbed by the phase
  correlation (reported as `align_shift_px_metric`; larger than 8 metric px is not
  corrected and will lower NCC/sharpness).
* Absolute sharpness and speckle numbers are camera-limited (about 2x undersampled
  against the panel) and valid for A/B comparison on one setup only.
* Ghost needs the previous screencap and a big change; scrolls of mostly-white
  pages give a tiny support region (`ghost_region_frac`), so read `ghost_residual`
  next to it.
* Ordinary quiet detection uses a threshold from the pre-roll noise; a very slow
  waveform tail below it is cut off (settle reads early); lower the threshold by
  editing the scenario `quiet_ms` or re-analysing with `report`.
