#!/usr/bin/env bash
# Host test of 0048 v2 (POWER-PATH; in the series since eink-baseline, opt-in,
# hv_hold_interactive_ms default 0): extracts the verbatim worker policy from
# the production driver (which carries 0048) and runs it against mocks.
# No device, no network.
set -euo pipefail
T=$(cd "$(dirname "$0")" && pwd); A9=$(cd "$T/../.." && pwd)
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
mkdir -p "$W/tree/drivers/gpu/drm/a9_eink"
cp "$A9/scripts/mainline/eink/driver/a9_eink_drv.c" "$W/tree/drivers/gpu/drm/a9_eink/"
python3 "$T/idle-policy.py" "$W/tree/drivers/gpu/drm/a9_eink/a9_eink_drv.c" "$W/idle-policy.inc"
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror -I"$W" "$T/idle-boost.c" -o "$W/test-boost"
"$W/test-boost"
