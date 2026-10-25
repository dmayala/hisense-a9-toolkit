# A16 composition path, boot hooks, and instrumentation — findings

**Date:** 2026-08-14
**Status:** **ROOT CAUSE FOUND 2026-10-11 (slot-b build), see §12.** The Treble app
re-enables HWC composition at boot and on every start, and with HWC on, frames
composed in hardware never reach the E Ink engine inside the vendor composer, so
the glass freezes. Reproduced both ways and fixed on the device by a preference;
the image fix is still to do. **§6 is wrong for that build.** §10 is kept as the
history of the investigation.

> **Update 2026-10-11.** A software panel sensor now exists: the E Ink engine runs
> inside the vendor HWC composer and its panel state can be read out
> ([epd-panel-screenshot.md](epd-panel-screenshot.md), `scripts/epd-snap.sh`).
> Measurements on the current slot-b build (testkey LOS 23.2) are in §11. They
> change how §7, §8 and §10 apply there: on that build the §8 counters cannot fire.

Written after an investigation in which four agents spent most of a day
rediscovering facts that existed nowhere in `docs/` or `CLAUDE.md`. Each section
below is something that cost real time to find and will cost it again if it is not
written down.

Every claim is labelled **MEASURED** (with the file and command that produced it) or
**INFERRED**. Sources are the image actually booted (`v24.img`), this repo, the stock
firmware package, and `TrebleDroid/platform_frameworks_native` branch
`android-16.0.0_r4-td` — which is the real A16 GSI framework. Reproduction commands
are in the appendix.

---

## 1. The image deliberately runs with HWC composition DISABLED

**MEASURED** — `v24.img` → `/system/etc/init/vndk.rc` lines 26-30, extracted with
`debugfs`:

```
on property:sys.boot_completed=1
    setprop sys.linevibrator_type 96
    exec_background u:r:phhsu_daemon:s0 root -- /system/bin/cmd overlay enable me.phh.treble.overlay.misc.aod_systemui
    exec_background u:r:phhsu_daemon:s0 root -- /system/bin/service call SurfaceFlinger 1008 i32 1
    start a9_eink_server
```

Injected by `app/external_scripts/system_img_patcher/patch_system_img.py:2219-2220`.
`sys.linevibrator_type` is injected separately by `tools/finalize-image.sh:96-99`,
which is why it lands first in the block.

Two facts that are easy to get backwards, and one team member did:

- **SurfaceFlinger code 1008 with `i32 1` DISABLES hardware composition.** **MEASURED**,
  not inferred: `SurfaceFlinger.cpp:7121` is literally `case 1008: // Toggle forced GPU
  composition.` with body `sfdo_forceClientComposition(data.readInt32() != 0)` at `:7122`
  (definition at `:9400`). So `i32 1` forces client composition, i.e. HWC off. HWC-off is
  this panel's intended state. **If you find HWC *enabled*, that is the anomaly.**
- **`me.phh.treble.overlay.misc.aod_systemui` is ENABLED on every boot.** So finding
  it *disabled* is positive evidence that something disabled it since boot — it is
  not the pristine post-flash state.

Both are re-asserted every boot. **Nothing recorded this before this doc except those
two `.py` lines.**

> **The artefact in the repo is not the artefact that ships.** **MEASURED** — the
> `vndk.rc` inside `system_patched.img` (the `patch-gsi.sh` output sitting in this repo)
> does **not** contain `setprop sys.linevibrator_type 96`; its `boot_completed` block
> starts straight at the `cmd overlay enable` line. That is not a contradiction with the
> `v24.img` listing above — the setprop is injected *later*, by
> `tools/finalize-image.sh:96-99`. So **anyone grepping `system_patched.img` expecting the
> final boot payload will be misled.** Grep the flashable image. This is the same shape of
> trap as §3: an intermediate artefact that looks authoritative and is not.

Note `/system/bin/service call` writes `service->getInterfaceDescriptor()` looked up
from the binder at runtime, so it always sends the correct interface token. That is
why the init hook works where the Treble app's hardcoded token does not (§6).

## 2. HAZARD: never restart SurfaceFlinger on this device

**MEASURED** — `v24.img` → `vndk.rc` lines 103-117 carry a *deliberately
commented-out* rule and a comment recording why (this is upstream phh's comment,
inherited in the GSI, not written by this project):

```
# ro.sf.lcd_density is set by rw-system.sh during post-fs, before
# surfaceflinger starts.  the previous `restart surfaceflinger` here
# killed the already-running SF to pick up the new density, but that
# caused the DRM pipeline to reset without a clean panel power-off.
# on devices with an external backlight IC (e.g. ktz8866) the kernel
# panel driver's state tracking desyncs from the hardware: it believes
# the bias/boost is still enabled from the bootloader, so the power-on
# path in the second SF never calls bias_enable(1).  the backlight IC
# stays off and the display is permanently blank.
...
#on property:ro.sf.lcd_density=*
#    restart surfaceflinger
```

**MEASURED** — this device carries external backlight ICs: `vndk.rc` chmods, chowns
and chcons `/sys/class/backlight/aw99703-bl-1`, `aw99703-bl-2`, `ktd3137-bl-3` and
`ktd3137-bl-4`.

**INFERRED** — that the A9 is therefore in the hazard class the comment describes.
The comment names `ktz8866` as its example, not `aw99703`/`ktd3137`; the shared
property is "external backlight IC with driver-side state tracking". The inference is
strong but it has not been tested on this device, and the cost of testing it is a
permanently blank panel.

**Consequence: `setprop ctl.restart surfaceflinger` must not appear in any repair or
diagnostic path on this hardware.** Prefer live-read properties and read-only dumps.

### Proposed `CLAUDE.md` safety rule (for director to place; I have not edited it)

> 11. **Never restart SurfaceFlinger on this device** — not
>     `setprop ctl.restart surfaceflinger`, not `killall surfaceflinger`, not
>     `stop`/`start` of any service that takes SF down. Restarting SF resets the DRM
>     pipeline without a clean panel power-off. This device has external backlight ICs
>     (`aw99703-bl-1/2`, `ktd3137-bl-3/4`); on such devices the kernel panel driver's
>     state tracking desyncs from the hardware — it believes the bias/boost is still
>     enabled from the bootloader, so the second SF's power-on path never calls
>     `bias_enable(1)`, **the backlight IC stays off and the display is permanently
>     blank until a reboot.** Documented upstream in the GSI's own `vndk.rc`
>     (lines 103-117), where the `restart surfaceflinger` rule is commented out for
>     exactly this reason. See
>     [a16 composition findings §2](docs/a16-composition-and-epd-findings.md).
>     Also note the inverse of the usual assumption: **HWC composition is deliberately
>     DISABLED on this panel** (`service call SurfaceFlinger 1008 i32 1`, re-asserted
>     every boot from `vndk.rc:29`). HWC *enabled* is the anomaly.

## 3. TRAP: "0 patches fired" counts SMALI PATCHES ONLY

This single misreading sent the whole team down a wrong path for a long stretch. It
must be impossible to make again.

