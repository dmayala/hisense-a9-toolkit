# A9 mainline E Ink compatibility contract

2026-10-17: optional bounded numeric mode/lamp provenance and kernel input-cost
metrics are documented in [E Ink quality diagnostics](mainline-eink-quality.md).
Both are off by default; mode policy and electrical settings are unchanged.

**Date: 2026-10-15. Status: implemented and tested offline; kernel/vendor integration
and optical validation pending.** No phone operations were run. The v7 system image
and stock evidence were read only. This is the handoff to the E Ink DRM driver and
Android vendor owners; their directories were not edited.

**Follow-up 2026-10-15:** removed the card0 assumption, added platform/verified-driver
discovery and configurable frontlight paths, and reviewed the vendor-owned separate
service handoff. The original cross-partition init `override` design was rejected
by A16 init, as recorded by the vendor agent; it is superseded below.

**Control-center follow-up 2026-10-16 (offline; run-10 observations supplied by
the coordinator):** the E Ink key's IRQ remained at zero, and TLMM84 was an
active-low input with a pull-down. The base board now attaches a stock-derived
GPIO84 GPIO/input/pull-up/2 mA state to `gpio-keys`; Android inherits it.
PM6125 GPIO5 volume-up keeps its stock normal/input/pull-up/power-source-0
state and explicitly disables output. The PMIC pinctrl driver does not clear
an inherited output-enable bit on `input-enable` alone. GPIO84 remains
unreserved and has no other GPIO consumer. PM6125 GPIO5 and PMI632 GPIO5 are
different banks; an identically numbered debug line is not proof of a conflict.

Factory `/system/usr/keylayout/gpio-keys.kl` maps scan 766 to private `AI`.
The inspected standard A16 keycode definitions lack `AI`; Generic also lacks a
766 mapping. The mainline vendor now ships `usr/keylayout/gpio-keys.kl` with
`key 766 BUTTON_16` plus volume/power mappings. BUTTON_16 is a standard A16 token and
preserves scanCode 766, which is what `A9AccessibilityService.onKeyEvent`
checks. This is an A16 translation, not a byte-for-byte copy of stock. The
platform file contexts label the directory and file
`vendor_keylayout_file`; no new SELinux type or permission is needed. The app
returns `super.onKeyEvent`, so the key may also reach the focused app.
**Coordinator-measured on run 10:** injected EV_KEY 766 with no mapping reached
InputDispatcher as UNKNOWN/scanCode 766 and the service did nothing. A runtime
`key 766 BUTTON_16` layout plus input-device rebind made single press clear and
double press open the floating menu (screencap confirmed). This supersedes the
prior claim that an unmapped key alone suffices on mainline. The committed
vendor layout and physical GPIO84 IRQ recovery still need validation after
the combined rebuild.
The app's default screen-on assignment is single press = clear, double press
= open menu (`button_mapper/ButtonActionManager.kt`); existing user assignments
and overlay permission still govern behavior.

Refresh mode already uses socket `b/c/s/p` → `513/515/518/521`; knobs use
`stb/stw/sco` → the discovered connector's black/white threshold and contrast,
0..100. The app declares these knob commands but has no current Kotlin call
sites beyond their declarations. The shim now retains the **latest pending**
value per setting if discovery or a sysfs write fails, retrying at one-second
intervals. The socket event loop alone owns this state; malformed requests
cannot replace it. Success ends retries, so it does not periodically undo a
coordinator's direct sysfs setting. A requesting client may disconnect without
discarding pending state. `r` stays a one-shot clear and is never replayed
after failure. Pending state is in memory, not persisted across daemon restarts.

Boot defaults were already correct: kernel mode **Smooth 518**, controls
**30/30/0**, matching the app's `refresh_setting` default index 2. The shim
leaves these defaults untouched until a control request. Run 10's 515 was a
coordinator override, not an alternate shim boot default. Existing
`sys.linevibrator_type` (shader/AOD, including 96/97), `_on` (haptic choice),
`_touch` (frontlight maximum) and `_open/_short` (frontlight levels) are **not
EPD mode/threshold properties** and do not change these controls.

**MEASURED** below means host source/binary inspection or the explicitly cited prior
device measurement, not a new observation on the phone. **INFERRED / REQUIRED** means
a proposed mainline ABI or behavior still to implement and validate. This document
does not claim that the mainline panel or new vendor already works.

**Manual-clear follow-up (patch 0041, offline):** socket `r` now writes only
`manual_clear=1`. With known history in the selected steady mode, the kernel runs
that mode's complete clear sequence and keeps its conditioned tone and history.
Unknown state, pending initialization, recovery and mode-entry requirements keep
the conservative full GC16 path. `force_clear` remains the separate recovery ABI
used by the vendor gate and diagnostics. Discovery requires both attributes;
there is no userspace fallback from a missing/failed manual clear to GC16 and no
replay of the event. Root-only access and all existing drive admission and timing
limits remain in place. This is source/host verification, not optical validation.

