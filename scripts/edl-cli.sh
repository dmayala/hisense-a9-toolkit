#!/usr/bin/env bash
#
# edl-cli.sh — drive the Hisense A9 over EDL (9008) from Linux, using bkerler's
# edlclient through ./edl. Subcommands: see usage below.
#
# Requires: phone in EDL, visible to this machine as 05c6:9008 (lsusb).
#
# Sahara: edl uploads the programmer on the first call after an EDL entry and
# detects that the device is already in firehose mode on later calls, so the
# old `connect` + SAHARA_DONE=1 dance is not needed. `connect` is kept as a
# harmless connection test, and SAHARA_DONE is accepted and ignored.
#
# Usage:
#   ./edl-cli.sh                        # = gpt: print every LUN's table (read-only)
#   ./edl-cli.sh gpt [lun]              # print partition table(s)        (read-only)
#   ./edl-cli.sh readgpt [lun] [out]    # dump LUN sectors 0-33 to a file (read-only)
#   ./edl-cli.sh slots                  # dump LUN4 GPT + decode A/B slot bits (read-only)
#   ./edl-cli.sh read <lun> <start> <count> <outfile>                     (read-only)
#   ./edl-cli.sh connect                # load the programmer, print storage info
#   ./edl-cli.sh reset                  # reboot out of EDL
#   ./edl-cli.sh write <lun> <label> <file>      # WRITE a named partition
#   ./edl-cli.sh writeraw <lun> <start> <file>   # WRITE raw sectors
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
SECTOR=4096
a9_usb_lock_inherit

if ! a9_in_edl; then
  echo "ERROR: no Qualcomm 9008 (EDL) device on USB." >&2
  other=$(lsusb | grep -iE '05c6|109b|18d1' | head -3)
  [ -n "$other" ] && echo "       Present instead: $other" >&2
  echo "       Enter EDL first (adb reboot edl). 900e is diagnostics, not EDL." >&2
  exit 1
fi

case "${1:-gpt}" in
  connect)
    exec "$EDL" getstorageinfo
    ;;

  reset)
    echo "-- Firehose: reset (device will reboot out of EDL)"
    exec "$EDL" reset
    ;;

  gpt)
    if [ -n "${2:-}" ]; then exec "$EDL" printgpt --lun="$2"; else exec "$EDL" printgpt; fi
    ;;

  readgpt)
    lun="${2:-4}"; out="${3:-$PWD/gpt-lun$lun.bin}"
    # 34 sectors covers protective MBR + GPT header + all entry sectors.
    echo "-- reading LUN$lun sectors 0-33 -> $out"
    "$EDL" rs 0 34 "$out" --lun="$lun" || exit
    ls -l "$out"
    ;;

  slots)
    out="${2:-$PWD/gpt-lun4.bin}"
    "$EDL" rs 0 34 "$out" --lun=4 || exit
    python3 "$HERE/parse-slot-attrs.py" "$out"
    ;;

  read)
    [ "$#" -eq 5 ] || { echo "usage: $0 read <lun> <start> <count> <outfile>" >&2; exit 2; }
    lun="$2"; start="$3"; count="$4"; out="$5"
    "$EDL" rs "$start" "$count" "$out" --lun="$lun" || exit
    want=$(( count * SECTOR )); got=$(stat -c %s "$out" 2>/dev/null || echo 0)
    [ "$got" = "$want" ] || { echo "ERROR: $out is $got bytes, expected $want" >&2; exit 1; }
    ;;

  write)
    # Writes by partition NAME: edl resolves the start sector from the device's
    # own GPT (not start_sector=0, which would only suit a whole-LUN image).
    [ "$#" -eq 4 ] || { echo "usage: $0 write <lun> <label> <file>" >&2; exit 2; }
    lun="$2"; label="$3"; file="$4"
    [ -f "$file" ] || { echo "ERROR: $file not found" >&2; exit 1; }
    echo "WRITE $label on LUN$lun from $(basename "$file") ($(stat -c %s "$file") bytes)"
    exec "$EDL" w "$label" "$file" --lun="$lun"
    ;;

  writeraw)
    # Raw sector write at an explicit start_sector. Used for the GPT, which is
    # not a normal named partition. Never hand-patch a GPT and write it with
    # this — see restore-factory-gpt-lun4.sh and the a9-edl skill.
    [ "$#" -eq 4 ] || { echo "usage: $0 writeraw <lun> <start> <file>" >&2; exit 2; }
    lun="$2"; start="$3"; file="$4"
    [ -f "$file" ] || { echo "ERROR: $file not found" >&2; exit 1; }
    sz=$(stat -c %s "$file")
    (( sz % SECTOR == 0 )) || { echo "ERROR: $file is not a whole number of $SECTOR-byte sectors" >&2; exit 1; }
    echo "WRITE RAW: LUN$lun start_sector=$start sectors=$(( sz / SECTOR ))  <- $(basename "$file")"
    A9_EDL_ALLOW_RAW=1 exec "$EDL" ws "$start" "$file" --lun="$lun"
    ;;

  *) echo "usage: $0 [gpt [lun] | readgpt [lun] [out] | slots | read <lun> <start> <count> <out> | connect | reset | write <lun> <label> <file> | writeraw <lun> <start> <file>]" >&2; exit 2 ;;
esac
