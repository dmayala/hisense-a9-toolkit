#!/usr/bin/env bash
# build-frontlight-module.sh -- build the frontlight LED driver
# (scripts/mainline/frontlight/driver, owned by the frontlight work) as an
# EXTERNAL module against the Android kernel build, so it carries that build's
# vermagic and symbol CRCs; Android only (the diskless E3/E5 image and
# scripts/mainline/kernel/ are untouched). Output:
# $AW/work/frontlight-mod/a9-frontlight.ko, picked up by snapshot-kernel.sh.
# Offline. Usage: build-frontlight-module.sh [build_dir]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../../.." && pwd)
B=${1:-$HOME/A9-build/mainline/build-7.2.10-android}
SRC=${A9_KSRC:-$HOME/A9-build/mainline/linux-7.2.10}
M=${A9_ANDROID:-$HOME/A9-build/mainline/android}/work/frontlight-mod
case "$M" in *a9-assets*) echo "refusing" >&2; exit 2 ;; esac
for k in NEW_LEDS LEDS_CLASS GPIOLIB I2C OF; do grep -q "^CONFIG_$k=y" "$B/.config" || { echo "CONFIG_$k not =y in $B" >&2; exit 1; }; done
rm -rf "$M"; mkdir -p "$M"; cp "$REPO"/scripts/mainline/frontlight/driver/*.[ch] "$REPO/scripts/mainline/frontlight/driver/Makefile" "$M/"
nix-shell "$REPO/scripts/mainline/kernel/shell.nix" --run \
  "make -C '$SRC' O='$B' ARCH=arm64 CROSS_COMPILE=aarch64-unknown-linux-gnu- M='$M' CONFIG_LEDS_A9_FRONTLIGHT=m -j4 modules" >"$M/build.log" 2>&1 \
  || { tail -20 "$M/build.log"; exit 1; }
echo "frontlight module: $M/a9-frontlight.ko ($(stat -c %s "$M/a9-frontlight.ko") B, src git $(git -C "$REPO" log -1 --format=%h -- scripts/mainline/frontlight/driver))"
