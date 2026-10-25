# Porting Damian's full e-ink patch set to an Android 16 GSI

**Status: WORKING 2026-08-11.** Damian-patched Doze-off LineageOS 23.2
(Android 16 QPR2) runs on the Hisense A9, with **E Ink screen control confirmed
working on-device**. Boots in 60 s, 18 of 21 patch groups applied,
`a9_eink_server` running, accessibility service enabled, app drawer and haptics
stable.

## AOD on Android 16 — SOLVED 2026-08-11

There are **two different AOD implementations** and they are mutually
exclusive. Both now work; pick one at build time.

E Ink retains its last image at zero power, so "AOD" here means *what image is
left on the glass when the screen goes off*. Nothing is continuously drawn.

### Mode 1 — static AOD (retains your last screen)

`A9_PATCH_COLORFADE=1` + `sys.linevibrator_type` = a passthrough index.

ColorFade screenshots the display at screen-off and renders it through one of
96 baked-in shader variants. **Passthrough** indices leave the captured image
untouched; every other index deliberately fades to black or white:

```
moon   6  7 14 15 22 23 30 31 38 39 46 47
pause 54 55 62 63 70 71 78 79 86 87 94 95
index = shape(0 moon|48 pause) + size(0 large|24 small)
      + glyph_opacity(0|8|16) + bg_opacity(0=1.0|2=0.6|4=0.2|6=0.0) + colour(0|1)
```

Result: your last screen, retained, with a small moon/pause glyph.

### Mode 2 — overlay AOD (clock / chess / battery / music)

`A9_DISABLE_COLORFADE=1` **plus doze enabled at runtime.**

a9service draws a `TYPE_ACCESSIBILITY_OVERLAY` on `ACTION_SCREEN_OFF`. Two
things must both be true:

1. **ColorFade must be off.** It screenshots the display *before* the overlay
   becomes visible (the broadcast reaches apps in parallel with the transition),
   so the panel latches the snapshot — your last app screen — not the overlay.
   Measured: with the passthrough shader the panel showed the last app screen
   plus the corner moon, which is the snapshot.
2. **The device must DOZE, not sleep.** In doze the display stays in a
   low-power ON state and SurfaceFlinger keeps compositing, so the overlay
   reaches the panel. On a full screen-off, compositing stops before the
   overlay is drawn. This is the key that took longest to find.

Runtime settings (all applied by `scripts/a9-aod-setup.sh`):

```
doze_always_on = 1        doze_enabled = 1
me.phh.treble.overlay.misc.aod_systemui  enabled
animator_duration_scale = 1     <- 0 is a documented cause of black AOD
enabled_accessibility_services  = a9service's A9AccessibilityService
```

Plus, in the app: **Chess AOD** on and **Refresh AOD after screen off** on.
The latter sends `FORCE_CLEAR` ("r" -> `epd_force_clear`) 150 ms after
screen-off so the EPD takes a new frame. Its XML `defaultValue="true"` never
applies, because the code reads it with a `false` default until the settings
screen has been opened — so on a fresh install it is effectively OFF.

**`vndk.rc` sets `doze_enabled 0` as a one-shot** guarded by
`/data/local/tmp/disable_ambient_display`. After any `/data` wipe that fires
again and silently breaks mode 2. Re-run `a9-aod-setup.sh`.

### App / daemon pairing — mixing generations degrades silently

| a9service | a9_eink_server | commands |
|---|---|---|
| v1.4.1 (has `aod_views`) | **47,224** (May 2024) | no `smb`, `stl`, `wov` |
| v3.x | **43,216** (Sept 2024) | full set |

Both speak the same abstract socket `a9_eink_socket`, so a mismatch does not
error — features just stop working. Running v3.x against the May daemon made
the temperature/brightness slider misbehave (`smb` absent); pairing it with
the Sept daemon fixed it.

### Our fork

`dmayala/A9PatchingService`, branch `a16-port` — carries the A16 patcher
fixes, the restored `aod_views` rebased onto v3.8.x (so **both** modes exist in
one codebase), `tools/patch-gsi.sh`, `tools/finalize-image.sh`, and CI that
builds and signs with our own key (`CN=a9-release`,
`c2ee5e84899e1a4b84db7d61aa7e4f47bccd403e9bff0848487a09e504f63d97`).

Because that key differs from upstream's `CN=Damian`, builds from the fork
**cannot** be installed over a Damian-signed install
(`INSTALL_FAILED_UPDATE_INCOMPATIBLE`), and dropping one into
`/system/priv-app` on a device whose `/data` still records the old signature
makes PackageManager reject it. Ship it in the image **and wipe `/data`**, or
change `applicationId`.

## Working configuration

Build with:

```sh
nix-shell -p apktool jdk17 apksigner android-tools python313 e2fsprogs attr util-linux \
  --run "work-gsi/run-patcher.sh <dozeoff-a16.img>"
```

All three `A9_PATCH_*` gates default OFF, which is correct for this base.
Then inject the adb props (§ toolchain) and flash `system` from **fastbootd**.

| Component | Value |
|---|---|
| Base | Doze-off `LineageOS-23.2_GSI_treble_arm64-ab-VANILLA-20260603` (ext4) |
| `system_b` image | `dozeoff_PATCHED_v3.img`, 3,145,728,000 B (deleted from $A9_DATA 2026-10-10; original on the Windows PC) |
| `services.jar` | 23,131,234 B (patched, vibrator group excluded) |
| `boot_b` | `magisk_patched-27001_7a6jR.img` (`9600ed2c…`; Kitsune, historical, file deleted 2026-10-11) |
| `TrebleApp.apk` | GSI original, 14,834,868 B — **not** replaced |
| Slot | `b` (note: `system_a` does not exist) |

## Known-broken / known-limited

| Item | Status |
|---|---|
| `Vibrator(Manager)?Service` patch | **BROKEN on A16** — disabled. See root-cause section. |
| `VibrationScaler` patch | never matched on A16 |
| `*Wallpaper*` animator-scale patch | never matched on A16 |
| SystemUI patches (AOD) | not applied — signature-gated, and **untested on A16** |
| 64-bit Vulkan | **absent from the vendor** — unfixable by GSI choice |
| `ktd3137-bl-3` / `-4` backlights | **do not exist on this device.** Damian's `vndk.rc` chmod/chcon/write lines for them fail harmlessly. Only `aw99703-bl-1`, `aw99703-bl-2`, `panel0-backlight` are present. |
| `a9service.apk` `targetSdk` | **27** (Android 8.1) → Android 16 shows "built for an older version of Android". Cosmetic. **Do not bump casually** — the app relies on `SYSTEM_ALERT_WINDOW` for its floating menu and overlay window-type rules tightened at SDK 26+, so raising the target risks breaking the menu. |
| `adb root` | unavailable — `ro.debuggable`/`ro.build.type` are set before `/system/build.prop`, and `ro.` props are first-write-wins |
| Magisk | boot image patched but not bootstrapped (`/data` was wiped; needs the APK + direct install) |
| `ro.build.fingerprint` | still reports Hisense A11 — the stale A11 `system_ext`/`product` props win |

