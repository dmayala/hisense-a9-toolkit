#!/usr/bin/env bash
#
# a9-aod-mode.sh — show which AOD mode the phone is in (READ-ONLY).
#
#   ./a9-aod-mode.sh        show the state and whether it is consistent
#   ./a9-aod-mode.sh 1|2    same, plus how to select that mode (nothing is changed)
#
# The selector is the a9service pref "aod_mode" (stock | static | overlay,
# default overlay since a9service 3.8.3), set in
#   E-Ink Settings app > Always On Display mode.
# The app exposes NO broadcast/intent for it (the only exported broadcasts are
# EINK_FORCE_CLEAR / EINK_REFRESH_SPEED_*), so it can only be chosen in the app
# UI. Setting it there makes the app do the rest, persistently:
#
#   overlay (mode 2)  setDoze(1): doze_always_on=1, doze_enabled=1, and it sends
#                     the daemon `stl96`, so sys.linevibrator_type=96 and the
#                     patched DisplayPowerController reports
#                     mColorFadeEnabled=false (no ColorFade). The overlay is
#                     drawn while the display is Dozing.
#   static  (mode 1)  setDoze(0) (full sleep) and sends stl<shader>, so
#                     sys.linevibrator_type is the shader index (not 96) and
#                     ColorFade runs: its painted frame is what the panel latches.
#   stock             leaves doze alone; Android's own AOD setting governs.
#
# The pref is persistent (app data, survives reboot). The app re-sends stl96 at
# every service start. Setting doze_*/debug props by hand is NOT a switch: the
# pref wins on the next change or service start. (The old debug.a9.colorfade
# property this script used to set is not persistent and not what the app reads.)
#
# Reads: the pref via root `cat` of the app's shared_prefs, doze_always_on,
# doze_enabled, sys.linevibrator_type, mColorFadeEnabled. Docs: docs/aod.md.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

case "${1:-}" in
  ""|1|2) ;;
  *) echo "usage: a9-aod-mode.sh [1|2]   (read-only; the mode is chosen in the app)" >&2; exit 1 ;;
esac

state() { a9_adb_state; }
[ "$(state)" = "device" ] || { echo "device not booted to Android (state='$(state)')" >&2; exit 1; }

PREFS=/data/data/com.lmqr.ha9_comp_service/shared_prefs/com.lmqr.ha9_comp_service_preferences.xml

# A missing aod_mode key means the app default (overlay) is in effect.
mode=$("$ADB" shell "su -c 'cat $PREFS'" 2>/dev/null </dev/null | tr -d '\r' \
  | sed -n 's/.*name="aod_mode">\([a-z]*\)<.*/\1/p' | head -1)
[ -n "$mode" ] || mode="(not in prefs file -> app default: overlay)"
da=$("$ADB" shell settings get secure doze_always_on 2>/dev/null </dev/null | tr -d '\r')
de=$("$ADB" shell settings get secure doze_enabled 2>/dev/null </dev/null | tr -d '\r')
stl=$("$ADB" shell getprop sys.linevibrator_type 2>/dev/null </dev/null | tr -d '\r')
cf=$("$ADB" shell "dumpsys display 2>/dev/null | grep -oE 'mColorFadeEnabled=[a-z]+' | head -1" 2>/dev/null </dev/null | tr -d '\r')

echo "current state (read-only):"
printf "  %-22s %s\n" "aod_mode (app pref)" "$mode"
printf "  %-22s %s\n" "doze_always_on" "$da"
printf "  %-22s %s\n" "doze_enabled" "$de"
printf "  %-22s %s\n" "sys.linevibrator_type" "$stl"
printf "  %-22s %s\n" "mColorFadeEnabled" "$cf"

eff="$mode"; case "$mode" in "(not"*) eff=overlay ;; esac
echo
case "$eff" in
  overlay)
    if [ "$da" = 1 ] && [ "$de" = 1 ] && [ "$stl" = 96 ] && [ "$cf" = "mColorFadeEnabled=false" ]; then
      echo "consistent: overlay AOD (mode 2)"
    else
      echo "INCONSISTENT for overlay: want doze 1/1, linevibrator_type 96, mColorFadeEnabled=false"
      echo "  (re-select Overlay in the app, or restart the accessibility service)"
    fi ;;
  static)
    if [ "$da" != 1 ] && [ "$de" != 1 ] && [ "$stl" != 96 ] && [ "$cf" = "mColorFadeEnabled=true" ]; then
      echo "consistent: static / ColorFade AOD (mode 1)"
    else
      echo "INCONSISTENT for static: want doze 0, linevibrator_type != 96, mColorFadeEnabled=true"
    fi ;;
  *) echo "stock: framework AOD; doze values are whatever Android settings say" ;;
esac

case "${1:-}" in
  1) echo; echo "To select mode 1: E-Ink Settings app > Always On Display mode > Static (keep last screen)." ;;
  2) echo; echo "To select mode 2: E-Ink Settings app > Always On Display mode > Overlay (clock / chess / battery)." ;;
esac
echo "Takes effect on the next screen-off."
