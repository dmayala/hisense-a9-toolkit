#!/usr/bin/env bash
#
# verify-partitions-edl.sh — read key partitions back off the device over EDL
# and diff them against the byte-verified backups.
#
# Purpose: the device makes a real boot attempt (slot retry counter decrements)
# but Android never completes boot. boot_b, recovery_b, misc and the GPT are
# all supposedly restored. This checks that what is ACTUALLY on flash matches
# what we believe is there -- in particular whether any earlier tool's
# write landed on an unintended target.
#
# Read-only. Writes nothing to the device.
#
# Requires: phone in EDL (9008), visible to this machine (lsusb -d 05c6:9008).
#
# Usage:  ./verify-partitions-edl.sh            (BACKUP=<dir> to compare elsewhere)
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
BACKUP="${BACKUP:-$A9_BACKUPS/2026-08-08_055553}"
a9_usb_lock_inherit
OUT="${OUT:-$A9_DATA/verify_$(date +%Y-%m-%d_%H%M%S)}"
MAP="$HERE/partition-map.txt"

a9_in_edl || { echo "ERROR: no 9008 (EDL) device on USB." >&2; exit 1; }

# Partitions worth checking, in order of how likely a mismatch explains the
# failure. metadata is included because /data cannot mount without it.
PARTS="recovery_b boot_b metadata vbmeta_b dtbo_b keystore persist"

mkdir -p "$OUT"
echo "== EDL partition verification"
echo "   backups: $BACKUP"
echo "   dumps:   $OUT"
echo

printf '%-14s %-6s %-12s %-12s %s\n' PARTITION LUN SECTORS BYTES RESULT
for p in $PARTS; do
  read -r _ lun start sectors <<<"$(grep -m1 "^$p " "$MAP")"
  if [ -z "${sectors:-}" ]; then
    printf '%-14s %-6s %-12s %-12s %s\n' "$p" "?" "-" "-" "NOT IN MAP"; continue
  fi
  dump="$OUT/$p.img"
  "$EDL" rs "$start" "$sectors" "$dump" --lun="$lun" >/dev/null 2>&1
  if [ ! -s "$dump" ]; then
    printf '%-14s %-6s %-12s %-12s %s\n' "$p" "$lun" "$sectors" "-" "READ FAILED"; continue
  fi
  sz=$(stat -c %s "$dump")
  if [ -f "$BACKUP/$p.img" ]; then
    if cmp -s "$dump" "$BACKUP/$p.img"; then
      res="MATCHES backup"
    else
      # where does it first differ, and is it all-zero (i.e. erased)?
      off=$(cmp "$dump" "$BACKUP/$p.img" 2>/dev/null | sed 's/.*byte //;s/,.*//')
      if ! tr -d '\000' < "$dump" | head -c1 | grep -q .; then
        res="*** ALL ZERO (erased!)"
      else
        res="*** DIFFERS from backup (first at byte ${off:-?})"
      fi
    fi
  else
    res="no backup to compare"
  fi
  printf '%-14s %-6s %-12s %-12s %s\n' "$p" "$lun" "$sectors" "$sz" "$res"
done

echo
echo "Dumps kept in $OUT for further inspection."
