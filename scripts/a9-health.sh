#!/usr/bin/env bash
#
# a9-health.sh — one read-only look at what state the A9 is in. Run it before
# and after every test-firmware flash.
#
# Reports the USB/boot mode (android, recovery, sideload, fastbootd, bootloader,
# edl, diag, usb-only, absent) and the facts that matter in that mode: slot,
# boot_completed, build, setup-wizard state, BCB, fastboot slot variables.
#
# STRICTLY READ-ONLY: adb get-state/devices/shell getprop|dumpsys|settings get|
# pm list|df|dd-from-misc, fastboot devices/getvar, lsusb. Never reboots, never
# flashes, never writes, never talks to an EDL device. Every adb/fastboot call
# is wrapped in `timeout`, so this cannot hang.
#
# Usage:
#   ./a9-health.sh            # key/value lines
#   ./a9-health.sh --json     # one JSON object on stdout
#
# Exit code: 0 = android and sys.boot_completed=1
#            1 = some other reachable mode
#            2 = absent (nothing on USB at all)
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

JSON=0
case "${1:-}" in
  --json) JSON=1 ;;
  "") ;;
  -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "usage: $0 [--json]" >&2; exit 2 ;;
esac

T=8   # seconds per adb/fastboot call

# Ordered key/value store, emitted at the end in either format.
KEYS=(); VALS=()
add() { KEYS+=("$1"); VALS+=("$2"); }
WARNINGS=()
warn() { WARNINGS+=("$1"); }

adbsh() { timeout "$T" "$ADB" shell "$1" 2>/dev/null </dev/null | tr -d '\r'; }
fbvar() { # fbvar <var> -> value ("" if unavailable)
  timeout "$T" "$FB" getvar "$1" 2>&1 </dev/null | tr -d '\r' \
    | sed -n "s/^\\(.*) \\)\\{0,1\\}$1: *//p" | head -1
}

# ---------------------------------------------------------------- mode
ADBSTATE=$(timeout "$T" "$ADB" get-state 2>/dev/null </dev/null | tr -d '\r\n')
ADBLIST=$(timeout "$T" "$ADB" devices 2>/dev/null </dev/null | tr -d '\r' | sed 1d | awk 'NF')
FBLIST=$(timeout "$T" "$FB" devices 2>/dev/null </dev/null | tr -d '\r' | awk 'NF')
if [ -n "${ANDROID_SERIAL:-}" ]; then
  FBLIST=$(printf '%s\n' "$FBLIST" | grep -F "$ANDROID_SERIAL" || true)
fi
HISENSE=$(lsusb -d 109b: 2>/dev/null)

MODE=absent
if [ "$ADBSTATE" = "device" ]; then MODE=android
elif [ "$ADBSTATE" = "recovery" ]; then MODE=recovery
elif [ "$ADBSTATE" = "sideload" ]; then MODE=sideload
elif printf '%s\n' "$FBLIST" | grep -q fastboot; then
  if [ "$(fbvar is-userspace)" = "yes" ]; then MODE=fastbootd; else MODE=bootloader; fi
elif a9_in_edl; then MODE=edl
elif lsusb -d 05c6:900e >/dev/null 2>&1; then MODE=diag
elif [ -n "$ADBLIST" ] || [ -n "$HISENSE" ]; then MODE=usb-only
fi

add mode "$MODE"
add serial "${ANDROID_SERIAL:-${A9_SERIAL:-}}"
[ "$MODE" = usb-only ] && [ -n "$ADBLIST" ] && add adb_devices "$(printf '%s' "$ADBLIST" | tr '\n\t' ';  ')"

is_b() { case "$1" in b|_b) return 0;; *) return 1;; esac; }
TRAP="slot B is the TEST slot (slot A is main since 2026-10-11, docs/slots-and-promotion.md). Fine during a test; otherwise slot a probably failed to boot (automatic fallback). Repair a, then: fastboot set_active a"
BOOTED=0

# ---------------------------------------------------------------- per mode
case "$MODE" in
android)
  P=$(adbsh '
    echo "slot=$(getprop ro.boot.slot_suffix)"
    echo "boot_completed=$(getprop sys.boot_completed)"
    echo "lineage=$(getprop ro.lineage.version)"
    echo "display_id=$(getprop ro.build.display.id)"
    echo "fingerprint=$(getprop ro.build.fingerprint)"
    echo "provisioned=$(settings get global device_provisioned)"
    echo "setup_complete=$(settings get secure user_setup_complete)"
    echo "wizard=$(pm list packages -d | grep -q org.lineageos.setupwizard && echo disabled || echo enabled)"
    echo "wakefulness=$(dumpsys power | sed -n "s/.*mWakefulness=//p" | head -1)"
    echo "focus=$(dumpsys window | sed -n "s/^ *mCurrentFocus=//p" | head -1)"
  ')
  g() { printf '%s\n' "$P" | sed -n "s/^$1=//p" | head -1; }
  SLOT=$(g slot); BC=$(g boot_completed)
  add slot "$SLOT"
  add boot_completed "$BC"
  add lineage_version "$(g lineage)"
  add build_display_id "$(g display_id)"
  add fingerprint "$(g fingerprint)"
  add device_provisioned "$(g provisioned)"
  add user_setup_complete "$(g setup_complete)"
  add setupwizard "$(g wizard)"
  add wakefulness "$(g wakefulness)"
  add current_focus "$(g focus)"
  SU=$(timeout "$T" "$ADB" shell 'su -c id' 2>/dev/null </dev/null | tr -d '\r')
  case "$SU" in *uid=0*) add su_works yes ;; *) add su_works no ;; esac
  # E Ink: HWC composition must be OFF (1010 = 1) on this panel; UBWC off (vendor rc)
  if [ "$BC" = 1 ] && case "$SU" in *uid=0*) true ;; *) false ;; esac; then
    HWC=$(timeout "$T" "$ADB" shell 'su -c "service call SurfaceFlinger 1010"' 2>/dev/null </dev/null | tr -d '\r\n' | grep -o "0x00000010: [0-9a-f]*" | sed 's/0x00000010: 0000000//')
    add hwc_disabled "${HWC:-?}"
    [ -n "$HWC" ] && [ "$HWC" != 1 ] && warn "mDebugDisableHWC=$HWC: HWC composition is ON, the E Ink panel will freeze (a16 findings §12)"
    PON=$(timeout "$T" "$ADB" shell 'su -c "dmesg | grep -E \"Power-(on|off) reason\" | head -4"' 2>/dev/null </dev/null | tr -d '\r' | sed 's/^\[[^]]*\] *//' | tr '\n' ';')
    add pon_reason "$PON"
  fi
  add ubwc_disabled "$(adbsh 'getprop vendor.gralloc.disable_ubwc')"
  [ "$BC" = 1 ] && BOOTED=1
  is_b "$SLOT" && warn "$TRAP"
  ;;
