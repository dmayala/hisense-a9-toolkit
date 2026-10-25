#!/usr/bin/env bash
#
# sync-slot-a.sh — make slot A a byte-for-byte copy of slot B, over EDL.
#
# Why: slot A used to have no system at all (system_a absent) while the
# bootloader still treated it as bootable, so a slot-B boot failure fell back
# to nothing: the 2026-08-08 brick precondition. With A a copy of the golden B,
# a failing test image on B falls back to a working system instead.
#
# What it writes (one EDL session):
#   super    a prebuilt image with system/system_ext/product/vendor for BOTH
#            slots (groups qti_dynamic_partitions_a/_b, no orphaned -cow
#            partitions), built with lpmake from the dump's own super; see
#            docs/plans/server-handoff-plan.md item 8 for the exact command
#   <p>_a    from the dump's <p>_b for every per-slot partition that differed:
#            xbl xbl_config abl tz hyp rpm keymaster devcfg qupfw uefisecapp
#            imagefv featenabler modem bluetooth boot dtbo vbmeta vbmeta_system
#            (an OTA does the same to the inactive slot)
# Never: persist, modemst1/2, fsg, fsc, misc, the GPT, any _b partition.
# xbl_a/xbl_config_a live on LUN1, the boot LUN of slot A, which is not used
# while slot B is active. Even a bad xbl write is recoverable: EDL is in the
# SoC's boot ROM, and the dump's original _a images can be written back.
#
# Afterwards (not done here): test slot A once
#   adb reboot bootloader; fastboot set_active a; fastboot reboot   -> a9-health
#   adb reboot bootloader; fastboot set_active b; fastboot reboot   -> a9-health
# then take a new golden dump (backup-edl.sh), since super changed.
#
# Usage:
#   SUPER=<super image> ./sync-slot-a.sh             # check inputs only
#   SUPER=<super image> ./sync-slot-a.sh --write --enter
#   FROM=<edl dump dir>  (default: the golden dump) supplies the _b images
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
FROM="${FROM:-$A9_BACKUPS/edl_2026-10-17_102417}"
SUPER="${SUPER:-}"
export A9_EDL_ALLOW_BOOTCHAIN=1   # copies the phone's OWN _b chain to _a (scripts/edl write guard)
PARTS=(xbl xbl_config abl tz hyp rpm keymaster devcfg qupfw uefisecapp imagefv featenabler modem bluetooth boot dtbo vbmeta vbmeta_system)
LPDUMP="${LPDUMP:-$(command -v lpdump || echo /nix/store/q17av8m8k96y1yqqssbbpi9r8fnbyhp2-android-tools-35.0.2/bin/lpdump)}"

WRITE=0; ENTER=0
for a in "$@"; do case "$a" in --write) WRITE=1 ;; --enter) ENTER=1 ;; *) echo "usage: SUPER=<img> $0 [--write [--enter]]" >&2; exit 2 ;; esac; done

[ -f "$SUPER" ] || { echo "ERROR: set SUPER=<prebuilt super image>" >&2; exit 1; }
[ "$(stat -c %s "$SUPER")" = 10737418240 ] || { echo "ERROR: $SUPER is not 10 GiB" >&2; exit 1; }
layout=$("$LPDUMP" "$SUPER" 2>/dev/null)
for p in system system_ext product vendor; do for s in a b; do
  printf '%s' "$layout" | grep -q "Name: ${p}_$s\$" || { echo "ERROR: $SUPER lacks ${p}_$s" >&2; exit 1; }
done; done
printf '%s' "$layout" | grep -q -- '-cow' && { echo "ERROR: $SUPER still has -cow partitions" >&2; exit 1; }
echo "super image OK: $SUPER (both slots, no -cow)"

declare -A SRC LUN
for p in "${PARTS[@]}"; do
  b=$(ls "$FROM"/lun*/"${p}_b.bin" 2>/dev/null | head -1); a=$(ls "$FROM"/lun*/"${p}_a.bin" 2>/dev/null | head -1)
  [ -n "$a" ] && [ -n "$b" ] || { echo "ERROR: ${p}_a/_b missing in $FROM" >&2; exit 1; }
  [ "$(stat -c %s "$a")" = "$(stat -c %s "$b")" ] || { echo "ERROR: ${p}_a and ${p}_b differ in size" >&2; exit 1; }
  l=$(basename "$(dirname "$a")"); SRC[$p]="$b"; LUN[$p]="${l#lun}"
  printf '  %-14s _b -> _a  lun%s  %10s bytes\n' "$p" "${LUN[$p]}" "$(stat -c %s "$b")"
done
if [ -f "$FROM/SHA256SUMS" ]; then
  ( cd "$FROM" && for p in "${PARTS[@]}"; do grep " ./lun[0-9]/${p}_b.bin\$" SHA256SUMS; done | sha256sum -c --quiet ) \
    || { echo "ERROR: hash mismatch in $FROM" >&2; exit 1; }
  echo "source hashes OK"
fi

[ "$WRITE" = 1 ] || { echo "Check only. Re-run with --write [--enter]."; exit 0; }

a9_usb_lock_inherit
if ! a9_in_edl; then
  [ "$ENTER" = 1 ] && [ -n "$(a9_adb_state)" ] && { echo "-- adb reboot edl"; "$ADB" reboot edl; for _ in $(seq 1 30); do a9_in_edl && break; sleep 1; done; }
  a9_in_edl || { echo "ERROR: no 9008 (EDL) device on USB." >&2; exit 1; }
fi

log="${TMPDIR:-/tmp}/a9-sync-slot-a"
w() { printf '  %-16s ' "$1"; if timeout 1800 "$EDL" w "$1" "$2" --lun="$3" >"$log-$1.log" 2>&1; then echo OK; else echo "FAILED ($log-$1.log) — still in EDL, re-run with --write"; exit 1; fi; }
echo "-- writing super (10 GiB, ~5 min) and ${#PARTS[@]} slot-A partitions"
w super "$SUPER" 0
for p in "${PARTS[@]}"; do w "${p}_a" "${SRC[$p]}" "${LUN[$p]}"; done

echo "-- edl reset"; timeout 60 "$EDL" reset >/dev/null 2>&1
for _ in $(seq 1 60); do [ "$(a9_adb_state)" = device ] && break; sleep 3; done
for _ in $(seq 1 60); do [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ] && break; sleep 3; done
"$HERE/a9-health.sh"
