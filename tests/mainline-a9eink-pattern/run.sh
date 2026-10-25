#!/usr/bin/env bash
# Offline host tests of the exact diagnostic-pattern helper used by a9_eink.
set -euo pipefail
T=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
A9=$(dirname "$(dirname "$T")")
if ! command -v "${CC:-cc}" >/dev/null; then
	exec nix-shell -p gcc --run "bash '$T/run.sh'"
fi
if ! command -v python3 >/dev/null; then
	exec nix-shell -p gcc python3 --run "bash '$T/run.sh'"
fi
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror \
	-I"$A9/scripts/mainline/eink/driver" -I"$A9/scripts/mainline/tcon" \
	"$T/test.c" -o "$W/test-pattern"
"$W/test-pattern"
python3 "$T/extract.py" "$A9/scripts/mainline/eink/driver/a9_eink_drv.c" \
	"$W/production.inc"
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror -Wno-unused-parameter \
	-I"$W" -I"$A9/scripts/mainline/eink/driver" -I"$A9/scripts/mainline/tcon" \
	"$T/control-test.c" "$A9/scripts/mainline/eink/driver/a9_eink_engine.c" \
	"$A9/scripts/mainline/eink/driver/a9_eink_damage.c" \
	"$A9/scripts/mainline/tcon/a9tcon.c" -o "$W/test-controls"
"$W/test-controls"