recovery|sideload)
  P=$(adbsh '
    echo "twrp=$(getprop ro.twrp.version)"
    echo "slot=$(getprop ro.boot.slot_suffix)"
    echo "data=$(df /data 2>/dev/null | tail -1)"
    echo "bcb=$(dd if=/dev/block/bootdevice/by-name/misc bs=1 count=32 2>/dev/null | tr "\000" "." )"
    echo "bcb_nonzero=$(dd if=/dev/block/bootdevice/by-name/misc bs=1 count=32 2>/dev/null | tr -d "\000" | wc -c)"
  ')
  g() { printf '%s\n' "$P" | sed -n "s/^$1=//p" | head -1; }
  add twrp_version "$(g twrp)"
  add slot "$(g slot)"
  D=$(g data)
  case "$D" in
    *"/data"*) add data_mounted "yes ($D)" ;;
    *) add data_mounted "no" ;;
  esac
  add bcb_first32 "$(g bcb)"
  NZ=$(g bcb_nonzero)
  if [ -n "$NZ" ] && [ "$NZ" -gt 0 ] 2>/dev/null; then
    add bcb_state "NON-EMPTY"
    warn "BCB (misc) is non-empty: next boot will go to recovery. Clear with scripts/twrp.sh bcb before rebooting."
  elif [ -n "$NZ" ]; then
    add bcb_state "empty"
  else
    add bcb_state "unreadable"
  fi
  ;;
bootloader|fastbootd)
  CS=$(fbvar current-slot)
  add current_slot "$CS"
  for v in slot-successful slot-unbootable slot-retry-count; do
    for s in a b; do add "$v:$s" "$(fbvar "$v:$s")"; done
  done
  add is_userspace "$(fbvar is-userspace)"
  add snapshot_update_status "$(fbvar snapshot-update-status)"
  add unlocked "$(fbvar unlocked)"
  is_b "$CS" && warn "$TRAP"
  ;;
edl)
  add note "EDL (9008) — use scripts/edl-cli.sh / restore-golden.sh; the Sahara hello is one-shot, if nothing has talked to it since it enumerated it is usable, otherwise it may be wedged (hold Power 10–15 s)"
  ;;
diag)
  add note "Qualcomm 05c6:900e: not adb, not fastboot, not EDL. This kernel sets download mode early (set_dload_mode=1), so a KERNEL PANIC most likely lands here (crashdump, RAM intact; inferred). Record the time and the last thing run BEFORE holding Power 10-15 s; then check the boot logs."
  ;;
usb-only)
  add usb "$(printf '%s' "$HISENSE" | head -3 | tr '\n' ';')"
  add hint "Hisense USB device but no usable adb/fastboot: unauthorized/offline adb, or still booting. Check the phone screen, wait, re-run. Also check lsusb -t."
  ;;
absent)
  add hint "Nothing on USB. Check cable and USB passthrough: the VM owns the Alder Lake xHCI 00:14.0, check lsusb -t."
  ;;
esac
[ "$MODE" = sideload ] && add note "adb sideload mode (TWRP/recovery sideload)"

# ---------------------------------------------------------------- output
jesc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' | tr -d '\000-\037'; }

if [ "$JSON" = 1 ]; then
  printf '{'
  for i in "${!KEYS[@]}"; do
    [ "$i" -gt 0 ] && printf ','
    printf '"%s":"%s"' "$(jesc "${KEYS[$i]}")" "$(jesc "${VALS[$i]}")"
  done
  printf ',"warnings":['
  for i in "${!WARNINGS[@]}"; do
    [ "$i" -gt 0 ] && printf ','
    printf '"%s"' "$(jesc "${WARNINGS[$i]}")"
  done
  printf ']}\n'
else
  for i in "${!KEYS[@]}"; do
    printf '%-22s %s\n' "${KEYS[$i]}" "${VALS[$i]}"
  done
  for w in "${WARNINGS[@]+"${WARNINGS[@]}"}"; do
    printf '\n!!! WARNING: %s\n' "$w"
  done
fi

case "$MODE" in
  absent) exit 2 ;;
  android) [ "$BOOTED" = 1 ] && exit 0 || exit 1 ;;
  *) exit 1 ;;
esac