## THE ROOT CAUSE — read this first

The boot failure was **not** the smali patches. All 19 patch groups are fine.

`patch_system_img.py` calls
`replace_file("d/system/priv-app/TrebleApp/TrebleApp.apk")`, dropping in its
own **bundled, pre-signed** TrebleApp. That APK is signed with the AOSP test
key `C8:A2:E9:BC…`; Doze-off's GSI platform is signed with crDroid's
`A8:2A:F5:80…`. `me.phh.treble.app` declares a `sharedUserId`, so a signature
mismatch is fatal:

```
java.lang.IllegalStateException: Signature mismatch on system package
    me.phh.treble.app for shared user
  at com.android.server.pm.ReconcilePackageUtils.reconcilePackages
  at com.android.server.pm.PackageManagerService.<init>
  at com.android.server.SystemServer.startBootstrapServices
```

`system_server` dies inside `initSystemApps` → bootloop → the bootloader
retires the slot after 3 attempts.

**This is the §1 signing constraint, which applies to `replace_file()` targets
too — not only to the `sign=True` smali targets.** The original analysis missed
that the patcher ships pre-signed APKs.

| Bundled APK | Signature | vs crDroid platform |
|---|---|---|
| `TrebleApp.apk` (1,611,562 B) | `C8:A2:E9:BC…` AOSP testkey | ❌ **fatal** |
| `treble-overlay-Hisense-HLTE556N.apk` | `C8:A2:E9:BC…` AOSP testkey | ❌ removed |
| `a9service.apk` | v2/v3 only, no `sharedUserId` | ✓ fine |

### The fix

Do **not** replace `TrebleApp.apk` on a non-testkey base — the GSI ships its own
correctly-signed copy (14,834,868 B, `A8:2A:F5:80…`). Drop the treble overlay
too. Everything else in the patch set applies unchanged.

### How it was found

Baking `ro.adb.secure=0` into `build.prop` **before** flashing brings adb up
~33 s into a *failing* boot, because `adbd` starts long before `system_server`.
That turns a blind 3-flash bisect into one `logcat` read. Do this first on any
future boot failure.

---

**Started:** planning, 2026-08-11.
**Goal:** all features of `damianmqr/A9PatchingService` running on an Android 16
(LineageOS 23 / TrebleDroid-family) GSI on the Hisense A9.

---

## 0. What we actually have today (measured 2026-08-11)

The running ROM does **not** have Damian's framework patches. Measured by
extracting files from both local images with `debugfs` (no root, no mount) and
comparing:

| File | `…arm64_bgN.img` | `…_damianmqr_a9_patched.img` | |
|---|---|---|---|
| `system/framework/services.jar` | `d8de3c74e7202dfb1592e1505530c2fa` | `d8de3c74e7202dfb1592e1505530c2fa` | identical |
| `system/system_ext/priv-app/SystemUI/SystemUI.apk` | `13eac900457fb99c081c15f774872714` | `13eac900457fb99c081c15f774872714` | identical |
| `system/framework/framework-res.apk` | `e7e333e2741ece3edfbb4e2661895d69` | `e7e333e2741ece3edfbb4e2661895d69` | identical |
| `system/etc/init/vndk.rc` | `2f273301d7c1e816f5357dbb2248b6f3` | `b02c034d8ffe6838f7014edf53618637` | **+5 lines** |
| `system/bin/a9_eink_server` | absent | 47,224 bytes | **added** |

The five added `vndk.rc` lines are exactly those emitted by
`14/patch_system_img.sh` (the 3.6 KB shell script (deleted from $A9_DATA 2026-10-10; original on the Windows PC)), not by
`patch_system_img.py` (1,561 lines). **The full patcher has never been run
here.**

Note the binary sizes differ across sources: `14/a9_eink_server` (deleted from $A9_DATA 2026-10-10; original on the Windows PC) is 47,224
bytes, the copy in the `A9PatchingService` repo is 43,216. Provenance of the
larger one is unknown — treat `14/` as the newer of the two but do not assume.

Reproduce:

```sh
debugfs -R "dump /system/framework/services.jar out.jar" <image>.img
```

---

## 1. The signing constraint (measured)

`smali_patcher.py`'s `JarPatcher.patch()` signature is
`patch(self, install=[], sign=False, use_src=True, use_res=False, copy_meta_inf=True, api=None)`.
Call sites in `patch_system_img.py`:

| Target | Call | Re-signed? |
|---|---|---|
| `system/framework/services.jar` | `.patch(api=29)` | **No** — `sign` defaults to `False` |
| `system_ext/priv-app/SystemUI/SystemUI.apk` | `.patch(install=[…], sign=True, api=29)` | **Yes**, `platform.pk8` |
| `product/priv-app/Dialer/Dialer.apk` | `.patch(sign=True, use_res=True, use_src=False, api=29)` | **Yes**, `platform.pk8` |

`services.jar` is not an APK. It is loaded directly by `system_server` and is
never signature-verified, so **the majority of the patch set has no signing
dependency**. Only SystemUI and Dialer do.

`platform.pk8` / `platform.x509.pem` in the repo are the **public AOSP test
keys**. They only work if the base GSI's platform components are signed with
the same key — SystemUI carries `sharedUserId=android.uid.system` and must
match the `android` package's signature.

The Lineage 21 GSI in `14/` **is** testkey-signed:

```
subject=C=US, ST=California, L=Mountain View, O=Android, OU=Android,
        CN=Android, emailAddress=android@android.com
issuer =  (same — self-signed)
```

Reproduce:

```sh
unzip -p SystemUI.apk META-INF/CERT.RSA \
  | openssl pkcs7 -inform DER -print_certs -noout
```

### Consequence

The base A16 GSI must satisfy **all three**:

1. **phh / TrebleDroid derived.** The entire `vndk.rc` mechanism depends on
   phh's superuser: `exec_background u:r:phhsu_daemon:s0`, the
   `u:object_r:phhsu_exec:s0` label on `a9_eink_server`, and
   `cmd overlay enable me.phh.treble.overlay.misc.aod_systemui`. Google's
   official AOSP 16 GSI has none of this and is **disqualified**.
2. **Signed with the public AOSP test keys** — otherwise SystemUI and Dialer
   patches cannot be applied. (services.jar patches would still work.)
3. arm64, **A/B**, **non-vndklite**.

### Candidate signing keys (measured 2026-08-11)

Method: download, `unxz`/`7z`, `debugfs`-extract `SystemUI.apk` and
`framework-res.apk`, read the v1 cert. Both APKs matched in every case.

**Compare SHA-256 fingerprints, never subject strings.** MisterZtr's cert has
subject `O=Android, OU=Android, CN=Android` — visually identical to AOSP's —
but is a different key. Only the email field and the fingerprint reveal it.

