# Vulkan on A16 (Hisense A9 / HLTE556N) — claim check

**Status (2026-10-11):** section 8 adds a real-app test (PPSSPP falls back to GL, no crash). Sections 1-6 are offline research; section 7 holds the on-device
measurements (booted A16 GSI), which confirm the mechanism but show NO crash. Supersedes the Vulkan section of
`a16-gsi-port-plan.md` (section 5) where they differ — see 2.4.

Labels: **MEASURED** = file + command in the appendix. **SOURCED** = URL.
**INFERRED** = reasoning from the above, not observed.

## 0. The claim and its split

> "There seems to be a problem with A16 concerning Vulkan support. A16 mandates
> Vulkan and the VNDK does not seem to have it, so applications crash when they
> attempt to use it."

| # | Sub-claim | Verdict |
|---|---|---|
| C1 | A16 mandates Vulkan | PARTLY (for devices launching on A16 only; we are an A11-launch upgrade) |
| C2 | "The VNDK doesn't have it" | FALSE as stated (the loader is in system; the ICD is a vendor file). The real gap is a missing 64-bit vendor ICD |
| C3 | Apps crash when they use it | PARTLY / UNVERIFIED: apps needing Vulkan fail at device enumeration; the system UI should not crash because HWUI/SF default to GL here. Per-app crashes not measured |

## 1. Vendor side (MEASURED, stock HLTE556N L2037.6.01.01, Android 11, SDK 30)

Source: `super_1..5.img` from `$A9_DATA/HLTE556N`, reassembled at the sector
offsets in `_flash_super.xml`, `lpunpack`, then `debugfs -R` read-only on
`vendor_a.img` (appendix A). The golden EDL `super.bin` was not needed.

### 1.1 Vulkan driver files per ABI

| Path | Present? | Notes |
|---|---|---|
| `/vendor/lib/hw/vulkan.adreno.so` (32-bit, ELF EM=40 ARM) | **YES**, 1,449,512 B | |
| `/vendor/lib64/hw/vulkan.*.so` | **NO** (full `ls` of `/vendor/lib64/hw`: 57 entries, none vulkan; also no `vulkan.default.so`) | |
| `/vendor/lib/libVkLayer_q3dtools.so`, `/vendor/lib64/libVkLayer_q3dtools.so` | yes both (1,686,080 / 2,364,696 B) | Qualcomm profiling layer, not an ICD |
| `/vendor/lib*/libvulkan*` | none | libvulkan is a system lib |
| `/vendor/lib64/egl/*adreno*` and `/vendor/lib/egl/*adreno*` | yes both: eglSubDriverAndroid, libEGL_adreno, libGLESv1_CM_adreno, libGLESv2_adreno, libq3dtools_* | GLES 64-bit and 32-bit complete |

### 1.2 Driver version strings (MEASURED, python string extraction)

32-bit `vulkan.adreno.so`: "Qualcomm Technologies Inc. Adreno Vulkan Driver",
"Driver Build: 704ecd9a2b, Ib3f3e69395, 1609240670", "Date: 12/29/20",
"Compiler Version: EV031.32.02.06". It exports `vkEnumerateInstanceVersion`,
`vkBindImageMemory2`, `vkGetPhysicalDeviceFeatures2`, `vkCreateRenderPass2`,
`VK_KHR_maintenance1-3`, `VK_ANDROID_native_buffer`,
`VK_ANDROID_external_memory_android_hardware_buffer`,
`vkGetSwapchainGrallocUsage2ANDROID` (238 `vk*` strings) -> consistent with
Vulkan 1.1 (INFERRED; the apiVersion constant was not decoded).
64-bit `libGLESv2_adreno.so` carries the identical build: "V@0502.0 (GIT@704ecd9a2b,
Ib3f3e69395, 1609240670) (Date:12/29/20)" — i.e. the same Adreno driver drop
(Dec 2020) is shipped for GLES in 64-bit, only the Vulkan half is missing.

### 1.3 Properties and feature XML (MEASURED)

