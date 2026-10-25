# Always-On Display on the A9 (A16 / Doze-off LOS 23.2)

Dated 2026-10-11, Doze-off GAPPS v3 image, a9service 3.8.3 (defaults to `aod_mode=overlay`; v3 is built from patcher a16-port `4604adb`, and a flashed, wiped-/data phone comes up in overlay AOD). Labels: MEASURED = seen on the phone, INFERRED = deduced.

The measurements below use the stock display stack. Mainline drm_hwcomposer
needs continuous composition instead of DOZE_SUSPEND; use the separate
[mainline setup and k5 findings](mainline-key-aod.md). Its root helper now edits
the actual app SharedPreferences without opening the UI.

## Which AOD we use

The project AOD is the **a9service overlay** (mode 2): `AlwaysOnDisplay.kt` adds a `TYPE_ACCESSIBILITY_OVERLAY` clock/date/battery view on ACTION_SCREEN_OFF.
It needs, together:

| Item | Value | Why |
|---|---|---|
| a9service `aod_mode` pref | `overlay` | selects the mode; on change the app writes `doze_always_on`/`doze_enabled` = 1 and sends `stl96` |
| `sys.linevibrator_type` | 96 (sentinel) | patched DisplayPowerController then reports `mColorFadeEnabled=false`, so ColorFade does not screenshot/fade the panel |
| `doze_always_on`, `doze_enabled` | 1 | display goes Dozing (DOZE_SUSPEND) and keeps compositing the overlay; with full sleep the panel latches the old frame |
| `me.phh.treble.overlay.misc.aod` | framework availability | static, gated by `persist.sys.overlay.aod`; declares config_dozeAlwaysOnDisplayAvailable |
| `me.phh.treble.overlay.misc.aod_systemui` | enabled on stock; disabled on mainline | advertises SystemUI hardware DOZE and DOZE_SUSPEND support; verified in the shipped GSI resources on 2026-10-20 |

SystemUI's own AOD is not what you see: doze is on only so the framework keeps the display alive; the a9service overlay is what is drawn.
`aod_mode=static` writes doze 0 (full sleep + ColorFade, "keep last screen" mode 1); `stock` leaves doze alone.

## Cause of "AOD turns itself off"

* MEASURED: `doze_always_on=1, doze_enabled=1` set via `settings put` survive a reboot untouched (checked after `adb reboot`, no SettingsShellCmd write to doze_ in the log). So nothing resets them at every boot.
* MEASURED: the phone's a9service pref was `aod_mode=static` (the code/XML default). The `aod_mode` handler does `setDoze(0)` for static and sends `stl<shader>` (not 96) so `sys.linevibrator_type` was 0 and ColorFade was on. Mode "static" is the wrong mode for overlay AOD and writes doze 0 whenever it is selected.
* MEASURED: the vndk.rc one-shot (`/data/local/tmp/disable_ambient_display`, created 15:56 today, i.e. on the first boot after the flash/wipe) runs `settings put secure doze_enabled 0`. After a /data wipe it fires again.
* INFERRED: the earlier reads of 0 came from one of those two writers (the one-shot on a first boot, or the app when `static` was (re)selected); the old log was gone so the exact write was not captured.
* MEASURED: `sys.linevibrator_type` is only 96 when the app is in overlay mode; the app re-sends `stl96` at every service start (it read 96 after a reboot).

## Fix

* Phone now (MEASURED): E-Ink settings app > Always On Display mode > Overlay. Resulted in doze 1/1, `sys.linevibrator_type=96`, `mColorFadeEnabled=false`, and it persists across `adb reboot` (pref is in app data).
* Image / patcher (branch a16-port, cherry-picked to a16-dozeoff-hwc-guard and a16-hwc-guard; MEASURED in image v3, a9service 3.8.3, flashed to B and A 2026-10-11, overlay AOD consistent): app default `aod_mode` is now `overlay` (code + XML), and the vndk.rc one-shot sets `doze_always_on 1; doze_enabled 1` instead of `doze_enabled 0`. A wiped /data then comes up in overlay AOD. 
* Older images (before v3), after a wipe: run `scripts/a9-aod-setup.sh`, then select Overlay in the app. `a9-aod-mode.sh` is now a read-only state check; the mode is selected only in the app UI.

## Proof (MEASURED)

`input keyevent KEYCODE_SLEEP`, wait 12 s: `mWakefulness=Dozing`, `mScreenState=DOZE_SUSPEND`, `mColorFadeEnabled=false`. `epd-snap.sh` shows a white panel with a large clock ("05:08"), date ("Sun, 11 Oct") top-left and a charging battery "100%" bottom-left: the overlay AOD. Same after a reboot ("05:07", same layout).

Gotcha: the first `epd-snap.sh` run after boot discovers the buffer by pulling the shade, waking the lockscreen; its PNG then shows the lockscreen (grey, notification). Re-sleep and snap again (buffer is cached).

## Open

* `enabled_accessibility_services` holds the service twice (full and short class name) after the first-boot script; harmless so far.
* Overlay AOD needs the a9service accessibility service to be running; the first-boot script covers that.

## Background image for the overlay AOD

MEASURED (code read, a9service 3.8.3 `AlwaysOnDisplay.kt`; not a panel proof of a background):

* Pick: E-Ink Settings > Always On Display (Overlay) > "Select Background for AOD". "Remove Background for AOD" reverts to the plain white overlay.
* Stored at `/sdcard/a9_comp_service/bgimage` or `<filesDir>/bgimage`.
* `loadBackgroundImage` scales the image to cover the panel and picks black or white text by the image luminance.
* Stock e-ink cat asset: Siamese cat line art, 824x1648 grayscale PNG, sha256 `097edf776c0226e2939d22bda7d59e779af9b6669734659ac8f30b70170baa2b`, 104,597 B, saved at `$A9_DATA/assets/stock-aod/eink-cat.png` (README there; asset id `aod-cat` in `data/assets.tsv`). Copied to the phone as `/sdcard/Download/eink-cat.png` for the user to pick in the app.
* It is deliberately NOT the default and not bundled in the app (user's choice): the default stays the plain overlay.