Reference: Damian's `platform.x509.pem` =
`C8:A2:E9:BC:CF:59:7C:2F:B6:DC:66:BE:E2:93:FC:13:F2:FC:47:EC:77:BC:6B:2B:0D:52:C1:1F:51:19:2A:B8`

| GSI | Android | Base | Cert fingerprint (SHA-256, first 8) | Signer | Tier |
|---|---|---|---|---|---|
| TrebleDroid `ci-20250617` `system-td-arm64-vanilla` | 16.0.0_r1 | AOSP | `C8:A2:E9:BC…` | **AOSP test keys — exact match** | **A** |
| MisterZtr `LineageOS-23.2-20260524-VANILLA-EXT4` | 16 QPR2 | LineageOS | `78:FD:40:EC…` | own (`dmayala@users.noreply.github.com`, RU) | B |
| Doze-off `LineageOS-23.2…VANILLA-20260603` | 16 QPR2 | LineageOS | `A8:2A:F5:80…` | crDroid (`contact@crdroid.net`) | B |
| `lineage-21.0-20240217-UNOFFICIAL-arm64_bgN` *(installed)* | 14 | LineageOS | `C8:A2:E9:BC…` | **AOSP test keys** | — |

So the XDA report was right about Doze-off, and the reason is now known: those
builds carry **crDroid's** keys. MisterZtr's carry his own. On either base,
SystemUI and Dialer patches are impossible.

`Dialer.apk` in the MisterZtr image is signed with a *different* key again
(`96:5E:DD:29…`) — it is a media/product key, not the platform key, so the
Dialer patch has its own signature constraint independent of SystemUI's.

**Result: there is currently no testkey-signed LineageOS 23 GSI.** The only
testkey-signed Android 16 treble base found is TrebleDroid's vanilla AOSP.

### phh derivation (requirement 1) — measured

| GSI | `phhsu_daemon` in `vndk.rc` | `/system/bin/phh-su` |
|---|---|---|
| TrebleDroid | yes | **present** |
| MisterZtr | **21 uses** | absent ("no inbuilt root") |
| Doze-off | yes | absent |

The `phh-su` *binary* is irrelevant to this port. What matters is that the
`phhsu_daemon` domain and `phhsu_exec` file context exist in the sepolicy, and
that `vndk.rc` has an `on property:sys.boot_completed=1` block for
`update_vndk_rc()` to anchor to. MisterZtr has both (anchor at line 23).
**All three candidates satisfy requirement 1.**

Ruled out as bases:

- **Google's official AOSP 16 GSI** — not phh-derived (requirement 1).
- **AndyYan / `andyyan-gsi`** — builds the currently-installed `arm64_bgN`
  image and is testkey-signed, but ships **no LineageOS 23**; newest is
  lineage-21/22 as of 2026-06-19.
- **ponces `treble_build_los`** — last release 2023-09-14, Android 13.

Still to measure: **MisterZtr `LineageOS_gsi`** — LineageOS 23.2 / A16 QPR2,
TrebleDroid-derived, ships **ext4** variants, and its 2026-05-24 changelog
explicitly says *"Added support for Android 11 vendors"*, which is the A9's
exact situation. Downloads from SourceForge, not GitHub releases.

**EROFS variants are unusable.** `patch_system_img.py` runs
`e2fsck` / `resize2fs` / `mount -o loop,rw` and edits files in place. Only
**ext4** builds can be patched.

---

## 2. Fallback tiers, in case no testkey A16 GSI exists

> **SUPERSEDED 2026-08-13 — the tiers collapsed.** Tier B now gets everything
> Tier A did. Re-signed system apps no longer need to match the base's platform
> certificate; see §2a. The table below is kept because the reasoning behind the
> base selection still holds.

| Tier | Base | What you get | Cost |
|---|---|---|---|
| **A** | testkey-signed A16 treble GSI | everything | port smali patches only |
| **B** | any A16 treble GSI | ~~services.jar only~~ **everything, via §2a** | same |
| **C** | self-built LOS 23 GSI, own keys | everything | **not possible on this box** — see §6 |

### 2a. Re-signing system apps on a base whose key we don't have

**Measured 2026-08-13 on LineageOS 23.2 (SDK 36), Doze-off VANILLA-20260603.**

Patching `SystemUI.apk` means repacking and re-signing it. We hold no platform
key for any A16 GSI (crDroid signs the Doze-off builds; MisterZtr signs his
own), so a re-signed SystemUI shares no signer with the platform and all ~155 of
its signature-protected permissions were denied. That was the whole basis for
"testkey bases only".

**The shared UID was never the obstacle.** `com.android.systemui` declares
`sharedUserId="android.uid.systemui"`, *not* `android.uid.system`, and it is the
**sole member** of it — measured: appId 10127, one package, against 19 for
`android.uid.system`. `SharedUserSetting.signaturesChanged` is transient and
null at every boot, so with one member PackageManager takes the tolerated
branch instead of the `IllegalStateException` that makes TrebleApp fatal:

```
W PackageManager: System package com.android.systemui signature changed; retaining data.
```

So grant by package name rather than by certificate. Three parts, all in the
patcher (`patch-gsi.sh` wires them together automatically):

1. **`patch_AppIdPermissionPolicy`** — early `return true` in
   `AppIdPermissionPolicy.shouldGrantPermissionBySignature` for the packages we
   re-signed. It is the single gate for signature grants; the sibling
   `mayGrantByPrivileged` comes from the priv-app allowlist and is
   signature-independent. A16 has only the new permission engine — there is no
   legacy `PermissionManagerServiceImpl` to patch as well. The class survives
   jarjar with its name intact.
2. **`add_signer_to_mac_permissions`** — adds the signing cert to
   `/system/etc/selinux/plat_mac_permissions.xml` with `seinfo="platform"`.
   SELinux is a *separate* check: `plat_seapp_contexts` keys the domain off
   `user=_app seinfo=platform`, so without this SystemUI silently drops from
   `platform_app` to `priv_app`.
3. **`A9_SIGN_KEY` / `A9_SIGN_CERT`** — the signing key is a parameter.
   **Do not leave it on the AOSP test key** once its cert is in
   `plat_mac_permissions.xml`: that private key is published in AOSP, so any
   APK signed with it would land in the platform SELinux domain. We use
   `keys/release.*`.

Only packages actually re-signed get the bypass. Re-signing *without* part 1
yields an image that boots and then silently has no permissions, so the patcher
now refuses `A9_PATCH_SYSTEMUI=1` together with `A9_PATCH_SERVICES=0`.

4. **`/system/etc/default-permissions/a9-resigned-permissions.xml`** — grants
   the **runtime** permissions a re-signed app also loses. Added 2026-08-13; see
   §2b, this one is not optional and cost most of a day.

**Result on-device:** signature `f64f0efb` → `ee40af21`, SystemUI running,
`u:r:platform_app:s0`, **403 permissions granted / 0 denied**, no `avc: denied`,
no crashes, chess AOD still rendering.