| Item | Value |
|---|---|
| `ro.product.first_api_level` | 30 (vendor `build.prop`) |
| `ro.vendor.build.version.sdk`, `ro.vndk.version` | 30, 30 |
| `ro.board.first_api_level`, `ro.vendor.api_level` | not set in vendor `build.prop`/`default.prop` |
| `ro.board.platform` / `ro.product.board` | bengal |
| `ro.hardware.vulkan`, `ro.hardware.egl` | **not set anywhere**: vendor, odm, stock system, system_ext, product props, vendor `etc/init/*.rc` (no string hit on the whole vendor image except the file name `vulkan.adreno.so`) |
| `ro.hwui.use_vulkan` | present in vendor build.prop **with an empty value** |
| `ro.hwui.*` (stock system) | only cache sizes; `persist.sys.force_sw_gles=1` (stock system build.prop; effect on A16 GSI unknown) |
| `ro.surface_flinger.*` (vendor default.prop) | wide color, HDR, color management, touch timer etc.; nothing renderer related |
| `vendor.opengles.version` | 196610 (GLES 3.2) |
| `/vendor/etc/permissions/android.software.vulkan.deqp.level.xml` | present, version 132383489 = 0x07E40301 = deqp level 2020-03-01 |
| `/vendor/etc/permissions/android.hardware.vulkan.level/version/compute.xml` | **absent** (only `android.hardware.opengles.aep.xml`, `deqp.level`, `sensor_features.xml`). Stock system image also has no such xml |

Open question (not resolved): with `ro.hardware.vulkan` unset, how does the
loader find the file named `vulkan.adreno.so`? Per source.android.com the
lookup is `vulkan.<ro.hardware.vulkan>.so` with `ro.board.platform`
("bengal") as alternative; `vulkan.bengal.so` does not exist. The same applies
to `ro.hardware.egl` and `libEGL_adreno`. Something outside the property files
we searched must set these on stock, or the loader has fallbacks we have not
read. Port-plan section 5 reports `ro.hardware.vulkan` empty on the booted A16.
**Resolved on device (section 7): the loader only tries `vulkan.bengal.so`; the 32-bit ICD is NOT selected on A16.**

## 2. System side (MEASURED, `a9-los23.2-dozeoff-gapps-20260603-hwcguard-v2.img`, SDK 36)

### 2.1 Loader and GLES
- `/system/lib64/libvulkan.so` 223,704 B and `/system/lib/libvulkan.so` 150,752 B: **present** (the Vulkan loader is system, not vendor).
  Its strings include `ro.hardware.vulkan`, `ro.board.platform`, `ro.vulkan.apex`,
  `debug.vulkan.layers`, "updatable gfx driver".
- ANGLE: `libEGL_angle.so`, `libGLESv2_angle.so`, `libGLESv1_CM_angle.so` in both lib and lib64. No ANGLE apk in `/system/app` or `priv-app`; no vulkan/angle APEX in `/system/apex`.
- No SwiftShader (`libvk_swiftshader.so` absent from `/system/lib64`); no vulkan xml under `/system/etc/permissions`; no vulkan in `/system/etc/vintf/manifest.xml`.

### 2.2 Properties
`/system/build.prop`, `system_ext`, `product/etc/build.prop`: **no**
`ro.hwui.use_vulkan`, `debug.hwui.renderer`, `ro.gfx.angle*`, `persist.graphics.*`.
Only `ro.surface_flinger.uclamp.min=180`, `game_default_frame_rate_override=60`,
`supports_background_blur` (0 system, 1 product).

### 2.3 Scripts
`rw-system.sh`, `phh-on-boot.sh`, `vndk.rc`: no mention of Vulkan or SwiftShader.
Graphics lines in `rw-system.sh`: `setprop debug.hwui.renderer opengl` only for
PowerVR/msm8917-8940 devices (not us; our board is bengal); `debug.renderengine.backend skiaglthreaded`
only for Redmi lancelot/galahad. `phh-prop-handler.sh` merely validates a user-set
`debug.renderengine.backend` (skiagl, skiaglthreaded, skiavk, skiavkthreaded).
The Doze-off/our patcher set nothing Vulkan-related that we can find.

### 2.4 Consequence for the existing note
Port-plan says `/vendor/lib/hw/vulkan.adreno.so` EXISTS and `/vendor/lib64/hw/vulkan.*.so`
does not. **Confirmed** by the stock vendor (1.1) at firmware level. The plan's
"cause of XDA #3325" and "every 64-bit app that requires Vulkan will fail" are
inference, not a crash measurement; see section 4.

## 3. What Android 16 requires (SOURCED)

