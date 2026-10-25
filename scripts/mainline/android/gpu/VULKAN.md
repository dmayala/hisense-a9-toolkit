# A610 Vulkan on msm DRM

GPU images already contained Mesa's `lib64/hw/vulkan.adreno.so`, built alongside
freedreno GLES. They selected SwiftShader with `ro.hardware.vulkan=pastel` and
also installed `com.google.cf.vulkan.apex`. Its bootstrap init set `ro.vulkan.apex`
and its permissions advertised Vulkan1.3, hardware level1 and a dEQP level. The
Turnip file was not the selected Vulkan implementation. Integ24's vendor image
confirms this layout; its Turnip SELinux label is `same_process_hal_file`.

GPU builds now select `ro.hardware.vulkan=adreno`, require the existing Turnip
member in Mesa's SHA256SUMS, and verify that the vendor's EGL/GLES/Gallium bytes
come from that same build. They remove the Cuttlefish Vulkan APEX and its CPU/
APEX selector properties. The HAL keeps the same_process_hal_file label used by
EGL. Fresh `build-vendor.sh --gpu`, integrated vendor refresh and incremental
`reboot/build14.py` all consume the shared `gpu/vulkan.py` payload. The incremental
installer accounts explicitly for the removed APEX and newly added feature XMLs.
No stock KGSL blob, kernel change, firmware rebuild or new Mesa compilation is
needed: the existing Mesa recipe uses `-Dvulkan-drivers=freedreno` and
`-Dfreedreno-kmds=msm`.

## A610 feature declarations

The local build's BUILDINFO pins Mesa26.2.5 to
`ddaf85e353442d9d7f192fdcf1823e652e3f2ee3`, tree
`530997e99b40566a33cc61d22b921c08873dd65e`, clean source. Its capability declarations
are more specific than the generic Turnip Vulkan1.3 description:

- [freedreno_devices.py at the built revision](https://chromium.googlesource.com/chromiumos/third_party/mesa/+/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/freedreno/common/freedreno_devices.py):
  A610 at294 inherits a6xx_gen1_low at297; that group has has_hw_multiview=false
  at170.
- [tu_device.cc at the built revision](https://chromium.googlesource.com/chromiumos/third_party/mesa/+/ddaf85e353442d9d7f192fdcf1823e652e3f2ee3/src/freedreno/vulkan/tu_device.cc):
  tu_has_multiview at179 uses that hardware flag unless TU_DEBUG=noconform is
  requested. Physical-device apiVersion at1296 returns Vulkan1.0 without it,
  despite the driver's global instance-version support. No noconform override is
  installed. Features at425-462 include ETC2 and all Android hardware-level1
  requirements, including geometry/tessellation on chips other than A702.
- [Android's hardware-level definition](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/java/android/content/pm/PackageManager.java):
  level1 requires fullDrawIndexUint32, imageCubeArray, independentBlend,
  geometryShader, tessellationShader, sampleRateShading, ASTC_LDR,
  fragmentStoresAndAtomics, shaderImageGatherExtended and uniform-buffer/sampled-
  image array dynamic indexing. All are enabled for A610 by the source above.

The vendor therefore installs the standard AOSP declarations:
`android.hardware.vulkan.level=1` and
`android.hardware.vulkan.version=4194307` (Vulkan1.0.3/0x400003). It does not claim
Vulkan1.1/1.3, a dEQP level or CPU Vulkan from the removed Cuttlefish APEX. Source
support is not a CTS result. Recheck these declarations when changing Mesa's
pinned revision; a newer driver's generic instance version is not the A610
physical-device version.

[Android's loader documentation](https://source.android.com/docs/core/graphics/implement-vulkan)
describes both the vendor HAL selector and the separate APEX selector; changing
only the filename or property would have left the inherited APEX feature claims.
The current GSI's system permissions do not add Vulkan declarations.

## Host checks

Run `python3 -B scripts/mainline/android/gpu/test-vulkan.py`.

Four focused groups cover property/XML selection, same-build provenance and
missing-driver refusal, fresh staging plus both real ext4 incremental/refresh
paths with metadata/readback, and actual Android16 bionic/QEMU RTLD_NOW loading
of the existing Turnip HAL. The last test checks its public Vulkan HMI entry;
it never creates an instance, opens DRM or submits GPU work. The temporary
filesystems contain only synthetic configs and Mesa libraries and are removed
by the test.

The existing built-image `android/tests/test-gpu-egl-props.sh` now checks Turnip
selection, driver presence, these XMLs, and absence of the Cuttlefish Vulkan
APEX/CPU claim. Physical-device enumeration, presentation, compute/render output
and Android CTS remain untested until a coordinator-owned phone run. No hardware
PASS is inferred from these host checks. Non-GPU software images retain their
existing SwiftShader selection.
