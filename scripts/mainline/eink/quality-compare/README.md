# Row 05: saved-camera comparison

Prepared by agent for agent, 2026-10-23. Host-built and synthetic-tested;
no device run or optical qualification. The user runs all device commands below.
The worker never ran adb, fastboot, edl, a phone script, or phone input.

The same fullscreen APK supplies embedded 824×1648 PNGs on both stacks. It uses
one Activity throughout, so ordinary page switches do not request an
activity-change clear. The four exposed user modes are Balanced/513, Clear/515,
Smooth/518 and Speed/521. Internal waveform words behind `any_mode` are outside
this comparison. Mode writes have readbacks before and after each step; an
observed override aborts the run. Leave automatic per-app/dynamic mode switching
off; these boundary readbacks do not trace every waveform within a scroll.

For every mode, three repetitions show: black reference plus manual clear,
white reference plus manual clear, 16-level 4×4 ramp, 8×16 checker, white after
checker, text, a 600 ms upward scroll, white after text, then cleared white.
There is no explicit clear between the checker/text and their ghost-test white
pages. All steps settle for four seconds after command completion, followed by
0.6 seconds of final photos. Clears have an extra fixed 0.5 seconds between
showing the page and requesting the clear. Camera bursts start 0.4 seconds
before each action and run at a requested 10 snapshots/s. HTTP completion and
the actual sampling cadence are recorded rather than assumed. All snapshots
are saved verbatim; the capture program makes no image-dependent decisions.

Keep camera position, focus, lighting and exposure fixed for both runs. Lock
the camera's exposure and white balance if its server allows it. The programs
do not configure the webcam. The native glass is in view in the one saved host
framing photo checked during preparation. Four nested-square fiducials encode
orientation and perspective, including letterboxing from logical display size.

Run stock first, while slot A is booted and unlocked. The scripts temporarily
set Android brightness to manual/128 and plugged-in stay-awake to 3; original
values and the original refresh mode are restored in `finally`. Root on stock
uses `su -c`. The test APK remains installed for reuse and is force-stopped at
the end. Stock never flashes or reboots. Its existing HWC handles refreshes.

Mainline uses the existing b-try owner, its native `onb-root.py` transport and
the vendor `a9ml-eink.sh open/close` admission/fault watcher. Firmware staging
follows `scripts/mainline/play/onb.py`; the vendor opener verifies stock PG
telemetry in both components. No direct gate
bypass or HV-cap change is added. The hook closes its session; b-try owns the
final return to A. This is an optical comparison of the image's existing
settings: skip-same-target/prewarm are recorded and not toggled. Android's
`input swipe` does not exercise the kernel touch handler, so these results
cannot qualify a prewarm benefit. No SurfaceFlinger restart is used.

The updated image pair is `/home/user/A9-build/mainline/integ-20261023-3/`.
These commands use the existing b-try path and do not add a format flag. Existing
owner decisions about shared-data compatibility still belong to the user.
Capture directories must be new. Each run saves at most 1 GiB; leave room for
both runs above the repository's 15 GiB free-space floor.

```sh
Q=/home/user/A9/scripts/mainline/eink/quality-compare
P=/home/user/A9-build/mainline/eink-quality-20261023

"$Q/stock.sh" "$P/stock"

A9_BTRY_ONB="$Q/onb.sh" A9_BTRY_HOLD=1800 A9_BTRY_LOGS="$P/mainline3" \
  /home/user/A9-build/mainline/b-try.sh \
  /home/user/A9-build/mainline/integ-20261023-3/boot.img \
  /home/user/A9-build/mainline/integ-20261023-3/vendor.img

/nix/store/ykripfs7ljz7i7ibwsfzy6d4hpjmcddk-python3-3.13.7-env/bin/python3 \
  "$Q/analyze.py" --stock "$P/stock" --mainline "$P/mainline3/quality" \
  --out "$P/comparison"
```

`comparison.md` prints stock/mainline rows for each mode and a comparison
verdict. `measurements.json` retains per-repetition greys, both ghost scores,
per-step onset/settle/transition bounds and any qualification failures.
`stock/registration.jpg` and `mainline/registration.jpg` show the fitted
fiducials; normalized ramp/white previews accompany them. Raw photos, monotonic
HTTP request/response brackets, action windows, mode readbacks and cleanup
status remain under each capture directory.

Grey count is the longest increasing separated subset of the 16 patch medians,
after black/white flat-field normalization. Separation must exceed both 2% of
the measured black/white range and three times combined robust local/temporal
noise. Adjacent resolved pairs and non-monotonic reversals are also saved.
These are camera-resolved apparent greys: a photo at this scale cannot prove
whether an apparent grey uses intrinsic pigment levels or spatial dithering.

