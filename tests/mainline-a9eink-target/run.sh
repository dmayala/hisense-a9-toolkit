#!/usr/bin/env bash
# Offline actual-engine regression; synthetic tables contain no vendor data.
set -euo pipefail
T=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
A9=$(dirname "$(dirname "$T")")
if ! command -v "${CC:-cc}" >/dev/null || ! command -v python3 >/dev/null; then
	exec nix-shell -p gcc python3 --run "bash '$T/run.sh'"
fi
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror \
	-I"$A9/scripts/mainline/eink/driver" -I"$A9/scripts/mainline/tcon" \
	"$T/test.c" "$A9/scripts/mainline/eink/driver/a9_eink_engine.c" \
	"$A9/scripts/mainline/eink/driver/a9_eink_damage.c" \
	"$A9/scripts/mainline/tcon/a9tcon.c" -o "$W/test-target"
"$W/test-target"
if [[ -n ${A9_WF_BLOB:-} ]]; then
	"$W/test-target" "$A9_WF_BLOB"
fi
python3 "$T/policy-test.py" "$A9/scripts/mainline/eink/driver/a9_eink_drv.c" "$W/policy-update.inc"
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror \
	-I"$W" -I"$A9/scripts/mainline/eink/driver" -I"$A9/scripts/mainline/tcon" \
	"$T/policy-test.c" -o "$W/test-policy"
"$W/test-policy"

# Review 22: combined live-rail containment and last-physical-output hold.
bash "$T/rail-policy.sh"
python3 "$T/idle-policy.py" "$A9/scripts/mainline/eink/driver/a9_eink_drv.c" "$W/idle-policy.inc"
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror -I"$W" "$T/idle-policy.c" -o "$W/test-idle"
"$W/test-idle"
