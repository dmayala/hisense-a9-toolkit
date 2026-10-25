#!/usr/bin/env bash
#
# backup-partitions.sh — image the Hisense A9's partitions to the PC over adb.
#
# Why this does NOT stream dd over `adb exec-out`:
#   Streaming through exec-out can CRLF-translate the byte stream (first seen
#   with Windows adb: every 0x0A became 0x0D 0x0A, images came back silently
#   CORRUPTED and slightly oversized, boot_a +48,840 bytes, frp +1). Native
#   Linux adb does not do that, but the method is kept anyway: dd to a temp file on the
#   device, then `adb pull` (binary-safe file transfer), and every image is
#   size-verified against partition-sizes.txt with a mismatch a hard failure.
#
# Requires: device booted into Android, USB debugging on, root granted to the
# shell in Magisk (Magisk > Superuser > Shell).
#
# Usage:
#   ./backup-partitions.sh              # tiers 1+2 (~800 MiB)
#   ./backup-partitions.sh all          # tiers 1+2+3, includes 10 GiB super
#   ./backup-partitions.sh critical     # tier 1 only (~150 MiB)
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
SIZES="$HERE/partition-sizes.txt"     # label:bytes, derived from COM4_PartitionsList.xml
TMP_ON_DEVICE="/sdcard/.a9-backup-tmp.img"

OUTBASE="${OUTBASE:-$A9_BACKUPS}"
OUTDIR="$OUTBASE/$(date +%Y-%m-%d_%H%M%S)"

# ── Tier 1: IRREPLACEABLE ────────────────────────────────────────────────────
# Device-unique. NOT in the stock firmware package. Sensor/touch calibration,
# modem NV and IMEI, keymaster keyblobs, and the dm-default-key metadata that
# /data is encrypted against.
TIER1=(
  persist modemst1 modemst2 fsc fsg
  devinfo keystore metadata frp
  apdp dip secdata prospecfg
  tpf_a tpf_b uefivarstore spunvm ssd
)

# ── Tier 2: CURRENT STATE ────────────────────────────────────────────────────
# Reconstructible from the stock package plus the GSI, but backing them up
# turns a rebuild into a restore. boot_* holds your Magisk patch.
TIER2=(
  boot_a boot_b
  recovery_a recovery_b
  dtbo_a dtbo_b
  vbmeta_a vbmeta_b
  vbmeta_system_a vbmeta_system_b
  logo_a logo_b
  misc
)

# ── Tier 3: BULK ─────────────────────────────────────────────────────────────
TIER3=(
  super
  modem_a modem_b
  dsp_a dsp_b
  mdtp_a mdtp_b
)

# NOT backed up here: userdata (228 GiB and FBE-encrypted — a raw image is only
# restorable to this exact device with keystore/metadata/TEE intact; use an
# app-level backup instead), plus cache / phonelog / kdebuginfo / rawdump /
# logdump, which are scratch.

case "${1:-default}" in
  critical) PARTS=("${TIER1[@]}") ;;
  all)      PARTS=("${TIER1[@]}" "${TIER2[@]}" "${TIER3[@]}") ;;
  default)  PARTS=("${TIER1[@]}" "${TIER2[@]}") ;;
  *) echo "usage: $0 [critical|default|all]" >&2; exit 2 ;;
esac

echo "== Hisense A9 partition backup"
echo "   adb:    $ADB"
echo "   output: $OUTDIR"
echo "   set:    ${1:-default} (${#PARTS[@]} partitions)"
echo

[ -f "$SIZES" ] || { echo "ERROR: missing $SIZES — cannot verify image sizes." >&2; exit 1; }

if ! "$ADB" get-state >/dev/null 2>&1; then
  echo "ERROR: no device. Plug in, enable USB debugging, authorise the host." >&2
  exit 1
fi
if ! "$ADB" shell 'su -c id' 2>/dev/null | grep -q 'uid=0'; then
  echo "ERROR: no root. Grant root to 'Shell' in Magisk > Superuser." >&2
  exit 1
fi

mkdir -p "$OUTDIR"
MANIFEST="$OUTDIR/manifest.txt"
{
  echo "# Hisense A9 (HLTE556N) partition backup"
  echo "# date: $(date -Is)"
  echo "# set:  ${1:-default}"
  echo "# method: on-device dd + adb pull (never exec-out), size-verified"
  "$ADB" shell 'getprop ro.build.display.id; getprop ro.vendor.build.version.incremental; getprop ro.boot.slot_suffix' 2>/dev/null | sed 's/^/# /'
  echo
} > "$MANIFEST"

fail=0

for p in "${PARTS[@]}"; do
  src="/dev/block/bootdevice/by-name/$p"
  dst="$OUTDIR/$p.img"
  want=$(grep -m1 "^$p:" "$SIZES" | cut -d: -f2)

  printf '%-22s ' "$p"

  if ! "$ADB" shell "su -c 'test -e $src'" 2>/dev/null; then
    echo "SKIP (absent)"; echo "$p SKIPPED absent" >> "$MANIFEST"; continue
  fi

  # dd on-device, then pull. Never stream binary through adb stdout.
  if ! "$ADB" shell "su -c 'dd if=$src of=$TMP_ON_DEVICE bs=4M'" >/dev/null 2>&1; then
    echo "FAILED (dd)"; echo "$p FAILED dd" >> "$MANIFEST"; fail=1
    "$ADB" shell "su -c 'rm -f $TMP_ON_DEVICE'" >/dev/null 2>&1
    continue
  fi

  "$ADB" pull "$TMP_ON_DEVICE" "$dst" >/dev/null 2>&1
  "$ADB" shell "su -c 'rm -f $TMP_ON_DEVICE'" >/dev/null 2>&1

  if [ ! -s "$dst" ]; then
    echo "FAILED (pull)"; echo "$p FAILED pull" >> "$MANIFEST"; fail=1; continue
  fi

  got=$(stat -c %s "$dst")
  if [ -n "$want" ] && [ "$got" != "$want" ]; then
    echo "CORRUPT  got $got, expected $want"
    echo "$p CORRUPT got=$got want=$want" >> "$MANIFEST"; fail=1; continue
  fi

  sum=$(sha256sum "$dst" | cut -d' ' -f1)
  printf 'OK  %12s bytes  verified\n' "$got"
  echo "$p OK $got $sum" >> "$MANIFEST"
done

echo
echo "Manifest: $MANIFEST"
du -sh "$OUTDIR"
if [ "$fail" -eq 0 ]; then
  echo "All partitions imaged and size-verified."
else
  echo "WARNING: failures above — see manifest. DO NOT trust this backup." >&2
fi
exit "$fail"
