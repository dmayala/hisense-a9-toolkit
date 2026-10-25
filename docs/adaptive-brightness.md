# Adaptive brightness on the A16 GSI: three causes, fixed in v4, v5, v6

**Date:** 2026-10-11
**Status:** Fixed in image v6 (both slots). MEASURED unless marked INFERRED.

Image v6: `$A9_DATA/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v6.img`,
sha256 `92b2ab2ea97b7b61aae67cd7a1c6eeeaaa0121c855fa50429c4cb09e83c305ee`,
patcher `a16-port` `9ff1bcf`, 44 patches fired, 0 zero-match.

## Symptom

Auto-brightness was missing or wrong: no "Adaptive brightness" entry in Settings, and
the frontlight did not follow ambient light sensibly.

## Causes, in the order found

1. **Overlay overwritten (fixed in v4).** The device overlay
   `me.phh.treble.overlay.Hisense.HLTE556N` (light sensor, brightness curve, thresholds)
   was replaced by an SDK-less copy during patching. v4: the patcher keeps/installs the
   A9 overlay.
2. **phh `+*glob*` gate not supported on A16 (fixed in v5).** The overlay manifest uses
   `requiredSystemPropertyName` / `requiredSystemPropertyValue="+*Hisense/HLTE556N*"`.
   A16 compares the value by exact string equality
   (`FrameworkParsingPackageUtils.checkRequiredSystemProperties`), so the `+*...*` gate
   never matched and the overlay stayed disabled although installed. v5 installs the overlay
   UNGATED. Consequence (INFERRED from the same code path): **every phh device overlay with a
   `+*...*` fingerprint gate is inert on this GSI**, not only ours.
3. **Inversion hooks (fixed in v6).** The legacy patches `patch_GetBrightness`,
   `patch_SetBrightness`, `patch_GetSetBrightness` in `AutomaticBrightnessController`
   applied `1 - x`, inverting the curve and corrupting the ambient-light thresholds. v6
   removes them.

## Evidence (v6, slot B then A)

* `cmd overlay list`: `[x] me.phh.treble.overlay.Hisense.HLTE556N`.
* Settings shows Adaptive brightness; `screen_brightness_mode` 1.
* `dumpsys display`: light sensor `stk_stk3b6x Ambient Light Sensor Non-wakeup`.
* Curve: lux 30 -> 0.3976 (83 %), lux 23.0 -> rcmdBrt 0.3581 (slot A, 81 %). Monotonic
  rising, thresholds sane.
* HWC `1010`=1, `epd-trace` engine updates > 0, AOD with the cat unaffected.

## User covering test (slot B, v6)

| Condition | Smoothed lux | `sys.linevibrator_short` | `screen_brightness` |
|---|---|---|---|
| room | 16 | 639 | 82 |
| sensor covered | 10.7 | 100 | 82 (setting not yet updated; the frontlight ramped down first) |
| uncovered, brighter | 32 | 821 | 105 |

Dark = dim, light = bright, as wanted.

## Not changed

Refresh rate stays 60 Hz: the overlay's `defaultPeakRefreshRate=180` has no effect since
the panel exposes a single 60 Hz mode. Color mode unchanged.

## Related

Frontlight write flood is closed with an honest status in
[a16-composition-and-epd-findings.md](a16-composition-and-epd-findings.md) section 12.
