#!/usr/bin/env bash
#
# edl-restore-minimal.sh — undo exactly what we changed, over EDL.
#
# Writes back ONLY the two partitions this session modified:
#   misc        — clears the stuck BCB "boot-recovery" command
#   recovery_b  — the device's own stock recovery (byte-verified backup)
#
# Everything else is left alone: /data, super, modem, persist, bootloader.
# This is NOT a full factory restore and does not wipe anything.
#
# Uses bkerler edlclient through ./edl.
#
# Before running: phone in EDL (9008) and visible here — `lsusb -d 05c6:9008`.
# Get there with `adb reboot edl`, or with the phone off: hold
# Power + Vol Up + Vol Down while plugging in the EDL cable.
#
# Usage:
#   ./edl-restore-minimal.sh check     # storage info only, writes nothing
#   ./edl-restore-minimal.sh gpt       # print LUN0 partition table
#   ./edl-restore-minimal.sh slots     # dump LUN4 GPT + decode slot bits
#   ./edl-restore-minimal.sh restore   # write misc + recovery_b
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
BACKUP="${BACKUP:-$A9_BACKUPS/2026-08-08_055553}"
a9_usb_lock_inherit

[ -f "$LOADER" ] || { echo "ERROR: loader not found at $LOADER" >&2; exit 1; }
a9_in_edl || { echo "ERROR: no 9008 (EDL) device on USB." >&2; exit 1; }

case "${1:-check}" in
  check)
    echo "== storage info (writes nothing)"
    exec "$EDL" getstorageinfo
    ;;
  gpt)
    echo "== LUN0 partition table (writes nothing)"
    exec "$EDL" printgpt --lun=0
    ;;
  slots)
    # Dump LUN4's GPT so we can read the A/B slot attribute bits for
    # boot_a/boot_b (priority, tries-remaining, successful). This is where the
    # bootloader records "slot B is unbootable" — NOT in misc or recovery.
    # Read-only.
    OUT="${2:-$PWD/gpt-lun4.bin}"
    echo "== dumping LUN4 GPT to $OUT (writes nothing to the device)"
    "$EDL" rs 0 34 "$OUT" --lun=4 && ls -l "$OUT" \
      && python3 "$HERE/parse-slot-attrs.py" "$OUT"
    ;;
  restore)
    for f in misc recovery_b; do
      [ -f "$BACKUP/$f.img" ] || { echo "ERROR: missing $BACKUP/$f.img" >&2; exit 1; }
    done

    # Size-check against the real partition table before writing anything.
    declare -A WANT=([misc]=1048576 [recovery_b]=100663296)
    for f in misc recovery_b; do
      got=$(stat -c %s "$BACKUP/$f.img")
      if [ "$got" != "${WANT[$f]}" ]; then
        echo "ERROR: $f.img is $got bytes, expected ${WANT[$f]} — refusing to write." >&2
        exit 1
      fi
    done
    echo "Backup images size-verified."

    # A misc image carries the BCB in its first bytes. One captured after
    # `adb reboot bootloader` holds "bootonce-bootloader" and would send every
    # boot straight back to fastboot — the opposite of what this restore is for.
    bcb=$(head -c 32 "$BACKUP/misc.img" | tr -d '\000')
    if [ -n "$bcb" ]; then
      echo "ERROR: $BACKUP/misc.img carries a BCB command ('$bcb') — refusing." >&2
      exit 1
    fi
    echo

    # misc first: clearing the BCB is what stops the recovery boot loop, and it
    # is the smaller/faster write, so we learn quickly whether writes work.
    for f in misc recovery_b; do
      printf '%-14s writing... ' "$f"
      if "$EDL" w "$f" "$BACKUP/$f.img" --lun=0; then
        echo "OK"
      else
        echo "FAILED"; exit 1
      fi
    done

    echo
    echo "Done. Both partitions restored. Rebooting the device:"
    "$EDL" reset
    ;;
  *) echo "usage: $0 [check|gpt|slots|restore]" >&2; exit 2 ;;
esac