## Scope and evidence

The user-facing implementation stays in a9service 3.9.0 and the patched A16
framework. Hand the native socket to a separate helper from the vendor init configuration;
translate frontlight properties to four LED devices in that replacement. A property
daemon alone cannot translate refresh requests: the old helper opens absolute stock
sysfs paths directly. No patcher changes are needed for this GSI-specific route.

Source abbreviations used in the inventory:

| ID | File and offline reproduction command |
|---|---|
| D | `work-gsi/A9PatchingService/app/external_scripts/root_service/jni/eink_daemon.c`; `nl -ba .../eink_daemon.c` (functions named in the table) |
| P | `work-gsi/A9PatchingService/app/external_scripts/system_img_patcher/patch_system_img.py`; run the `rg` command below (functions named in the table) |
| K | `work-gsi/A9PatchingService/app/src/main/java/com/lmqr/ha9_comp_service/`; `rg -n -e scanCode -e stl -e smb -e wov -e sct -e FORCE_CLEAR -e SPEED_ -e COMMIT_BITMAP DIRECTORY` |
| V | v7 `/system/etc/init/vndk.rc`, extracted with command V below; `rg -n -e linevibrator -e a9_eink_server -e SurfaceFlinger -e vendor /tmp/sol-einkcompat-v7-vndk.rc` |
| C | `/home/user/source/a9-display-evidence-20261012/vendor/vendor.qti.hardware.display.composer-service`; `strings FILE`; symbols/disassembly: [mainline-plan §4](mainline-plan.md), `scripts/mainline/elfdis.py` and `tcon-oracle.py` |
| S | Stock framework/composer investigation, file/method and commands in [poweroff-image](poweroff-image.md#how-stock-did-it-measured-unless-marked), [composition findings §8/§13](a16-composition-and-epd-findings.md), [waveform source](epd-waveform-source.md) |

Actual commands run on the host (Nix supplies tools):

```sh
nix-shell -p e2fsprogs binutils gcc gnumake
# V: debugfs defaults to read-only; no -w, fsck, resize or image mount.
debugfs -R 'cat /system/etc/init/vndk.rc' \
  $A9_DATA/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v7.img \
  > /tmp/sol-einkcompat-v7-vndk.rc
debugfs -R 'dump /system/bin/a9_eink_server /tmp/sol-einkcompat-v7-server' \
  $A9_DATA/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v7.img
sha256sum /tmp/sol-einkcompat-v7-server \
  work-gsi/A9PatchingService/app/external_scripts/system_img_patcher/a9_eink_server
rg -n 'linevibrator|epd_|SurfaceFlinger|wakeup_on_volume|/sys/' \
  work-gsi/A9PatchingService/app/external_scripts/system_img_patcher/patch_system_img.py
rg -n 'vendor\.|/sys/|/proc/|ioctl|scanCode|sct' \
  work-gsi/A9PatchingService/app/src/main/java
```

**MEASURED:** both server binaries hash to
`123645f9b5f1c961ec0425bd34c73809dd88499deb5fcb98614e19697b81b997`.
Patcher source branch `a16-port`, inspected HEAD
`f789eaf6ce240cfba38e5437015e1b5ccb96f630`; this is a source snapshot, not a claim
that v7 was built from that later commit. V7 init initially sets
`sys.linevibrator_type=47`; the app subsequently sends `stl96` for overlay AOD.
The healthy runtime sentinel 96 in [aod.md](aod.md) is consistent with that boot default.

## Interface inventory and mainline mapping

Let **OLD** = `/sys/devices/platform/soc/soc:qcom,dsi-display-primary/`.
Let **NEW** = the discovered connector's `eink/` group (no fixed DRM card number).
Rows are MEASURED source interfaces unless explicitly marked transitive or historical.
All mainline mappings are INFERRED / REQUIRED.

| ROM interface / command | Producer and stock consumer; file + reproduction | Mainline mapping / owner |
|---|---|---|
| Abstract Unix `a9_eink_socket`, newline-delimited ASCII | K `command_runners/UnixSocketCommandRunner.kt:connect/sendCommand`; D `setupServer/processCommand`. The C string `0a9_eink_socket` becomes NUL + `a9_eink_socket`, without trailing NUL in the address length. | Vendor service `a9_eink_compat` owns the same socket after the ROM's `a9_eink_server` exits. No app edit. Only `sct` replies. |
| `b`, `c`, `s`, `p` → OLD `epd_display_mode` = 513, 515, 518, 521 | K `RefreshModeManager.kt`, D `writeToEpdDisplayMode`; stock kernel attribute relays state to vendor composer `EpdSetDisplayMode` / update-thread request builder (C/S; mainline-plan §4.5). Device-measured mode map: composition findings §13. | NEW `mode`, same integer mode word; kernel TCON performs conditioning and transition sequence. Sysfs shadow of DRM state; daemon does not acquire DRM master. |
| `r` → OLD `epd_force_clear` = 1 | D `epdForceClear`; kernel `EPD_CLEAR=1` uevent → composer `UEventHandler` → full redraw; S poweroff-image step 6. Used by E Ink key, AOD and power-off image. | NEW `manual_clear=1`: request the selected mode's complete clear sequence even if pixels are identical. Kernel safety state can require conservative GC16; the shim never substitutes `force_clear`. |
| `stbN`, `stwN`, `scoN` → OLD `epd_black_threshold`, `epd_white_threshold`, `epd_contrast` | D `setBlackThreshold/setWhiteThreshold/setContrast`; composer `EpdSetContrast` builds g2/g16 maps (C `0x1a48c8/0x1a49a4`, `tcon/a9tcon.c:a9t_set_controls`). K Commands declares these; no current Kotlin call site beyond declarations. | NEW `black_threshold`, `white_threshold`, `contrast`, 0..100; kernel uses existing TCON control algorithm. Defaults 30/30/0. |
| `cm` → OLD `epd_commit_bitmap=1` | D `epdCommitBitmap`; kernel `EPD_COMMIT=1` → composer `CommitBitMap`. No pixel payload. K constant has no current call site. V7 never uploads a bitmap; S poweroff-image explains why this is ineffective. | Explicit unsupported/logged command; never substitute clear or invent pixels. No impact on v7 power-off overlay. A future bitmap upload protocol would need separate design. |
| `sys.linevibrator_short` | P `patch_updatePowerStateInternal` and `patch_onColorTemperatureChanged`; V lines 275ff write same logical value to `/sys/class/backlight/aw99703-bl-2/brightness` and `ktd3137-bl-4/brightness`. Kernel drivers apply per-chip gain. | Daemon property notification → white center AW#2 ×0.75; white edge KTD#4 ×0.72. LED targets below. |
| `sys.linevibrator_open` | Same P functions; V lines 270ff → `/sys/class/backlight/aw99703-bl-1/brightness`, `ktd3137-bl-3/brightness`. | Daemon → warm center AW#1 ×0.81; warm edge KTD#3 ×0.78. Warm/white meaning inferred from P's Night Light ratio (4082 K = short only), wiring identity needs optical confirmation. |
| `smbN` → `sys.linevibrator_touch` | D `writeMaxBrightnessProp`; P display-brightness hook reads maximum, valid 1..2200, default 2000. K `A9AccessibilityService` sends preference `override_max_brightness`. Despite name, not touch input. | Keep framework property unchanged. Framework already computes warm/cool values; daemon must not multiply by maximum again. |
| `stlN` → `sys.linevibrator_type` | D `writeLockscreenProp`; K `StaticAODOpacityManager.kt`: 0..95 shader index, 96 disables ColorFade, 97 restores framework shader. P `a9ColorFadeEnabled`, patched `readFile` and ColorFade hooks consume it. | Preserve property in existing privileged domain. No driver property, XON write or waveform lookup for 96/97. |
| `debug.a9.colorfade` | P `A9_COLORFADE_HELPER_SMALI`; optional override of the sentinel-based decision, consumed only by patched DisplayPowerController. Composition findings §4b. | Preserve existing framework behavior; shim does not set it. A forced override can supersede the app's AOD mode. |
| `wov0/1` → `sys.wakeup_on_volume` | D `writeWakeOnVolumeProp`; P `handleWakeUpOnVolume` in patched PhoneWindowManager consumes it. K reader-mode selection sends it. | Preserve framework property; input driver must expose normal volume keys and allow their interrupts to wake the SoC. Not an E Ink mode. |
| `sys.linevibrator_on=1/2/3` → `/sys/class/leds/vibrator/{reg,seq,loop}` | P `patch_startVibrationLocked/add_pattern_to_initrc`; V lines 218ff. Consumer is stock Awinic haptic driver, not a frontlight. Pattern 1 programs 04/04/04/04/04/05/05/05; pattern 2 04/05/04/04/04/05/05/05; pattern 3 01/00, with separate loop/register values in P. | Separate haptic port: preserve these compatibility attributes or patch framework hooks to the mainline vibrator HAL if necessary. E Ink shim intentionally does not translate register values into LED brightness. Repeated identical props are unsuitable as haptic event counters. |
| `sct2596..4082`; `service call color_display 7 i32 1`, then `9 i32 K`; reads `ro.build.version.sdk=36` | D `setNightDisplayTemperature/callNightDisplay`; A16 framework `IColorDisplayManager` activates Night Light and sets temperature, invoking P warm/cool hooks. It is a system Binder service, not stock vendor Binder. | Retain exact SDK-gated native calls and validate exception/result words. Reply `OK\n` only after both calls return true; otherwise `NO\n`. Preserve schedule and adaptive-brightness mode. |
| `themeI RRGGBB`; `settings put secure theme_customization_overlay_packages JSON` | D `applyThemeCustomization/execute_settings_command`; framework SettingsProvider/SystemUI consume style and palette. | Same fixed-argv invocation; no shell interpolation. Not a kernel interface. |
| E Ink key EV_KEY `0x2fe` (766), press/release; volume 115/114 | K `A9AccessibilityService.onKeyEvent` checks **scanCode == 766**, single/double/long handling. Prior measured v7 and run-10 observations above; Generic.kl lacks 766. | DT preserves EV_KEY 766 with input/pull-up pinctrl and wakeup-source. Vendor gpio-keys.kl maps it to the measured BUTTON_16 while preserving scanCode. No kernel refresh remap or daemon evdev reader; accessibility retains assignments. Volume remains KEY_VOLUMEUP/KEY_VOLUMEDOWN. |
| AOD `doze_always_on`, `doze_enabled`; accessibility overlay, display DOZE/DOZE_SUSPEND | K `AlwaysOnDisplay.kt`, `StaticAODOpacityManager`; V enables `misc.aod_systemui` RRO; [aod.md](aod.md). Stock composer `SetPowerMode` blank/unblank manages panel state. | Mainline HWC/kernel support doze commits and retained glass on disable. Keep overlays, preferences, RRO, sensor curve and framework shader selection unchanged. No proprietary EPD Binder dependency. |
| `ACTION_SHUTDOWN`, read `sys.shutdown.requested`, overlay, wait 600 ms, `r`, wait 2400 ms | K `PowerOffImage.kt`; reboot starts with `1` and is skipped. Framework broadcasts and composer present the overlay. [poweroff-image.md](poweroff-image.md) records unplugged glass validation. | Same overlay and command; kernel must drain queued full update before rails off and retain image. No shutdown clear, no init flash on display disable. Bootloader charging splash remains outside this daemon's control. |
| `SurfaceFlinger` transaction **1008 i32 1**, diagnostic **1010**, `debug.a9.hwc` | V boot hook / `a9-hwc-guard.sh`; P `HWC_GUARD_SCRIPT`. A16 SF consumes it; 1008 forces client/GPU composition, 1010's fifth int is `mDebugDisableHWC`. Composition findings §8/§12; note §6's descriptor theory is superseded by §12. | Leave ROM guard in place. New HWC must accept client targets and present them via DRM. These calls are not sent to the E Ink driver. Shim never calls SF or restarts it. |
| `persist.sys.phh.enable_sf_hwc_backpressure`, `debug.sf.enable_gl_backpressure`, `persist.sys.sf.disable_blurs`, `persist.sys.rotation.animation`, `sys.disable_ext_animation` | P `update_build_prop` and stock GSI init/SF. First property is read per SF commit; debug GL value is startup-only. Composition findings §7; P explicitly withdraws the proposed backpressure disable. | Retain existing values. HWC must return valid fences, bound pending work and avoid stalling SF indefinitely. Do not use props to hide a fence bug. |
| `vendor.debug.sf.latch_unsignaled` → `debug.sf.latch_unsignaled`; `persist.sys.phh.*` → `debug.sf.*`; `ro.vendor.build.fingerprint` gates | V inherited generic GSI rules; P overlay-install code. Not a9service E Ink commands. Adaptive-brightness doc: `+*Hisense/HLTE556N*` gates are inert on A16; A9 overlay is already ungated. | No invented `vendor.eink.*` property needed. Preserve generic vendor build props independently; keep the A9 brightness overlay and implement the ambient-light sensor HAL. |

**MEASURED negative result:** no direct `vendor.*` property access, ioctl, `/proc`
path or proprietary vendor Binder call exists in the Kotlin E Ink feature path or
D. Native `service`/`settings` and Android APIs use framework Binder. The display
HAL and kernel interact transitively; that is a different boundary, inventoried next.

| Transitive stock interface (not called by v7 app) | Consumer/evidence | Mainline disposition |
|---|---|---|
| OLD `epd_connect=0`, `EPD_CONNECT=0` | Stock framework/SF → composer `EpdSetBlank` shutdown latch; S steps 2/6/7. | No emulation needed by v7. Do not latch ordinary screen-off permanently. |
| OLD `epd_info`, `epd_xon_en`, `epd_thermal`; PMI632 `...vadc@3100/iio:device0/in_temp_epd_top_therm_input` | C strings, [waveform source](epd-waveform-source.md), mainline-plan §4.5. PMIC temperature read powers hardware in the stock path; not a safe health probe. | Kernel owns XON, VCOM, panel sequencing and temperature compensation. Use a driver/IIO thermistor provider and PMIC temperature only under the driver's sequencing lock. No userspace writes to XON/VCOM and no stock thermal-path emulation. |
| `/sys/class/leds/aw99703-bl-{1,2}/brightness`, old four `/sys/class/backlight/*/brightness`; `/sys/class/backlight/panel0-backlight/brightness` | C strings show composer LED access; V chmod/chown/chcon plus brightness rules; project a9-device skill records panel backlight. | Four new LED-class devices exclusively owned by translator; normal Lights HAL must not independently change raw chip levels. Display-power gating is coordinated below. Old class names deliberately absent. |
| `android.hardware.graphics.composer` / QTI composer `IQtiComposerClient::sendImageData(hidl_memory)` | Stock SF case **200001**, stock `EpdManagerService`, `libandroid_runtime.so`, QTI composer 3.0 library; S steps 1–6 and disassembly commands. Standard composer methods also carry normal frames/power state. | New vendor provides its standard Android composer HAL. No QTI extension needed by v7. **Never send 200001 or older A6L 30/33/34 codes to A16 SF.** |
| `/dev/dri/card0`, DRM atomic/page-flip ioctls; `/dev/kgsl-3d0`, dmabuf/ashmem and binder/hwbinder/vndbinder; `/proc/dri/%d/name`, PCI/DRM discovery | C strings and prior composer open-fd capture in waveform-source §6. Exact Qualcomm ioctl request numbers are not established by strings and are not used by app/helper. | New HWC/Mesa/DRM stack replaces these internals with mainline DRM/dma-buf. Daemon issues **no ioctls**, allocates no pixels and cannot interfere with DRM master. |
| `/dev/epd_flash`, `.wbf`/`/vendor/firmware/wf_6_13inch.bin`; `/sys/devices/virtual/graphics/fb0/epd_info` | Recovery dead `ReadEpdFlash`/`SaveEpdInfo`, waveform-source §1/§5b. Prior live check found no epd_flash driver and no runtime waveform-file access. | Do not recreate vestigial nodes. Kernel TCON uses separately extracted verified waveform tables, not this daemon. |

## Required kernel ABI

Behavioral requirements below remain **INFERRED / REQUIRED** until device validation.
The connector group and layout are now **MEASURED from driver source**. The sysfs ABI
is chosen so the daemon can operate independently
of the compositor's DRM master and atomic ownership. The kernel agent may implement
equivalent DRM properties too, but must expose this sysfs group or coordinate a
source change to the daemon. Do not create old Hisense platform-device paths.

Driver `scripts/mainline/eink/driver/a9_eink_drv.c:a9_connector_late_register`
creates a named `eink` group on the connector. The platform node is `eink-display`,
the platform driver's `.name` is **`a9-eink`** (hyphen), and the DRM driver's name is
**`a9_eink`** (underscore). The connector displays **824×1648 logical pixels**, with
transport packing and rotation below the Android client. Built-in vkms can own
card0; neither the shim nor its policy templates assumes a fixed card number.

Before every E Ink write the daemon discovers a complete group through:

1. `/sys/devices/platform/eink-display/drm/card[0-9]*/card*-*/eink`, the known device.
2. `/sys/class/drm/card*-*/eink`, accepted only when a canonical ancestor's `driver`
   or `device/driver` link names **`a9-eink` or `a9_eink`**. This covers renamed
   platform devices and standard DRM class/card device links.

All five control attributes must exist. Canonical paths deduplicate platform and
class aliases. More than one distinct matching group fails with EEXIST; missing
groups fail with ENODEV. A vkms/msm/unknown driver is never selected merely because
its connector contains an `eink` directory. Discovery is repeated on each command
to handle late probe/removal/renumbering. Commands sent while no group exists are
logged and dropped, as before; they are not queued for replay. With `--root` all
matches must resolve inside that fake sysfs tree.

`genfs_contexts` now labels the actual **`/devices/platform/eink-display/drm` prefix**
without a glob or numeric card index; `ueventd.rc` uses connector path wildcards.
These remain reference snippets; the Android vendor owns the shipped policy/rules.

| Attribute | Access, syntax, default | Required behavior |
|---|---|---|
| `mode` | 0644 root; ASCII decimal + optional newline; read returns decimal + newline; default **518** | Accept **513/515/518/521**. Reject any other value with EINVAL; do not silently change mode. Atomically update persistent mode, use next redraw's old/new transition builder (`a9t_request_build` in existing TCON port). Low byte 1/3/6/9 is waveform type; preserve high-byte conditioning flags. |
| `black_threshold` | 0644 root; 0..100, default 30 | Rebuild g2/g16 controls with coherent snapshots. Since 0037 an actual value change queues full Clear to avoid mixed old/new tone mappings; identical writes and reads queue nothing. |
| `white_threshold` | 0644 root; 0..100, default 30 | Same; formula in `tcon/a9tcon.c:a9t_set_controls`, not a direct panel voltage. |
| `contrast` | 0644 root; 0..100, default 0 | Same; copy stock g2/g16 arithmetic. |
| `force_clear` | 0200 root; only `1` (newline accepted), every store is an event | Queue a **full GC16 redraw of the latest accepted composed image** even if identical to previous pixels, using the type-2/full-clear path in the TCON request builder. Do not run the long power-on initialization flash or permanently change `mode`. Return success only after reserving the event; return EBUSY/EIO/ENODEV on failure. |
| `manual_clear` | 0200 root; only `1` (newline accepted), one-shot event | Clear the latest accepted composed image using the configured steady mode with `clear=1`, full-screen and no mask, when committed history is known and no initialization, recovery or mode entry is due. Otherwise the kernel chooses the conservative GC16 path. Preserve complete request-builder steps and final target semantics. Socket `r` uses this attribute exclusively. |

One lock or a coherent state snapshot must cover DRM property setters, sysfs mode/
knob stores, the TCON old-level state and queued requests. Unrelated DRM atomic
commits must not reset sysfs-selected mode/controls to defaults. Repeated mode stores
may be no-ops; repeated manual/forced clear stores request another clear after the
store, with requests coalesced until a job is taken. Clear takes the
newest accepted logical buffer, not an old transport frame; internal updates need
their own buffer reference even after userspace releases it. Store never waits for
the entire waveform; completions/fences report work through normal DRM/HWC paths.

For `manual_clear`, `a9e_begin(..., current_mode, clear=1, ...)` runs Smooth/Speed's
existing complete mode-specific clear request. Successful completion commits that
mode's final target and previous-mode history, so the next ordinary update avoids
an extra Clear-to-mode compensation sequence. A request arriving during active
work remains pending for a later job; a stronger pending GC16 request can satisfy
both clear requests. The store reserves an event and does not acknowledge physical
completion; admission can still refuse it, and the shim never retries `r`.

For the separate `force_clear` ABI and required recovery, run Clear `0x203` with
`clear=1` (full-screen, no mask), selecting the full type-2 final pass. The selected
mode remains unchanged while the engine records the actual Clear history for the
next required transition. Userspace cannot bypass this kernel safety decision.

Every update must finish with the TCON's zero-drive frame before shutting down
rails. Drain work before display disable/suspend/shutdown; never truncate a waveform.
DOZE_SUSPEND must preserve the last glass contents while allowing the framework's
next AOD/notification commit to trigger an update. Atomic disable / shutdown must
not draw black/white or invoke the init flash. Respect runtime/system PM without
losing old-level history. A power-off overlay's 2400 ms completion budget needs
optical/timing validation at cold temperature; the existing ROM has no completion
acknowledgment for `r`. The pre-existing window-teardown race discussed in
poweroff-image.md is not claimed solved by this shim.

Optional future DRM equivalents: persistent range `EINK_MODE` carrying the same
mode word (validate against the four values), `EINK_BLACK_THRESHOLD`,
`EINK_WHITE_THRESHOLD`, `EINK_CONTRAST`; a monotonically changing
`EINK_CLEAR_SEQUENCE` for events. Names here are proposals, not discovered kernel
properties. **Do not require the vendor daemon to set atomic properties itself**:
the compositor owns atomic state and DRM master.

## Frontlight ABI and scaling

**MEASURED from stock disassembly:** mainline-plan §2.4 gives the four gains and
register layouts; its appendix provides `kern-dis.py` reproduction for
`aw99703_set_brightness`, `ktd3137_update_brightness` and the AW constant at
`0xffffff8009bb7130:8`. DT wiring: §2.1. KTD's pre-scale 0x7ff clamp is **INFERRED**
in that document. The shim explicitly chooses that clamp and integer truncation;
nominal gain equality is tested, not bit-identical floating-point rounding on stock.

**REQUIRED:** LED class `brightness` accepts raw, **unscaled** 11-bit values 0..2047;
`max_brightness=2047`. Drivers must not apply the stock gain a second time.
Zero disables output; next nonzero must safely reinitialize the chip. Scaling is
in this daemon exactly once. Using the old backlight class names would let unchanged
system init race these writes; the following distinct names avoid that.

**MEASURED source check, 2026-10-15:** no AW99703/KTD3137 frontlight implementation
in `scripts/mainline/kernel/patches`, `scripts/mainline/eink`, or the built mainline
driver tree registers these names yet. The four names below remain **proposed
defaults**, not confirmed runtime devices. Do not auto-select stock-looking names:
they may apply the gain again or be written by the ROM's legacy init rules.

| LED-class name | Chip / bus / address / hwen (stock DT) | Framework property | Daemon raw brightness |
|---|---|---|---|
| `a9:warm:center` | AW99703 #1, i2c1 0x36, GPIO83 | `sys.linevibrator_open` | `floor(min(value,2200)*81/100)` |
| `a9:white:center` | AW99703 #2, i2c0 0x36, GPIO85 | `sys.linevibrator_short` | `floor(min(value,2200)*75/100)` |
| `a9:warm:edge` | KTD3137 #3, i2c1 0x35, GPIO88 | `sys.linevibrator_open` | `floor(min(value,2047)*78/100)` |
| `a9:white:edge` | KTD3137 #4, i2c0 0x35, GPIO101 | `sys.linevibrator_short` | `floor(min(value,2047)*72/100)` |

Each default full path is `/sys/class/leds/NAME/brightness`; brightness files 0644 root.
Configure each independently with full absolute sysfs paths in the vendor-owned
service's argv when the drivers choose their final names:

```text
--led-warm-center /sys/class/leds/FINAL_AW1_NAME/brightness
--led-white-center /sys/class/leds/FINAL_AW2_NAME/brightness
--led-warm-edge /sys/class/leds/FINAL_KTD3_NAME/brightness
--led-white-edge /sys/class/leds/FINAL_KTD4_NAME/brightness
```

Options require a `/sys/` path; fake-root tests apply `--root` to those paths too.
They change the target only, preserving the channel/property association and gain.
At logical 1000 outputs are 810/750/780/720. At logical 2200 outputs are
1782/1650/1596/1473. Reject malformed or out-of-range **property** values rather
than guessing; the framework's maximum is 2200. Properties already include both
user brightness and Night Light temperature split: do not reinterpret them as
on/off events or mix temperature a second time. Writes are cached per chip after
success; missing devices retry independently after probe. Healthy Android operation
waits on Bionic global property notifications, with no periodic polling wakeups;
failures retry once per second. Host fake properties poll at 50 ms.

Display-power gating must have a single owner in the Lights HAL/LED driver:
logical desired brightness persists while non-interactive output is gated to zero,
then is restored on unblank. Do not rewrite `brightness=0` underneath this daemon's
cache as a second independent writer. Keep sensor HAL input and the existing
ungated A9 overlay/monotonic curve ([adaptive-brightness.md](adaptive-brightness.md));
light adaptation is still performed by the ROM. Gating, channel color assignment
and real low-level current/brightness need later optical validation.

## Integration, policy and unavoidable limits

Artifacts are in `scripts/mainline/eink-compat/`:

- `eink-compat.c`: socket replacement, property translator, bounded framework calls.
- `a9-eink-compat.rc`: reference mirror of the vendor's **a9_eink_compat** service,
  executable `/vendor/bin/a9-eink-compat`. The shipped rc belongs to
  `scripts/mainline/android/vendor/etc/init/a9-eink-compat.rc`; install one copy.
- `sepolicy.te`, `file_contexts`, `genfs_contexts`, `ueventd.rc`: integration snippets.
  No new permissive domain, no allow for untrusted_app, no broad I2C permissions.

**MEASURED source review:** the vendor rc uses the existing `phhsu_daemon` label,
root UID, default argv/socket and `restart_period 5`, matching the daemon. On
`init.svc.a9_eink_server=running` it requests `stop a9_eink_server`, then
`start a9_eink_compat`. It avoids the A16 cross-Treble `override` rejection.
The following source-level issues remain; no vendor-directory edit was made:

- **Socket handoff race:** init stop is asynchronous. The old process can still
  own `a9_eink_socket` when the shim binds. The shim deliberately exits 1 on an
  occupied socket; this non-oneshot service should retry after five seconds.
  Vendor integration should start only after observing the old service **stopped**,
  with a handoff guard/boot condition so it does not start prematurely. A second
  stock start while the shim is already running should still be stopped.
- **State replay gap:** the app retries a broken socket on its next command, but
  ordinary writes have no acknowledgment. A command accepted by the old helper
  just before stop can be lost, and the existing app does not automatically replay
  the current mode/knobs on helper replacement. Properties survive and the shim
  reads initial frontlight values. Validate startup ordering or provide an explicit
  app/service state reapply after handoff; do not claim lossless switchover.
- **Stale prebuilt:** `scripts/mainline/android/build-vendor.sh:85–92` builds the
  shim **only if the binary is missing**, then copies it into the vendor stage.
  After any source update run the documented `make ... android` before rebuilding
  the vendor, or have its owner make freshness checking unconditional.
- **Permissions:** the rc's ability to stop a system service and the shim's exec,
  socket, property, framework Binder and newly discovered sysfs traversal permissions
  still need enforcement validation. Avoiding an init override is not itself proof
  of SELinux access. The vendor owns the eventual LED labels and raw-value ABI.

The app reconnects to the same abstract name and retains existing single/double/
long E Ink key actions, per-app modes, brightness slider, warmth slider, AOD and
power-off settings. The binary accepts multiple idle clients and buffers split
lines; only `sct` sends replies so failed old commands cannot corrupt a later reply.
The protocol has no positive acknowledgment for other commands; errors go to stderr.

**Policy limitation:** `sys.linevibrator_*` are system properties (P documents
`system_prop`), and Night Light/theme require framework Binder access. A normal
Treble vendor domain cannot simply relabel these as vendor properties or become a
core domain. This GSI already carries and uses `phhsu_daemon` for this functionality;
the rc explicitly selects that existing privileged domain. The supplied policy
snippet is for **combined GSI policy assembly**, not a promise that private
`phhsu_daemon` is visible to a standalone vendor-policy compiler. Do not silently
turn SELinux off to make it work. Vendor integration must resolve its existing
platform domain/public mapping and verify the linked policy, executable entrypoint,
socket label and sysfs real-path labels. No linked new-vendor policy was compiled
or validated as part of this change; enforcement is untested.

If a strictly isolated production vendor cannot use this domain, a **described-only
patcher change** is unavoidable: retain a small system-side broker for `stl/smb/wov`,
`sct` and `theme`, and expose a narrowly authorized vendor socket or stable AIDL
interface for refresh and frontlight values. Define public exported system-to-vendor
properties for brightness, with explicit system_server producer/vendor reader rules,
instead of granting access to all `system_prop`. Keep app protocol and UI unchanged.
No patcher edit, rebuild or push was performed here.

Haptic register compatibility and the ambient-light sensor HAL remain separate
vendor/kernel responsibilities, as do composer HAL, buffer import, fences, shutdown
drain and panel power management. The daemon does not pretend that missing bitmap
upload support works, send stock EPD SF transactions, load waveform blobs, open
temperature nodes or control GPIO/I2C. Build/test completion is not device validation.

## Offline build and verification

An existing Nix Android SDK supplies NDK **27.0.12077973**, so no musl/glibc fallback
or network NDK download was necessary:

```sh
nix-shell -p gcc gnumake --run 'make -C scripts/mainline/eink-compat test'
nix-shell -p gcc gnumake --run 'make -C scripts/mainline/eink-compat test-sanitize'
export ANDROID_NDK_HOME=/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973
nix-shell -p gnumake --run 'make -C scripts/mainline/eink-compat android'
nix-shell -p binutils --run \
  'readelf -h scripts/mainline/eink-compat/build/a9-eink-compat-android; readelf -d scripts/mainline/eink-compat/build/a9-eink-compat-android'
```

The Makefile accepts any compatible `ANDROID_NDK_HOME`; the store path above only
records this host's measured toolchain. `ANDROID_API` defaults to **28**, the first
Bionic API exporting `glob/globfree`; the target ROM is API36. Output is ignored
under `build/`. Rebuild the binary explicitly before vendor packaging, as noted above.
Host tests require explicit fake sysfs/property/tool directories and unique abstract
sockets; host executable refuses to run without them. Fixtures never invoke Android
tools. Tests cover mode mapping, repeated manual clear, required-attribute discovery,
missing/failed manual stores without GC16 fallback or event replay, thresholds, framing/oversized/NUL
input, concurrent idle clients, sentinels, per-chip gains/clamping/zero, unchanged
write suppression, late LED probe, native warmth calls, SDK/range guards, Binder
exception/false/exit/timeout and unchanged reply stream after unsupported `cm`.
Follow-up tests cover platform-only discovery, verified class fallback with either
driver spelling or card/device links, vkms/unknown-driver exclusion, deduplicated
aliases, ambiguous groups, reprobe/card renumbering, all four configured LED paths,
and occupied-socket handoff/restart. Fake framework tools use shell builtins to
avoid Python startup exhausting the production tool deadline on a busy build host.

**MEASURED results (follow-up):** 23 integration tests pass, also with AddressSanitizer and
UndefinedBehaviorSanitizer. Host GCC and NDK ARM64 builds pass
with `-Wall -Wextra -Werror -Wconversion -Wformat=2`. ARM64 artifact is ELF64 AArch64
PIE with `/system/bin/linker64`, requiring only Android `libc.so` and `libdl.so`.
No device measurement is required to review these artifacts; the coordinator owns
all eventual on-phone integration and optical checks.
