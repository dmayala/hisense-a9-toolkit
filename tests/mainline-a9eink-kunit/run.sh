#!/usr/bin/env bash
# run.sh -- KUnit tests of the a9_eink KMS state (patch 0032) on arm64 QEMU:
# the driver's own a9_kms_init() and atomic checks (fixed-mode CRTC,
# ACTIVE-only commits, disable keeps the mode, framebuffer sizes). No phone,
# no power path. ~3 min (first build), seconds afterwards.
#   tests/mainline-a9eink-kunit/run.sh [KERNEL_TREE]   (default ~/A9-build/mainline/linux-7.2.10)
set -euo pipefail
K=${1:-${A9_KSRC:-$HOME/A9-build/mainline/linux-7.2.10}}
B=${A9_KUNIT_BUILD:-$HOME/A9-build/mainline/kunit-a9}
case "$B" in *a9-assets*) echo "refusing to build on the NFS share" >&2; exit 2;; esac
[ -f "$K/drivers/gpu/drm/a9_eink/.kunitconfig" ] || { echo "no a9_eink .kunitconfig in $K (patch 0032 missing?)" >&2; exit 2; }
NIX=$(mktemp); trap 'rm -f "$NIX"' EXIT
cat > "$NIX" <<'N'
let p = import <nixpkgs> {}; in
p.mkShell { nativeBuildInputs = [ p.pkgsCross.aarch64-multiplatform.buildPackages.gcc p.qemu p.python3 p.bc p.flex p.bison p.perl p.openssl p.elfutils p.gnumake ]; }
N
cd "$K"
nix-shell "$NIX" --run "python3 tools/testing/kunit/kunit.py run --arch=arm64 --cross_compile=aarch64-unknown-linux-gnu- --kunitconfig=drivers/gpu/drm/a9_eink/.kunitconfig --build_dir='$B' --jobs=$(nproc) --timeout=600"
# reference leaks / underflows only WARN in the kernel log: fail on them too
if grep -iE 'WARNING|refcount|leaked|BUG:|timed out|dotclock = 0' "$B/test.log"; then
	echo "KUNIT: kernel warnings in $B/test.log (above)" >&2; exit 1
fi
echo "KUNIT: PASS, no kernel warnings"