### 2b. The part that bites: runtime permissions, and a poisoned `/data`

**Measured 2026-08-13.** Everything above is necessary but *not sufficient*.
Signature permissions are only half the story.

Re-signing means SystemUI is no longer platform-signed, so
`DefaultPermissionGrantPolicy` stops pre-granting it **runtime** permissions.
SystemUI's `BluetoothEventManager` then handles the adapter broadcast and dies:

```
FATAL EXCEPTION: SysUiBg
Caused by: java.lang.SecurityException: Need android.permission.BLUETOOTH_CONNECT
  permission for ... BluetoothAdapterServiceBinder.getSupportedProfiles()
```

That is fatal, so SystemUI restarts, receives the broadcast again and dies again
— **measured at ~34 restarts**. A SystemUI cycling every couple of seconds tears
down window and input state constantly.

> **The symptom looks nothing like the cause.** It presents as *"app lists will
> not scroll"* while taps still work, the launcher still works and composition
> is alive. It reads as a graphics or E Ink fault. It is not — SystemUI is
> simply never up long enough to sustain a gesture.

**Fix:** `/system/etc/default-permissions/*.xml` grants runtime permissions to a
package **by name**, needs no signature, and is the supported mechanism. The ROM
already ships one for `co.aospa.sense`, so the format is copied from it.

#### Why this was so hard to bisect — read before debugging anything similar

Default runtime grants are applied **once** and recorded in
`/data/system/users/<u>/runtime-permissions.xml`. Once that file says the
defaults were applied, flashing a **stock** SystemUI back does **not** re-run
them, so a known-good image stays broken until `/data` is wiped.

**Consequence: bisecting system images without wiping between points gives
contradictory answers.** It wrongly cleared the SystemUI patches early on,
because the control build was flashed onto already-poisoned `/data`. Every point
in a bisect needs `adb shell twrp format data` first, or the result is noise.

Isolation, every step on a freshly formatted `/data`:

| Build | SystemUI | Result |
|---|---|---|
| patches off | untouched | works |
| services.jar + `plat_mac_permissions` only | untouched | works |
| repacked + re-signed, **zero** patch groups | repacked | **broken** |
| stock APK, **re-signed only, no apktool** | stock bytes | **broken** ← the certificate |
| full patches + the default-permissions fix | patched | **works** |

The APK diff also cleared the repack itself: `resources.arsc` and
`AndroidManifest.xml` come out **byte-identical** and no zip entries are lost;
only the dex grows (~17%) from the baksmali/smali round-trip.

Two traps worth recording from the same session:

- **`A9_RESIGN_ONLY` must zipalign before signing.** Signing in place destroys
  the 4-byte alignment of the STORED `resources.arsc`; Android then refuses the
  APK and SystemUI does not exist *at all* — no package, no process. That reads
  as "the bug is fixed" while actually removing the component under test.
- **Compare screenshots by pixel, not by bytes.** A status-bar clock tick makes
  two otherwise identical frames differ, which produced a confident and wrong
  "the app *is* scrolling, so it must be a panel-refresh problem".

### 2c. First-boot setup (fixed 2026-08-13)

On a clean `/data` a new user landed with the A9 accessibility service disabled
— no E Ink controls, no AOD — and the ROM in dark mode, which is wrong for a
bistable panel. Three defects, all invisible on a developer device because they
only appear on a freshly wiped `/data`:

1. **Android init does not group single-quoted arguments.** It splits on
   whitespace and only honours *double* quotes, so
   `exec_background ... -- /system/bin/sh -c 'a b c'` reached `sh` as fragments
   and silently did nothing. This is why the accessibility auto-enable **never**
   worked on a clean install — a pre-existing bug, not a new one. The tell:
   on a clean first boot every *simple* hook in the same block had run
   (`cmd overlay enable`, `appops set`, `start a9_eink_server`) and every quoted
   one had not.
2. **`settings get` prints the literal string `"null"`** for an unset key, not
   an empty string. `[ -n "$existing" ]` therefore built the malformed list
   `null:com.lmqr.ha9_comp_service/.A9AccessibilityService`.
3. **`accessibility_enabled=1` was never set**, so the service stays dormant
   even with a correct list.

Fixed by moving the logic to **`/system/bin/a9-firstboot.sh`**, invoked by a
single unquoted command so there is no quoting for init to mangle. It verifies
its own write and retries ~2 min, since on a freshly wiped FBE device the
settings provider is not writable the instant `sys.boot_completed` fires. It
also sets the light theme, only when `ui_night_mode` is unset.

Verified on a genuinely clean first boot, nothing touched afterwards: night mode
`no`, accessibility list set, `accessibility_enabled=1`, a9service running,
SystemUI stable with 0 denied and 0 fatals.

### 2d. Release images

For current mainline integrated images, use the
[mainline release switch and signing instructions](mainline-release.md).
It corrects system and vendor defaults and signs the remaining public test APEXes;
the older patcher-only switch described below does not do that signing work.

`finalize-image.sh` bakes `ro.debuggable=1`, `ro.secure=0`, `ro.adb.secure=0`
and `persist.sys.usb.config=adb` by default — right for debugging, wrong for
anything handed to another person. **`A9_RELEASE=1` skips them**, leaving the
base's own values (`ro.build.type=user`, `ro.secure=1`, `ro.adb.secure=1`,
`ro.debuggable=0`). The verification step reports how many debug props the base
ships so a stray one cannot slip through; on this base that count is 0.

Note these buy less than they appear to anyway: on LineageOS 23.2 `ro.debuggable`
stays 0 and `adb root` is refused regardless.

### 2e. The two AOD modes — two gates, and a passthrough index

Two separate things make "static AOD is selected but does nothing", and they
look identical from the outside.

**Build both ColorFade gates.** They are *not* alternatives, despite the names
and despite older comments saying "mutually exclusive":

| Gate | What it actually does |
|---|---|
| `A9_PATCH_COLORFADE` | the 96 `SHADER_LIST` variants + moon/pause glyph — what **draws** mode 1 |
| `A9_DISABLE_COLORFADE` | **not** a hard disable: rewrites reads of `mColorFadeEnabled` to consult `sys.linevibrator_type`, so `96` = "overlay AOD, skip ColorFade" and any real index = "run it" — the **runtime switch** |

Both now default on. With only `DISABLE`, mode 1 has no shader to draw and falls
back to stock fade-to-black while the app *correctly* reports static mode —
which is exactly how it failed on 2026-08-13. Sanity check a build with
`grep -c 'ColorFade\.smali' build.log` (want 1) and the report at **46** fired,
not 45.

**Static AOD needs a passthrough shader index.** The app computes the index as
`opacity + type + mix_color + bg_opacity`, and **only `bg_opacity = 6`
(“Transparent”) is passthrough**:

```
moon   6  7 14 15 22 23 30 31 38 39 46 47
pause 54 55 62 63 70 71 78 79 86 87 94 95
```

