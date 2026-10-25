# Camera2 still probe

Setup and warmup share one60s deadline from Activity creation; drain/still
gets at most15s more. `result.json` is atomically updated during setup and
capture (`completed=0`) and once at completion (`completed=1`). Partial output
retains numeric `camera_phase` and actual warmup/activity state when the host
times out. It is not a successful photo. Phases:1 setup,2 opening,3 configuring,
4 warmup,5 still. Only the final `pass:true` records a validated JPEG and capture
completion.

This small foreground APK opens the requested camera (rear by default), configures one JPEG output,
warms up with serial `TEMPLATE_PREVIEW` requests on the same JPEG output, then
submits one `TEMPLATE_STILL_CAPTURE` request and exits. It uses Camera2 through
the framework/provider/SoftISP/JPEG encoder. No extra preview surface or vendor API is
needed. The warm-up drains each JPEG without saving it and waits for at least
16 completed frames, four seconds and eight consecutive CONVERGED AE results.
A60s AE timeout drains the current preview and saves a still with
`ae_timeout:true`, `ae_converged:false`, `ae_converged_before_still:false` and
reason `capture_after_ae_timeout`. A separate15s deadline bounds the drain/still;
device errors or missing callbacks can still fail without an image. The HAL reports
actual cached AE state, so the former hardcoded CONVERGED value cannot qualify. The only requested permission is CAMERA.

The visible Activity sets `FLAG_KEEP_SCREEN_ON` for the bounded capture and
clears it at completion. Keep the Activity foreground and the phone unlocked;
explicit screen-off, backgrounding, privacy or device policy can still revoke
camera access. Error3 is `ERROR_CAMERA_DISABLED`, distinct from device error4.
Failure JSON includes the numeric/symbolic camera error and Activity resumed,
window focus, interactive and keyguard state; pause/resume events are logged.
This makes a policy revocation distinguishable from a sensor/HAL failure.

The FPS POC requests the advertised `[15,30]` AE range for preview and still.
JSON records requested/result FPS and the current sensor frame duration. Use
`--ei min_fps 30 --ei max_fps 30` for a fixed30 comparison. A missing advertised
range fails explicitly; use this APK with the matching FPS vendor overlay.

Build on the host with the same cached API36 SDK/JDK used by the GNSS probe:

```sh
python3 -I scripts/mainline/cameras/camera2-probe/build.py
```

Output: `~/A9-build/mainline/cameras-20261020/camera2-probe/a9-camera2-probe.apk`,
`SHA256SUMS` and `build-result.json`. The script compiles, dexes, aligns and
signs with a local test key. It verifies the APK signature/alignment and
prints the manifest/package information. It has no phone commands, network
dependencies or image build. Temporary compiler output is removed automatically;
the local test key is retained for `install -r`.

Coordinator only, within the existing owned test boot and its ADB transport
(these commands are instructions, not part of the host build):

```sh
adb install -r -g /home/user/A9-build/mainline/cameras-20261020/camera2-probe/a9-camera2-probe.apk
adb shell am force-stop org.a9.cameraprobe
adb shell am start -W -n org.a9.cameraprobe/.CaptureActivity --ei width 1280 --ei height 960
```

`am start -W` waits for activity launch, not capture completion. Allow up to75s
after session configuration (60s warmup plus15s drain/still)
for `A9_CAMERA2_PASS` or `A9_CAMERA2_FAIL` in logcat. The activity then closes
its session/device/ImageReader and finishes. The result records warmup_frames, warmup_ms, ae_converged_before_still,
ae_state, ae_timeout, ae_converged, initial_exposure_time_ns, final exposure_time_ns
and frame_duration_ns. Each invocation removes its old
outputs before starting. A normal failure writes `result.json` with `pass:false`
and a stage/error reason; a process crash may leave no result at all.

Read the result and retrieve the JPEG using the debuggable package's run-as:

```sh
adb logcat -d -s A9Camera2Probe:I '*:S'
adb exec-out run-as org.a9.cameraprobe cat files/result.json
adb exec-out run-as org.a9.cameraprobe cat files/picture.jpg > /home/user/A9-build/mainline/cameras-20261020/camera2-probe/picture.jpg
```

Require a **fresh** `pass:true`, `capture_completed:true`, positive `jpeg_bytes`,
and decoded JPEG dimensions equal to `selected_width`/`selected_height`.
`pass:true` means a complete, validated JPEG capture; AE convergence is a separate
quality result. Require `ae_converged:true` to claim converged exposure. A timeout
still is retained for inspecting darkness, colour or a metering failure.
The default requests1280x960. If that exact advertised size is absent, the
probe picks the largest JPEG no larger in area, or the smallest available;
the result records both requested and selected dimensions. The app checks
JPEG start/end markers and Android decoder bounds before saving. Confirm the
retrieved file has the reported byte count, opens in a host image viewer and
shows the intended scene. A syntactically valid JPEG alone does not prove
exposure, Bayer colors, orientation or image quality.

Check the same run's logcat/tombstones for app, cameraserver, provider or IPA
worker crashes; confirm `vendor.camera-provider-a9` remains running and
`dumpsys media.camera` still lists the device. Preserve the camera logs in the
same session if it fails. Do not run a manual raw capture concurrently with
the provider. Use `--es facing front` for Hi556, or `--es facing back` (also `rear`) for S5K3L6.
The probe records `requested_facing` and numeric `lens_facing`; it fails if that
facing is absent rather than silently opening the other camera. The front result
must report `lens_facing:0`, the rear `lens_facing:1`. Install this updated APK
once before using the facing argument. For example, append `--es facing front`
to the activity command above, then repeat with `--es facing back` to check rear.

`python3 -I scripts/mainline/cameras/camera2-probe/test-warmup.py` replays the
actual transition methods with a host JVM and fake callbacks/time: converged
capture, both timeout callback orders, stalled drain and setup failure. It does
not emulate Camera2 or establish hardware capture success.
