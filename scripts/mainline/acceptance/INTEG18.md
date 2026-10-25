# Integ18 follow-up (host only)

Saved inputs: `A9-build/mainline/sweep-integ{12,17-f1,18-f1}-20261024/`.
No phone commands or modifications to those sweeps. Runtime results remain
unchanged until the coordinator measures rebuilt assets.

## Display admission

Integ12 and17 report `eink_open=1`; integ18 reports0 with the same live
SurfaceFlinger/composer/display/binder evidence and80 layers. The integ18
image's `acceptance-assets/firmware/` directory is absent. Integ17 contains
both `a9-waveform.bin` and `a9-ctrl-template.bin`. `build14.py --assets`
replaces the whole inherited acceptance bundle, while `acceptance/build.py`
previously built only APKs and host probes. Unlike the older image recipe,
it omitted the display firmware. `Sweep.eink()` returns before native
admission when the first local-file push fails; consequently no native
failure diagnostic appears in the saved log. This is a packaging regression,
and does not establish an E Ink power or composer failure.

The asset builder now copies both existing cached display inputs and pins
their hashes in `assets.json`. Preparation requires those entries before
installing any probe. Rebuild acceptance assets for the next image. The gate
helper, firmware lookup path, settling interval and fault handling are unchanged.

## USB / MTP

Integ12 completed a real same-device MTP session and kept ADB; integ17 never
attempted the row. Integ18's `mute_verified=0` is the actual USB prerequisite
branch, not a result-key mixup. `onb.log:35–40` records hardware AVC transport
failure, then an absent mute result, then failed native pushes (offline).
The branch returned before USB function queries, descriptors or an MTP session.
There is no saved evidence that MTP itself failed in integ18.

USB now runs after feature cleanup and before the disruptive hardware encoder
probe. Its mute prerequisite and same-device/session checks stay intact. A
prerequisite refusal explicitly records `mtp_attempted=0` and
`prerequisite_failed=1`; no USB success is inferred. Encoder implementation is
unchanged and stays with its assigned owner. If USB cannot recover its
transport, the encoder is unattempted instead of generating another false row.

## Rear still photo

Integ12 saved a1280x960 rear JPEG after580 warmup frames. Integ17 never
launched the rear still activity (`app_stage=1`, command_rc1) after the
front-video transport disruption. Integ18 successfully launched the activity
but the host's95s result deadline expired (`app_stage=3`). No app JSON was
saved. Its zero warmup/activity fields were host defaults, not measured absence
of callbacks or a paused activity. Rear software video subsequently delivered
183 frames and a valid MP4, so this does not establish a dead rear sensor.
Integ17/18 pin identical still-probe source hashes. At this host milestone,
integ18's boot-log archive is not yet available; an exact HAL/Camera2 failure
cause cannot be assigned from the saved rows alone.

One concrete deadline defect is repaired: the app used a60s setup deadline
and reset it to60s again after configuration, followed by15s for drain/still.
With setup longer than20s, its legitimate result window could exceed the host's
95s wait. Setup and warmup now share the original60s budget, retaining the15s
drain/still deadline. A JVM replay of the actual timeout methods proves a40s
setup leaves20s warmup and reaches bounded failure at75s. This source defect
is established; it is not proof that integ18 actually took that path.

The app also atomically publishes partial JSON before opening/configuring and
while warming, at most once per second during callbacks. The host keeps it
until a terminal result or deadline. Numeric `camera_phase` identifies1 setup,
2 opening,3 configuring,4 warmup or5 still capture; `app_stage` keeps its host
meaning. Unobserved activity/warmup fields now use-1. A partial record never
passes a photo. JPEG validation, actual still/result pairing, AE thresholds
and camera error handling remain unchanged. Rebuild the still APK/assets.

## Rear AF / torch scope

Both are implementation gaps already measured in integ12; integ18 did not
introduce them. [STOCK-CAPABILITIES.md](../cameras/STOCK-CAPABILITIES.md)
now identifies the stock wiring and the kernel/DT, libcamera, Android controls
and calibration work needed for each. The AF algorithm/Android translation
already exists from the A6L port but has no A9 lens driver/graph. The flash
module is configured, but its PMI632 DT/control path and Android torch
implementation are missing. This is a scoped implementation plan, not a
claim those controls are fixed or qualified. Rear rows retain
`implementation_missing=1`; front hardware N/A is unchanged.
