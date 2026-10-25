# Surface R2: Venus capture with RGB display buffers

The m image's R1 allocation patch let YUV buffers reach SurfaceFlinger, where
Skia aborted importing AHardwareBuffer format35 (384x256). R1 must not be used
for direct Surface playback. The selected renderer is Mesa freedreno/FD610,
not llvmpipe/kms_swrast: gpu/build-mesa.sh builds only freedreno, and
vendor/bin/a9ml-gpu.sh selects it for the qualified eink variant. Mesa BUILDINFO
pins26.2.5/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3. Earlier captured logcat
explicitly reports `Gralloc doesn't support lock_ycbcr` and `Using fallback
gralloc implementation`.

That revision's [fallback importer](https://github.com/chaotic-cx/mesa-mirror/blob/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/util/u_gralloc/u_gralloc_fallback.c)
requires the missing legacy lock_ycbcr callback for YUV, returning EINVAL before
DRI can import it. RGB uses the first dma-buf fd and pixel stride. Thus stride,
LINEAR modifiers, or marking a YUV layer for client composition cannot repair
this missing metadata path; client composition is exactly where Skia aborted.

The POC changes two vendor libraries:

- `/vendor/lib64/a9-video/libv4l2_codec2.so`: Surface clients retain their own
  BufferQueue for RGBA_8888 output. Venus captures into a separate CPU-readable
  HIDL pooled GRALLOC NV12 allocation, preserving patch0009's padded stride.
  libyuv copies the visible crop into real RGBA bytes. The app still configures
  MediaCodec with its Surface; Gallery/ExoPlayer use the same HAL path.
- `/vendor/lib64/libminigbm_gralloc.so`: vkms refuses YUV texture, scanout and
  render requests, including0x400900, before returning a handle. Private NV12
  decoder/CPU allocation and existing RGB imports remain supported.

No property enables an unsafe YUV fallback. Unsupported Surface allocators,
protected playback, bad layouts and allocation/map/copy failures return codec
errors without publishing a YUV frame. Surface dequeue pressure is retried
asynchronously in FIFO order (16ms, at most125 retries); flush and stop invalidate
pending copies. CPU maps are released before the RGB block is shared. This
removes the observed YUV import crash path; host checks cannot guarantee the
absence of every GPU/system failure. Phone rendering remains unqualified.

The converter deliberately matches the accepted ByteBuffer POC: 8-bit YUV420,
limited-range BT.601. HDR, other color matrices/ranges, performance, protected
video, seek and output-Surface switching remain unqualified. This is hardware
Venus decoding plus CPU color conversion, with additional memory/copy cost.
ByteBuffer BASIC_GRAPHIC handling is retained. Android16's mapper describes
RGBA_8888 as TYPE_RGB/3 channel planes with colInc4 (alpha TODO); both that layout
and explicit RGBA/4-plane metadata are validated, and alpha bytes are filled255.

## Host build

Use existing Codec2 dependency caches and object roots; no kernel rebuild.
`codec2/build.py` replays all patches from pinned archives before compiling.

```sh
R=/home/user/A9-build/mainline/codec2-surface-r2-20261020
python3 -I scripts/mainline/video/codec2/build.py --out "$R/codec2" --overlay "$R/staging"
python3 -I scripts/mainline/video/codec2/tests/surface-rgba.py --codec2-out "$R/codec2"
python3 -I scripts/mainline/video/minigbm/build.py --out "$R/minigbm" --overlay "$R/staging"
mkdir -p "$R/vendor-overlay/lib64/a9-video"
cp "$R/staging/lib64/a9-video/libv4l2_codec2.so" "$R/vendor-overlay/lib64/a9-video/"
cp "$R/staging/lib64/libminigbm_gralloc.so" "$R/vendor-overlay/lib64/"
python3 -I scripts/mainline/android/refresh-integrated-vendor.py --overlay-only \
  --base-vendor /home/user/A9-build/mainline/integrated-20261020m/vendor.img \
  --overlay "$R/vendor-overlay" --out "$R"
sha256sum "$R/vendor.img"
```

This small overlay requires the m base's standalone allocator packaging and
Codec2 dependency closure. Full build-integrated.sh stages those dependencies
normally; do not apply the two-library overlay to a pre-R1 vendor with the old
CF gralloc APEX. The refresh preserves the m kernel/boot and unrelated vendor
pieces. Do not replace shared graphics libraries in a running Android session.

The QEMU converter test calls the production conversion with synthetic mapped
planes (padded Venus NV12, odd planar sizes, clipping/channel-order/alpha and
invalid layouts/no writes). The allocator's actual callbacks run with a mocked
CREATE_DUMB, proving YUV display refusal, private capture layout, RGB support and
protected/other-backend regressions. Original exports and prebuilt mapper/
allocator linkage are checked. These tests open no DRM device and do not run
SurfaceFlinger. The refreshed ext4 image is read back and checked with e2fsck.

## Coordinator phone commands — worker never executes

Boot the refreshed vendor with the unchanged m boot using your existing owned
slot-B procedure. No system image change. Do not try the unsafe old m vendor
again with Surface=true. Reuse the v4 probe (unchanged, SHA256
3a138134e20c844d5c453e1b5cd7d035d87f80eb3830172ed6b249a25bb4c0d5).

```sh
adb install -r /home/user/A9-build/mainline/codec2-surface-20261020/probe/a9-codec2-playback.apk
adb shell am force-stop org.a9.videoprobe
adb shell pidof surfaceflinger
adb shell am start -W -n org.a9.videoprobe/.PlaybackActivity --ez surface true
# Wait about10s; the probe has a30s decode deadline plus render-callback wait.
adb exec-out run-as org.a9.videoprobe cat files/result.json
adb shell pidof surfaceflinger
adb logcat -d -s A9VideoProbe:V DecodeComponent:V VideoFramePool:V C2BqBuffer:V GraphicBufferAllocator:V MESA:V RenderEngine:V '*:S'
```

Expect `A9 Surface R2: private NV12 CAPTURE -> CPU RGBA_8888 BufferQueue`,
`presentation:hardware_direct_surface`,60 decoded/Surface-submitted frames, at least one rendered
callback, both EOS and `pass:true`; drawn_frames stays0. Confirm the SurfaceFlinger
PID stayed the same and no new RenderEngine abort occurred. Physical E Ink
presentation still needs observation. A codec error/no result is a failure, not
permission to rerun against old YUV output. Preserve that evidence for diagnosis.

For the known ByteBuffer regression:

```sh
adb shell am force-stop org.a9.videoprobe
adb shell am start -W -n org.a9.videoprobe/.PlaybackActivity --ez surface false
# Wait about10s, then collect the fresh result.
adb exec-out run-as org.a9.videoprobe cat files/result.json
```

Expect60 decoded/drawn,0 Surface submissions. Re-launching removes the old result.
