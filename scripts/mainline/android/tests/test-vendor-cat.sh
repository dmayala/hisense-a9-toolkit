#!/usr/bin/env bash
# test-vendor-cat.sh -- android-boot.sh's vendor_cat (extracted verbatim): a failed debugfs read is retried
# and then reported, never taken as "the line is absent" (S2c-2: GPU pairing read as 0 once). Offline.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
python3 -I - "$HERE/android-boot.sh" > "$T/fn.sh" <<'PY'
import sys
s=open(sys.argv[1]).read(); a=s.index('vendor_cat() {'); b=s.index('\n}\n', a)+3; print(s[a:b])
PY
source "$T/fn.sh"
fails=0; ok() { echo "  ok   $1"; }; bad() { echo "  FAIL $1"; fails=$((fails+1)); }
mkdir -p "$T/bin"; VENDOR=$T/vendor.img; : > "$VENDOR"
fake() {   # $1 = behaviour: ok | flaky | rcfail | stderr
  cat > "$T/bin/debugfs" <<S
#!/bin/sh
echo "debugfs 1.47.3 (8-Jul-2025)" >&2
n=\$(cat "$T/count" 2>/dev/null || echo 0); n=\$((n+1)); echo \$n > "$T/count"
case "$1" in
  ok) printf 'a=1\nro.vendor.a9ml.gpu=1\n' ;;
  flaky) [ \$n -ge 2 ] && printf 'ro.vendor.a9ml.gpu=1\n' || { echo "/build.prop: Short read" >&2; exit 0; } ;;
  rcfail) exit 1 ;;
  stderr) echo "/build.prop: File not found by ext2_lookup" >&2 ;;
esac
S
  chmod +x "$T/bin/debugfs"; rm -f "$T/count"
}
for c in ok flaky rcfail stderr; do
  fake $c
  out=$(PATH="$T/bin:$PATH" vendor_cat /build.prop 2>"$T/err"); rc=$?
  n=$(cat "$T/count")
  case "$c" in
    ok) [ $rc = 0 ] && grep -qx 'ro.vendor.a9ml.gpu=1' <<<"$out" && [ "$n" = 1 ] && ok "read ok (1 try)" || bad "ok rc=$rc n=$n" ;;
    flaky) [ $rc = 0 ] && grep -qx 'ro.vendor.a9ml.gpu=1' <<<"$out" && [ "$n" = 2 ] && grep -q 'Short read' "$T/err" && ok "a failed first read is retried (2 tries) and its error printed" || bad "flaky rc=$rc n=$n" ;;
    rcfail) [ $rc = 1 ] && [ "$n" = 3 ] && ok "debugfs rc != 0 three times -> rc 1 (fail loudly, not 'absent')" || bad "rcfail rc=$rc n=$n" ;;
    stderr) [ $rc = 1 ] && [ "$n" = 3 ] && grep -q 'File not found' "$T/err" && ok "rc 0 but an error on stderr -> rc 1 with the message" || bad "stderr rc=$rc n=$n" ;;
  esac
done
# the GPU check reads build.prop and modules.options only through vendor_cat
grep -q 'bp=$(vendor_cat /build.prop)' "$HERE/android-boot.sh" && grep -q 'mo=$(vendor_cat /lib/modules/modules.options)' "$HERE/android-boot.sh" \
  && ! sed -n '/^gpu_offline_checks() {/,/^}/p' "$HERE/android-boot.sh" | grep -q 'debugfs -R "cat ' \
  && ok "gpu_offline_checks has no direct 'debugfs -R cat' pipe left" || bad "gpu_offline_checks still pipes debugfs cat"
echo "== result: $fails failure(s)"; [ "$fails" = 0 ] && echo "VENDOR-CAT TEST PASS"; exit $((fails > 0))