Every preference defaults to `0`, so a **freshly wiped device sums to index 0**,
which deliberately fades the panel instead of retaining the screen. Retaining
the last screen is the *point* of static AOD, so the out-of-box default makes
the feature look broken. Set *Static Lockscreen Background Opacity →
Transparent*. Read the live value with
`adb shell getprop sys.linevibrator_type`.

**Ghosting on Opaque is E Ink, not a bug.** Residue is only erased by a full
refresh; the shader repaint alone does not do one. *"Set Refresh Mode to Clear
on Lock"* (`refresh_on_lock`, default off) issues `FORCE_CLEAR` 150 ms after
screen-off and cleans it, at the cost of a black/white flash. Overlay AOD looks
clean because it draws a full new frame and takes that path anyway.

This also removes the last reason to want a testkey base or someone else's
keys — see §6, which is now moot for this purpose.

---

## 3. What the patch set actually contains

### `services.jar` — no signing needed

- `PhoneWindowManager` — volume-key wake: `mVolumeWakeLock`,
  `mLastDownKeyEvent` / `mLastUpKeyEvent`, deferred down/up replay,
  `forceHideKeyguard()`, `handleWakeUpOnVolume`, `wakeUpFromPowerKey`
- `ColorFade` — replaces `SHADER_LIST` / `readFile` with the repo's
  `color_fade_frag.frag` and `color_fade_frag_pause.frag`
- `ColorDisplayService` + `ColorDisplayServiceInternal` — `mCdsi`,
  `mChangedBrightnessValue`, `isActivated()`
- `DisplayPowerController` — `mColorFadeOffAnimator`, `mLeadDisplayId`,
  `updateBurnInProtection`, `BrightnessMappingStrategy`, `HysteresisLevels`,
  `Object`/`ValueAnimator` neutering
- Vibrator — `startVibrationLocked` → `originalStartVibrationLocked` +
  `tempStartVibrationLocked`
- `overrideDurationScale(F)V` — animation kill

### `SystemUI.apk` — ~~requires testkey base~~ works on any base, see §2a

- `ImageWallpaperEngine` — adds `mInAmbientMode`, AOD static wallpaper
  drawing (`drawFrameOnCanvas`, `getWallpaperFlags`) — **fires on A16**
- `DozeSensors` / `DozeTriggers`, `ProximitySensor` / `ThresholdSensor` /
  `DevicePostureController` neutering — **fires on A16**
- `KeyguardViewMediator` bouncer-dozing wake removal — **fires on A16**
- per-state scrim alpha values (`AOD` → `mFrontAlpha = 0x0`, …) —
  **ROTTED, zero-match on A16** (all 5 `ScrimState` patches)

### Patch rot, and its repair (2026-08-13)

v19 reported **38 fired / 10 zero-match**. Each was checked against the
decompiled A16 SystemUI and services.jar rather than pattern-guessed. **v20:
42 fired / 0 zero-match.**

| Pattern | # | Verdict |
|---|---|---|
| `.*ScrimState.*` | 5 | **Retargeted** — enum ctors moved `<init>` → `<clinit>`; all fields intact |
| `.*Clock.*` | 1 | **Retargeted** — `AnimatableClockView` deleted, now `DigitalClockTextView` |
| `.*[Kk]ey[Gg]uard.*` (`darkAmount`) | 1 | **Removed** — patching it would *enable* an animation |
| `.*Notification.*Doze.*` | 1 | **Disabled** — redundant, already covered |
| `.*[Ww]allpaper.*` (services.jar) | 1 | **Disabled** — obsolete, nothing to patch |
| `VibrationScaler` | 1 | **Disabled** — mechanism changed; a tuning call, not a port |

**ScrimState.** The scrim is the dimming layer over lockscreen and AOD — pure
loss on a panel with no backlight. The A14 patch matched the enum constant name
inside `<init>`; on A16 the names appear only in `ScrimState.<clinit>`, which
builds each constant as an anonymous subclass (`ScrimState$1..$12`), with the
field writes in those subclasses' `prepare()`.

Subclass numbering is an R8 artefact that shifts whenever the enum changes, so
the mapping is derived at patch time from `<clinit>` **by pairing registers**:

```
new-instance v7, ScrimState$7;      <- register v7
const-string v8, "AOD"
sput-object  v7, ScrimState;->AOD:  <- same register
```

> **Trap.** Pairing by *order* is wrong: there are **13 name constants for 12
> subclasses** (`UNINITIALIZED` is the base instance, not a subclass), so an
> ordered zip is off by one and silently mislabels every state. Measured: an
> ordered zip claims `AOD → ScrimState$8`; the truth is `ScrimState$7`.

Verified at runtime in `state: AOD` — `frontScrim alpha=0.0 tint=0x0`,
`behindScrim alpha=0.0 tint=0x0`, against `UNLOCKED` where `behindScrim
tint=0xb9bebec6`. The tints are normally non-zero, so the AOD zeros are the
patch. `behindScrim`'s tint also going to zero is expected: in AOD's `prepare()`
`mFrontTint` and `mBehindTint` are written from the same source register.
Harmless, since AOD sets `mBehindAlpha = 0`.

**Clock.** A16 picks styling via parallel ternaries in
`DigitalClockTextView.animateDoze(boolean isDozing, boolean animate)`:

```
fontVariation = isDozing ? fontVariations.doze : fontVariations.lockscreen
color         = isDozing ? aodColor            : lockscreenColor
textSize      = isDozing ? aodFontSizePx       : lockScreenPaint.getTextSize()
```

Rewriting the two doze-branch reads to their lockscreen counterparts is the same
edit as the A14 rename. Font **size** is deliberately untouched — the A14 patch
only ever changed weight and colour, and forcing the larger lockscreen size into
the AOD layout risks clipping.

**`darkAmount` — do not "fix" this one.** Only three read sites survive: two in
`NotificationPanelViewController.dump()` (debug only) and one behavioural in
`NotificationStackScrollLayout.notifyHeightChangeListener()`:

```java
if (needsAnimation && mInterpolatedDarkAmount == 0f)
    mAnimateNextPositionUpdate = true;
```

Forcing that read to `0` makes the comparison **true** and *enables* an
animation — the opposite of what E Ink wants. Repairing it mechanically would
have been a regression.

**`updateGrayscale`.** R8 reduced `NotificationDozeHelper` to one field and zero
methods; the grayscale maths inlined into `StatusBarIconView.updateIconColor()`,
driven by `mDozeAmount`. The `.*Icon.*` patch that zeroes `mDozeAmount` still
fires, so icons already come out unsaturated.

**`VibrationScaler`.** The static `SCALE_FACTOR_*` constants are gone; A16
computes `Math.pow(mVibrationConfig.getDefaultVibrationScaleLevelGain(), level)`
for `level ∈ [-2,2]`. Raising that gain (1.4 → 1.8 takes level 2 from 1.96× to
3.24×) would reproduce the intent, but haptic strength is a judgement call, so
it is documented rather than guessed at. **Open if you want stronger haptics.**
This is *strength* only — the waveform patch itself is ported and on, below.

