#!/usr/bin/env bash
#
# abl-check-tests.sh — positive and negative controls for scripts/abl-check.py,
# which runs the phone's own ABL image checks in Unicorn. Kept out of
# run-tests.sh because it needs python unicorn (nix-shell) and reads ~400 MB of
# images from ~/a9-assets. No phone, no network, no writes to ~/a9-assets.
#
# Usage: tests/abl-check-tests.sh      (about 40 s; exit 0 = all passed)
#
set -uo pipefail
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; A9="$(dirname "$T")"
D=${A9_DATA:-$HOME/a9-assets}
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
PASS=0; FAILN=0
pass() { echo "  PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL  $1"; FAILN=$((FAILN + 1)); }

if python3 -c 'import unicorn' 2>/dev/null; then PY=(python3 -P)
else PY=(nix-shell -p python3Packages.unicorn --run); fi
py() { if [ "${PY[0]}" = nix-shell ]; then "${PY[@]}" "python3 -P $(printf '%q ' "$@")"; else "${PY[@]}" "$@"; fi; }

# in-memory controls (positive images + constructed negatives)
py "$T/abl_check_cases.py"; rc=$?
[ $rc = 0 ] && pass "abl_check_cases.py: all controls" || fail "abl_check_cases.py (exit $rc)"

echo "== abl-check.sh command line"
TWRP=$D/recovery/twrp-HLTE556N-31434216263.img
"$A9/scripts/abl-check.sh" "$TWRP" recovery --quiet >"$W/out" 2>&1; rc=$?   # positional form, as flash-recovery.sh calls it
[ $rc = 0 ] && grep -q "RESULT: PASS" "$W/out" && pass "TWRP image (positional target): exit 0, RESULT: PASS" || fail "TWRP image (exit $rc)"
# a FAIL case as a file: first 48 MiB of TWRP with recovery_dtbo_size zeroed
head -c $((48 << 20)) "$TWRP" > "$W/bad.img"
printf '\000\000\000\000' | dd of="$W/bad.img" bs=1 seek=1632 conv=notrunc status=none
"$A9/scripts/abl-check.sh" "$W/bad.img" --target recovery --json >"$W/out.json" 2>&1; rc=$?
[ $rc = 1 ] && python3 -I -c "import json,sys; r=json.load(open(sys.argv[1])); sys.exit(r['result']!='FAIL' or r['stages']['dtbo']['pass'] is not False)" "$W/out.json" \
  && pass "recovery_dtbo_size 0 file: exit 1, JSON stage dtbo FAIL" || fail "recovery_dtbo_size 0 file (exit $rc)"
"$A9/scripts/abl-check.sh" "$W/missing.img" --target recovery >/dev/null 2>&1; rc=$?
[ $rc = 2 ] && pass "missing image: exit 2" || fail "missing image (exit $rc)"
"$A9/scripts/abl-check.sh" "$TWRP" --target recovery --abl "$D/HLTE556N/abl.elf" >"$W/out" 2>&1; rc=$?
[ $rc = 2 ] && grep -q "not the pinned build" "$W/out" && pass "package abl.elf (other build): refused, exit 2" || fail "unpinned ABL accepted (exit $rc)"

echo "== $PASS passed, $FAILN failed"
[ "$FAILN" = 0 ]
