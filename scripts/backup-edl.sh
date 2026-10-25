#!/usr/bin/env bash
#
# backup-edl.sh — full-device EDL backup of the Hisense A9, restorable onto a
# bricked phone.
#
# Uses bkerler's edlclient, which is what the community guides for this exact
# device use (aimindseye/hisense-a9). `rl --genxml` reads every partition AND
# writes the rawprogram XML alongside it, so the dump is self-describing and
# can be restored with `edl wl` or flashed as a factory-style rawprogram package.
#
#   backup:   edl rl <dir> --memory=ufs --loader=<loader> --skip=userdata --genxml
#   restore:  edl wl <dir> --memory=ufs --loader=<loader>
#
# Requires: phone in EDL (9008) and visible to this machine (lsusb -d 05c6:9008).
#
# Usage:
#   ./backup-edl.sh gpt          # connection test: print the partition table
#   ./backup-edl.sh              # everything except userdata (~12 GiB, ~20 min)
#   ./backup-edl.sh userdata     # userdata alone (228 GiB, hours)
#   ./backup-edl.sh full         # both in one pass
#
# Prefer the two-step form over `full`. The first run banks the irreplaceable
# partitions in ~20 minutes and emits the rawprogram XML; the multi-hour
# userdata read can then fail, be interrupted, or be retried without putting
# any of that at risk.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
# LOADER comes from a9-env.sh: the _001360E1 variant. The two firehose loaders
# in the firmware package are the same size but NOT identical (they differ
# from byte 4161), and every community guide for this device uses _001360E1.

a9_usb_lock_inherit

OUTBASE="${OUTBASE:-$A9_BACKUPS}"
OUTDIR="$OUTBASE/edl_$(date +%Y-%m-%d_%H%M%S)"

MODE="${1:-default}"
[ -f "$LOADER" ] || { echo "ERROR: loader not found: $LOADER" >&2; exit 1; }
[ -x "$EDL" ]    || { echo "ERROR: edl wrapper not executable: $EDL" >&2; exit 1; }

echo "== Hisense A9 EDL backup (bkerler edlclient)"
echo "   loader: $LOADER"
echo

case "$MODE" in
  gpt)
    # Loads the programmer and reads the partition table without writing
    # anything. Always run this first — it proves the device enters 9008, that
    # passthrough works, and that this handset accepts this loader.
    exec "$EDL" printgpt --memory=ufs --loader="$LOADER"
    ;;
  userdata)
    # Just the one partition. Pair it with a `default` run — the userdata image
    # is FBE-encrypted and is only restorable alongside the keystore and
    # metadata captured at the same time.
    OUTDIR="$OUTBASE/edl_userdata_$(date +%Y-%m-%d_%H%M%S)"
    mkdir -p "$OUTDIR"
    echo "Output: $OUTDIR"
    echo "228 GiB over EDL — expect several hours. Do not let the host sleep."
    echo
    "$EDL" r userdata "$OUTDIR/userdata.bin" --memory=ufs --lun=0 --loader="$LOADER"
    rc=$?
    [ "$rc" -eq 0 ] && du -sh "$OUTDIR"
    exit "$rc"
    ;;
  default) SKIP=(--skip=userdata) ;;
  full)    SKIP=() ;;
  *) echo "usage: $0 [gpt|default|userdata|full]" >&2; exit 2 ;;
esac

mkdir -p "$OUTDIR"
echo "Output: $OUTDIR"
[ "$MODE" = "full" ] && echo "WARNING: including userdata — 228 GiB, expect several hours."
echo

# --genxml is the point of this: it emits rawprogram XML next to the images, so
# the dump can also be flashed via `edl qfil`, not only by `edl wl`.
"$EDL" rl "$OUTDIR" --memory=ufs --loader="$LOADER" "${SKIP[@]}" --genxml
rc=$?

echo
if [ "$rc" -eq 0 ]; then
  du -sh "$OUTDIR" 2>/dev/null
  ls "$OUTDIR"/*.xml 2>/dev/null && echo "(rawprogram XML written — edl qfil can flash this too)"
else
  echo "edl exited $rc — check output above." >&2
fi

cat > "$OUTDIR/RESTORE.md" <<EOF
# Restoring this backup

Captured $(date -Is) with bkerler edlclient, loader:
  $(basename "$LOADER")

Put the phone in EDL (9008), check \`lsusb -d 05c6:9008\`, then from $HERE:

    ./edl wl "$OUTDIR" --memory=ufs --loader="$LOADER"

A **lun6 error is expected and should be ignored** — the device reports six
LUNs but only 0-5 carry partitions.

Single partition instead of everything:

    ./edl w persist "$OUTDIR/persist.bin" --memory=ufs --loader="$LOADER"

userdata is $( [ "$MODE" = full ] && echo "INCLUDED" || echo "NOT included" ) in this backup.

See docs/backup-and-recovery.md — in particular, userdata + metadata +
keystore must be restored as a set or not at all.
EOF

echo "Wrote $OUTDIR/RESTORE.md"
exit "$rc"
