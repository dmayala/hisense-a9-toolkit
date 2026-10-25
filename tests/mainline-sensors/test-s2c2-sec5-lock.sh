#!/usr/bin/env bash
# Host test of run-s2c2-sec5.sh's end_holder (extracted verbatim): TERM to our own android-boot.sh --write
# only, bounded waits, and NEVER a kill of a lock holder. Uses a private lock file; touches no phone.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
T=$(mktemp -d); PIDS=(); trap 'for p in "${PIDS[@]:-}"; do kill -9 "$p" 2>/dev/null; done; rm -rf "$T"' EXIT
python3 -I - "$ROOT/scripts/mainline/sensors/run-s2c2-sec5.sh" > "$T/fn.sh" <<'PY'
import sys
s=open(sys.argv[1]).read()
a=s.index('lock_holders() {'); b=s.index('\n}\n', s.index('end_holder() {'))+3
print(s[a:b])
PY
export XDG_RUNTIME_DIR=$T USB_LOCK=$T/a9-usb.lock
source "$T/fn.sh"
fails=0; ok() { echo "  ok   $1"; }; bad() { echo "  FAIL $1"; fails=$((fails+1)); }
# a fake wrapper: its argv contains "android-boot.sh --write"; it takes the lock with a keeper child (the
# main 966c853 pattern: the keeper holds fd 9 and dies with the wrapper)
mkwrapper() {   # $1 = term|ignore
  cat > "$T/android-boot.sh" <<'W'
mode=$1
exec 9>"$XDG_RUNTIME_DIR/a9-usb.lock"; flock -n 9 || exit 3
setpriv --pdeathsig KILL sh -c 'exec sleep 1000' </dev/null >/dev/null 2>&1 & exec 9>&-
[ "$mode" = ignore ] && trap '' TERM
while :; do sleep 0.2; done
W
  bash "$T/android-boot.sh" "$1" --write & WP=$!; PIDS+=("$WP"); sleep 0.5
}
mkwrapper term
TERM_WAIT=10 LOCK_WAIT=10 end_holder "$WP" > "$T/o1" 2>&1; rc=$?
[ "$rc" = 0 ] && ! kill -0 "$WP" 2>/dev/null && [ -z "$(lock_holders)" ] && ok "TERM ends the wrapper, its keeper releases the lock" || { bad "normal end rc=$rc"; cat "$T/o1"; }
mkwrapper ignore
TERM_WAIT=3 LOCK_WAIT=3 end_holder "$WP" > "$T/o2" 2>&1; rc=$?
[ "$rc" = 1 ] && kill -0 "$WP" 2>/dev/null && grep -q 'not killing it' "$T/o2" && ok "a wrapper ignoring TERM is reported, not killed (rc 1)" || { bad "ignore rc=$rc"; cat "$T/o2"; }
kill -9 "$WP" 2>/dev/null; sleep 0.5
# a foreign lock holder (not ours): never signalled
( exec 9>"$USB_LOCK"; flock 9; exec sleep 1000 ) & FP=$!; PIDS+=("$FP"); sleep 0.5
FH=$(lock_holders | head -n1)
TERM_WAIT=2 LOCK_WAIT=3 end_holder 999999 > "$T/o3" 2>&1; rc=$?
[ "$rc" = 1 ] && kill -0 "$FH" 2>/dev/null && grep -q 'not killing it' "$T/o3" && ok "a foreign lock holder is reported, not killed (rc 1)" || { bad "foreign rc=$rc"; cat "$T/o3"; }
bash -c "sleep 60" & NP=$!; PIDS+=("$NP")
end_holder "$NP" > "$T/o4" 2>&1; rc=$?
[ "$rc" = 1 ] && kill -0 "$NP" && grep -q 'refusing to signal it' "$T/o4" && ok "a pid that is not android-boot.sh --write is not signalled" || bad "non-wrapper rc=$rc"
echo "== result: $fails failure(s)"; [ "$fails" = 0 ] && echo "S2C2-SEC5-LOCK TEST PASS"; exit $((fails > 0))