- **CDD**: Android 16 CDD section 2.2.1 cites 7.1.4.2/H-1-1 ("If handheld device
  implementations include support for Vulkan ... MUST satisfy the Android Baseline 2021
  profile") and, in a block "requirements added in Android 16", 7.1.4.2/H-2-1: handhelds
  declaring a 64-bit ABI and not low-RAM "MUST support Vulkan 1.1".
  https://source.android.com/docs/compatibility/16/android-16-cdd (fetched via
  summarizer; the 7.1.4.2 body itself was not read verbatim — verify). CDD applies to
  devices **launching** with that release; upgraded devices are measured against their launch CDD
  (INFERRED, standard CDD practice). Our device launched with Android 11 (`first_api_level` 30)
  and is not a certified A16 device at all; a GSI is outside CDD/CTS.
- **Vulkan version table**: "Devices that launch with Android 16 and higher must support
  Vulkan 1.4"; ICDs live in `/vendor/lib/hw` and `/vendor/lib64/hw`, named
  `vulkan.<ro.hardware.vulkan>.so` (alternative `ro.board.platform`); `ro.vulkan.apex` since A15.
  https://source.android.com/docs/core/graphics/implement-vulkan
- **HWUI default** (android16-release `libs/hwui/Properties.cpp`, `peekRenderPipelineType()`):
  `bool useVulkan = use_vulkan().value_or(false);`
  `base::GetProperty(PROPERTY_RENDERER, useVulkan ? "skiavk" : "skiagl")`; only the string
  `"skiavk"` selects `SkiaVulkan`, anything else is `SkiaGL`. `PROPERTY_RENDERER` is
  `debug.hwui.renderer` (`Properties.h`).
  https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/libs/hwui/Properties.cpp
  `use_vulkan()` comes from the generated `HWUIProperties.sysprop.h` (the `ro.hwui.use_vulkan`
  sysprop; the sysprop file itself 404'd, so that mapping is INFERRED). An unset prop
  therefore yields **GL**. The default is turned on by device makefiles. A 2023 change
  ("libhwui: Use Vulkan by default", via a mirror,
  https://gerrit.omnirom.org/plugins/gitiles/android_frameworks_base/+/511cca95282355a498f59d248b42104453fe82ea
  was not retrievable here) is not reflected in the A16 code read.
  The vendor's empty `ro.hwui.use_vulkan=` is consistent with this (an empty value is not true).
  Our GSI `libhwui.so` has the `skiavk`, `skiagl`, `debug.hwui.renderer` strings and no
  `ro.hwui.use_vulkan` string (read through the sysprop lib).
- **No runtime fallback skiavk -> GL** was found in the code read: HWUI chooses once at
  start-up; if Vulkan init fails inside a skiavk process, that process can fail (INFERRED,
  not read in RenderThread). Defaulting to GL avoids the question here.
- **SurfaceFlinger RenderEngine** (android16-release `SurfaceFlinger.cpp`,
  `chooseRenderEngineType`): `debug.renderengine.backend` in {skiagl, skiaglthreaded, skiavk,
  skiavkthreaded} forces it; else `useVulkan = useGraphite || (FlagManager::vulkan_renderengine()
  && RenderEngine::canSupport(kVulkan))`, then `GL` otherwise. So Vulkan RE needs the aconfig flag
  AND a working Vulkan. No runtime fallback from a forced skiavk was found (summarizer read only
  first 100k of 574k chars; INFERRED).
  https://android.googlesource.com/platform/frameworks/native/+/refs/heads/android16-release/services/surfaceflinger/SurfaceFlinger.cpp
  Measured: port-plan reports `vulkan_renderengine: false` on the booted device; our
  `/system/bin/surfaceflinger` contains `vulkan_renderengine`, `debug.renderengine.backend` handling,
  and "Vulkan 1.1 is required but not available".
- **ANGLE as GLES default**: not found in the sources read. Apps and the system still use the vendor GLES
  here (ANGLE libs are present in system but the 64-bit ANGLE-on-Vulkan path would need a Vulkan ICD anyway).
  Not researched deeper (INFERRED/unknown).

## 4. Verdicts

**C1 "A16 mandates Vulkan" — PARTLY.** The A16 CDD makes Vulkan 1.1 a MUST for handhelds with a
64-bit ABI that launch on A16 (7.1.4.2/H-2-1), and Vulkan 1.4 for A16-launch hardware; Vulkan is
otherwise optional ("if ... include support for"). A9 launched on A11, and a GSI is not a CDD device.
The platform itself does not hard-fail without Vulkan (HWUI and RE default to GL when the flags are off).

**C2 "the VNDK does not have it" — FALSE as worded.** VNDK is a set of system libs for vendor
modules; Vulkan has nothing to do with it. The loader (`libvulkan.so`) is in /system and is present in
both ABIs. The ICD is a *vendor* file. The measured fact is: **stock vendor has no 64-bit
`vulkan.*.so`** (only 32-bit `vulkan.adreno.so`). That is a vendor-image gap, independent of the
GSI choice or the A16 version (it was equally absent on A11 stock).

**C3 "apps crash when they attempt to use it" — see section 7 (MEASURED: graceful 0 devices for loader-level use; crash only for apps that ignore it).**
In a 64-bit process the loader finds no ICD, so `vkEnumeratePhysicalDevices` returns 0 devices /
`vkCreateInstance` returns an error (INFERRED from loader design). Well-behaved apps and engines
(Unity, Unreal, Godot, Chrome/Skia) check this and fall back to GLES; apps that assume Vulkan or
hard-require it will abort or crash (specific games/apps only). System UI should not be
affected: HWUI is `skiagl` unless `debug.hwui.renderer=skiavk`, and SurfaceFlinger RE is GL with the
flag off. The same condition already applied to the A11/A14/A15 builds on this device, so this
is not an A16 regression unless an A16-specific component (flagged Vulkan RE, `ro.hwui.use_vulkan`
in a different GSI) opts into Vulkan. A 32-bit app would be the only consumer able to see a real
Vulkan ICD, if the loader can resolve the name (see 1.3 open question).

## 5. Open items
1. How `vulkan.adreno.so` is resolved with `ro.hardware.vulkan` unset (1.3).
2. Whether `persist.sys.force_sw_gles=1` in stock props still has any effect (it is not in the GSI props).
3. Actual app behaviour on A16 (no crash measured yet).
4. Real Vulkan apiVersion of the 32-bit driver.
5. 7.1.4.2 verbatim and the HWUI sysprop name were not retrieved.

## 6. On-device test plan (for later; phone must be booted to Android, USB adb)

### 6.1 State and properties
```sh
adb shell getprop ro.hardware.vulkan; adb shell getprop ro.hardware.egl; adb shell getprop ro.board.platform
adb shell getprop ro.hwui.use_vulkan; adb shell getprop debug.hwui.renderer
adb shell getprop debug.renderengine.backend; adb shell getprop ro.zygote
adb shell getprop ro.product.first_api_level; adb shell getprop ro.vendor.api_level
adb shell ls -l /vendor/lib/hw/vulkan.* /vendor/lib64/hw/vulkan.*
adb shell pm list features | grep -i -E 'vulkan|opengles'
adb shell dumpsys SurfaceFlinger | grep -i -E 'renderengine|backend|GLES|vulkan'
adb shell dumpsys gpu | head -60
adb shell cmd gpu vkjson | head -50          # may not exist; try also: adb shell cmd gpu vkjson | wc -c
adb shell dumpsys gfxinfo com.android.systemui | grep -i -E 'pipeline|Vulkan|OpenGL'
```
### 6.2 Is the 32-bit ICD loadable, and why not 64-bit
```sh
adb logcat -b all -d | grep -i -E 'vulkan|hwvulkan|libvulkan|adreno|hw_get_module|vkCreateInstance'
adb shell getprop | grep -i -E 'vulkan|egl|gfx|hwui'
adb shell ls -lZ /vendor/lib/hw/vulkan.adreno.so
```
### 6.3 App test
- Install "Vulkan Hardware Capability Viewer" or vkcube (arm64 and, separately, armv7 build).
  Run `adb shell am start ...` and check `adb logcat -c; ... ; adb logcat -d`.
  Expected: 64-bit build reports 0 devices; 32-bit build may list Adreno 610 (if resolved).
- Compare with a known GLES-only app; then a Vulkan-preferring game/browser benchmark.
- Optional: `adb shell setprop debug.hwui.renderer skiavk` then restart `adb shell stop; adb shell start`
  (do not leave it set) to see whether HWUI really fails without a 64-bit ICD.
### 6.4 Crash patterns
```sh
adb logcat -b crash -d
adb logcat -d | grep -E 'FATAL EXCEPTION|SIGABRT|SIGSEGV|libvulkan|vkCreate|VK_ERROR|Failed vulkan call|DEVICE_LOST|INCOMPATIBLE_DRIVER|swapchain'
adb shell dumpsys dropbox --print data_app_native_crash | head -80
```
After tests: force-stop the app, sleep the Thor (per MEMORY).

### 6.5 Mitigations to try only if a real regression shows
`persist.graphics.vulkan.disable`/ANGLE and SwiftShader are untested here; SwiftShader
is absent from this GSI (2.1), so it would need an added 64-bit ICD plus `ro.hardware.vulkan`.

## 7. On-device results (MEASURED 2026-10-11)

The A9, booted Android 16 GSI (`ro.build.version.release=16`, `ro.zygote=zygote64_32`), adb + su read-only.
No props were changed, SurfaceFlinger/framework not restarted.

**Deviation from the plan (APK):** SaschaWillems/VulkanCapsViewer has NO prebuilt APK anywhere (all 30+ GitHub
releases have zero assets; README: "The Android apk needs to be built from Qt Creator"; no F-Droid package; no
Actions artifacts). KhronosGroup/Vulkan-Tools, Vulkan-Samples and SaschaWillems/Vulkan releases also have no APKs. Instead a
minimal probe was built on the server with the nixpkgs Android NDK 27 toolchain (`pkgsCross.{aarch64,armv7a}-android-prebuilt`,
Vulkan-Headers 1.4.321): `dlopen("libvulkan.so")`, `vkCreateInstance(apiVersion 1.1)`, `vkEnumeratePhysicalDevices`. Built once for
arm64-v8a (sha256 2289eb37...ec835) and armeabi-v7a (sha256 6df44945...9ae1). They were run as native executables from
`/data/local/tmp` as uid shell (no app was installed; nothing to uninstall; binaries deleted afterwards). Same bionic
linker/libvulkan/ICD lookup as an app (`android_load_sphal_library`), but NOT an app process: no app Java/Skia/engine
fallback logic was exercised, and the app SELinux domain was not used.

### 7.1 State
```
getprop: ro.hardware.vulkan='' ro.hardware.egl='' ro.board.platform=bengal ro.hwui.use_vulkan='' debug.hwui.renderer='' debug.renderengine.backend=''
ls: /vendor/lib64/hw/vulkan.*: No such file or directory
    -rw-r--r-- 1 root root 1449512 2009-01-01 00:00 /vendor/lib/hw/vulkan.adreno.so
pm features: android.hardware.opengles.aep ; android.software.vulkan.deqp.level=132383489   (no android.hardware.vulkan.level/version)
dumpsys SurfaceFlinger: "RE GLES (Ganesh)"; GLES: Qualcomm, Adreno (TM) 610, OpenGL ES 3.2 V@0502.0 (GIT@704ecd9a2b, Ib3f3e69395, 1609240670) (Date:12/29/20); graphite_renderengine_preview_rollout: false
dumpsys gpu: driverPackageName = system; glLoadingCount = 2; glLoadingFailureCount = 0; angleLoadingCount = 0; vkLoadingCount = 0; vkLoadingFailureCount = 0;
             vulkanVersion = 0; cpuVulkanVersion = 0; glesVersion = 196610; per-app (launcher3, settings): createdVulkanDevice = 0, vulkanApiVersion = 0x0
cmd gpu vkjson (shell and via su, same): apiVersion 4210688 (= 1.3.x loader), "devices" : [], "deviceGroups" : [], instance extensions only
             (VK_KHR_surface, VK_KHR_android_surface, ... VK_EXT_debug_report), "layers" : []     (not denied)
dumpsys gfxinfo com.android.systemui: Pipeline=Skia (OpenGL)
```
Logcat (`-b all -d`, vulkan/libvulkan grep; every boot-time hit is the generic nativeloader public-library list, plus):
```
10-11 17:09:04.074  1648  1695 E vndksupport: Could not load vulkan.bengal.so from sphal namespace: dlopen failed: library "vulkan.bengal.so" not found.
```
(pid 1648 is a system service, from `cmd gpu vkjson`. No `hwvulkan`/`hw_get_module` lines.)

### 7.2 Probe results
```
64-bit: dlopen libvulkan.so ok; vkCreateInstance=0 (VK_SUCCESS); vkEnumeratePhysicalDevices=0 count=0; exit=0
        logcat: E vndksupport: Could not load vulkan.bengal.so from sphal namespace: dlopen failed: library "vulkan.bengal.so" not found.
32-bit: dlopen libvulkan.so ok; vkCreateInstance=0 (VK_SUCCESS); vkEnumeratePhysicalDevices=0 count=0; exit=0
        logcat: E vndksupport: Could not load vulkan.bengal.so from sphal namespace: ... (identical)
logcat -b crash: empty. No SIGSEGV/SIGABRT/FATAL EXCEPTION.
```
Loader lookup: with `ro.hardware.vulkan` empty it tries only `vulkan.<ro.board.platform>.so` = `vulkan.bengal.so`, in the sphal
namespace, in BOTH ABIs. `vulkan.adreno.so` is never tried, so the 32-bit ICD is never loaded: **32-bit Vulkan does NOT
work either** (my expectation in section 4 was wrong). Android 11 stock loaded it from some route we did not find (offline notes
in 1.3; plausible that stock sets ro.hardware.vulkan from a source other than the property files we searched). Not tested:
whether setting `ro.hardware.vulkan=adreno` would make the 32-bit ICD load (forbidden persistent prop change).

### 7.3 Verdicts
| Claim | Verdict | Evidence |
|---|---|---|
| A16 mandates Vulkan | PARTLY (unchanged) | CDD only for A16-launch devices; the A11-launch device boots fine without it |
| "VNDK doesn't have it" | FALSE as worded | `libvulkan.so` loads in both ABIs; the missing part is the vendor ICD (no 64-bit file; 32-bit file not selected) |
| Apps crash when they attempt Vulkan | NOT CONFIRMED at API level: degrades gracefully | `vkCreateInstance` succeeds, 0 physical devices, no crash, empty crash buffer, in both ABIs. Apps that do not handle 0 devices can still crash (not tested; no real app was run) |
| Crash from the system UI | NO | SystemUI `Pipeline=Skia (OpenGL)`, SF RenderEngine GLES (Ganesh), `vkLoadingFailureCount = 0` |
| 32-bit ICD gives Adreno 610 Vulkan | NO | only `vulkan.bengal.so` is tried (same log line for 32-bit) |
| Real Vulkan available to any app here | NO | `vkjson` devices `[]`, `vulkanVersion = 0`, no `android.hardware.vulkan.*` feature |
| Fix candidates | UNTESTED | a vendor/overlay `vulkan.bengal.so` (copy/symlink) in 64-bit would need a 64-bit Adreno Vulkan driver (none exists on stock); 32-bit only via `ro.hardware.vulkan=adreno` |

## 8. Real apps (MEASURED 2026-10-11)

Same device/build as section 7 (A16 GSI, no props changed, SF/framework untouched). Apps installed with default ABI via `adb install`,
launched with monkey, uninstalled afterwards. Panel contents via `scripts/epd-snap.sh` (the phone is in ROTATION_90 for landscape apps;
the panel buffer shows them rotated, touch coordinates are in landscape space).

### 8.1 Vulkan-required app: Eden (SKIPPED at the user's request, one incidental observation)
Eden v0.2.1 "standard" (https://stable.eden-emu.dev/v0.2.1/Eden-Android-v0.2.1-standard.apk, versionName `1f6734c`, pkg
`dev.eden.eden_emulator`, sha256 `befbf70b1a05715811fe092ef9cf71e6f1c1672008a6c5caa31d633e99615668`, lib/ = arm64-v8a only) had already been
installed when the user said to skip it. Two launches (15 s and 10 s) both died in `Application.onCreate`, before any renderer/native code:
```
E AndroidRuntime: FATAL EXCEPTION: main   Process: dev.eden.eden_emulator
E AndroidRuntime: java.lang.RuntimeException: Unable to create application org.yuzu.yuzu_emu.YuzuApplication
E AndroidRuntime: Caused by: java.lang.NullPointerException
E AndroidRuntime: 	at coil.util.-Collections.start(SourceFile:18)
E AndroidRuntime: 	at org.yuzu.yuzu_emu.YuzuApplication.onCreate(SourceFile:15)
```
No `vulkan` log lines in the app process, no signal. This is a Java NPE at startup, NOT evidence about Vulkan (cause not investigated).
Uninstalled. So: no Vulkan-required app was actually tested; that half of the question remains OPEN.

### 8.2 Vulkan-optional app: PPSSPP
- PPSSPP 1.20.4, https://www.ppsspp.org/files/1_20_4/ppsspp.apk, pkg `org.ppsspp.ppsspp`, sha256
  `dd702a31270ecd68db37afee311d6efe08b1d4da9601d2750ce1a73bbd316c3f`; lib/ has arm64-v8a, armeabi-v7a, x86_64. Installed ABI:
  `primaryCpuAbi=arm64-v8a`, `secondaryCpuAbi=null`.
- Result: NO crash, process alive after 15 s and after navigating (`pidof` set), `logcat -b crash` empty. It reached the welcome screen and
  main menu (panel snapshots show the UI correctly), rendering with the GL backend (Adreno GLES lines in logcat).
- Settings > Graphics > Backend: the picker lists only **OpenGL** (checked). Vulkan is not offered at all (not greyed out, no message), so no
  restart/fallback notice appears: PPSSPP probes Vulkan at startup and hides it when it is unusable.
- Evidence (app pid 6612):
```
E vndksupport: Could not load vulkan.bengal.so from sphal namespace: dlopen failed: library "vulkan.bengal.so" not found.
E PPSSPP  : [G3D] VulkanMayBeAvailable: No physical Vulkan devices (count = 0).
dumpsys gfxinfo org.ppsspp.ppsspp: Pipeline=Skia (OpenGL)
dumpsys gpu: vkLoadingCount = 1, vkLoadingFailureCount = 1, vulkanVersion = 0
```
  (no FATAL EXCEPTION, SIGABRT, SIGSEGV or VK_ERROR lines in the whole log.)

### 8.3 Updated verdict
| Claim | Verdict |
|---|---|
| Apps crash when they attempt to use Vulkan, Vulkan-OPTIONAL apps | NOT CONFIRMED / contradicted by PPSSPP: it detects 0 physical devices, hides the Vulkan backend and runs on OpenGL ES. Matches the section 7 probe (`vkCreateInstance` OK, 0 devices) |
| Same, Vulkan-REQUIRED apps | NOT TESTED (Eden skipped, and it crashed for an unrelated Java NPE). Expected from 0 devices: an error or exit chosen by the app (apps that do not check the device count could crash); unverified |
| Practical effect today | Vulkan is simply absent for apps (`vkjson` devices `[]`); GL apps and the system UI are unaffected |

## Appendix: reproduction commands (all read-only)
```sh
# A. vendor extraction
mkdir -p ~/A9-build/vulkan && cd ~/A9-build/vulkan; i=1
for s in 225032 225288 1268744 1369864 1416456; do   # start sectors from _flash_super.xml
  dd if=$A9_DATA/HLTE556N/super_$i.img of=super.raw bs=4096 seek=$((s-225032)) conv=notrunc,sparse; i=$((i+1)); done
lpunpack super.raw out        # (super_*.img are RAW chunks, not Android sparse: simg2img fails)
debugfs -R "ls -l /lib/hw"   out/vendor_a.img | grep -i vulkan
debugfs -R "ls -l /lib64/hw" out/vendor_a.img | grep -i vulkan     # empty
debugfs -R "dump /lib/hw/vulkan.adreno.so x/vulkan.adreno32.so" out/vendor_a.img
python3 -I strs.py x/vulkan.adreno32.so 'Driver Build|Date:|Compiler' 10   # strs.py = printable-string grep (no `strings` on this host)
debugfs -R "cat /build.prop" out/vendor_a.img | grep -i -E 'vulkan|hwui|first_api|opengles'
debugfs -R "ls -l /etc/permissions" out/vendor_a.img | grep -i -E 'vulk|opengl'
# B. GSI
G=$A9_DATA/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v2.img
debugfs -R "ls -l /system/lib64" $G | grep -i -E 'libvulkan|angle'
debugfs -R "cat /system/build.prop" $G | grep -i -E 'vulkan|hwui|renderer|angle|surface_flinger'
debugfs -R "dump /system/bin/rw-system.sh g/rw-system.sh" $G; grep -n -i -E 'vulkan|hwui|renderengine' g/rw-system.sh
# C. sources
# android16-release: frameworks/base libs/hwui/Properties.cpp ; frameworks/native services/surfaceflinger/SurfaceFlinger.cpp
```
Scratch files live in `~/A9-build/vulkan` (about 6 GB of sparse raw `super.raw` and `out/*.img`; delete when done).
