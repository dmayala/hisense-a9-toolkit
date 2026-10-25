#!/usr/bin/env bash
#
# a9-aod-setup.sh — apply the runtime settings the chess/clock AOD needs.
#
# Run after ANY `/data` wipe (format data, fastboot -w, factory reset).
# Idempotent; safe to re-run. Requires the device booted with adb.
#
# WHY EACH ONE MATTERS (all measured 2026-08-11 on Doze-off LineageOS 23.2 / A16):
#
#   doze_always_on / doze_enabled = 1
#       THE critical pair. In doze the display stays in a low-power ON state and
#       SurfaceFlinger keeps compositing, so a9service's TYPE_ACCESSIBILITY_OVERLAY
#       actually reaches the panel. On a full screen-off, compositing stops before
#       the overlay is drawn (ACTION_SCREEN_OFF is delivered to apps only after the
#       display is already down) and the panel latches the previous frame instead.
#       Damian's stock vndk.rc sets doze_enabled=0 as a ONE-SHOT guarded by
#       /data/local/tmp/disable_ambient_display -- after a wipe that fires again
#       and silently breaks AOD. (Patcher a16-port 2026-10-11 changed it to set 1/1;
#       older images still do 0.) The a9service "aod_mode" pref must ALSO be
#       "overlay" (E-Ink app > Always On Display mode): "static" writes doze 0 and
#       sends stl<shader> instead of the 96 sentinel. See docs/aod.md.
#
#   me.phh.treble.overlay.misc.aod_systemui enabled
#       Declares config_dozeAlwaysOnDisplayAvailable; without it the device goes
#       straight to Asleep and never dozes.
#
#   animator_duration_scale = 1
#       0 is the documented cause of the black-AOD symptom (damianmqr patches
#       overrideDurationScale(1.0f) for exactly this). Do not set it to 0.
#
#   enabled_accessibility_services
#       a9service draws the AOD. vndk.rc is supposed to set this at boot but has
#       been observed not to on this base -- set it explicitly.
#
# The image must carry the A9 ColorFade kill-switch (A9_DISABLE_COLORFADE=1, the
# default). That patch does NOT hard-disable ColorFade: every read of
# DisplayPowerController.mColorFadeEnabled (including the dumpsys line) goes
# through a9ColorFadeEnabled(), which is (sys.linevibrator_type != 96), overridden
# by debug.a9.colorfade when that is set. So the dump value follows the mode the
# app selected via the stl command (measured 2026-10-11, Doze-off GAPPS v2):
#   sys.linevibrator_type = 96      -> mColorFadeEnabled=false (overlay AOD, mode 2)
#   sys.linevibrator_type = 0..95   -> mColorFadeEnabled=true  (mode 1 / app idle)
# finalize-image.sh boots with 47 (a mode 1 passthrough index), so "true" is
# EXPECTED until the app selects overlay AOD and is not a build defect. Only
# "true while the sentinel is 96" is a defect. The check below is read-only.
#
# In the E-Ink Settings app, under "Always On Display", also ensure:
#   Disable Overlay AOD          OFF
#   Chess AOD                    ON
#   Refresh AOD after screen off ON   <- sends FORCE_CLEAR ("r") 150ms after
#                                        screen-off so the EPD takes a new frame.
#                                        Its XML defaultValue=true never applies
#                                        because the code reads it with a false
#                                        default until the settings screen is opened.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
SVC="com.lmqr.ha9_comp_service/com.lmqr.ha9_comp_service.A9AccessibilityService"

state() { a9_adb_state; }
if [ "$(state)" != "device" ]; then
  echo "Device is not booted to Android (state='$(state)')." >&2
  exit 1
fi

echo "Applying A9 AOD settings..."
"$ADB" shell "
  settings put secure enabled_accessibility_services $SVC
  settings put secure accessibility_enabled 1
  appops set com.lmqr.ha9_comp_service SYSTEM_ALERT_WINDOW allow
  settings put secure doze_always_on 1
  settings put secure doze_enabled 1
  settings put global animator_duration_scale 1
  settings put global transition_animation_scale 1
  settings put global window_animation_scale 1
  cmd overlay enable me.phh.treble.overlay.misc.aod_systemui
" >/dev/null 2>&1

sleep 2
echo
echo "Verification:"
"$ADB" shell '
  printf "  %-22s %s\n" "accessibility"  "$(settings get secure enabled_accessibility_services)"
  printf "  %-22s %s\n" "accessibility_enabled" "$(settings get secure accessibility_enabled)"
  printf "  %-22s %s\n" "doze_always_on" "$(settings get secure doze_always_on)"
  printf "  %-22s %s\n" "doze_enabled"   "$(settings get secure doze_enabled)"
  printf "  %-22s %s\n" "animator_scale" "$(settings get global animator_duration_scale)"
  printf "  %-22s %s\n" "aod_overlay"    "$(cmd overlay list --user current 2>/dev/null | grep aod_systemui)"
  printf "  %-22s %s\n" "mColorFadeEnabled" "$(dumpsys display 2>/dev/null | grep -oE "mColorFadeEnabled=[a-z]+" | head -1)"
' 2>/dev/null | tr -d '\r'
echo
cf=$("$ADB" shell 'dumpsys display 2>/dev/null | grep -oE "mColorFadeEnabled=[a-z]+" | head -1' 2>/dev/null | tr -d '\r')
lt=$("$ADB" shell 'getprop sys.linevibrator_type' 2>/dev/null | tr -d '\r')
dbg=$("$ADB" shell 'getprop debug.a9.colorfade' 2>/dev/null | tr -d '\r')
echo "ColorFade: $cf  sys.linevibrator_type='$lt'  debug.a9.colorfade='${dbg:-<unset>}'"
if [ -n "$dbg" ]; then
  echo "  NOTE: debug.a9.colorfade is set ($dbg) and overrides the app-driven mode."
fi
case "$cf" in
  mColorFadeEnabled=false)
    echo "  OK: ColorFade is off -- overlay AOD mode (mode 2)." ;;
  mColorFadeEnabled=true)
    if [ "$lt" = "96" ]; then
      echo "  PROBLEM: sentinel is 96 but ColorFade is enabled -- the kill-switch is missing"
      echo "  from services.jar (image built without A9_DISABLE_COLORFADE=1) or debug.a9.colorfade=1."
    else
      echo "  Expected while the sentinel is not 96: ColorFade runs (mode 1, retain last screen)."
      echo "  For overlay AOD (mode 2) turn Disable Overlay AOD OFF in the E-Ink Settings app so it"
      echo "  sends stl96; this script cannot setprop it (SELinux allows phhsu_daemon only)."
    fi ;;
  *) echo "  could not read mColorFadeEnabled (display service not ready?)" ;;
esac
