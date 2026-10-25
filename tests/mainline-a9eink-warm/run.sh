#!/usr/bin/env bash
# Offline test: a warm Night Display matrix drops white below the g2 white threshold.
set -euo pipefail
T=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
A9=${A9_SOURCE_ROOT:-$(dirname "$(dirname "$T")")}
if ! command -v "${CC:-cc}" >/dev/null; then exec nix-shell -p gcc --run "bash '$T/run.sh'"; fi
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror -I"$A9/scripts/mainline/tcon" \
	"$T/warm-white.c" "$A9/scripts/mainline/tcon/a9tcon.c" -o "$W/warm-white"
"$W/warm-white"
# eink-trial3 default (user decision 2026-10-17): stock-like Smooth 518, app mode requests live
BV="$A9/scripts/mainline/android/build-vendor.sh"
[ "$(grep -c '^ro.vendor.a9ml.eink.default_mode=518$' "$BV")" = 1 ] || { echo "FAIL build-vendor.sh: default_mode must be 518 exactly once"; exit 1; }
! grep -q '^ro.vendor.a9ml.eink.lock_mode=' "$BV" || { echo "FAIL build-vendor.sh: an active lock_mode line"; exit 1; }
echo "ok   build.prop default_mode=518, no lock_mode line"
