> Surface R2: the R1/m direct-Surface test crashed SurfaceFlinger. Run direct
> mode only with the [R2 vendor](../codec2/SURFACE-R2.md), which converts private
> Venus NV12 to RGB inside the hardware decoder HAL. The v4 APK is unchanged;
> Surface=true still exercises MediaCodec's Surface path, with no app conversion.

# Codec2 playback v4: direct Surface or ByteBuffer/RGB

V4 adds a direct MediaCodec Surface mode to the same named hardware decoder.
The default remains the v3 ByteBuffer/RGB path. Neither mode falls back to a
software codec. The coordinator reports k5 ByteBuffer playback passed60/60 with
Codec2 stride patch0009; R2 Surface playback on hardware remains pending.

Build the v4 APK (host only):

```sh
python3 scripts/mainline/video/playback/build.py --out /home/user/A9-build/mainline/codec2-surface-20261020/probe
```

Coordinator only, after booting a vendor with the [minigbm fix](../minigbm/README.md):

```sh
adb install -r /home/user/A9-build/mainline/codec2-surface-20261020/probe/a9-codec2-playback.apk
adb shell am force-stop org.a9.videoprobe
adb shell am start -W -n org.a9.videoprobe/.PlaybackActivity --ez surface true
# Allow ~10 seconds; collect up to40 seconds on failure.
adb exec-out run-as org.a9.videoprobe cat files/result.json
adb logcat -d -s A9VideoProbe:V C2BqBuffer:V GraphicBufferAllocator:V V4L2Decoder:V V4L2Device:V '*:S'
```

The new artifact directory has its own test signing key; uninstall the old probe
first if signatures differ. No platform key is used. Do not replace graphics
libraries in a live SurfaceFlinger session; use the coordinator's normal next
vendor boot. The worker never runs any of these phone commands.

Direct mode configures the real SurfaceView as the MediaCodec output. It never
maps decoder Images, converts YUV or posts substitute Canvas frames. It matches
opaque output buffers to submitted PTS (including when size is zero), schedules
`releaseOutputBuffer` at the clip timestamps, and separately counts unique
`OnFrameRenderedListener` callbacks. Both EOS,60 input/decoded/submitted frames,
visible320x240 and at least one actual render callback are required for
`pass:true`; `drawn_frames` remains0. `all_render_callbacks_observed:true` means
all60 callbacks arrived, but is not a pass requirement. Android documents those
callbacks as delayed/batched and not guaranteed for every rendered frame.
`surface_render_unobserved` after complete decode means rendering was not
observed by the probe; it does not establish an allocator failure by itself.
Physical E Ink presentation still needs the coordinator/user's observation.

For the accepted ByteBuffer regression, force-stop and launch again with
`--ez surface false` (or omit the extra). Expect60 decoded/drawn,0 Surface
submissions and0 render callbacks. Cleanup errors are retained separately so
`codec.stop` cannot overwrite the original failure reason.

---

## Codec2 playback POC

Version3 plays the bundled six-second H.264 clip through the explicitly named
hardware decoder `c2.v4l2.avc.decoder`, using MediaExtractor → MediaCodec YUV
ByteBuffer output → CPU BT.601 conversion → RGB Canvas posts to a SurfaceView.
The fixture is 320×240, 10fps, 60 frames, Constrained Baseline with inter frames,
no B frames, an IDR each second and no audio. No permissions or network access.

## Why RGB presentation

On k4, v2 remained foreground but decoded0frames. The first buffer failed in
minigbm: flexible YUV420 format35 plus GPU_TEXTURE, COMPOSER_OVERLAY and
VIDEO_DECODER usage `0x400900` was unsupported. BufferQueue reported
`createGraphicBuffer failed`, then the HAL's C2BqBuffer retried the same allocation
until the probe deadline. No output buffer was ever allocated; this was not a
Surface-switch or CAPTURE-slot recycling failure. `setOutputSurface`'s earlier
consumer-usage BAD_INDEX was followed by `Surface configure completed` and is
not the allocation's diagnostic reason. The same log warns Mesa's active gralloc
fallback lacks lock_ycbcr / YUV-buffer support.

V3 configures the same hardware decoder with a null MediaCodec output Surface
and COLOR_FormatYUV420Flexible. CPU-readable YUV output avoids that YUV display
allocation; decoded Image planes are converted to RGB and posted to the display
Surface. Row/pixel strides, plane positions and visible crop are respected.
The six-second fixture uses limited-range BT.601; this is not general color
management. The output is letterboxed to preserve aspect ratio.