### Haptics — ported 2026-08-13, `A9_PATCH_VIBRATOR` back ON by default

The last piece of Damian's functionality not carried over. It writes the A9's
three LRA waveforms into `vndk.rc` and rewrites `startVibrationLocked` to select
between them via `sys.linevibrator_on`, keyed on the vibration's
`VibrationAttributes` usage.

**The original diagnosis was wrong.** The old comment said A16 had *removed*
`Vibration.callerInfo`. It had not — the field was **retyped**, and the method
signature changed as well:

| | A14 | A16 |
|---|---|---|
| parameter | `Vibration` | `SingleVibrationSession` |
| reaching CallerInfo | `p1.callerInfo` (field) | `p1.getCallerInfo()` (method) |
| CallerInfo type | `Vibration$CallerInfo` | `VibrationSession$CallerInfo` |
| `CallerInfo.attrs`, `USAGE_*`, return type | — | unchanged |

Dex resolves fields by name **and type**, which is precisely why the old patch
assembled cleanly, booted, and only then threw `NoSuchFieldError` on the first
haptic event. Stock A16 code in the same class settles it: `VibratorManagerService`
still reads `Vibration->callerInfo`, just typed `VibrationSession$CallerInfo`.

`p1`'s type is read from the method signature rather than hardcoded, so a future
rename of the session class cannot silently reintroduce the same failure.

> **Two gates, not one.** `patch-gsi.sh` had `A9_PATCH_VIBRATOR="${…:-0}"`, which
> overrode the patcher's own default. Flipping only the patcher produced a build
> byte-identical to the previous one — 42 fired / 0 zero-match, no
> `VibratorManagerService` touched. Check the log for the file, not just the exit
> code. With both flipped: **45 fired / 0 zero-match**.
>
> Note also that disabling this gate drops the **waveforms** too, since
> `add_pattern_to_initrc` is called from `patch_startVibrationLocked` — "off"
> meant no custom haptics at all. Measured before the fix: zero `linevibrator`
> entries in the device's `vndk.rc`.

**Verified on device, not merely at build time** — a clean boot proves nothing
here, since that is how the old patch failed:

| Check | Result |
|---|---|
| `vndk.rc` | 3 waveform triggers (was 0) |
| emitted smali | `getCallerInfo()` → `VibrationSession$CallerInfo` → `attrs` |
| `cmd vibrator_manager` | `sys.linevibrator_on = 1` (usage UNKNOWN) |
| real touch long-press | `sys.linevibrator_on = 2` (usage TOUCH) |
| `system_server` pid | 2247, unchanged across every trigger |
| logcat | no `NoSuchFieldError`, no `Vibration Error` |

The CLI cannot exercise the touch path — `cmd vibrator_manager` has no usage
flag and always reports `1`. Use a real long-press
(`input swipe X Y X Y 1200`) to see it select `2`.

`verify_scrim_patches()` was added because the generic ZERO-MATCH counter cannot
detect this failure mode: the action runs once per `ScrimState$N` file and
returns early for the ten states it does not target, so it always "fires".

### `Dialer.apk` — cosmetic only (same mechanism as §2a if wanted)

- call-UI tint

### `build.prop` and `vndk.rc` — version-independent, always portable

`update_vndk_rc()` is worth harvesting regardless of which tier we land in.
It is strictly larger than both the shell script's and ManDay's version:

- perms + `sysfs_leds` labels on **four** backlight nodes —
  `aw99703-bl-1/2` *and* `ktd3137-bl-3/4`
- accessibility service enabled by **appending** to
  `enabled_accessibility_services`, not clobbering it
- one-shot carrier reset and `doze_enabled 0`
- two init property triggers that wire the frontlight sliders:
  - `on property:sys.linevibrator_open=*` → `ktd3137-bl-3`, `aw99703-bl-1`
  - `on property:sys.linevibrator_short=*` → `ktd3137-bl-4`, `aw99703-bl-2`

---

## 4. A14 → A16 breakage — MEASURED, and much smaller than expected

**Result (2026-08-11): 19 of 21 patch groups fire on Android 16.** Measured by
running the instrumented patcher against MisterZtr LineageOS 23.2 (A16 QPR2)
with `A9_PATCH_SYSTEMUI=0 A9_PATCH_DIALER=0`. Output: `patch-report.json`.

Everything predicted below as "highest risk" in fact applied cleanly. The
pessimism in the original version of this section was wrong.

| Patch group | Target | Fired |
|---|---|---|
| `patch_GetBrightness` / `SetBrightness` / `GetSetBrightness` | `AutomaticBrightnessController\S*` | 3 / 3 / 8 |
| `patch_updatePowerStateInternal`, `patch_AddFadeOffInterpolator`, `patch_OverrideAnimatorScale`, λ | `DisplayPowerController[0-9]*` | 1 / 1 / 2 / 5 |
| `patch_ColorFade` | `ColorFade` | 1 |
| `patch_ColorDisplayServiceInternal` | `ColorDisplayService$*Internal*` | 1 |
| `patch_onColorTemperatureChanged` | `ColorDisplayService$*NightDisplay*` | 1 |
| `patch_interceptKeyBeforeQueueing`, `patch_phoneWindowManagerInit`, λ | `PhoneWindowManager` | 1 / 1 / 6 |
| `patch_windowManagerHandler` | `PhoneWindowManager*Handler` | 1 |
| `patch_startVibrationLocked`, `extract_invokeVibrationLocked`, λ | `Vibrator(Manager)?Service` | 1 / 1 / 1 |
| λ | `BurnInProtection*` | 1 |
| λ | `BatterySaverPolicy*` | 17 |

**Dead on A16 — 2 groups:**

| Patch | Target | Note |
|---|---|---|
| `patch_OverrideAnimatorScale` | `.*[Ww]allpaper.*\.smali` | the same action **does** fire on `DisplayPowerController` (×2), so only the wallpaper-animation variant is lost |
| λ | `VibrationScaler\.smali` | the `Vibrator(Manager)?Service` patches all fired, so vibration is only partly affected |

`ColorFade` **does still exist** in Android 16, contradicting the guess below.

### Base image is builder-independent

The same run against **Doze-off** LineageOS 23.2 produced **byte-for-byte the
same patch report** — 21 groups, **0 differing counts**, same 2 dead. So the
result is a property of Android 16 / LineageOS 23.2, not of one builder, and
switching base costs no rework.

**Base selected: Doze-off `LineageOS-23.2_GSI_treble_arm64-ab-VANILLA-20260603`.**
Chosen over MisterZtr because it is the only A16 image with a device-specific
report — theManDay ran it on an A9 and noted the Treble "Disable HW Overlays"
setting *sticks from early boot*, unlike the A14/A15 images. MisterZtr's only
advantage was a changelog line ("Added support for Android 11 vendors"), which
is weaker evidence than someone booting it on this device.

