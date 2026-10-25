---
name: a9-los-setup
description: Get a freshly flashed or wiped LineageOS GSI on the Hisense A9 (HLTE556N) from the "Welcome to LineageOS" setup wizard to a usable home screen entirely over adb, then apply the A9 first-boot settings. Use whenever the phone is sitting in the LineageOS setup wizard, after any /data wipe or new ROM flash, or when asked to "get through setup" / "skip the setup screen".
---

# LineageOS first-boot setup on the A9, over adb

Verified 2026-10-10 on `lineage 23.2-20260603-UNOFFICIAL-gsi` (Android 16,
the Damian-patched A16 build), from build-host with native adb.

## TL;DR

```bash
cd ~/A9/scripts
./los-skip-setup.sh status   # where is it?
./los-skip-setup.sh          # skip the wizard, verify, screenshot
./a9-aod-setup.sh            # A9 AOD/accessibility settings after any /data wipe
```

`./los-skip-setup.sh undo` puts the wizard back.

## 1. Know where the phone is

```bash
adb devices                                   # <serial>  device
adb shell getprop sys.boot_completed          # 1
adb shell settings get secure user_setup_complete   # 0 = wizard pending
adb shell dumpsys window | grep mCurrentFocus
```

`mCurrentFocus=null` while `mFocusedApp=...setupwizard/.WelcomeActivity`
means **the screen is asleep**, not that something is broken. A
`screencap` then comes back **solid black**. Wake it first:

```bash
adb shell input keyevent KEYCODE_WAKEUP
adb shell dumpsys power | grep mWakefulness   # Awake
adb exec-out screencap -p > shot.png          # then look at it
```

`screencap` goes through SurfaceFlinger and is safe. **Never restart
SurfaceFlinger** to "fix" a blank screen (CLAUDE.md safety rule 11).

No adb at all after a wipe means the image was built without the debug props
(`A9_RELEASE=1`). Turn on USB debugging on the panel, or boot TWRP and run
`scripts/enable-adb-root.sh`.

## 2. Skip the wizard (preferred)

The wizard's own finish amounts to two settings plus disabling itself. Set
them directly. The adb shell uid can do all of it, no root:

```bash
adb shell '
settings put global device_provisioned 1
settings put secure user_setup_complete 1
pm disable-user --user 0 org.lineageos.setupwizard
am start -W -a android.intent.action.MAIN -c android.intent.category.HOME'
```

Verify, all four:

| Check | Expect |
|---|---|
| `settings get global device_provisioned` / `secure user_setup_complete` | `1` / `1` |
| `pm list packages -d \| grep setupwizard` | listed (disabled) |
| `dumpsys window \| grep mCurrentFocus` | `com.android.launcher3/...QuickstepLauncher` |
| `dumpsys statusbar \| grep mDisabled` | `mDisabled1=0x0 mDisabled2=0x0` (nav/home/recents not blocked) |

Then a screenshot. Home screen with clock, dock and back/home/recents means done.

If the build carries GApps there is also `com.google.android.setupwizard`;
disable it the same way. The A16 GSI here has only the LineageOS one.

## 3. Fallback: drive the wizard by taps

Only if a future wizard refuses the skip. The panel is 824×1648, and
`screencap` coordinates are `input tap` coordinates. Loop: wake → screencap →
read the PNG → `adb shell input tap X Y` on the button → repeat. On the
Welcome page, **Start** sat at (644, 1468) and **Emergency call** bottom-left.
Expect pages for language, date/time, Wi-Fi (Skip), LineageOS services,
security/PIN (Skip) and theme, ending with Start/Next. Use
`input keyevent KEYCODE_TAB`/`ENTER` if a target is hard to hit.

## 3b. Root: Magisk manager + shell grant (automated)

The golden `boot_b` and `boot_a` are official **Magisk v30.7**: `magisk -v` =
`30.7:MAGISK:R`, `magisk -V` = `30700` (Kitsune Mask was removed 2026-10-11, see
`docs/root-magisk.md`). After any `/data` wipe Magisk auto-installs only its
**stub** manager (`com.topjohnwu.magisk`, small versionCode), which pops
"Please connect to the Internet! Upgrading to full Magisk is required." at boot,
and `su` is denied.

`los-skip-setup.sh` (also `los-skip-setup.sh root` alone) fixes this:
1. Installs the full manager, the newest `Magisk-v*.apk` under `$A9_DATA`
   (`$A9_DATA/root/Magisk-v30.7.apk`; override with `MAGISK_APK=`). Signer
   `CN=John Wu`, cert SHA-256 `b4cb83b4…3ee6`.
2. Grants root to the adb shell: one `su -c id`, then taps **GRANT** on the
   Superuser prompt (it finds the button via `uiautomator dump`, otherwise taps
   (574, 1060)). Result: Magisk policy `uid=2000 policy=2 until=0`.

Traps (each one bit on 2026-10-10):
- **The prompt auto-denies after ~5 s, and with "Forever" preselected a timeout
  is stored as a PERMANENT deny.** Never fire a `su` "just to check" when
  nobody will tap the prompt. Fix a stored deny: Magisk app → Superuser → Shell
  toggle on (or as root: `magisk --sqlite "UPDATE policies SET policy=2 WHERE uid=2000"`).
- **Never tap OK on the app's "Requires Additional Setup / reflash Magisk"
  dialog.** It re-flashes the boot partition. Tap CANCEL.
- The manager must be signed with the same key as the boot image's stub (official
  Magisk v30.7 here). A manager from a different Magisk fork shows
  "Installed: N/A" and `su` hangs.
- No full APK available → the script disables the stub instead (popup gone,
  root unusable).

## 4. After setup: A9 first-boot items

1. **`./a9-aod-setup.sh`**: doze, accessibility service, AOD overlay. Damian's
   `vndk.rc` one-shot re-disables doze after every wipe, so this is needed
   every time.
2. **Clock:** with no network it shows a stale date (it said Fri Aug 14 on
   2026-10-10). It corrects itself on Wi-Fi; or set auto-time later.
3. **Check, don't assume, the ColorFade state:**
   `adb shell dumpsys display | grep -o 'mColorFadeEnabled=[a-z]*'` (see
   `a9-aod-setup.sh` and the a16 port plan for what it should be per image).
4. **Bisecting images:** wipe `/data` between every image
   (`adb shell twrp format data` from TWRP, **never** `fastboot -w`, safety
   rule 10), then run this skill again from step 1.

## Traps

- **GApps on Android 17 (seen on the A6L LOS 24 port, 2026-10): Google's "Copy
  apps & data" (`com.google.android.apps.restore`, `UsbD2dMigrateService`)
  crash-loops "Getting your phone ready" while USB is plugged in, even with
  adb disabled.** Unplug during that page, or skip the wizard as in step 2.
  Not seen on our A16 GApps build.

- **Black screenshot ≠ broken display.** It's asleep (step 1).
- **Disabling the wizard before it has run is fine**, but do it with the two
  settings. Setting only `user_setup_complete` leaves `device_provisioned=0`,
  and SystemUI then keeps parts of the UI locked.
- `pm disable-user` is per-user and survives reboots. `pm enable` reverts it.
- Don't `pm uninstall` the wizard on a GSI: it lives in `/system`, the
  uninstall only hides it for user 0, and the skip above is cleaner.
