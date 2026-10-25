#!/usr/bin/env bash
#
# dt-abl-check-tests.sh -- host test for scripts/mainline/dt-abl-check.sh: the
# board DTS is compiled from source with its local includes (a9-pmi632.dtsi) in a
# clean workdir, and a failed compile fails the check instead of reusing a stale
# a9.dtb. Needs nix-shell (dtc, cpp, unicorn) and reads the ABL from ~/a9-assets
# (read-only). No phone. The dt-bindings/upstream dtsi cache is copied from
# $A9_MAINLINE/dtcheck/inc when present, else fetched from git.kernel.org.
#
# Usage: tests/dt-abl-check-tests.sh      (about 5 s; exit 0 = all passed)
#
set -uo pipefail
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; A9="$(dirname "$T")"
CK=$A9/scripts/mainline/dt-abl-check.sh
DTS=$A9/docs/mainline/sm6115-hisense-hlte556n.dts
SEED=${A9_MAINLINE:-$HOME/A9-build/mainline}/dtcheck/inc
X=$(mktemp -d); trap 'rm -rf "$X"' EXIT
PASS=0; FAILN=0
pass() { echo "  PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL  $1"; FAILN=$((FAILN + 1)); }
# a fresh workdir holding only the upstream cache (no DTS, no a9-pmi632.dtsi, no dtb)
fresh() {
	mkdir -p "$1/inc"
	if [ -d "$SEED" ]; then
		cp -r "$SEED"/dt-bindings "$1/inc/"
		cp "$SEED"/sm6115.dtsi "$SEED"/pm6125.dtsi "$SEED"/pmi632.dtsi "$1/inc/"
	fi
}

echo "== clean tree, current DTS"
fresh "$X/ok"
"$CK" "$X/ok" >"$X/ok.log" 2>&1; rc=$?
[ $rc = 0 ] && pass "clean workdir: exit 0" || { fail "clean workdir (exit $rc, log below)"; tail -20 "$X/ok.log"; }
[ "$(grep -c 'RESULT: PASS' "$X/ok.log")" = 2 ] && pass "recovery + boot/--dtbo: RESULT: PASS" || fail "expected two RESULT: PASS"
[ -s "$X/ok/a9.dtb" ] && [ "$X/ok/a9.dtb" -nt "$DTS" ] && pass "a9.dtb built this run" || fail "a9.dtb missing or older than the DTS"
grep -aq 'hisense,hlte556n-pmi632-power' "$X/ok/a9.dtb" \
	&& pass "a9-pmi632.dtsi content is in the compiled DT" || fail "a9-pmi632.dtsi content missing from the DT"

echo "== stale a9.dtb + DTS that cannot compile (missing include)"
fresh "$X/bad"
cp "$X/ok/a9.dtb" "$X/bad/a9.dtb" 2>/dev/null || echo x > "$X/bad/a9.dtb"
{ cat "$DTS"; echo '#include "a9-does-not-exist.dtsi"'; } > "$X/broken.dts"
DT_ABL_CHECK_DTS="$X/broken.dts" "$CK" "$X/bad" >"$X/bad.log" 2>&1; rc=$?
[ $rc != 0 ] && pass "compile failure: exit $rc (nonzero)" || fail "compile failure passed (stale a9.dtb reused?)"
[ ! -e "$X/bad/a9.dtb" ] && pass "stale a9.dtb deleted, not reused" || fail "stale a9.dtb still present"
! grep -q 'RESULT: PASS' "$X/bad.log" && pass "no ABL PASS on a failed compile" || fail "ABL ran on a stale DT"

echo "== $PASS passed, $FAILN failed"
[ $FAILN = 0 ]
