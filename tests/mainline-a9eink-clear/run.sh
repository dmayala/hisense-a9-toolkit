#!/usr/bin/env bash
# Offline tests of actual engine and extracted production clear selectors.
set -euo pipefail
T=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
A9=${A9_SOURCE_ROOT:-$(dirname "$(dirname "$T")")}
if ! command -v "${CC:-cc}" >/dev/null || ! command -v python3 >/dev/null; then
	exec nix-shell -p gcc python3 --run "bash '$T/run.sh'"
fi
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror \
	-I"$A9/scripts/mainline/eink/driver" -I"$A9/scripts/mainline/tcon" \
	"$T/engine-test.c" "$A9/scripts/mainline/eink/driver/a9_eink_engine.c" \
	"$A9/scripts/mainline/eink/driver/a9_eink_damage.c" "$A9/scripts/mainline/tcon/a9tcon.c" -o "$W/test-engine"
"$W/test-engine"
if [[ -n ${A9_WF_BLOB:-} ]]; then
	"$W/test-engine" "$A9_WF_BLOB"
fi
python3 "$T/extract.py" "$A9/scripts/mainline/eink/driver/a9_eink_drv.c" "$W/production.inc"
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror -Wno-unused-parameter \
	-I"$W" -I"$A9/scripts/mainline/eink/driver" -I"$A9/scripts/mainline/tcon" \
	"$T/select-test.c" "$A9/scripts/mainline/eink/driver/a9_eink_engine.c" \
	"$A9/scripts/mainline/eink/driver/a9_eink_damage.c" "$A9/scripts/mainline/tcon/a9tcon.c" -o "$W/test-select"
"$W/test-select"