This is a probe presentation workaround. Direct hardware-decoded YUV Surface
playback still needs allocator and Mesa YUV compatibility work. V3 does not
claim to fix arbitrary Gallery/Surface players, zero-copy presentation, seek,
HDR, audio, arbitrary media or performance. It never chooses a software decoder.
The [A6L 0010 reference](https://github.com/PaddleStroke/hisense-a6l-lineageos/blob/main/device/hisense/a6l/rom/android/patches/external/v4l2_codec2/0010-a6l-venus-decoder-surface-switch-slots.patch)
addresses AIDL Surface-switch ownership/retries; it does not remedy this first
allocation's unsupported format/usage combination, so no new HAL retry/slot
heuristics are imported.

API references: [MediaCodec.getOutputImage](https://developer.android.com/reference/android/media/MediaCodec#getOutputImage(int)),
[Image plane row/pixel strides](https://developer.android.com/reference/android/media/Image.Plane),
[Surface.lockCanvas](https://developer.android.com/reference/android/view/Surface#lockCanvas(android.graphics.Rect)).

## Build on host

```sh
python3 -I scripts/mainline/video/playback/build.py
```

Uses the cached API36 SDK/JDK and FFmpeg, with no network or phone commands.
Output directory `~/A9-build/mainline/codec2-playback-20261020/` contains:

- `a9-codec2-playback.apk`: aligned, test-signed APK including `assets/test.mp4`.
- `test.mp4`, `fixture.json`: generated fixture and FFprobe frame/stream metadata.
- `build-result.json`, `SHA256SUMS`: source commit/hashes and artifact hashes.
- `test-only.p12`: retained local reinstall key; never commit it.

The build compiles/dexes Java, generates and fully decodes the fixture with
FFmpeg, checks all60frames, verifies APK signature/alignment and dumps the
manifest. Compiler scratch is removed. Focused host conversion smoke source:
`tests/YuvToRgbTest.java` (black/white/clipping, reference colors, padded planar
rows, cropped interleaved UV with buffer offsets, unchanged plane positions).

## Coordinator test

Use the existing owned permissive slot-B session with the rebuilt Codec2 HAL
from patch `0009` (k4's original HAL rejects the first v3 CAPTURE buffer), with
other decoder probes stopped. See [the allocation diagnosis](../../../../runs/codec2-capture-agent-20261020.md).
The activity
requests turnScreenOn/showWhenLocked, keeps the screen on during playback and
waits for resumed state plus a valid display Surface. It does not dismiss the
keyguard. No worker runs these phone commands:

```sh
adb install -r /home/user/A9-build/mainline/codec2-playback-20261020/a9-codec2-playback.apk
adb shell am force-stop org.a9.videoprobe
adb shell am start -W -n org.a9.videoprobe/.PlaybackActivity
```

`am start -W` waits for launch only. Allow about10seconds normally, up to40seconds
on failure. The last RGB frame and result remain visible. A launch removes the
old result. Read the fresh result:

```sh
adb logcat -d -s A9VideoProbe:I '*:S'
adb exec-out run-as org.a9.videoprobe cat files/result.json
```

Require `A9_VIDEO_PASS` / `pass:true`, plus:

- `requested_codec`, `codec_name`, `canonical_name` = `c2.v4l2.avc.decoder`,
  `hardware_accelerated:true`, `software_only:false`.
- `presentation:hardware_bytebuffer_to_rgb_surface`.
- `input_samples:60`, `decoded_frames:60`, **`drawn_frames:60`**, both EOS true.
- Normally PTS0..5900000 and output format cropped to visible320×240.

`rendered_callbacks:0` is expected: v3 configures no MediaCodec Surface, so it
reports Canvas posts separately rather than labeling them codec render callbacks.
The app gets actual decoder output Images, converts their pixels and posts them;
it does not display a separately decoded reference. Watch the moving pattern
on the glass to assess physical E Ink output and color/aspect quality.

`A9_VIDEO_SELECTED` and CCodec's named allocation establish the component.
`A9_VIDEO_PROGRESS` reports decoded/drawn counts. A conversion/plane failure
records stage `map_yuv`; a display failure records `present_rgb`; codec waits
retain `decode`. Lifecycle cancellation has a separate `cancel_reason`.
A crash may leave no result. Preserve same-session errors before retrying:

```sh
adb logcat -d -s A9VideoProbe:V CCodec:V CCodecConfig:V Codec2Client:V MediaCodec:V C2BqBuffer:V GraphicBufferAllocator:V V4L2DecodeComponent:V V4L2Decoder:V V4L2Device:V '*:S'
adb shell dumpsys media.codec
```

Check for remaining CPU-readable allocation/map errors, V4L2 queue/STREAMON
errors, service death, EOS stalls or app exceptions. If these occur, retain
Venus/HFI/IOMMU kernel errors through the existing coordinator root session.
Close the app after collecting the result:

```sh
adb shell am force-stop org.a9.videoprobe
```

Host checks passed. On k4, v3 reached graphic allocation but failed at the first
CAPTURE QBUF with zero decoded frames. Patch `0009` corrects the HAL's NV12
allocation stride; playback with that HAL awaits the coordinator's phone test.
V1's sleeping-launch cancellation was fixed in v2; k4 confirmed v2 no longer
cancels. V2's unsupported YUV Surface allocation remains a separate issue.
