# A9 Codec2 Surface allocations — R2

R1 permitted flexible YUV420 format35 + usage0x400900. The m phone test then
crashed SurfaceFlinger in Skia's AHardwareBuffer texture import. **Do not use
R1 for direct Surface playback.** Mesa's selected GPU is freedreno/FD610; the
shipped gralloc fallback has no legacy lock_ycbcr hook, so it cannot describe
YUV planes for EGL native-buffer import. RGB import uses fd/stride directly.

R2 refuses YUV texture/scanout/render allocations on vkms (including0x400900),
rather than letting unsupported handles reach SurfaceFlinger's fatal import.
Private CPU-readable NV12 decoder allocations remain supported. Codec2 patch0010
captures into those buffers and converts to genuine RGBA_8888 buffers from the
client's BufferQueue. RGB, other dumb backends and protected refusal are retained.
See [the R2 build and test instructions](../codec2/SURFACE-R2.md).

This vendor uses Cuttlefish's generic minigbm **vkms** allocator, exporting shmem
dma-bufs, and Mesa's freedreno render driver. DRV_MSM is not enabled in minigbm.
The allocator/GPU topology and Mesa binaries remain unchanged.

Camera preview patch0002 resolves IMPLEMENTATION_DEFINED + camera-write +
texture/composer/render usage to ABGR8888 and enables camera-write on that RGB
combination for vkms. The matching camera HAL postprocessor supplies RGBA bytes
and reports concrete RGBA_8888 to Android/Mesa. The observed Aperture0x20930
allocation is covered by the production-core host test at1280x960. CPU-only
and encoder camera streams retain NV12, as does explicit YUV_420_888. The YUV
display refusal above remains in force. Encoder+display YUV remains unsupported;
applications should use separate preview and encoder streams.

Vkms decoder NV12 storage is aligned to128 bytes for the shared Y/UV stride and
32 Y scanlines. Codec2 patch0009 already requests driver bytesperline/scanlines,
e.g.384x256 for visible320x240. The allocator retains visible dimensions and
uses aligned storage for UV placement. The fixture yields stride384, UV98304,
capacity147456. Larger/page-padded cases are also covered by the host test.

## Build and packaging

```sh
python3 scripts/mainline/video/minigbm/build.py
python3 scripts/mainline/video/playback/build.py --out /home/user/A9-build/mainline/codec2-surface-20261020/probe
```

Default output `/home/user/A9-build/mainline/codec2-surface-20261020` contains
`out/libminigbm_gralloc.so`, `vendor-overlay`, `build-result.json` and evidence.
`--overlay DIR` merges into an integration overlay. Five public source archives
are pinned by commit and content-tree hash in sources.lock.json; `--fetch` can
fetch a missing archive. On this host the archives and the existing Codec2
NDK/header/platform caches suffice offline. The QPR2 minigbm revision retains
all161 old core exports, including `get_plane_fd`; plain android16-release
omitted that ABI required by the existing wrappers.

The CF allocator originally executes inside `com.google.cf.gralloc.apex`, which
contains a private old core. A vendor-only library replacement would not fix
that allocator. The bundle therefore contains:

- Patched `/vendor/lib64/libminigbm_gralloc.so`, also used by the existing v5/v4 mapper.
- The unchanged CF allocator executable at
  `/vendor/bin/hw/android.hardware.graphics.allocator-service.minigbm`.
- Its original CF wrapper dependencies, including `libminigbm_gralloc4_utils.so`,
  under `/vendor/lib64/a9-gralloc` (listed with hashes in build-result.json).
  Only this allocator gets that private LD_LIBRARY_PATH. Vendor-wide
  libhidlbase, allocator-V2, libc++ and other HAL dependencies are preserved.
- Vendor init and allocator V2 VINTF declaration. The service preserves the
  original name, class/user/groups/capability/task profile. It has no automatic
  SurfaceFlinger restart action.

`build-integrated.sh` builds this bundle with the default Codec2 video payload.
`refresh-integrated-vendor.py` recognizes the complete explicit bundle, removes
`apex/com.google.cf.gralloc.apex`, appends the executable file context and assigns
`hal_graphics_allocator_default_exec` to the new service. Incomplete bundles
are refused. The same rule applies to `--overlay-only` updates. This changes
no kernel, Venus firmware, GPU backend selection or SELinux type definitions.
Enforcing/runtime namespace qualification and actual Surface playback remain
phone tests.

Coordinator, to refresh only these graphics files into an existing integrated
vendor (host operation; select your actual base/output directories):

```sh
python3 scripts/mainline/android/refresh-integrated-vendor.py --overlay-only \
  --base-vendor /path/to/current/vendor.img \
  --overlay /home/user/A9-build/mainline/codec2-surface-20261020/vendor-overlay \
  --out /path/to/new/graphics-vendor
```

Keep the existing integrated boot; this fix needs only the next vendor boot.
R2 includes a host-built overlay-only refresh of the m vendor; no worker flashes it. Do not hot-swap these
shared graphics libraries or restart SurfaceFlinger. Probe commands and
interpretation are in [playback/README.md](../playback/README.md).

## Host checks and limits

The build runs QEMU against the actual patched core with a mocked CREATE_DUMB,
no DRM device opens. It covers usage0x400900 refusal, YUV texture/scanout/render rejection, private CPU NV12
and converted RGBA support, protected refusal, RGB and other-backend regressions, and NV12
384x256/raw320x240/640x480/1920x1088/384x96 geometry. It dlopens the existing
prebuilt mapper and uses the Android linker's `--list` to resolve the standalone
allocator without executing its main. No allocator service or Binder/GPU/phone
operation runs on the host. These are allocation/layout and ABI checks, not
hardware decode or composition proof.

A separate small synthetic ext4 fixture exercises the production refresh:

```sh
python3 scripts/mainline/video/minigbm/tests/refresh.py \
  --out /home/user/A9-build/mainline/codec2-surface-20261020/refresh-fixture \
  --overlay /home/user/A9-build/mainline/codec2-surface-20261020/vendor-overlay
```

It verifies exact payload/labels/mode, old fake APEX removal, unrelated templates,
readback and fsck. The fixture mocks only the real-image512MiB free-space reserve;
production disk checks remain unchanged.

Upstream source anchors: pinned minigbm `dumb_driver.c`,
`drv_helpers.c:drv_resolve_format_and_use_flags_helper/drv_dumb_bo_create_ex`,
`msm.c:msm_init/msm_calculate_layout`, and `cros_gralloc_driver.cc`.
[Android MediaCodec](https://developer.android.com/reference/android/media/MediaCodec#setOnFrameRenderedListener(android.media.MediaCodec.OnFrameRenderedListener,%20android.os.Handler))
documents the render callback semantics used by the v4 probe.