### Output verified (2026-08-11)

`patched-dozeoff.img`, 2,851,487,744 bytes (≈2.66 GiB — fits `system_b` at
`0xFDC7F000` ≈ 3.96 GiB with room to spare).

| Invariant | Expected | Measured |
|---|---|---|
| `SystemUI.apk` | **unchanged** — signature must stay intact | `6c404703…` both sides ✓ |
| `services.jar` | **changed** — patches applied | `c0757665…` → `e3aed50c…` ✓ |
| `/system/bin/a9_eink_server` | injected, 43,216 B | present ✓ |
| `/system/priv-app/a9service.apk` | injected | present ✓ |
| `vndk.rc` | `service a9_eink_server`, both `sys.linevibrator_open`/`_short` triggers, 4× `chcon u:object_r:sysfs_leds` | all present ✓ |
| `build.prop` | `HLTE556N`, `ro.sf.lcd_density=360`, blurs disabled | all set ✓ |

That SystemUI is untouched is the load-bearing check: it proves no signature
was broken, which is the whole reason Tier B exists.

### Instrumentation caveat

`FilePatch` patterns match many inner classes (`AutomaticBrightnessController$1`,
`$Injector`, …). A patch that correctly fires in one of them does not fire in
its siblings, so **per-file** zero-match logging is almost all false alarms.
The reporting was changed to aggregate across all matching files and only flag
a patch whose total is 0. Judge from the aggregate, never the log spam.

### Original predictions (kept for the record — mostly wrong)

The patcher matches on class/method/field/instruction patterns
(`Matcher.regex`), not offsets, so patches are individually re-portable. But
the repo has not moved since **2024-09-19** and targets Android 14. Anticipated:

- **Display brightness pipeline** was reworked across A15/A16 —
  `DisplayPowerController`, `BrightnessMappingStrategy`, `HysteresisLevels`
  are the highest-risk group.
- **`ColorFade` may not exist in A16** in a recognisable form; the screen-off
  animation path has changed. If so the shader swap needs a new home.
- **`PhoneWindowManager` key interception** has moved substantially.
- SystemUI's `ImageWallpaperEngine` and scrim controller have both been
  refactored.

Mitigation: the patcher currently logs and continues on non-matching patterns.
**Make zero-match patches loud** before porting anything, or breakage will be
silent — the same failure mode this project has been bitten by repeatedly.

---

## 4a. Prior art — someone has already done Tier B on A16

Reported on XDA (relayed 2026-08-11). **This is the single most important
input to the plan: Tier B is proven, by someone else, on this device.**

> I managed to run Android 16 successfully. […] **I disabled any modification
> to the System UI in damian's patcher as they are very likely to need an
> update for Android 16.** Keep in mind this is highly experimental.

Note the reason given is **framework churn**, not signing — an independent
second reason to drop the SystemUI patches, on top of §1. Working image
uploaded to
`https://drive.google.com/file/d/1cWqn9VqZ_-nyEUaOl3P4hk5_9G1xSj97/view`
(unverified provenance — useful mainly as a **reference to diff against**, to
see exactly which patches applied).

### First-boot procedure (from the same report)

The device may not be usable on the panel before these are set, so drive it
from a PC:

1. Connect with **`scrcpy`** — "at the first time you have to adjust some
   settings in the treble settings app to make screen work and maybe it stops
   before you get there."
2. Treble Settings → **Display Settings → Disable HW overlays = ON**
3. For calls: **IMS settings → Install IMS APK → Create IMS APN → Request IMS
   network → Force presence**
4. Network settings → preferred network **LTE** → reboot

Caveat carried from ManDay's README: the Treble app will try to reset
"Disable HW Overlays" unless it is set via Developer Options or the Treble app
itself. theManDay separately reported that on Doze-off's LOS 23 build the
setting *does* stick from early boot, unlike the A14/A15 images.

### Confirms the signing analysis independently

> using `adb remount` to place ferreira's eink center in `priv-app` to avoid
> wrong signature → **right way would be to build whole GSI from scratch with
> my own keys, but that is for later**
> — <https://xdaforums.com/t/hisense-a9-root-snapdragon-662.4495809/post-90351157>

> the pipeline on GitHub so that @dferreira's app builds. **It gets signed with
> the AOSP (test) platform keys, so embedding it in the system image as
> `priv-app` works.** — <https://github.com/ManDay/hisense>
> — <https://xdaforums.com/t/hisense-a9-root-snapdragon-662.4495809/post-90359180>
> (battery fixes)

The second quote is exactly the fingerprint match measured in §1: ManDay's CI
pulls `platform.pk8` / `platform.x509.pem` from
`android.googlesource.com/platform/build` — the same public AOSP test keys as
Damian's `platform.x509.pem`, fingerprint `C8:A2:E9:BC…`.

**Adopted plan:** Tier B on LineageOS 23.2 now, SystemUI and Dialer patches
disabled — matching the known-working configuration. A from-source LineageOS
build with own keys becomes **Phase 6**, and is the only route to the three
remaining patches (AOD static wallpaper, doze-sensor neutering, Dialer tint).

## 5. Open risk: Vulkan — an app-compatibility risk, not a boot risk

XDA post #3325 (2026-06-22, theManDay): A16 mandates Vulkan; the VNDK appears
not to provide it, so apps that use it crash. The post is truncated and the
outcome is unknown.

**A successful boot does not clear this.** The reported failure mode is
individual applications crashing when they call into Vulkan — so the working
A16 image in §4a is not evidence either way. The concrete risk is a
**regression against the current A14/A15 setup**: apps that work today stop
working, and the loss may not show up until you happen to open one.

Adreno 610 *does* expose Vulkan 1.1 in the SD662 Android 11 vendor blobs, so
this is most likely a loader/VNDK-boundary problem — the A16 Vulkan loader
failing to load a driver built against a much older ABI — rather than missing
hardware support. **Unverified.**

### ANSWERED 2026-08-11 — measured on the booted A16

```
/vendor/lib/hw/vulkan.adreno.so     ← 32-bit, EXISTS
/vendor/lib64/hw/vulkan.*.so        ← DOES NOT EXIST
ro.hardware.vulkan                  ← empty
GLES: Qualcomm Adreno (TM) 610, OpenGL ES 3.2 V@0502.0 (Date:12/29/20)
vulkan_renderengine: false
```

**The Hisense vendor ships no 64-bit Vulkan driver at all.** This is the
concrete cause of XDA #3325 — every 64-bit app that requires Vulkan will fail,
and **no choice of GSI can fix it**; it is a vendor blob limitation of the
device. OpenGL ES 3.2 works normally, so anything GLES-based is unaffected.

SwiftShader remains the only route to a working 64-bit Vulkan. Untested.

### How to actually test it

Boot-time / static:

```sh
getprop ro.hardware.vulkan                    # which driver the loader wants
ls -l /vendor/lib64/hw/vulkan.*.so            # is it even present
logcat -d | grep -iE 'vulkan|adreno|loader'   # load failures at boot
dumpsys SurfaceFlinger | grep -i -A2 renderengine
getprop debug.hwui.renderer                   # is HWUI itself on Vulkan
```

