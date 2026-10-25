# Image on the panel at power off

**Date:** 2026-10-13
**Status (2026-10-14):** installed on the phone (a9service 3.9.0, versionCode 48, `a16-port` `59314b1`, built on 3.8.8); preview verified on the glass with `epd-snap`. **Real power-off verified 2026-10-14** (user, USB unplugged): the image stays on the glass. With USB attached the phone enters off-mode charging and the bootloader splash replaces it. The text below was written 2026-10-13 against the first build `fbed691` (versionCode 43, on 3.8.3), which was superseded because the phone already ran 3.8.8.
`poweroff-image` in `work-gsi/A9PatchingService` (commit `fbed691`, a9service
**3.9.0**, versionCode 43), local only, not pushed. A local debug build compiles
(`assembleDebug`, exit 0). It is not signed with the release key: CI does that.
Labels: **MEASURED** means read from the stock firmware (file + tool named);
**INFERRED** means deduced and not checked on the device.

## Goal

When the A9 switches off, the bistable E Ink panel should keep a chosen image
(stock showed a Hisense logo with "DEVICE POWERED OFF"), not whatever app frame
was on screen when power was cut.

## Verdict

| Route | Feasible on our GSI? | Why |
|---|---|---|
| (a) vendor HWC bitmap path (`sendImageData`) | **Possible in principle, not used** | The stock vendor composer still has the whole path, and any process that can reach the composer can call it (the client is a singleton). But it needs a HIDL client with `hidl_memory`, running as root or as SurfaceFlinger. A mistake there can crash the composer, which takes SurfaceFlinger down. A SurfaceFlinger restart is the permanent-blank hazard (CLAUDE.md rule 11) |
| (b) epd sysfs nodes alone | **No** | `epd_commit_bitmap` carries no pixels. It only makes the kernel send `EPD_COMMIT=1`, and the HWC then re-commits the bitmap it already holds. On the GSI nobody has sent it one |
| (c) a9service overlay + force clear at `ACTION_SHUTDOWN` | **Yes, implemented** | Uses only paths that already work: an accessibility overlay (as the AOD does) and the daemon's `r` command (`epd_force_clear`). Nothing on the GSI clears the glass at shutdown, so the last composed frame stays there |

## How stock did it (MEASURED unless marked)

Sources: stock `super_*.img` from `$A9_DATA/HLTE556N/` (L2037.6.04.06.00),
joined at the `_flash_super.xml` sector offsets, then `lpunpack` and `debugfs rdump`.
Tools: `jadx` 1.5.0 for `services.jar` / `framework.jar`, radare2 6.0.2 for
the native code (`aaa`, `axt`, `pdf`), `readelf -r` for the vtables.

1. **Framework, `ShutdownThreadHX.run()`** (`services.jar`): if `EpdManager.EPD_SUPPORT`,
   it calls `IEpdManager.notifyShutdown()`, sleeps 600 ms, broadcasts
   `com.hmct.intent.action.ACTION_SHUTDOWN_WALLPAPER`, sleeps 2400 ms, and only
   then sends the normal `ACTION_SHUTDOWN`.
2. **`EpdManagerService.notifyShutdown()`**: `SurfaceControl.connectEpdDisplay(0)`,
   `setEpdModeImmediately(2)`, decodes a framework drawable (res `0x1080…`, id
   17303750; 17303748 when the IMEI is null), converts it to RGB_565, and calls
   `commitFbBufferLocked(..., "bitmap_low.raw")` → `SurfaceControl.setBitmapToExternal(bmp, fileName)`.
   This is refused while the screen is on.
3. **`libandroid_runtime.so` `nativeSetBitmapToExternal`** (strings): writes the raw
   pixels to `/kdebuginfo/edpd/<fileName>` ("save bitmap.raw duration") and sends
   SurfaceFlinger transaction **200001** with the buffer as a Parcel blob.
   `/kdebuginfo/edpd/bitmap_low.raw` in the golden dump is exactly that file:
   2,715,904 B = 824×1648×2 (RGB565). Decoded at
   `$A9_DATA/assets/stock-aod/edpd-bitmap-low-poweroff.png`: Hisense logo,
   "⏻ DEVICE POWERED OFF".
