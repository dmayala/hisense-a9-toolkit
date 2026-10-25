#!/usr/bin/env bash
#
# backup-from-recovery.sh — image partitions while booted in TWRP recovery.
#
# Better than backing up from a running Android:
#   * already root, no Magisk needed
#   * /data and system are not mounted read-write, so images are quiescent
#   * works even when Android will not boot
#
# Same binary-safety rule as backup-partitions.sh: never stream dd through
# `adb exec-out` (Windows adb CRLF-corrupted images; the rule outlived it).
# dd to a temp file on the device, then `adb pull`, then size-verify.
#
# Requires: booted into TWRP with adb (root shell), free space on /tmp or an
# SD card at /external_sd.
#
# Usage:
#   ./backup-from-recovery.sh              # tiers 1+2
#   ./backup-from-recovery.sh critical     # irreplaceable only
#   ./backup-from-recovery.sh all          # + super (10 GiB) -- needs SD card
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
SIZES="$HERE/partition-sizes.txt"
OUTBASE="${OUTBASE:-$A9_BACKUPS}"
OUTDIR="$OUTBASE/recovery_$(date +%Y-%m-%d_%H%M%S)"
TMP_ON_DEVICE="/tmp/.a9bk.img"     # TWRP's /tmp is a ramdisk -- keep chunks small

TIER1="persist modemst1 modemst2 fsc fsg devinfo keystore metadata frp apdp dip secdata prospecfg tpf_a tpf_b uefivarstore spunvm ssd"
TIER2="boot_a boot_b recovery_a recovery_b dtbo_a dtbo_b vbmeta_a vbmeta_b vbmeta_system_a vbmeta_system_b logo_a logo_b misc"
TIER3="super modem_a modem_b dsp_a dsp_b mdtp_a mdtp_b"

case "${1:-default}" in
  critical) PARTS="$TIER1" ;;
  default)  PARTS="$TIER1 $TIER2" ;;
  all)      PARTS="$TIER1 $TIER2 $TIER3" ;;
  *) echo "usage: $0 [critical|default|all]" >&2; exit 2 ;;
esac

state=$(a9_adb_state)
if [ "$state" != "recovery" ]; then
  echo "ERROR: device is not in recovery (got '${state:-nothing}')." >&2
  echo "       Boot TWRP first; this script is for recovery-mode backups." >&2
  exit 1
fi
if ! "$ADB" shell 'id' 2>/dev/null | grep -q 'uid=0'; then
  echo "ERROR: no root in recovery shell." >&2; exit 1
fi
[ -f "$SIZES" ] || { echo "ERROR: missing $SIZES" >&2; exit 1; }

mkdir -p "$OUTDIR"
MANIFEST="$OUTDIR/manifest.txt"
{
  echo "# Hisense A9 (HLTE556N) backup taken from TWRP recovery"
  echo "# date: $(date -Is)"
  echo "# set:  ${1:-default}"
  "$ADB" shell 'getprop ro.twrp.version; getprop ro.boot.slot_suffix' 2>/dev/null | sed 's/^/# /'
  echo
} > "$MANIFEST"

echo "== Backup from recovery -> $OUTDIR"
fail=0

for p in $PARTS; do
  src="/dev/block/bootdevice/by-name/$p"
  want=$(grep -m1 "^$p:" "$SIZES" | cut -d: -f2)
  printf '%-22s ' "$p"

  if ! "$ADB" shell "test -e $src" 2>/dev/null; then
    echo "SKIP (absent)"; echo "$p SKIPPED" >> "$MANIFEST"; continue
  fi
  # /tmp in TWRP is RAM-backed; skip anything that would not fit comfortably.
  if [ -n "$want" ] && [ "$want" -gt 268435456 ]; then
    echo "SKIP (${want} bytes too large for the /tmp ramdisk -- use an SD card)"
    echo "$p SKIPPED too-large" >> "$MANIFEST"; continue
  fi

  if ! "$ADB" shell "dd if=$src of=$TMP_ON_DEVICE bs=4M" >/dev/null 2>&1; then
    echo "FAILED (dd)"; echo "$p FAILED dd" >> "$MANIFEST"; fail=1
    "$ADB" shell "rm -f $TMP_ON_DEVICE" >/dev/null 2>&1; continue
  fi
  "$ADB" pull "$TMP_ON_DEVICE" "$OUTDIR/$p.img" >/dev/null 2>&1
  "$ADB" shell "rm -f $TMP_ON_DEVICE" >/dev/null 2>&1

  dst="$OUTDIR/$p.img"
  [ -s "$dst" ] || { echo "FAILED (pull)"; echo "$p FAILED pull" >> "$MANIFEST"; fail=1; continue; }
  got=$(stat -c %s "$dst")
  if [ -n "$want" ] && [ "$got" != "$want" ]; then
    echo "CORRUPT got $got want $want"; echo "$p CORRUPT $got/$want" >> "$MANIFEST"; fail=1; continue
  fi
  printf 'OK  %12s bytes  verified\n' "$got"
  echo "$p OK $got $(sha256sum "$dst" | cut -d' ' -f1)" >> "$MANIFEST"
done

echo; echo "Manifest: $MANIFEST"; du -sh "$OUTDIR"
[ "$fail" -eq 0 ] && echo "All imaged and size-verified." || echo "WARNING: failures above." >&2
exit "$fail"
