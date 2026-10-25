#!/usr/bin/env bash
# Host test of the 0049b companion (POWER-PATH, review 67b): applies
# candidates/0049b-companion-a9-eink-seq to a temp COPY of HEAD (unless HEAD
# already carries it, as since eink-trial2), then runs
#  1. the full BusyBox sequencer simulation (tests/mainline-eink-seq/run.sh)
#  2. a min_off_ms preflight matrix: 500 passes; 499, 1000, 250, 200, 0,
#     malformed and absent fail closed (pre FAIL, abort latched, no modprobe,
#     no pattern launched, later e3 refused).
# Usage: run-0049b.sh [matrix|full]   (default: both). BUSYBOX=... optional.
# No device. Production files are not modified.
set -u
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../.." && pwd)
MODE=${1:-both}; shift 2>/dev/null || true
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
git -C "$REPO" archive HEAD | tar -x -C "$W"
git -C "$W" init -q 2>/dev/null
C="$REPO"/scripts/mainline/kernel/candidates/0049b-companion-a9-eink-seq.patch.candidate
# the companion is in the tree since eink-trial2 8b20d17: then test HEAD as is
if (cd "$W" && git apply --reverse --check "$C") 2>/dev/null; then
	echo "companion already in HEAD; testing HEAD"
else
	(cd "$W" && git apply --check "$C" && git apply "$C") || { echo "companion does not apply" >&2; exit 2; }
fi
grep -q '^FP_MIN_OFF_MS=500 ' "$W/scripts/mainline/kernel/initramfs/a9-eink-seq" || { echo "constant not 500" >&2; exit 2; }
rc=0
if [ "$MODE" != matrix ]; then
	bash "$W/tests/mainline-eink-seq/run.sh" "$@" || rc=1
fi
if [ "$MODE" != full ]; then
	# prefix = everything run.sh does before its scenario list
	python3 - "$W" <<'PY'
import sys, pathlib
w = pathlib.Path(sys.argv[1]); t = w/'tests/mainline-eink-seq'
orig = (t/'run.sh').read_text()
prefix = orig.split('\nALL=', 1)[0]
new = prefix.replace('HERE=$(cd "$(dirname "$0")" && pwd)', 'HERE=' + str(t))
assert new != prefix
suffix = r'''
for value in 500 499 1000 250 200 0 bad missing; do
    newphone "pre-$value" pg-good
    if [ "$value" = missing ]; then
        rm "$SIM_ROOT/sys/module/fp9929a/parameters/min_off_ms"
    else
        printf '%s\n' "$value" > "$SIM_ROOT/sys/module/fp9929a/parameters/min_off_ms"
    fi
    seq pre >/dev/null
    if [ "$value" = 500 ]; then
        has 'RESULT pre PASS' && ok "$value: matching pre PASS" || no "$value: matching pre failed"
        has 'ok   pre.fp_min_off:.*500.*want 500' && ok "$value: expected value recorded" || no "$value: missing criterion"
    else
        has 'RESULT pre FAIL' && ok "$value: pre FAIL" || no "$value: pre passed"
        has 'FAIL pre.fp_min_off:.*want 500' && ok "$value: mismatch named" || no "$value: missing mismatch"
        [ ! -e "$A9_SEQ_DIR/modprobe.out" ] && ok "$value: modprobe skipped" || no "$value: modprobe ran"
        [ -e "$A9_SEQ_DIR/ABORTED" ] && ok "$value: abort latched" || no "$value: no abort latch"
        seq e3 >/dev/null
        has 'refused: ABORTED' && ok "$value: subsequent e3 refused" || no "$value: e3 accepted"
    fi
    [ ! -s "$SIM_ROOT/sim/launch.log" ] && ok "$value: no pattern launched" || no "$value: pattern launched"
    common_end "$value"
done
echo "precheck matrix: $pass passed, $fail failed"
[ "$fail" = 0 ]
'''
(t/'precheck-matrix.sh').write_text(new + suffix)
PY
	bash "$W/tests/mainline-eink-seq/precheck-matrix.sh" || rc=1
	# backstop arithmetic: cap + 1000 + FP_MIN_OFF_MS
	grep -q 'hvcap + 1000 + FP_MIN_OFF_MS' "$W/scripts/mainline/kernel/initramfs/a9-eink-seq" && echo "backstop uses FP_MIN_OFF_MS (cap + 1000 + 500 = cap + 1.5 s)" || { echo "backstop arithmetic changed"; rc=1; }
fi
[ $rc = 0 ] && echo "0049b companion: PASS" || echo "0049b companion: FAIL"
exit $rc
