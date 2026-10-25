#!/usr/bin/env bash
#
# restore-golden.sh — put the phone back to the GOLDEN state after a test flash.
#
# Writes over EDL only what test flashing touches:
#   super  boot_a/b  recovery_a/b  dtbo_a/b  vbmeta_a/b  vbmeta_system_a/b  misc
# misc comes from misc-clean.bin: the raw dump's misc.bin holds a stale
# "bootonce-bootloader" BCB, so the clean copy has bytes 0-2047 zeroed and
# the Virtual A/B message at 32768 kept (see GOLDEN.md).
# NEVER written: persist, modemst1/2, fsg, fsc (IMEI/calibration), xbl/abl/tz
# (bootloader chain), the GPT, or metadata/keystore (those belong with a
# matching userdata, which no golden dump contains).
#
# /data is left alone. The phone is in daily use: wipe /data only after a fresh
# twrp-backup.sh (restore across a format proven 2026-10-15, docs/twrp-backup.md). Only if they agree and the test build changed
# /data so that the golden system no longer boots: `adb shell twrp format data`
# from TWRP (never `fastboot -w`), then los-skip-setup.sh and a9-aod-setup.sh.
# What the dump holds per partition: data/golden.tsv. Each --write run logs to
# runs/<UTC>-restore-golden/ (a re-run never overwrites the previous logs).
#
# Default is CHECK ONLY: finds every image in the dump, checks sizes against
# partition-sizes.txt and hashes against SHA256SUMS, and refuses a misc image
# that carries a BCB command. Nothing touches the phone without --write.
#
# Usage:
#   ./restore-golden.sh                        # check the golden dump
#   ./restore-golden.sh --write                # phone must already be in EDL (9008)
#   ./restore-golden.sh --write --enter        # adb reboot edl first (phone in Android/TWRP)
#   GOLDEN=<dump dir> ./restore-golden.sh ...  # restore a different dump
#   ./restore-golden.sh --write --no-hash      # skip the ~2 min SHA-256 check
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
GOLDEN="${GOLDEN:-$A9_BACKUPS/edl_2026-10-17_102417}"
SIZES="$HERE/partition-sizes.txt"

PARTS=(super boot_a boot_b dtbo_a dtbo_b vbmeta_a vbmeta_b vbmeta_system_a vbmeta_system_b recovery_a recovery_b misc)
# Image file per partition when it is not <part>.bin
declare -A SRC=([misc]=misc-clean)

WRITE=0; ENTER=0; HASH=1
for a in "$@"; do
  case "$a" in
    --write) WRITE=1 ;; --enter) ENTER=1 ;; --no-hash) HASH=0 ;;
    *) echo "usage: $0 [--write [--enter] [--no-hash]]" >&2; exit 2 ;;
  esac
done

[ -d "$GOLDEN" ] || { echo "ERROR: golden dump not found: $GOLDEN" >&2; exit 1; }
echo "== golden dump: $GOLDEN"
grep -F "$(basename "$GOLDEN")" "$A9_HOME/data/golden.tsv" 2>/dev/null | awk -F'\t' '{printf "   holds %-9s %s\n", $2, $5}' 

# ---- locate + size-check every image
declare -A FILE LUN
for p in "${PARTS[@]}"; do
  img="${SRC[$p]:-$p}"
  f=$(ls "$GOLDEN"/lun*/"$img".bin 2>/dev/null | head -1)
  [ -n "$f" ] || { echo "ERROR: $img.bin missing from dump" >&2; exit 1; }
  lun=$(basename "$(dirname "$f")"); lun="${lun#lun}"
  want=$(grep -m1 "^$p:" "$SIZES" | cut -d: -f2)
  got=$(stat -c %s "$f")
  [ -n "$want" ] && [ "$got" != "$want" ] && { echo "ERROR: $img.bin is $got bytes, expected $want" >&2; exit 1; }
  FILE[$p]="$f"; LUN[$p]="$lun"
  printf '  %-16s lun%s  %12s bytes  OK\n' "$p" "$lun" "$got"
done

# ---- hashes
if [ "$HASH" = 1 ]; then
  [ -f "$GOLDEN/SHA256SUMS" ] || { echo "ERROR: no SHA256SUMS in dump (use --no-hash to skip)" >&2; exit 1; }
  echo "-- verifying SHA-256 of the images to be written"
  ( cd "$GOLDEN" && for p in "${PARTS[@]}"; do grep " ./lun${LUN[$p]}/${SRC[$p]:-$p}.bin\$" SHA256SUMS; done | sha256sum -c --quiet ) \
    || { echo "ERROR: hash mismatch — dump damaged, refusing." >&2; exit 1; }
  echo "   hashes OK"
fi

# ---- misc must not carry a BCB command
bcb=$(head -c 32 "${FILE[misc]}" | tr -d '\000')
[ -z "$bcb" ] || { echo "ERROR: ${FILE[misc]} carries a BCB command ('$bcb') — refusing." >&2; exit 1; }

if [ "$WRITE" != 1 ]; then
  echo "Check only. Re-run with --write (phone in EDL) or --write --enter (phone in Android/TWRP)."
  exit 0
fi

a9_usb_lock
a9_run_dir restore-golden "$@"
echo "-- logs: $A9_RUN"

# ---- get into EDL
if ! a9_in_edl; then
  if [ "$ENTER" = 1 ]; then
    s=$(a9_adb_state)
    [ -n "$s" ] || { echo "ERROR: not in EDL and no adb device to reboot from." >&2; exit 1; }
    echo "-- adb reboot edl (from '$s')"
    "$ADB" reboot edl
    for _ in $(seq 1 30); do a9_in_edl && break; sleep 1; done
  fi
  a9_in_edl || { echo "ERROR: no 9008 (EDL) device on USB." >&2; exit 1; }
fi

# ---- write. super first: it is what a test flash replaces, and the longest.
echo "-- writing ${#PARTS[@]} partitions (super is 10 GiB: ~5 min)"
for p in "${PARTS[@]}"; do
  printf '  %-16s ' "$p"
  if timeout 1800 "$EDL" w "$p" "${FILE[$p]}" --lun="${LUN[$p]}" >"$A9_RUN/$p.log" 2>&1; then
    echo "OK"
  else
    echo "FAILED (log: $A9_RUN/$p.log)"
    echo "Phone is still in EDL; fix and re-run with --write. Do NOT reset into a half-written state." >&2
    exit 1
  fi
done

echo "-- edl reset"
timeout 60 "$EDL" reset >/dev/null 2>&1
echo "-- waiting for Android"
for _ in $(seq 1 60); do [ "$(a9_adb_state)" = device ] && break; sleep 3; done
for _ in $(seq 1 60); do [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break; sleep 3; done
if [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; then
  echo "Android up, slot $("$ADB" shell getprop ro.boot.slot_suffix | tr -d '\r'), $("$ADB" shell getprop ro.lineage.version | tr -d '\r')"
  "$HERE/los-skip-setup.sh" status
else
  echo "WARNING: Android did not finish booting. If it bootloops, /data from the test build is the usual cause." >&2
  echo "         The phone is in daily use: do NOT wipe /data without the user's explicit OK (a9-recover R4)." >&2
  exit 1
fi
