The Android build pins libcamera v0.7.2 (191e202178f02430b5942397c70d215cdd2056fa).
`fetch.py` downloads versioned sources and checks the archive hashes in
`sources.json`. `build.sh` cross-builds the HAL, simple pipeline, CPU SoftISP,
JPEG and EXIF with the cached NDK r27/API30. It is incremental and never builds
Android or the kernel. IPA runs in libcamera's normal isolated proxy process;
no cross crypto library is used.

`0005-a9-variable-frame-duration.patch` is the stock-backed A9 FPS POC. Android
AE ranges map to real FrameDurationLimits. The existing AGC may extend exposure
to the15fps ceiling; the no-FRAME_SYNC pipeline writes VBLANK before increasing
exposure, and exposure before shortening VBLANK. Both A9 drivers retain their
eight-line margin and16x analogue limit. Current frame duration is reported in
capture metadata. Other simple-pipeline sensors retain their fixed timing.
Hi556's nominal30fps mode is about29.72fps with the stock VT timings; Android
advertises its nominal FPS while frame-duration metadata retains the real value.
Core/HAL/IPA/proxy must be packaged together because the IPA configuration
protocol now includes timing limits. The probe retains FLAG_KEEP_SCREEN_ON.

`patches/0001-android-softisp-a9.patch` consolidates the first fifteen patches
from the cached `hisense-a6l-lineageos/device/hisense/a6l/camera/libcamera/patches`
reference plus the A9 S5K3L6/Hi556 gain helpers, provisional sensor properties
and uncalibrated tuning. `patch-origins.json` records the original patch names
and hashes. The carry provides bionic affinity, CPU NV12 debayering, Android
single processed stream/crop/dma-heap allocation, camera order, timing,
native orientation, metadata schema and minigbm plane import. Existing A6L
sensor helpers remain inert on A9. No hardware ISP or autofocus backend is
carried. Source licenses and copyright headers are retained.

`0002-camera-error-result-metadata.patch` keeps partial_result=1 whenever a
capture result contains metadata, including buffer failures, and adds plane
validation diagnostics. The h retry reached post-processing but failed buffer
import; it did not establish a missing sensor frame.

`0003-jpeg-blob-logical-capacity.patch` uses minigbm BLOB width as its byte
capacity, excluding page/stride padding. The JPEG encoder reserves the eight-byte
camera3 trailer, checks the encoded size before copying from libjpeg-owned memory,
and writes a JPEG blob ID/size with memcpy at the logical end (which may be
unaligned). Invalid JPEG markers or payload/trailer overlap return a buffer error.
The simple pipeline also skips unsupported face-detection controls.
Focused Android arm64 replay with cached production HAL objects (no phone):
`python3 -I scripts/mainline/cameras/android/tests/jpeg.py`.
It decodes a 1280x960 JPEG, checks the odd BLOB end and untouched allocation
padding, then covers overflow/reuse, malformed payloads and invalid BLOB handles.

`0004-a6l-3a-statistics-request-lifecycle.patch` adapts verified A6L0016,
0017,0019,0020,0021. `quality-patch-origins.json` pins their hashes and lists
adaptations. It retains A9's JPEG buffer implementation and omits optional0018
resampling. AGC uses the existing sensor limits, a bounded1.5x relative step and
truthful cached AE state; statistics ownership and stop/drain fixes accompany it.
A9 has no frame-start events: controls are written directly and the IPA uses a
per-capture driver-cache snapshot, not an unadvanced delayed-control ring. This
fallback does not establish exact hardware-frame association. Isolated workers
receive fresh statistics storage after stop/configure. A9 does not enable Af.

`package.py` also extracts the exact stock S5K3L6 tuning blob and runs
`../extract-colour.py`. It pins the blob SHA and extracts five non-flash CCMs
with byte offsets; generated YAML/stock-colour.json remain local. The profile
uses fixed black64/1024, gray-world AWB, these CCMs with approximate CCT midpoint
anchors, gamma2.2 and the ported AGC. It is not a CamX tone/colour emulation.
`python3 -I scripts/mainline/cameras/android/tests/quality.py` runs four focused
production IPA/ownership groups under Android arm64 QEMU after package.py.
Init enables the port's first240-frame quality diagnostics for this POC.

The installed minigbm library exposes both stable-C v5 and HIDL v4 factories.
The camera bundle adds the HIDL v4 passthrough manifest and an `*-impl.minigbm.so`
alias of that same binary, leaving the native v5 entry intact. This lets the
Android11 transport retain imported FDs rather than using borrowed HIDL handles.
`liba9-camera-import.so`, preloaded only in the camera service, guards its two
HandleImporter import/free ABI entrypoints. Failed imports clear the borrowed
pointer; close frees only successful imports owned by that importer, once.
The original minigbm mapper still performs allocation/import/free.
Focused host replay (local pipes, no device):
`nix-shell -p gcc --run 'bash scripts/mainline/cameras/android/tests/importer.sh'`.

`package.py` extracts the stock standard HIDL 2.4 provider/legacy camera3
transport; it never installs `camera.qcom.so` or CamX. Its private closure uses
cached VNDK30 interfaces, with current GSI libbinder/libutils/libc++ and LLNDK
dependencies for Android 16 compatibility. Paths and input hashes are recorded
in `transport-sources.json`; these extracted binaries stay outside git. The
libcamera HAL is installed as `camera.default.so`, which the legacy provider's
libhardware fallback loads. Android provider/mapper runtime compatibility
still requires the coordinator's actual boot and preview/photo test.

Camera staging caps the device manifest's FCM target at 8, retaining its HAL
entries and any older target. The GSI declares its framework HIDL manager and
ashmem support with `max-level="8"`. R4's AIDL-only target 202504 filters those
entries out: hwservicemanager disables itself and the stock provider waits
forever for `hwservicemanager.ready`, before loading libcamera. This leaves the
VNDK-less linker configuration and shared platform libraries unchanged.

See `docs/mainline-cameras.md` for commands and limitations.

Camera display previews use patch0006: the internal SoftISP stream remains
NV12, and the HAL scales/converts IMPLEMENTATION_DEFINED GPU/composer outputs
to DRM ABGR8888 (RGBA bytes). It publishes the permitted camera3 format override
to concrete RGBA_8888 so Mesa's fallback importer takes its RGB path. Explicit
YUV, CPU-only and video-encoder destinations keep NV12; JPEG keeps its NV12
source. This requires the accompanying minigbm camera RGB patch0002. Explicit
YUV display allocations remain refused, preserving Surface r2's crash fix.

After the incremental Android build, the host-only production planner and
postprocessor replay covers Aperture preview plus YUV/BLOB still and optional
YUV analysis, full-range BT.601 conversion, channel order and padded buffers:

```sh
python3 -I scripts/mainline/cameras/android/tests/preview.py
```

The replay uses anonymous files in place of dma-bufs. It does not run the
camera pipeline's media-device validation or EGL/SurfaceFlinger. Those remain
coordinator phone tests. No AE/sensor tuning changes accompany preview support.
