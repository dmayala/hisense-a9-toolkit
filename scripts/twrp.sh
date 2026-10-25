#!/usr/bin/env bash
#
# twrp.sh — enter and leave TWRP without getting stuck in recovery.
#
# Why this exists: rebooting to recovery on an A/B device writes a
# `boot-recovery` command into the BCB (first bytes of the `misc` partition).
# A working recovery clears it on startup; this TWRP build does not, so the
# NEXT boot goes back into recovery instead of Android. Every exit therefore
# has to clear the BCB by hand -- this wraps that.
#
# Usage:
#   ./twrp.sh enter     # Android -> TWRP
#   ./twrp.sh exit      # TWRP -> Android (clears the BCB first)
#   ./twrp.sh bcb       # just show / clear the BCB, no reboot
#   ./twrp.sh status    # where is the device and what does the BCB say
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
MISC="/dev/block/bootdevice/by-name/misc"

state() { a9_adb_state; }
in_fastboot() { "$FB" devices 2>/dev/null | grep -q fastboot; }

# In TWRP the shell is already root. In Android it is not, so fall back to su
# (needs root granted to Shell in Magisk). Empty output means neither worked.
show_bcb() {
  out=$("$ADB" shell "dd if=$MISC bs=1 count=32 2>/dev/null | od -c | head -2" 2>/dev/null)
  case "$out" in *0000020*) echo "$out" | sed 's/^/   /'; return;; esac
  out=$("$ADB" shell "su -c 'dd if=$MISC bs=1 count=32' 2>/dev/null | od -c | head -2" 2>/dev/null)
  case "$out" in
    *0000020*) echo "$out" | sed 's/^/   /' ;;
    *) echo "   (unreadable - needs root; grant it to Shell in Magisk)" ;;
  esac
}

# Zero ONLY the 2048-byte bootloader_message. Do NOT use `fastboot erase misc`:
# on this Virtual A/B device misc also holds the virtual_ab message at offset
# 32768 (magic 0x56740AB0) and erasing destroys it.
clear_bcb() {
  "$ADB" shell "dd if=/dev/zero of=$MISC bs=1 count=2048 conv=notrunc" >/dev/null 2>&1 \
    && echo "   BCB cleared (virtual_ab header at 32768 untouched)" \
    || { echo "   ERROR: could not clear BCB" >&2; return 1; }
}

case "${1:-status}" in
  enter)
    a9_usb_lock_inherit
    s=$(state)
    if [ "$s" = "recovery" ]; then echo "Already in TWRP."; exit 0; fi
    if [ "$s" != "device" ]; then
      echo "ERROR: need Android with adb (state='${s:-none}')." >&2; exit 1
    fi
    echo "-- rebooting to TWRP"
    "$ADB" reboot recovery 2>&1 | head -1
    t0=$(date +%s)
    # Stable, not just present: TWRP re-enumerates USB right after adbd starts.
    a9_wait_stable recovery 180 && { echo "-- TWRP up after $(( $(date +%s) - t0 ))s"; exit 0; }
    echo "ERROR: TWRP did not come up." >&2; exit 1
    ;;

  exit)
    a9_usb_lock_inherit
    s=$(state)
    if [ "$s" != "recovery" ]; then
      echo "ERROR: not in TWRP (state='${s:-none}')." >&2; exit 1
    fi
    echo "-- BCB before:"; show_bcb
    clear_bcb || exit 1
    echo "-- rebooting to Android"
    "$ADB" reboot 2>&1 | head -1
    for i in $(seq 1 50); do
      sleep 5
      case "$(state)" in
        device)   echo "-- Android up after $((i*5))s"; exit 0 ;;
        recovery) [ "$i" -gt 8 ] && { echo "WARNING: back in recovery — BCB may not have taken." >&2; exit 1; } ;;
      esac
    done
    echo "WARNING: did not see Android come up; check the phone." >&2; exit 1
    ;;

  bcb)
    [ "$(state)" = "" ] && { echo "ERROR: no adb device." >&2; exit 1; }
    echo "-- BCB now:"; show_bcb
    clear_bcb
    echo "-- BCB after:"; show_bcb
    ;;

  status)
    s=$(state)
    if [ -n "$s" ]; then
      echo "adb state : $s"
      [ "$s" = "device" ] && "$ADB" shell 'echo "slot      : $(getprop ro.boot.slot_suffix)"; echo "build     : $(getprop ro.build.display.id)"' 2>/dev/null
      echo "BCB       :"; show_bcb
    elif in_fastboot; then
      echo "in fastboot:"
      "$FB" getvar all 2>&1 | sed 's/(bootloader) //' \
        | grep -E "current-slot|slot-successful|slot-unbootable" | sed 's/^/   /'
    else
      echo "no device on adb or fastboot"
    fi
    ;;

  *) echo "usage: $0 [enter|exit|bcb|status]" >&2; exit 2 ;;
esac
