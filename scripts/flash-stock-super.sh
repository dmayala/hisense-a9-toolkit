#!/usr/bin/env bash
#
# flash-stock-super.sh — write ONLY the stock `super` partition over EDL.
#
# Why this instead of a full factory restore:
#   * Touches NO bootloader chain (xbl/tz/abl), so there is no anti-rollback
#     risk -- that is the part of a factory flash that can hard-brick on a
#     downgrade.
#   * Never writes persist / modemst1 / modemst2 / fsg / fsc, so IMEI, radio
#     calibration and sensor calibration are untouched by construction.
#   * Restores system_a, converting slot A from an empty trap (the root enabler
#     of the 2026-08-08 brick) into a real fallback.
#   * Rewrites super's LP metadata, clearing the orphaned Virtual A/B COW
#     devices (system_b-cow etc.) left with no snapshot state.
#
# What it costs: the installed GSI. Re-flash it afterwards from the GSI images kept on the Windows PC (14/ and 16/ were pruned from $A9_DATA 2026-10-10).
#
# Requires: phone in EDL (9008), visible here (lsusb -d 05c6:9008).
#
# Usage:  ./flash-stock-super.sh            # size-check the chunks only, writes nothing
#         ./flash-stock-super.sh --write    # actually flash
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
FW="$A9_FW"
SECTOR=4096

# label=super, LUN0. start_sector / num_partition_sectors taken verbatim from
# rawprogram0_001360E1.xml -- do not hand-edit these.
CHUNKS="
super_1.img:225032:99
super_2.img:225288:1043289
super_3.img:1268744:100957
super_4.img:1369864:46563
super_5.img:1416456:281874
"

echo "== Flash stock super (LUN0) — GSI will be REPLACED by stock system"
echo

for c in $CHUNKS; do
  f="${c%%:*}"; rest="${c#*:}"; ss="${rest%%:*}"; ns="${rest##*:}"
  [ -f "$FW/$f" ] || { echo "ERROR: missing $FW/$f" >&2; exit 1; }
  want=$(( ns * SECTOR )); got=$(stat -c %s "$FW/$f")
  if [ "$got" != "$want" ]; then
    echo "ERROR: $f is $got bytes, XML declares $ns sectors = $want. Refusing." >&2
    exit 1
  fi
  printf '  %-14s start=%-9s sectors=%-9s %s bytes OK\n' "$f" "$ss" "$ns" "$got"
done
echo

# Writing needs --write; a bare run only does the size check above.
if [ "${1:-}" != "--write" ]; then
  echo "Size check only. Re-run with --write to flash." >&2
  exit 1
fi
a9_usb_lock_inherit
a9_in_edl || { echo "ERROR: no 9008 (EDL) device on USB." >&2; exit 1; }

echo "-- writing ~6 GB, expect 10-15 minutes. Do not interrupt."
for c in $CHUNKS; do
  f="${c%%:*}"; rest="${c#*:}"; ss="${rest%%:*}"
  echo "-- $f -> LUN0 sector $ss"
  A9_EDL_ALLOW_RAW=1 "$EDL" ws "$ss" "$FW/$f" --lun=0 2>&1 | grep -viE '^\s*$|progress' | tail -3
  rc=${PIPESTATUS[0]}
  [ "$rc" -eq 0 ] || { echo "ERROR: edl exited $rc on $f — STOP, do not reboot; re-run or restore." >&2; exit 1; }
done

echo
echo "Next: set slot A active (it now has stock system_a) and boot:"
echo "  fastboot set_active a && fastboot reboot"
