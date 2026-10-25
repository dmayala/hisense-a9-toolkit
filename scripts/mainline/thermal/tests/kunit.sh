#!/usr/bin/env bash
# kunit.sh -- KUnit (arm64 QEMU) of kernel/patches/0043 (the confirmation
# policy driven through thermal_zone_device_update(), the entry point of the
# polling work, sensor interrupt threads and sysfs) and 0045/0046 (trip
# windows while a temperature is emulated). No phone.
#   scripts/mainline/thermal/tests/kunit.sh <kernel tree WITH patches 0043-0046>
# PASS needs kunit.py's exit 0 AND "Ran N tests: passed: N" (no failed, no
# skipped) where N = the KUNIT_CASE count in thermal_confirm_kunit.c and
# N >= A9_KUNIT_MIN (default 8), AND both suites reported PASSED.
# Any tree build.sh has patched qualifies (the shared one once a build since
# 0043/0044 ran there); run-tests.sh uses a private copy.
set -euo pipefail
K=${1:?kernel tree}
B=${A9_KUNIT_BUILD:-${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}/kunit-thermal}
case "$B" in *a9-assets*) exit 2;; esac
[ -f "$K/drivers/thermal/thermal_confirm_kunit.c" ] || { echo "$K lacks patch 0043 (thermal_confirm_kunit.c)" >&2; exit 2; }
NIX=$(mktemp); trap 'rm -f "$NIX"' EXIT
cat > "$NIX" <<'N'
let p = import <nixpkgs> {}; in
p.mkShell { nativeBuildInputs = [ p.pkgsCross.aarch64-multiplatform.buildPackages.gcc p.qemu p.python3 p.bc p.flex p.bison p.perl p.openssl p.elfutils p.gnumake ]; }
N
cd "$K"
N=$(grep -cE '^\s*KUNIT_CASE(_SLOW)?\(' drivers/thermal/thermal_confirm_kunit.c)
MIN=${A9_KUNIT_MIN:-8}
[ "$N" -ge "$MIN" ] || { echo "KUNIT: only $N cases in the source, need >= $MIN" >&2; exit 1; }
mkdir -p "$B"
nix-shell "$NIX" --run "python3 tools/testing/kunit/kunit.py run --arch=arm64 --cross_compile=aarch64-unknown-linux-gnu- --kunitconfig=drivers/thermal/.kunitconfig --build_dir='$B' --jobs=$(nproc) --timeout=600" | tee "$B/kunit.out"
if grep -iE 'WARNING:|BUG:|refcount|timed out' "$B/test.log"; then echo "KUNIT: kernel warnings (above)" >&2; exit 1; fi
grep -qE "Testing complete\. Ran $N tests: passed: $N\s*$" "$B/kunit.out" ||
  { echo "KUNIT: expected 'Ran $N tests: passed: $N' (no failed/skipped):" >&2; grep 'Testing complete' "$B/kunit.out" >&2; exit 1; }
for s in thermal-critical-confirm thermal-emulation-trip-window; do
  grep -qE "\[PASSED\] $s\b" "$B/kunit.out" || { echo "KUNIT: suite $s not reported PASSED" >&2; exit 1; }
done
echo "KUNIT: PASS ($N/$N)"