Ghost contrast is the absolute difference between median residual darkening
on the previous pattern's dark and light masks, relative to the same
repetition's cleared white and black/white range. Checker and text ghosts are
reported separately, in percentage points of optical black/white contrast.
Residual RMS and signed contrast are retained. Checker and scrolled-text
correlation checks verify the stimuli reached the glass. Unresolved fiducials, insufficient reference contrast,
lighting/exposure drift over 5%, cleared-white differences over 3%, missing
repetitions or failed cleanup make the comparison inconclusive.

Host-to-camera latency is action submission to 90% settling of the spatial
mean image difference, requiring at
least three final-like photos and no later observed relapse. A transient white
flash cannot count as completion. Optical transition duration instead runs
from 10% visible change to this final settling. Both are intervals from
saved HTTP brackets, rather than an invented exact millisecond result. A fixed
camera delay cancels in transition duration; variable exposure/cache age does
not. Neither is a panel-only waveform timing measurement. No-change steps and
unresolved timing are labelled accordingly. Scroll timings are saved separately
because they include the deliberately 600 ms gesture; summary timing uses the
five static page transitions. This does not remeasure kernel frame conversion.

Default comparison margins are explicit: one fewer apparent grey, one
percentage point additional ghost contrast, and 100 ms additional observed
settling. The table reports regressions, matches, timing overlap or insufficient
evidence. These margins are a reproducible comparison rule; the user's optical
judgement remains pending. The script does not promote the build or mark row 05
complete. If registration fails, rerun the offline command with
`--stock-centers 'x1,y1,x2,y2,x3,y3,x4,y4'` and/or `--mainline-centers ...`, using
the four marker centers in TL, TR, BR, BL order. Supplied points must still pass
the marker template check.

`epd-snap.sh` and `epd-watch.sh` were inspected. They measure software target
buffers and kernel refresh events, so they are deliberately outside the camera
measurement loop. Their first-use shade discovery would alter stimulus history.
They remain useful separate diagnostics, not optical ground truth.

Offline rebuild and the five focused host checks:

```sh
/nix/store/ykripfs7ljz7i7ibwsfzy6d4hpjmcddk-python3-3.13.7-env/bin/python3 "$Q/build.py"
/nix/store/ykripfs7ljz7i7ibwsfzy6d4hpjmcddk-python3-3.13.7-env/bin/python3 "$Q/smoke.py"
```

The build uses the cached API 36 SDK/JDK path from the existing Camera2 probe.
No network download, platform signing key, extra Python environment or device
is required. Five host checks cover perspective registration/blank refusal,
16-vs-4 greys and known ghost contrast, latency with a transient flash, both
mocked capture controllers including restoration, and an end-to-end offline
saved-JPEG comparison. Phone-side shell literals were syntax-checked only;
there is no claim of real adb transport, Android runtime or optical testing.

## Boot 52 follow-up

The stock capture is already saved at the default stock path; reuse it. The
first mainline install failure is addressed by `adb install --no-incremental`.
The second run completed two Balanced repetitions before its seventh PG dip
latched the rail fault. The six earlier dips recovered in 27–28 ms. The fault
sample was 15 ms after waveform end, and PG was high again before VCOM was
lowered. This identifies recovery-token exhaustion rather than expiry of the
100 ms wait. The vendor helper now configures a 64-token burst before opening;
the 100 ms/two-good-read qualifier, 3/session and 1000/boot caps, refill rate,
fault latches, HV budgets and ordered shutdown remain in force. `rail_diag`
is included in future fault snapshots and retained numeric kernel summaries.
SELinux now grants the observed missing `dir create` permission to a9ml_eink.

Partial analysis is explicit and pairs only fully completed repetition numbers
in both stacks. It retains the aborted-run status and never gives a hardware
pass. The two saved Balanced repetitions report a quality match with overlapping
timing bounds. Fine-text verification compares both photos and target at
sigma=2 native pixels: raw correlation was .51/.52 on stock/mainline, increasing
to .83/.84 at camera-resolvable bandwidth. The .65 threshold is retained; raw
correlations are also saved. Grey and ghost scores use the original method.
NumPy scalar/array conversion is supported when writing measurements JSON.

```sh
/nix/store/ykripfs7ljz7i7ibwsfzy6d4hpjmcddk-python3-3.13.7-env/bin/python3 \
  "$Q/analyze.py" --stock "$P/stock" --mainline "$P/mainline2/quality" \
  --allow-partial --modes 513 --out "$P/comparison-mainline2-balanced"
```