**MEASURED** — `PATCH_REPORT` is defined in
`app/external_scripts/system_img_patcher/smali_patcher.py:15` and appended **only**
from `smali_patcher.py:275`, `smali_patcher.py:333` and
`patch_system_img.py:249`. The report printed at `patch_system_img.py:2384-2393`
therefore accounts for **dex/smali pattern patches and nothing else.**

These all run and report **nothing** in that count:

| Step | Where | Gated by |
|---|---|---|
| `update_vndk_rc()` | `patch_system_img.py:2382` | **nothing — always runs** |
| `add_signer_to_mac_permissions()` | `:2364` | nothing |
| `add_default_runtime_permissions()` | `:2368` | nothing |
| file replacement (`a9service.apk`, overlays, `a9_eink_server`) | various | various |
| `patch_systemui()` | `:2356` | `A9_PATCH_SYSTEMUI=1` (default **off**) |
| `patch_services_jar()` | `:2377` | `A9_PATCH_SERVICES` (default **on**) |

**So "0 patches fired" does NOT mean "the image is unpatched".** It means no smali
pattern matched. A 0-patch image still carries the full vndk.rc boot payload,
re-signing, mac_permissions and default runtime permissions.

**Suggested hardening** (not implemented — needs director's call): have the final
report print the non-smali steps explicitly, e.g. a line per step with fired/skipped,
so the summary describes the image rather than one layer of it.

## 4. A build with 0 smali patches has ColorFade with its kill-switch REMOVED

**MEASURED** — `v24.img` → `/system/framework/services.jar` (22,940,029 B), string
counts:

| String | Count | Meaning |
|---|---|---|
| `mColorFadeEnabled` | 2 | stock ColorFade **is present** in Android 16 |
| `ColorFadePrepared` | 3 | ditto |
| `ColorFadeLevel` | 7 | ditto |
| `a9ColorFadeEnabled` | **0** | our runtime kill-switch is **absent** |
| `sys.linevibrator` | **0** | the sentinel is read by **nobody** |
| `debug.a9.colorfade` | **0** | absent |

Byte-size identical with identical counts to `system_patched.img` in this repo, so
both images share the same unpatched `services.jar` (cross-checked with epd).

**Consequence, and this is the important part:** `vndk.rc:27` faithfully sets
`sys.linevibrator_type 96`, but the helper that consumes the sentinel does not exist,
so the write is dead. **v24 is the only A9 build on which ColorFade cannot be turned
off.** On a properly patched build sentinel 96 suppresses it.

So a 0-smali-patch build is **not** "stock GSI plus our payload" — it is a build with
a specific safety deliberately removed. Any symptom that a correctly patched build
masks will be *unmasked* on such a build. Do not use one as a "neutral" boundary
test without accounting for that.

## 4b. ColorFade on the Doze-off GAPPS v2 build (2026-10-11)

Image: `a9-los23.2-dozeoff-gapps-20260603-hwcguard-v2.img` (both slots; phone on slot a,
up 33 min when measured). Trigger: `scripts/a9-aod-setup.sh` printed
`mColorFadeEnabled=true` and said it must be false.

**Verdict: stale check, not a build defect.** The kill-switch is present; the check
assumed a hard-disable that the patch never was.

**What the field means.** **MEASURED** (AOSP `android16-release`,
`services/core/java/com/android/server/display/`): in `DisplayPowerController.java`,
`mColorFadeEnabled` is a `private final boolean` (:289) set once in the constructor
(:665) as `mInjector.isColorFadeEnabled() && !config_displayColorFadeDisabled`;
`isColorFadeEnabled()` is `!ActivityManager.isLowRamDeviceStatic()` (:3365-3367).
It gates creating the `ColorFade` object (:1002-1004), the screen-off animation
(:2326, :2448: if false, `setColorFadeLevel(0)` immediately and no
`prepareColorFade`) and `dump()` (:2656). `DisplayPowerState` holds the actual state
(`mColorFadePrepared/Level/Ready/DrawPending`, :74-77). So stock = `true` on any
non-low-RAM device.

**What the patch changes.** **MEASURED** `patch_system_img.py:63-111`,
`:1594-1603`: injects `static boolean a9ColorFadeEnabled()` into the DPC class and
rewrites every read of the field (incl. the dump line) to call it. The helper is
`getBoolean("debug.a9.colorfade", getInt("sys.linevibrator_type",0) != 96)`.
Not a hard disable: it is `false` only when the sentinel is 96 (app-selected overlay AOD,
mode 2) or `debug.a9.colorfade=0`. `patch-report.json`: `add_a9ColorFadeEnabled_helper`
fired 1, `patch_ColorFadeEnabledFromProp` fired 7 (matches the 7 non-constructor
read sites in the source above, with the dump line one of them). Both gates
default ON and were (`patch-gsi.sh:208-209`).

**Kill-switch in the shipped image.** **MEASURED**: `debugfs` dump of
`/system/framework/services.jar`, unzip, `grep -c -a` per dex: `a9ColorFadeEnabled`
1 (classes.dex), `debug.a9.colorfade` 1 (classes.dex), `sys.linevibrator` 1 in each of
the 3 dexes, `mColorFadeEnabled` 2 (classes.dex). Contrast §4's v24: 0/0/0. So the
helper exists. `vndk.rc:28` has `setprop sys.linevibrator_type 47` and no
`debug.a9.colorfade` line (correct: finalize leaves MODE empty).

**Phone state** (read-only, the A9): **MEASURED**
- `sys.linevibrator_type` = `0`; `debug.a9.colorfade` unset; no other colorfade props.
  Init set 47 at boot_completed, so something later wrote 0: `a9_eink_server` contains
  the string `sys.linevibrator_type` (it is the stl writer; a9service sends stl). **INFERRED**:
  the app sent `stl0`; I did not see the command in logcat.
- helper = (0 != 96) = true, so `mColorFadeEnabled=true` is exactly what the patch
  predicts. `dumpsys display`: `mScreenState=OFF`, `mColorFadePrepared=true`,
  `mColorFadeLevel=0.0`, `mColorFadeReady=true`, `mColorFadeDrawPending=false`
  (a completed screen-off fade, not stuck).
- `dumpsys power`: `mWakefulness=Dozing`, `mDozeAfterScreenOff=true`, dream:doze held.
- Settings: `doze_always_on=0`, `doze_enabled=0` (vndk.rc one-shot
  `disable_ambient_display` marker exists, dated 15:56), AOD overlay `aod_systemui`
  enabled, a9service accessibility service enabled. Note the a9-aod-setup.sh writes
  these as 1, so it had not been (successfully) applied to the current /data.

**Is anything wrong for the user?** Not shown by measurements. ColorFade is active
by design in mode 1 while the sentinel is not 96. Caveat **INFERRED**: index 0 is not a
passthrough index (passthrough = 6,7,14,...,47,...), so with the sentinel at 0
a screen-off may fade to black/white rather than retain the last screen with a
moon glyph. I did not observe the panel (no visual access), so that is unconfirmed.
Overlay AOD (mode 2) is not selected right now (sentinel != 96, doze settings 0),
so it would not be drawn: that is configuration, not an image defect.

**Fix.** Script expectation only (implemented): `a9-aod-setup.sh` now reads
`sys.linevibrator_type` and `debug.a9.colorfade` and reports `true` as expected
unless the sentinel is 96 (then it flags a missing kill-switch). No patcher or
image change is needed. Proposed follow-ups (not done, nothing built or flashed):
(a) if the app should not leave the sentinel at 0 after boot, that is an a9service
behaviour to look at (it overwrote the boot value 47); (b) to make 0 harmless,
`finalize-image.sh` cannot help since the app rewrites it. (c) to get overlay AOD
set `doze_always_on=1`, `doze_enabled=1` and select overlay AOD in the app so it
sends stl96; then expect `mColorFadeEnabled=false`.

## 5. The shipped Treble app is a major fork — upstream analysis does not transfer

**MEASURED** — `v24.img` → `/system/priv-app/TrebleApp/TrebleApp.apk` is
**14,834,868 B**, 4 dex files, **138 pref keys across 23 pref screens**. Upstream
`phhusson/treble_app` master `0c45670` (current upstream HEAD, Aug 2024) has ~30 keys
and 15 screens. For comparison the `TrebleApp.apk` in `14/` (deleted from $A9_DATA 2026-10-10; original on the Windows PC) is 1.6 MB — also not the
shipped build.

The whole keyspace was renamed:

| upstream `0c45670` | shipped in v24 |
|---|---|
| `key_misc_aod` | `key_display_aod` |
| `key_misc_no_hwcomposer` | `key_display_no_hwcomposer` |
| `key_misc_display_fps` | `key_display_display_fps` |
| `key_misc_headset_devinput` | `key_audio_headset_devinput` |
| `key_misc_mobile_signal` | `key_telephony_mobile_signal` |
| `key_misc_bluetooth` | `key_bt_workarounds` |
| `key_misc_remotectl` | `key_debug_remotectl` |
| `key_misc_restart_systemui` | `key_ui_restart_systemui` |
| `key_misc_fod_color` | `key_ui_fod_color` |
| `key_misc_linear_brightness`, `_minimal_brightness`, `_backlight_scale`, `_disable_buttons_backlight` | `key_backlight_*` |
| `key_misc_force_navbar_off` | **key gone** (`persist.sys.phh.mainkeys` remains) |
| `key_misc_fps_divisor` | **gone entirely** — zero hits for `fps_divisor` in the shipped dex |

The fork also adds a dedicated `res/xml/pref_display.xml` with a **"RENDER ENGINE"**
category exposing SurfaceFlinger tunables that upstream has no equivalent for:
`key_display_sf_latch_unsignaled`, `key_display_sf_auto_latch_unsignaled`,
`key_display_sf_disable_backpressure`, `key_display_disable_sf_gl_backpressure`,
`key_display_disable_sf_hwc_backpressure`, `key_display_sf_renderengine_backend`,
`key_display_sf_blur_algorithm`, `key_display_dynamic_fps`.

**Anyone reasoning from upstream `treble_app` about this device will be wrong about
key names, defaults, and which screens exist.** Read the shipped dex.

Note also (**MEASURED**) that of the five `persist.sys.phh.sf_*` props the app
exposes, only **one** is read by SurfaceFlinger in `android-16.0.0_r4-td`:
`persist.sys.phh.enable_sf_hwc_backpressure`. The other four do not appear in
`SurfaceFlinger.cpp` at all. `vndk.rc:149-186` mirrors them into `debug.sf.*` names,
but SF reads those only at startup, and nothing in the boot path restarts SF (§2), so
writing them cannot affect a running system. `persist.sys.phh.dynamic_fps` maps to a
`ro.` property, so `setprop` on it fails silently — doubly inert.

## 6. Every `surfaceFlinger.transact` in the Treble app is a silent no-op on A16

> **WRONG on the slot-b testkey LOS 23.2 build (measured 2026-10-11, §12).** There,
> starting the Treble app logs `PHH: Set surface flinger hwc overlay to true` and
> `service call SurfaceFlinger 1010` shows `mDebugDisableHWC` going 1 → 0, so its
> 1008 call **does** reach SurfaceFlinger, even though its dex contains only the
> legacy `android.ui.ISurfaceComposer` string. The descriptor reasoning below
> doesn't hold for that build. It may hold for v24 but was never measured there
> either: it was inferred from strings. Treat it as unverified everywhere.

**MEASURED** — the shipped `classes.dex` contains exactly one SurfaceComposer
interface descriptor, `android.ui.ISurfaceComposer` (the legacy C++ name), and zero
`android.gui.` variants. `/system/bin/surfaceflinger` from the patched A16 image
contains `android.gui.ISurfaceComposer` ×2 and **zero** occurrences of the legacy
name (measured by epd from both binaries).

**MEASURED** — `SurfaceFlinger.cpp` in `android-16.0.0_r4-td` (10,638 lines) shows the
backdoor switch is behind an interface check:

```
7086    if (const status_t error = CheckTransactCodeCredentials(code); error != OK) return error;
7090    status_t err = BnSurfaceComposer::onTransact(code, data, reply, flags);
7092    CHECK_INTERFACE(ISurfaceComposer, data, reply);
7095    if (CC_UNLIKELY(uid != AID_SYSTEM &&
7096            !PermissionCache::checkCallingPermission(sHardwareTest))) -> PERMISSION_DENIED
7121    case 1008: // Toggle forced GPU composition.
7122        sfdo_forceClientComposition(data.readInt32() != 0);
```

> **Cite TrebleDroid, not AOSP.** An earlier revision of this doc gave AOSP `main`
> line numbers (9,466 lines) under a header that names TrebleDroid — a reader
> following the stated source landed on unrelated code. The two files differ by
> ~1,200 lines. Only TrebleDroid coordinates appear below; it is the framework that
> actually ships. The *substance* was unaffected.

Code 1008 still exists in A16. The app **is** `AID_SYSTEM` (it declares
`sharedUserId="android.uid.system"`) so it would have passed the permission check.
**The interface descriptor mismatch is the sole blocker**: `CHECK_INTERFACE` fails,
the transact returns `PERMISSION_DENIED`, Java turns that into `SecurityException`,
and the app's `catch (Exception)` swallows it with a `Log.d`.

**So the Treble app cannot toggle HWC and cannot force a display mode**
(`forceFps`, code 1035) — every SF backdoor call it makes is inert. If you later
suspect that app of display misbehaviour, this rules out the whole SF-transact
surface. What it *can* still do on A16: `SystemProperties.set` on `persist.sys.*`,
`Settings.Global`/`Secure` writes, and `IOverlayManager` RRO toggles.

## 7. The `commit()` rate-limiter at `SurfaceFlinger.cpp:2894` — intended, not a bug

**MEASURED** — `TrebleDroid/platform_frameworks_native` `android-16.0.0_r4-td`,
`services/surfaceflinger/SurfaceFlinger.cpp:2894`, inside `SurfaceFlinger::commit()`
(function starts line 2845, runs every frame):

```cpp
if (base::GetBoolProperty("persist.sys.phh.enable_sf_hwc_backpressure"s, true)
        && pacesetterFrameTarget.isFramePending()) {
    if (mBackpressureGpuComposition || pacesetterFrameTarget.didMissHwcFrame()) {
        mScheduler->getVsyncSchedule()->getTracker().onFrameMissed(
                pacesetterFrameTarget.expectedPresentTime());
        const Duration slack = TimePoint::now() - pacesetterFrameTarget.frameBeginTime();
        scheduleCommit(FrameHint::kNone, slack);
        return false;      // composes NOTHING this frame
    }
}
```

Note the outer gate reads the **`persist.` property directly, per frame** — not the
`debug.sf.*` mirror. So it is live-read: `setprop` takes effect immediately, with no
SF restart and no reboot.

**MEASURED** — `mBackpressureGpuComposition` comes from
`debug.sf.enable_gl_backpressure` (`SurfaceFlinger.cpp:525`), read **once** at SF
construction, default **true**, and this project never sets it (zero hits for
`backpressure` in `patch_system_img.py`, `tools/*.sh`, and all four `build.prop`s in
`v24.img`). So the inner condition is `true || anything` — **always true — and
`didMissHwcFrame()` is dead code on our build.**

**MEASURED** — `Scheduler/src/FrameTargeter.cpp` (note the `src/` path component):

```
154   mFramePending = fence.fenceTime != FenceTime::NO_FENCE &&
155           isFencePending(fence.fenceTime, graceTimeForPresentFenceMs);
219   status_t status = fence->wait(graceTimeMs);  return status == -ETIME;
```

`mFramePending` is recomputed every frame from the *previous* frame's present fence,
and is true only while that fence is unsignalled. `graceTimeForPresentFenceMs`
computes to 0 or 1 (lines 140-150).

**So the whole thing reduces to:** *if HWC backpressure is enabled and the previous
present fence has not signalled within 0-1 ms, skip this frame.* That is a
rate-limiter pinning SurfaceFlinger to the panel's real present rate. It is intended
throttling. It fires constantly on a panel with hundreds-of-ms present times, and the
device renders fine at boot regardless.

**It is a throttle, not a latch.** An earlier hypothesis held that it self-sustains
(composing nothing keeps the frame pending forever); that is wrong and was withdrawn —
`mFramePending` is fence-driven, so it clears when the hardware finishes.
**A permanent freeze at this site requires a present fence that never signals, which
is a driver/HWC fault.** In other words a permanent stall here *implies* the
below-SurfaceFlinger hypothesis; the two are not independent.

> ### Do not "harden" this into a new bug
>
> I recommended setting `persist.sys.phh.enable_sf_hwc_backpressure=false` in the
> image as E Ink hardening. **That recommendation is withdrawn.** Given the above,
> this property is the only thing rate-limiting SF to the panel. Disabling it lets SF
> commit without waiting for a display that takes hundreds of ms per present, risking
> buffer-queue backup and growing latency — a *different* pathology, not a repair.
>
> It remains a legitimate **diagnostic**: it is live-read, so it applies instantly.
> But capture the dumpsys samples in §8 *first* (the repair destroys the evidence), it
> is a `persist.` prop so the state is durable and must be reverted explicitly, and if
> it restores rendering the conclusion is *"the present fence is not signalling"* —
> the fix then belongs in the panel/HWC layer, not in this property.

## 8. Instruments

All read-only. None restarts SurfaceFlinger (§2).

**Missed-frame counters** — `FrameTargeter.cpp:212-214` emits these into
`dumpsys SurfaceFlinger` in a deliberately stable, greppable format. Take **twice,
~5 s apart**:

```
adb shell dumpsys SurfaceFlinger | grep -i "missed frame count"
```

**"Total missed frame count" is the primary read.** `mFrameMissed = mFramePending || …`
(`FrameTargeter.cpp:162`), incremented at `:180`, so it is a strict superset of §7's
outer gate. Do **not** read a static "HWC missed frame count" as "the abort isn't
firing" — `didMissHwcFrame()` does not gate the abort on our build (§7).

The counters live in `beginFrame()` (spans `:99`-`:184`, increments at `:180-182`), **not**
downstream of the `commit()` abort at §7. So they keep climbing even when `commit()`
returns false — the instrument stays valid in exactly the case we care about.

### Reading the HWC/GPU split — two traps

The split does genuinely read composition mode: the `Hwc` bit comes from
**layer-level** `state.usesDeviceComposition`, not from HWC presenting the framebuffer
target (`SurfaceFlinger.cpp:3374-3382`). But:

**Trap 1 — reuse sets neither sub-counter.** Note the `if`/`else` at `:3378-3381`:

```cpp
if (state.usesDeviceComposition)      flags |= CompositionCoverage::Hwc;
if (state.reusedClientComposition)    flags |= CompositionCoverage::GpuReuse;
else if (state.usesClientComposition) flags |= CompositionCoverage::Gpu;
```

`mGpuFrameMissed` tests `CompositionCoverage::Gpu` **only** (`FrameTargeter.cpp:178`),
so a *reused* client-composed frame sets `GpuReuse` and therefore neither sub-counter.
Reuse is precisely the "nothing is changing" case — i.e. our broken state. Both
sub-counters static does **not** mean "no composition is happening"; it means SF
composed and concluded the output was identical.

**Trap 2 — the split is a snapshot, not a live read.** `mCompositionCoverage` is
assigned only in `endFrame()` (`:196-197`), which runs only when composition actually
completed. During a stall, coverage stays frozen at the last frame that really composed
while the counters keep incrementing against that retained value. **So the split answers
"what was the composition mode when it froze", not "what is it now"** — and it must
**not** be cross-checked against a live `service call … 1010` as though the two describe
the same instant. (Earlier drafts of this doc said the three HWC reads "should agree".
They do not describe the same moment; that claim was wrong.)

### Four outcomes, one grep

| Total | HWC | GPU | Reading |
|---|---|---|---|
| climbing | climbing | — | device composition was in use when it froze ⇒ the `vndk.rc:29` `i32 1` hook did not take effect, or was undone |
| climbing | static | climbing | fresh client composition every frame ⇒ HWC disabled as intended, SF actively re-rendering |
| climbing | static | static | last real composition was a **reuse** — SF had already decided nothing was changing ⇒ points at **H1** (held/static content above SF) rather than H3 |
| **static** | — | — | no frames being missed at all ⇒ the stall is not in this path; look above SF |

**Direct read of `mDebugDisableHWC`** (live, current state — unlike the split above) —
`SurfaceFlinger.cpp:7128`, `case 1010` ("Interrogate") writes five `int32`s and the
**fifth is `mDebugDisableHWC`** (`writeInt32(0)`, `writeInt32(0)`,
`writeInt32(mDebugFlashDelay)`, `writeInt32(0)`, `writeInt32(mDebugDisableHWC)`):

```
adb shell su -c "service call SurfaceFlinger 1010"
```

`1` = HWC disabled (the `vndk.rc:29` hook took effect), `0` = enabled.

> ### These backdoor calls REQUIRE ROOT, and the denial looks like "the code doesn't exist"
>
> **MEASURED** — there are two gates, not one. `CheckTransactCodeCredentials`
> (`SurfaceFlinger.cpp:6963`, called from `onTransact` at `:7086`) waves through codes
> 1000-1047 for anyone — the comment at `:7073` reads *"Numbers from 1000 to 1047 are
> currently used for backdoors"*. The **real** gate is `:7095-7096`:
> `uid != AID_SYSTEM && !PermissionCache::checkCallingPermission(sHardwareTest)`
> → `PERMISSION_DENIED`. `sHardwareTest` is
> `android.permission.HARDWARE_TEST` (`:404`).
>
> `adb shell` runs as **`AID_SHELL` (2000)**, which is neither `AID_SYSTEM` (1000) nor a
> holder of `HARDWARE_TEST`. **So a plain `adb shell service call SurfaceFlinger 1010`
> is denied.** Root (uid 0) passes via the permission check —
> `ActivityManager.checkComponentPermission` short-circuits `ROOT_UID`/`SYSTEM_UID` to
> `PERMISSION_GRANTED` (**INFERRED** for the short-circuit, though it is the same one
> that lets this app receive `BOOT_COMPLETED` without declaring the permission, and it
> is corroborated **MEASURED** by `vndk.rc:29` deliberately running the hook as
> `exec_background u:r:phhsu_daemon:s0 root`).
>
> `adb root` is refused on LineageOS 23.2 regardless of the debug props (see
> `ANDROID16.md`), so this needs Magisk `su` or the phhsu path.
>
> **THE TRAP:** a denial returns `Result: Parcel(Error: 0xffffffff ...)`, which is
> **indistinguishable from "code 1008 does not exist on A16"** — the precise wrong
> conclusion this project already spent effort ruling out once. **Anyone seeing an error
> from these commands must check the calling uid before concluding anything about the
> code.** This rule was handed to an operator in the wrong form once already and nearly
> cost the whole axis a second time.

Per-layer composition types
(`dumpsys SurfaceFlinger | grep -B4 -A25 -i "hwc layers"`) are also a live read, and
need no root. Compare those two live reads with each other; compare either with the
missed-count split only with Trap 2 in mind.

**Held-stale-snapshot checks** (screen ON, in the broken state):

```
adb shell dumpsys display | grep -iE "mColorFade|mScreenState|mPendingScreenO"
adb shell dumpsys window  | grep -iE "mDisplayFrozen|mAppsFreezingScreen|mWindowsFreezingScreen|mClientFreezingScreen"
adb shell dumpsys SurfaceFlinger --list | grep -iE "colorfade|ScreenRotationAnimation|Screenshot|freeze"
```

`mColorFadePrepared=true` or `mColorFadeLevel < 1.0` with the screen on indicates a
stuck ColorFade — and per §4 a 0-smali-patch build is exactly where that can happen.
The `mColorFade*` strings are present in the shipped `services.jar`, so
`DisplayPowerState.dumpLocked` does emit them (verified by epd).

**Fingerprint that the app's preference-default pass ran:**

```
adb shell cmd overlay list | grep -i aod_systemui     # disabled => something disabled it post-boot (§1)
adb shell getprop persist.sys.overlay.aod
adb shell cat /data/data/me.phh.treble.app/shared_prefs/me.phh.treble.app_preferences.xml
```

The app is `sharedUserId=android.uid.system`, so that prefs path is where its
SharedPreferences live. A file listing many `key_display_*` / `key_audio_*` entries
means an AndroidX preference screen was constructed and wrote its defaults.

## 9. Sensor discipline

Blunt, because getting this wrong produced two wrong conclusions in one session.

- **`screencap` reads the composition output, not the panel.** It will happily return
  a correct, freshly-composed image while the physical E Ink panel shows something
  stale, and it will equally return a stale image for reasons unrelated to the panel.
  **It cannot distinguish "SF stopped composing" from "the panel stopped updating" —
  which is the entire question.** Do not use it as evidence about the panel.
- **Panel sensor (added 2026-10-11):** `scripts/epd-snap.sh` reads the image the
  E Ink engine holds for the glass, verified against a photo of the panel.
  `scripts/epd-watch.sh` logs each refresh cycle with a panel snapshot and a
  screencap side by side. See [epd-panel-screenshot.md](epd-panel-screenshot.md).
  It is still a software reading: after vendor/firmware changes, re-validate it
  once against the user's eyes or a camera.
- **The valid sensors are the user's eyes and panel-side counters.** For the
  composition side, use the `dumpsys` counters in §8. The two together are a
  two-ended measurement: SF producing vs panel receiving, and a divergence localises
  the fault to the region between them.
- **Always record which image a measurement was taken on, and when.** Two separate
  provenance failures happened this session — a screencap and a force-clear
  observation, both real, both from an *earlier build*, both briefly treated as
  describing the current one. "The user said X" is not enough if X was measured on a
  different build. **Sensor and provenance, every time.**
- The panel-side driver sensors that exist (from epd, kernel strings): `dmesg | grep -i epd`
  emits `epd power on`, `epd temperature = %d`, `epd commit bitmap` and similar, and the
  driver pushes `EPD_COMMIT` / `EPD_CLEAR` / `EPD_CONNECT` / `EPD_TEMP` uevents.
  Caveat: `epd commit bitmap` is the store handler for a sysfs node the A9 stack never
  writes, so it will not fire during ordinary rendering. **Test whether `dmesg` is
  noisy at all in the GOOD state before relying on its silence in the broken state.**
  (Tested 2026-10-11: every refresh cycle logs `epd power on` … `enter sleep end`,
  so `dmesg` is a usable panel-side counter. Don't read `fp9929a` sysfs nodes to
  get the same information: see epd-panel-screenshot.md §7.)
- `a9_eink_server` **cannot be queried** — `processCommand()` handles only write
  commands and never writes to the client socket (epd, from `eink_daemon.c`).

## 10. OPEN: what actually causes the freeze

Not yet established. Do not write a conclusion here that has not been earned.

Every app-side mechanism examined has been refuted at mechanism level:

| Candidate | Verdict |
|---|---|
| App toggling HWC / `mDebugDisableHWC` | Dead — interface descriptor mismatch, §6 |
| ColorFade via `sys.linevibrator_type` | Dead — app has no write path to it or to `debug.a9.colorfade` |
| `Hct` / `IGestureWake` vendor HAL | Dead — HAL absent from all ~6 GB of stock `super`; zero lowercase `gesturewake`, zero `vendor.hct.hardware.*`. This device's Hisense HALs use the **HMCT** prefix (`vendor.qti.hardware.hmctface`, `ro.hmct.*`) |
| AOD RROs (`misc.aod_systemui`, `misc.aod`) | Dead as a *cause* — they override doze-only resources, none on the awake rendering path. Survives only as the §8 fingerprint |
| App writing SF composition props | Dead — only one is live-read, its default is already the active value, and the app can only move it in the harmless direction (§7) |

**The remaining causal question is two hypotheses, and the §8 measurements decide
between them:**

- **H1 — a held stale full-screen snapshot.** Two known producers: a stuck WMS display
  freeze (`mDisplayFrozen` never cleared) and a stuck ColorFade (`prepare()` succeeded,
  `dismiss()` never ran). §4 makes v24 the build where the second is possible.
  *Predicts:* an extra full-screen non-app layer present, SF frame counters still
  advancing.
- **H3 — the present fence never signals**, i.e. a fault at or below HWC/the panel
  driver. *Predicts:* no extra layer, and — per §7 — the `commit()` rate-limiter stalls
  as a **symptom**.

These predict different, mutually exclusive readings of §8 — see the four-outcome table
there, which separates them from a single grep taken twice. Note the asymmetry that
matters: a permanent stall at §7 does not compete with H3, it *implies* it. And note
Trap 1: under H1 the expected reading is **both sub-counters static**, because SF will
have settled into reusing client composition — not "GPU climbing".

One cheap, instrument-free discriminator not yet run: whether the symptom is
deterministic. The AndroidX preference-default mechanism (§5, §8) writes a screen's
defaults exactly **once** — on later opens `dispatchSetInitialValue` takes the
`onSetInitialValue(true, null)` branch and writes nothing. So *"it broke the first
time and has not since"* points at that mechanism; *"only sometimes"* points at a
load-triggered stall; *"every single time, only this app"* points at neither and back
to a deterministic per-open side effect that has not been found yet.

**Added 2026-10-11 — a third place the freeze can live.** The panel is not
driven by SurfaceFlinger's output directly. A software TCON inside the vendor HWC
composer (`epd::` in logcat) takes each composed frame, dithers it, computes 8
waveform frames and pushes them itself
([epd-panel-screenshot.md §2](epd-panel-screenshot.md#2-the-a9-has-no-hardware-tcon-the-e-ink-engine-lives-in-the-hwc)).
So a freeze can also sit **between SurfaceFlinger and the glass, inside that
engine**: frames composed but never taken (`EpdUpdateThreadHandler` stalled, queue
starved), or taken but only partly redrawn. Neither H1 nor H3 as written covers
this, and the §8 counters cannot see it. The decisive measurement for the next
reproduction:

1. Before reproducing, while the display works: `scripts/epd-snap.sh /tmp/ok.png`
   (runs discovery once).
2. Start `scripts/epd-watch.sh /tmp/freeze 120`, then reproduce.
3. In the frozen state: `epd-snap.sh /tmp/frozen.png`, `screencap`, the `epd::`
   lines from logcat, and §8's held-snapshot checks.

Reading: **panel == screencap, both stale** → the stall is at or above
SurfaceFlinger (H1-type). **screencap fresh, panel stale, no `epd power on` after
the change** → the engine never pushed it. **Refresh cycles logged but panel image
unchanged or partial** → the engine received frames but didn't redraw those
regions. A small instance of that last class was observed on 2026-10-11 (status and
nav bars missing on the glass after the notification shade collapsed;
epd-panel-screenshot.md §5).

## 11. Measurements on the current slot-b build (2026-10-11)

Build: testkey LOS 23.2 (`BP4A.251205.006 test-keys`, 46 smali patches, its own
2.8 MB `TrebleApp.apk`, **not** v24's 14.8 MB fork). Sections 4-6 describe v24 and do
not carry over unchanged.

- **HWC composition is ENABLED, despite the boot hook.** **MEASURED**: init logged
  `Service 'exec 59 (/system/bin/service call SurfaceFlinger 1008 i32 1)' … exited with
  status 0` at boot, yet `service call SurfaceFlinger 1010` (root) returns
  `mDebugDisableHWC = 0`, `dumpsys SurfaceFlinger` says `h/w composer enabled`, and
  every layer is `composition=DEVICE`. Per §1 that is the anomaly. Exit status 0 only
  means `service` ran. Not yet investigated why.
- **`sys.linevibrator_type` reads `0`, not `96`.** **MEASURED** (`getprop`).
- **The HWC never returns a present fence.** **MEASURED**: SurfaceFlinger logs
  `trackPendingFrame: Invalid present fence` ~20×/s, idle or active, in any app.
  Consequence (`FrameTargeter.cpp:154`: `mFramePending` needs a real fence):
  **all three §8 missed-frame counters stay 0 permanently**, the §7 rate-limiter can
  never engage, and every reading lands in the "static" row of §8's table. **On this
  build §8's counters cannot distinguish anything; use the panel sensor instead.**
  H3 in its §7-stall form cannot occur here.
- **Opening the Treble app did not freeze the panel** on this build in three
  attempts. Panel refresh cycles continued, no ColorFade, no extra layer, and the
  `aod_systemui` overlay stayed enabled. Its prefs file has only two keys.
- Every panel power-on logs `tc358767_send_init_cmd, epd ret=-107`, in the good state
  too. Noise.

## 12. ROOT CAUSE (slot-b build, 2026-10-11): the Treble app turns HWC back on, and HWC-composed frames never reach the E Ink engine

### The chain (each link MEASURED unless marked)

1. **`vndk.rc:29` forces GPU composition at `boot_completed`** (`1008 i32 1`). Read at
   boot_completed: `mDebugDisableHWC=1`.
2. **~7 s later the Treble app undoes it.** Its boot path logs
   `D PHH: Set surface flinger hwc overlay to true` (07:26:51, after the hook at
   07:26:44). `mDebugDisableHWC` is 0 at +10 s and +30 s. It does the same **every time
   the app starts**: hook value 1 → `am start me.phh.treble.app/.SettingsActivity` →
   0. The patcher already says so (`patch_system_img.py:2325-2338`: *"phh's TrebleApp
   calls SurfaceFlinger transact(1008) on startup and whenever its Misc screen is
   built (Misc.kt enableHwcOverlay)"*), which §6 contradicted.
3. **With HWC on, SurfaceFlinger keeps composing but the E Ink engine gets nothing.**
   One scroll in the frozen state (`scripts/epd-trace.sh`): Settings layer
   `frame=2737 → 2787` (50 frames), **0** engine updates (`epd::` logcat), **0** panel
   power-ons (dmesg). The engine thread is alive (`epd:: queue is empty` every ~3 s;
   `debuggerd -b` shows `EpdUpdateThread` and `EinkSwTconThreadHandler` idle in
   `pthread_cond_wait`, no thread blocked). Frames are dropped, not stuck.
4. **Causality, both directions, same session, same screen:**

   | `1008` | Scrolls | SF frames per scroll | Engine updates | Panel refreshes |
   |---|---|---|---|---|
   | `i32 0` (HWC on) | 3 + 1 | ~40-50 | **0** | **0** |
   | `i32 1` (GPU) | 4 | ~30 | 8-9 | 1 |

5. **Why it looks intermittent**, matching the user's report ("frozen… now it
   loaded"): **INFERRED**. With HWC on, the engine still gets a frame whenever
   SurfaceFlinger falls back to GPU composition for that frame (more layers, a
   toast, some animations). Freeze-1's dump was mostly `composition=CLIENT` and the
   panel updated then; freeze-2's was all `DEVICE` and stayed frozen. **INFERRED**:
   the engine takes its image from the GPU-composed client target, which isn't
   refreshed for hardware-composed frames.
6. **Why "opening the Treble app freezes rendering"** (the original §10 symptom):
   opening it runs step 2.

The status/nav-bar divergence in
[epd-panel-screenshot.md §5](epd-panel-screenshot.md#5-verification-and-what-the-panel-showed)
fits the same mechanism (**INFERRED**). With GPU composition forced, the glass shows
both bars again (epd-snap 2026-10-11 after the fix).

### Fix

- **On the device now (done 2026-10-11, survives reboot):** the Treble app preference
  `key_misc_no_hwcomposer=true` in
  `/data/data/me.phh.treble.app/shared_prefs/me.phh.treble.app_preferences.xml`
  (added as root; owner `system:system`, label `system_app_data_file` intact). The app
  then logs `Set surface flinger hwc overlay to false` and asserts HWC **off**. Verified
  across a reboot: `mDebugDisableHWC=1` at +0/+10/+30 s, after opening the Treble app,
  and scrolls reach engine and panel. The in-app equivalent is the HWC/overlay toggle
  on the Treble app's Misc screen. **A data wipe or a new image loses it.**
- **In the image (built, flashed and verified 2026-10-11):** branches `a16-hwc-guard`
  (testkey bases) and `a16-dozeoff-hwc-guard` (crDroid-keyed Doze-off, current on
  slots A and B). The guard sets **both** `key_misc_no_hwcomposer` and
  `key_display_no_hwcomposer`: Doze-off's TrebleApp fork reads the latter, so a
  misc-only guard did nothing there (measured after the GAPPS wipe). Two layers:
  - `a9-hwc-guard.sh` on **every base**, run at boot_completed: sets the preference
    (keeping other keys, owner, mode and label), restarts the app once if it already
    ran with `false`, and re-asserts `1008 i32 1`.
  - `patch_trebleapp_hwc` on **testkey bases only**, i.e. when the shipped
    TrebleApp's signer equals `platform.x509.pem`: forces both
    `enableHwcOverlay(Z)` (Misc, Huawei) to `false`, signed with `platform.pk8`.
  Both were tested on the device first, then in built images: testkey image on B
  and A, Doze-off GAPPS image on B (including after a `twrp format data`) and A.
  The boot hook alone is not enough because it runs before the app.
- **Recovery without root or a reboot:** `adb shell setprop debug.a9.hwc 1`
  (`vndk.rc:211-215` runs `1008 i32 1` as phhsu_daemon). The value must change to fire,
  so set it to 0 first if it's already 1. With root:
  `adb shell su -c "service call SurfaceFlinger 1008 i32 1"`. Neither restarts SF (§2).

### Side finding, not the freeze: frontlight writes flood I2C bus 0

**Status 2026-10-11: CLOSED, flood metric invalid.** **MEASURED** (earlier): the patched
`DisplayPowerController.updatePowerStateInternal` (`patch_system_img.py:619-673`) set
`sys.linevibrator_short` and `sys.linevibrator_open` on every call, even when unchanged,
and each set makes init rewrite the frontlight chips (`vndk.rc:218-225`). `aw99703-bl-2` and
`ktd3137-bl-4` sit on I2C bus 0 (`4a80000`) with the EPD PMIC and the bridge, and EPD
power-ups logged `i2c error :-107` in that window. It did not cause the freeze.
Fix: set the properties only when the value changes (patcher `9cdb113`, in the image
since v2). **Correction (MEASURED on v6, 2026-10-11):** the `aw99703` dmesg lines do NOT log
every frontlight write: frontlight values changed with 0 lines. So counting those lines
was never a valid flood metric, the earlier "about 8 writes/s" figure is unsupported, and the
flood effect was never quantified. What is measured: in steady light the values stay constant,
and with the legacy inversion hooks gone (v6) the brightness curve is sane
([adaptive-brightness.md](adaptive-brightness.md)). The `i2c_geni` -107 lines come from
`crtc_commit` (EPD power-up) on both images and are unrelated to the property writes.

---

## 13. E Ink engine behaviour, cross-checked with the Hisense A6L (2026-10-13)

The A6L (SDM660) runs the same Hisense TCON family one generation older; its
port disassembled and emulated it (docs/a6l-harvest-2026-10-13.md §3). What
carries over, and how sure we are on the A9:

| Fact | A9 evidence | Status |
|---|---|---|
| Waveform tables are inside `/vendor/bin/hw/vendor.qti.hardware.display.composer-service` (14 temperature bins; Clear, Balanced, Smooth, a 5th "Speed" table in the live copy), not on a flash chip | [epd-waveform-source.md](epd-waveform-source.md), live check 2026-10-13 | measured |
| Temperature input = PMI632 VADC `in_temp_epd_top_therm_input` (+ `epd_thermal`) read before each update | live: 31.8 °C ↔ `epd:: clear=0,temp=27,32` | measured |
| A forced clear (`epd_force_clear`, a9service FORCE_CLEAR) is one GC16 redraw of the current picture (~38 frames), not the long init flash (~99+, only at TCON init) | TWRP log `out frame: 162` then `38` | inferred from frame counts |
| Every `epd_display_mode` change costs a full GC16 refresh on the next update | composer strings `Pre_Mode=%d, cur_mode=%d`, `DU to G16` | inferred; do not toggle modes per gesture |
| The composer skips an image identical to the last one, and refuses all-black frames (`epd:: is black image, not display`) | composer strings | inferred: rule these out before calling "engine 0 updates" the HWC freeze (§12) |
| **Mode map, MEASURED 2026-10-14** (write `epd_display_mode`, scroll Settings, `epd-snap`, count grey levels; the composer logs the mode it used as `epd:: clear=..,mode=0x..`): 513 (BALANCED) → `0x201`, **2 levels**; 514 → `0x202`, 16; 515 (CLEAR) → `0x203`, 16; 518 (SMOOTH) → `0x206`, **2**; 520 → `0x208`, 16; 521 (FAST) → `0x209`, **2**; 512/516/517/519 are not valid and fall back to `0x3` (16 levels). The A6L map does not carry over as-is: 0x208 is a grey mode, and 518/521 are both 1-bit but logged as different modes (the composer has separate Smooth and Speed tables), so they most likely differ in drive speed, which a snapshot cannot show. Composer tables (epd-waveform-source.md): 513 Balanced, 515 Clear, 518 and 520 share the Smooth table, 521 Speed, 514 a separate table with Clear's timing. So 518 vs 520 differ only in the 1-bit conversion before the waveform, and the four stock UI modes are the four waveform families. Usage (user, stock experience): Speed 521 is the mode for video; it has the shortest waveform (7 frames per update in warm bins). a9service keeps a mode per app by default (`disable_perapprefresh` off), so choosing Speed once in a video app sticks to that app | live test, raw results in session scratch `modes/`; composer `.rodata` | measured (grey levels, mode used, table per type); speed difference inferred |
| Drive frames are XRGB at the 448×829 transport size (829 = 1648/2 + 5), 2 bits per pixel in byte 0 (00/55/aa) | composer buffers hold only 0/85/170/255 | inferred |
| Physical ceiling: A2 ≈ 8.5 updates/s, GC16 ≈ 2.2/s, a mode change ≈ 1/s. Refresh-rate props cannot raise this | adaptive-brightness.md (refresh props have no effect) | A6L measured, A9 consistent |

**Keys (MEASURED 2026-10-14, `getevent -lt` on v7):** Volume Up = `KEY_VOLUMEUP`
on `gpio-keys` (`/dev/input/event2`), Volume Down = `KEY_VOLUMEDOWN` on `qpnp_pon`
(`event0`), **the E Ink button = scancode `0x2fe` (766) on `gpio-keys`**. All three
use `/system/usr/keylayout/Generic.kl`, which has no line for 766 (nor the A6L's
616/555/539), so Android sees an unknown key; a9service's accessibility service
handles it by scancode (`A9AccessibilityService.onKeyEvent`, `scanCode == 766`:
single, double, long press; screen-off long press). Already in the ROM, no root,
no keylayout needed.

Never send the stock SurfaceFlinger EPD transaction codes (A6L Android 9:
`setDisplayType` 30, `setEpdMode` 33, `connectEpdDisplay` 34; A9 stock 200001)
to the A16 SurfaceFlinger: those numbers mean other things there (rule 11
territory). Never write `epd_xon_en`: the composer owns XON, and XON low while
the rails ramp darkens the whole panel (A6L measured).

## Appendix: reproduction commands

All host-side, read-only, no device required. `debugfs` is in `e2fsprogs`; on this
NixOS box use `nix-shell -p e2fsprogs unzip` if either is missing. **Never `e2fsck`,
`resize2fs` or rw-mount an image someone else may still flash.**

```bash
V=/path/to/v24.img          # the image under test

# §1, §2 — vndk.rc boot hooks and the SF-restart hazard comment
debugfs -R "cat /system/etc/init/vndk.rc" "$V" > vndk.rc
grep -n "on property:sys.boot_completed=1" -A 30 vndk.rc
grep -n "restart surfaceflinger\|permanently blank\|external backlight IC" vndk.rc
grep -n "aw99703\|ktd3137" vndk.rc

# §3 — PATCH_REPORT scope
grep -rn "PATCH_REPORT" app/external_scripts/system_img_patcher/
grep -n "update_vndk_rc\|A9_PATCH_SYSTEMUI\|A9_PATCH_SERVICES" \
        app/external_scripts/system_img_patcher/patch_system_img.py

# §4 — ColorFade kill-switch present or absent
debugfs -R "dump /system/framework/services.jar ./services.jar" "$V"
for p in mColorFadeEnabled a9ColorFadeEnabled sys.linevibrator debug.a9.colorfade; do
  printf '%-24s %s\n' "$p" "$(grep -a -o -F "$p" services.jar | wc -l)"
done

# §5 — shipped Treble app: keyspace, props, overlays
debugfs -R "dump /system/priv-app/TrebleApp/TrebleApp.apk ./TrebleApp.apk" "$V"
unzip -o -q TrebleApp.apk -d app && cd app
grep -a -o -E "key_[a-z0-9_]{2,50}"                classes*.dex | sed 's/^.*dex://' | sort -u
grep -a -o -E "persist\.sys\.[a-z0-9_.]{3,45}"     classes*.dex | sed 's/^.*dex://' | sort -u
grep -a -o -E "me\.phh\.treble\.overlay\.[a-z0-9_.]{2,45}" classes*.dex | sed 's/^.*dex://' | sort -u
ls res/xml/                                        # 23 pref screens
tr -c '[:print:]' '\n' < res/xml/pref_display.xml | grep -E '.{4,}' | sort -u

# §6 — the interface descriptor mismatch
grep -a -o -F "android.ui.ISurfaceComposer"  classes*.dex | wc -l   # 1
grep -a -o -F "android.gui.ISurfaceComposer" classes*.dex | wc -l   # 0
debugfs -R "dump /system/bin/surfaceflinger ./surfaceflinger" "$V"
grep -a -o -F "android.gui.ISurfaceComposer" surfaceflinger | wc -l # non-zero

# §6, §7, §8 — the A16 GSI framework sources (TrebleDroid, not AOSP)
B=https://raw.githubusercontent.com/TrebleDroid/platform_frameworks_native/android-16.0.0_r4-td
curl -sSL "$B/services/surfaceflinger/SurfaceFlinger.cpp" -o SFtd.cpp
curl -sSL "$B/services/surfaceflinger/Scheduler/src/FrameTargeter.cpp" -o FrameTargeter.cpp
curl -sSL "$B/services/surfaceflinger/Scheduler/include/scheduler/FrameTargeter.h" -o FrameTargeter.h
grep -n "persist.sys.phh\|enable_gl_backpressure" SFtd.cpp
grep -n "mFramePending\|mFrameMissed\|missed frame count\|isFencePending" FrameTargeter.cpp
# the backdoor switch and its two gates (all line numbers in this doc are from THIS file)
grep -n "CheckTransactCodeCredentials\|CHECK_INTERFACE\|AID_SYSTEM\|sHardwareTest\|case 1008\|case 1010\|sfdo_forceClientComposition" SFtd.cpp
wc -l SFtd.cpp    # expect 10638; if you get 9466 you have the AOSP file, not TrebleDroid

# §10 — the absent HCT HAL, across the stock firmware
cd "$A9_FW"   # $A9_DATA/HLTE556N
for f in super_*.img; do
  echo "### $f"
  grep -a -o -E "vendor\.hct\.hardware\.[a-z]+|gesturewake|GestureWake" "$f" | sort | uniq -c
done
grep -a -o -E "vendor\.[a-z]+\.hardware\.[a-z0-9]+" super_2.img | sort -u   # note hmctface
```

## References

- `app/external_scripts/system_img_patcher/patch_system_img.py` — `:249`, `:2219-2220`,
  `:2356`, `:2364`, `:2368`, `:2377`, `:2382`, `:2384-2393`
- `app/external_scripts/system_img_patcher/smali_patcher.py` — `:15`, `:275`, `:333`
- `tools/finalize-image.sh` — `:96-99` (`sys.linevibrator_type`)
- `TrebleDroid/platform_frameworks_native` `android-16.0.0_r4-td` — **the only
  SurfaceFlinger coordinate system used in this doc.** `SurfaceFlinger.cpp` (10,638 lines):
  `:404` (`sHardwareTest`), `:525`, `:2845`, `:2894`, `:3374-3382` (CompositionCoverage
  flags), `:6963` (`CheckTransactCodeCredentials`), `:7073` (backdoor range comment),
  `:7086`, `:7092` (`CHECK_INTERFACE`), `:7095-7096` (the uid/permission gate), `:7121-7122`
  (`case 1008`), `:7128` (`case 1010`), `:9400` (`sfdo_forceClientComposition`);
  `Scheduler/src/FrameTargeter.cpp` (226 lines): `:99-184` (`beginFrame`), `:140-155`,
  `:162`, `:177-182`, `:196-197` (`endFrame`, sole assignment of `mCompositionCoverage`),
  `:212-214`, `:219`;
  `Scheduler/include/scheduler/FrameTargeter.h:61-66`, `:80-82`
- AOSP `main` — used **only** for `Settings.java:16099` (`FPS_DEVISOR`) and
  `UserController.java:739`, `:976`. Do **not** mix its SurfaceFlinger line numbers with
  the above; the files differ by ~1,200 lines
  (BOOT_COMPLETED requires `RECEIVE_BOOT_COMPLETED` — but
  `ActivityManager.checkComponentPermission` short-circuits for `SYSTEM_UID`, so a
  `sharedUserId=android.uid.system` app receives it regardless)
- `phhusson/treble_app` master `0c45670` — **upstream reference only, not the shipped app**
- [a16 GSI port plan](a16-gsi-port-plan.md) · [TWRP port findings](twrp-port-findings.md)