App-level — this is the part that matters and must be done by hand:

- Run the apps you actually care about, not a synthetic test. Prioritise
  anything with a video player, a browser/WebView, a map, or a PDF/comic
  renderer — these are the common Vulkan consumers on an otherwise 2D device.
- Compare against the same apps on the current install before flashing, so
  "broken on A16" is distinguishable from "was already broken".

### Mitigations if it is broken

- **SwiftShader** (`ro.hardware.vulkan=swiftshader`) — a CPU Vulkan
  implementation. Normally rejected for being far too slow, but on an E Ink
  panel refreshing at ~1 Hz the frame-rate cost is largely irrelevant. This is
  a much more attractive fallback here than on a normal phone.
- Force HWUI to GLES so at least the system UI is unaffected.
- Neither helps an app that hard-requires a real Vulkan device.

This should be tested **via DSU, before any flash** — DSU touches nothing, so a
bad answer costs nothing.

---

## 6. Why there is no local source build

Measured on this box: **8 cores, 7 GB RAM**, 887 GB free on the (then) WSL ext4 root; that box was the retired Windows/WSL setup.
A LineageOS GSI build needs ~250 GB disk (fine) and realistically 32–64 GB RAM
(not fine — Soong/ninja at `-j8` on 7 GB will thrash and OOM). `docker` there was
Docker Desktop via WSL integration and inherited the same memory ceiling. The
toolkit now runs on a NixOS VM; AOSP builds stay on GitHub Actions.
GitHub Actions standard runners have ~14 GB of usable disk, far too little even
after aggressive cleanup.

So Tier C means a rented cloud VM. Do not plan around it unless Phase 1 rules
out every testkey-signed candidate.

---

## 7. Phases

### Phase 0 — device safety (blocking)

The device state changed under us and is not yet reconciled:

- `current-slot = b`; **`system_a` does not exist**. `scripts/abort-to-slot-a.sh`
  would strand the device — do not run it.
- The ROM on `system_b` has **not been fingerprinted**. Confirm
  `ro.build.fingerprint` before assuming it is the Lineage 21 build.
- **`super` has still never been backed up.** Every flashing step below writes
  `system`, which lives in `super`. Back it up first.
- adb currently reports `unauthorized` — accept the debugging prompt.

### Phase 1 — choose the base GSI

For each candidate: download, `debugfs`-extract `SystemUI.apk` and
`framework-res.apk`, read the signing cert with the §1 recipe, and record it.

Candidates: Doze-off LineageOS 23.2 (A16 QPR2), TrebleDroid AOSP 16, Infinity-X
3.x, MistOS 4.7, ponces LOS 23, XDA "LineageOS 23.0 userdebug released-keys".

Then DSU-boot the best testkey-signed candidate — **DSU touches nothing**, so
this settles "does A16 boot on the A9" and "is Vulkan fatal" at zero risk to
the working install.

Gate: a testkey-signed, phh-derived, A16 arm64 A/B image that boots under DSU.

### Phase 2 — toolchain and a known-good baseline

Measured 2026-08-11 — what resolves on this box:

| Tool | Source | Status |
|---|---|---|
| `apktool` | `nixpkgs#apktool` | **2.10.0** ✓ |
| `java` | `nixpkgs#jdk17` | **openjdk 17.0.15** ✓ |
| `apksigner` | `nixpkgs#apksigner` | **34.0.5** ✓ (standalone attr) |
| `simg2img`, `lpmake`, `lpunpack` | `nixpkgs#android-tools` | 35.0.1 ✓ |
| `e2fsck`, `resize2fs`, `debugfs` | system | ✓ |
| `setfattr` | system (`attr`) | ✓ |
| **`zipalign`** | — | **absent**; not a standalone nixpkgs attr |

`zipalign` is **non-fatal** — `JarPatcher.patch()` catches its failure and logs
`"Failed to align the apk"`. Residual risk: APKs containing uncompressed native
libraries must be aligned on modern Android. SystemUI is not expected to have
any, but if a signed target misbehaves, pull `zipalign` from
`androidenv.androidPkgs.build-tools`.

Working shell:

```sh
nix-shell -p apktool jdk17 apksigner android-tools python313 e2fsprogs attr openssl p7zip
```

Note `replace_file()` in `smali_patcher.py` **skips missing sources with a
warning** rather than failing, so the GApps / F-Droid / `ims-caf-u.apk` /
`hosts` replacements that ship no source file in the repo are harmless no-ops.

Then run `patch_system_img.py` unmodified against the **Android 14** image in
`14/` — the framework it was written for. This proves the harness end-to-end
and gives a baseline where every patch is expected to match. Do not start
fighting A16 deltas before this passes.

Gate: patcher runs clean on A14 and produces a bootable image.

### Phase 3 — port patch groups, one at a time

Order by value-per-risk, `services.jar` first since it needs no signing:

1. instrument for zero-match reporting
2. `overrideDurationScale` + vibrator (small, isolated)
3. `PhoneWindowManager` volume-key wake
4. `ColorFade` shaders
5. `DisplayPowerController` / brightness / burn-in (hardest)
6. SystemUI: AOD wallpaper, doze sensors, scrims *(Tier A only)*
7. Dialer tint *(Tier A only, cosmetic)*

### Phase 4 — integrate the daemon layer

Harvest Damian's full `update_vndk_rc()` block. Decide between
`a9_eink_server` (Damian) and `a9srv` (ManDay) as the sysfs mediator —
ManDay's is maintained, is 150 lines of readable C, and needs no APK signing;
Damian's is a prebuilt blob with no matching source in the repo except
`eink_daemon.c` (348 lines, the Magisk root-service variant).

### Phase 5 — flash and verify per feature

Flash `system` from **fastbootd**; note safety rule 3 — *recovery* flashes must
come from bootloader fastboot, but `system` is a logical partition and needs
fastbootd. Verify each ported feature individually against a checklist.

---

## Appendix — reproduction commands

```sh
# extract a file from a raw ext4 GSI without root
debugfs -R "dump /system/framework/services.jar out.jar" system.img

# read an APK's signing certificate
nix-shell -p openssl unzip --run '
  unzip -p SystemUI.apk META-INF/CERT.RSA \
    | openssl pkcs7 -inform DER -print_certs -noout'

# confirm image is raw (not sparse) ext4
file system.img   # → "Linux rev 1.0 ext2 filesystem data (extents)"
```

## Sources

- `damianmqr/A9PatchingService` — full patcher, last commit 2024-09-19
- `ManDay/hisense` — `a9srv` daemon + minimal GSI adaptation, 2026-06-14
- `Doze-off/lineage_treble` — LineageOS 23.2 / A16 QPR2 releases
- XDA "[Hisense a9] Root (Snapdragon 662)" posts #3321–#3325