4. **Stock `libsurfaceflinger.so`, `SurfaceFlinger::onTransact` case 200001**: logs
   `case 200001; bufSize:%d`, allocates `hidl_memory` from
   `IAllocator::getService("ashmem")`, `memcpy`s the blob into it, then
   `IComposer::getService()` → `V2_4::castFrom` → a second `createClient` →
   `IQtiComposerClient::castFrom` → vtable slot `0x248` = **`sendImageData(hidl_memory)`**.
   `SurfaceFlinger::setEpdMode(int)` writes `epd_display_mode`, and
   `SurfaceFlinger::commitBitmap()` writes `epd_commit_bitmap`.
5. **Vendor `vendor.qti.hardware.display.composer@3.0.so`**: `IQtiComposerClient`
   has exactly one Hisense method, `_hidl_sendImageData(hidl_memory)`.
6. **Vendor composer `vendor.qti.hardware.display.composer-service`** (still the
   one running under our GSI):
   * `QtiComposerClient::sendImageData` → `mapMemory` → `display->EpdSendBitMap(ptr)`.
   * `HWCDisplay::EpdSendBitMap(char*)`: `memcpy` of **0x297100 = 2,715,904 B**
     (824×1648 RGB565) into its bitmap buffer, then tail-calls vtable `+0x380`
     = **`CommitBitMap()`**.
   * `CommitBitMap()`: if the "normal mode" byte (`this+0x6c7`) is set it logs
     `epd:: is normal mode,can not commit bitmap` and returns. Otherwise it flags
     the bitmap pending and wakes the E Ink thread (`BitMapModeGetImage`).
   * `this+0x6c7` is 1 after `InitEpd` and after an unblank in `SetPowerMode`.
     It is 0 after power mode Off (`epd:: Blank---`) and after `EpdSetBlank`.
     So a bitmap goes to the glass only while the panel is blanked, i.e. with
     the screen off. That matches the framework refusing while the screen is on.
   * `EpdSetBlank()` also sets the "shut down" latch (`this+0x6c5`). After it,
     `SetPowerMode` refuses to unblank (`epd::is shut down, unblank is fail`), so
     normal SurfaceFlinger frames stop reaching the panel until the HWC restarts.
   * The HWC `UEventHandler` handles `change@/devices/platform/soc/soc:qcom,dsi-display-primary`:
     `EPD_COMMIT=1` → vtable `+0x380` `CommitBitMap`, `EPD_CLEAR=1` → `+0x390`
     (force clear), `EPD_CONNECT=` → `+0x3a0` `EpdSetBlank`. Slots come from the
     `R_AARCH64_RELATIVE` relocations at `0x1d0cd0..0x1d0cf8`: CommitBitMap,
     EpdSetContrast, (clear), EpdSetDisplayMode, EpdSetBlank, EpdSendBitMap.
7. **Kernel** (stock `boot.img` kernel strings, in sysfs order): `epd_force_clear`
   → `EPD_CLEAR=1`, `epd_connect` → `EPD_CONNECT=0`, `epd_commit_bitmap` →
   `EPD_COMMIT=1` + `epd commit bitmap`. INFERRED from that adjacency: each
   store handler only emits the uevent. None of them takes pixels.
8. Vendor `init.target.rc` chmods all these nodes 0666 and chowns them to system.
   SELinux label `vendor_sysfs_graphics`.

So stock's sequence was: blank and latch the panel (`epd_connect`=0), mode 2,
hand the RGB565 frame to the HWC over hwbinder, and the HWC draws it while
blanked. Nothing cleared it afterwards, because the latch blocks every later frame.

### Why route (a) is deferred

* Our A16 SurfaceFlinger has no case 200001 (task brief). Nothing on the GSI ever
  calls `sendImageData`, so the HWC bitmap buffer stays empty. The daemon's
  existing `cm` command (`epd_commit_bitmap`) therefore commits nothing useful.
  On an unblanked panel it is refused anyway.
* `QtiComposerClient::CreateQtiComposerClientInstance()` returns the existing
  singleton when one exists (it logs `...CreateQtiComposerClientInstance 0001`).
  So a second client process would get SurfaceFlinger's own client object.
  MEASURED in the binary, and it is how stock SurfaceFlinger itself did it. That
  makes (a) workable: get `IComposer/default`, `createClient`, `castFrom`
  `IQtiComposerClient`, then `sendImageData`. It must never call `registerCallback`,
  because that would steal SurfaceFlinger's callbacks.
