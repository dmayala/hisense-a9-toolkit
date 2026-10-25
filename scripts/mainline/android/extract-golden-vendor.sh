#!/usr/bin/env bash
# extract-golden-vendor.sh -- the restore source for vendor_b: lpunpack it from
# the golden EDL dump's super.bin (read-only) into $A9_ANDROID/golden/, with
# .sha256, plus the host lpdump of that super (android-boot.sh uses both).
# Offline; no phone. Usage: extract-golden-vendor.sh [golden_dir]
set -euo pipefail
GOLD=${1:-$HOME/a9-assets/Backups/edl_2026-10-14_110759}
OUT=${A9_ANDROID:-$HOME/A9-build/mainline/android}/golden
case "$OUT" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2 ;; esac
mkdir -p "$OUT"
SUPER="$GOLD/lun0/super.bin"
want=$(awk '$2=="./lun0/super.bin"{print $1}' "$GOLD/SHA256SUMS")
if [ ! -f "$OUT/super.bin.verified" ] || [ "$(cat "$OUT/super.bin.verified")" != "$want" ]; then
  echo "hashing golden super.bin (10.7 GB, NFS)..."
  [ "$(sha256sum "$SUPER" | awk '{print $1}')" = "$want" ] || { echo "golden super.bin does not match SHA256SUMS" >&2; exit 1; }
  echo "$want" > "$OUT/super.bin.verified"
fi
lpdump "$SUPER" > "$OUT/super-lpdump.txt"
lpunpack -p vendor_b "$SUPER" "$OUT"
( cd "$OUT" && sha256sum vendor_b.img > vendor_b.img.sha256 )
echo "golden vendor_b: $OUT/vendor_b.img $(cut -c1-16 "$OUT/vendor_b.img.sha256")... $(stat -c %s "$OUT/vendor_b.img") bytes"