* What (a) needs: a hwbinder client with `hidl_memory`. Either a native binary
  against `libhidlbase`, or Java `HwBinder`/`HwParcel`/`HidlMemory` (SystemApi)
  under root `app_process`. It also needs the `IQtiComposerClient` transaction
  code for `sendImageData`, and a domain allowed to find
  `hal_graphics_composer_hwservice` (`magisk` or the permissive `phhsu_daemon`;
  an app domain cannot). None of this is tested. The failure mode (a composer
  crash) is the SF-restart hazard. It is a possible later upgrade, not the prototype.

## What the prototype does (route c)

Code: `PowerOffImage.kt`, plus small hooks in `A9AccessibilityService.kt`,
`SettingsActivity.kt`, `Utils.kt`, `root_preferences.xml` and `AndroidManifest.xml`
(`WAKE_LOCK`).

1. Settings > **Power Off**: "Show image at power off" (`poweroff_image_enabled`,
   **default OFF**), "Select Power-Off Image" (the same `GetContent` picker as the
   AOD background; the copy goes to app-private `filesDir/poweroff_image`),
   "Preview Power-Off Image", and "Remove Power-Off Image". With no power-off
   image it falls back to the AOD background. With neither it does nothing.
2. The accessibility service registers for `ACTION_SHUTDOWN`. That is an ordered
   broadcast, and AOSP `ShutdownThread` waits up to 10 s for it (INFERRED from
   AOSP, not read from our GSI's `services.jar`). On receipt, `goAsync()`:
   * skip if `sys.shutdown.requested` starts with `1` (reboot). This is read by
     reflection; if the read fails the image is shown anyway.
   * if the screen is not interactive (0 % battery shutdown, AOD doze, static
     sleep), take a `SCREEN_BRIGHT | ACQUIRE_CAUSES_WAKEUP` wakelock, so that
     something is composited at all. targetSdk 28, so the legacy wake rules apply
     (INFERRED).
   * add a full-screen opaque `TYPE_ACCESSIBILITY_OVERLAY` `ImageView`
     (center-crop like the AOD background, white behind).
   * after 600 ms send `r` (FORCE_CLEAR, `epd_force_clear`, a full clear refresh
     of the current frame), then hold the broadcast 2400 ms more. That is stock's
     600 + 2400 ms budget, so the waveform finishes before shutdown continues.
   * the overlay is never removed. The process dies with the system.
3. Why the image should stay (INFERRED, strong): the user reports that today the
   last app frame stays on the glass after power-off, so nothing on the GSI cleans
   the panel at shutdown. Screen-off (`SetPowerMode Off`, `epd:: Blank---`) leaves
   the frame in place (static AOD relies on it). The fp9929a PMIC is powered only
   during a refresh. The HWC also has `epd:: is black image, not display...`, so an
   all-black frame as SurfaceFlinger's layers die is probably not pushed.

### Known risk and the follow-up that would close it

At `sys.powerctl`, init SIGTERMs every service together (zygote, so our app and
system_server, plus SurfaceFlinger and the HWC). If our window is torn down while
SurfaceFlinger and the HWC still live, one more frame (the shutdown dialog or the
app underneath) could reach the glass. INFERRED to be a very small window, since an
E Ink update takes hundreds of ms, but not measured. The stock-style fix is the
latch: write `0` to `epd_connect` after the clear, which runs `EpdSetBlank`. The
app cannot write sysfs. It needs a new `a9_eink_server` command (for example `dc`)
and a new image. Not implemented. Test it by hand first (step 6 below).

## How to test on the phone (main session; the phone is in use, so this was not done)

Preconditions: `scripts/a9-health.sh` returns 0. Test on slot **B** per the slot
rules, or treat it as an app-only update. It touches no partition, and the update
lives in `/data/app` until it is uninstalled.

1. **Build and sign** (as CI does):
   * `cd work-gsi/A9PatchingService && git switch a16-port` (since 2026-10-14 the feature is on `a16-port` as `59314b1`, versionCode 48, rebuilt on top of 3.8.8)
   * either push the branch to Gitea and take the CI artifact `a9service.apk`, or
     build locally: put `sdk.dir=/nix/store/d9paimjgmckzvr8z8vw64mdb44b2kj1q-androidsdk/libexec/android-sdk`
     in `local.properties`, run `nix-shell -p jdk17 --run "./gradlew --no-daemon assembleDebug"`,
     then `zipalign -p -f 4` and `apksigner sign --key release.pk8 --cert release.x509.pem`
     (1Password `vault/item`).
   * `apksigner verify --print-certs` must show `c2ee5e84…`. A debug-key APK will
     not install over the system copy.
2. **Install as an update of the priv-app:** `adb install -r a9service.apk`.
   `adb shell dumpsys package com.lmqr.ha9_comp_service | grep versionName` should
   print `3.9.0`. The accessibility service restarts, so check it is still enabled
   (`settings get secure enabled_accessibility_services`).
3. **Pick an image:** copy one to the phone (for example the stock one,
   `adb push $A9_DATA/assets/stock-aod/edpd-bitmap-low-poweroff.png /sdcard/Download/`).
   Then go to E-Ink Settings > Power Off > Select Power-Off Image and turn on
   "Show image at power off".
4. **Preview without powering off:** tap "Preview Power-Off Image", or
   `adb shell am broadcast -a com.lmqr.ha9_comp_service.POWEROFF_IMAGE_PREVIEW -p com.lmqr.ha9_comp_service`.
   Within about 3 s, `scripts/epd-snap.sh` should show the image full-screen,
   status and nav bars covered. `logcat -s A9PowerOffImage` should show
   `power-off image shown: ... (WxH)`. It goes away by itself after 10 s.
5. **Real power-off:** `adb shell reboot -p` (or the power menu). Check the glass
   by eye or camera, since `epd-snap` cannot run with the phone off. Expected: the
   image, crisp, with no app ghosting. Also try a reboot: `adb reboot` should *not*
   show it (logcat before the reboot prints `sys.shutdown.requested='1...'`). If
   the prop cannot be read, the image appears briefly and the boot splash
   replaces it. Power on and run `scripts/a9-health.sh`.
6. **Optional latch experiment (root, Magisk):** with the preview image on the
   glass, run `adb shell su -c 'echo 0 > /sys/devices/platform/soc/soc:qcom,dsi-display-primary/epd_connect'`.
   Expected (INFERRED from the HWC code): logcat `epd:: Blank---`, and after that
   no app frame reaches the panel, even when the preview overlay is removed 10 s
   later. Recover with `adb reboot` (the latch lives in the HWC process). It
   **does not touch SurfaceFlinger**. Do not "recover" by restarting SF or the
   composer (rule 11). If the latch behaves as expected, it is worth adding as a
   daemon command after the clear.
7. **Low battery:** the 0 % shutdown goes through the same `ACTION_SHUTDOWN`. The
   screen-wake branch covers a dozing or off panel. Simplest check: with the screen
   off, `adb shell reboot -p` should wake the screen briefly and then leave the image.

## How to turn it off

E-Ink Settings > Power Off > switch "Show image at power off" off (the default).
"Remove Power-Off Image" deletes the copied file. To go back to the image's
a9service 3.8.3, run `adb shell pm uninstall-system-updates com.lmqr.ha9_comp_service`. To go back to 3.8.8 (the popup warmth build that was installed before): `adb install -r -d $A9_DATA/apks/a9service-3.8.8-vc47-installed.apk`.
That removes only the `/data` update, and the `/system/priv-app` copy comes back.

## Reproduction (stock firmware)

```bash
F=$A9_DATA/HLTE556N; S=<scratch>
for p in "1 225032" "2 225288" "3 1268744" "4 1369864" "5 1416456"; do set -- $p
  dd if=$F/super_$1.img of=$S/super.raw bs=4096 seek=$(( $2 - 225032 )) conv=notrunc,sparse; done
lpunpack -p vendor_a -p system_a $S/super.raw $S
debugfs -R 'rdump / v' $S/vendor_a.img; debugfs -R 'rdump /system s' $S/system_a.img
jadx -d jx s/system/framework/services.jar   # ShutdownThreadHX, EpdManagerService
r2 -q -c 'aaa; pdf @ method.sdm::HWCDisplay.CommitBitMap__' v/bin/hw/vendor.qti.hardware.display.composer-service
r2 -q -c 'aaa; axt @ 0x5e28d' s/system/lib64/libsurfaceflinger.so   # case 200001
readelf -rW v/bin/hw/vendor.qti.hardware.display.composer-service | grep -E ' 1876ec$| 187b1c$'
debugfs -c -R 'dump /edpd/bitmap_low.raw out.raw' $A9_DATA/Backups/edl_2026-10-11_132444/lun0/kdebuginfo.bin
```
