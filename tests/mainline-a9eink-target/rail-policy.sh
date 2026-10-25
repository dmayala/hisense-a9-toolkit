#!/usr/bin/env bash
# Offline production control-flow regression; fake hardware only.
set -euo pipefail
T=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
A9=${A9_SOURCE_ROOT:-$(dirname "$(dirname "$T")")}
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
python3 "$T/rail-policy.py" \
    "$A9/scripts/mainline/eink/driver/a9_eink_drv.c" \
    "$A9/scripts/mainline/eink/driver/a9_eink_guard.c" "$W" >/dev/null
schema_flags=()
if [[ -n ${A9_FP_STATUS_HEADER:-} ]]; then
    schema_flags=(-DA9_TEST_FP_STATUS_EXTRACTED=1)
fi
"${CC:-cc}" -std=c11 -O2 -Wall -Wextra -Werror -Wno-unused-parameter \
    "${schema_flags[@]}" -I"$W" \
    -I"$A9/scripts/mainline/eink/driver" -I"$A9/scripts/mainline/tcon" \
    "$T/rail-policy.c" -o "$W/test-rail-policy"
"$W/test-rail-policy"
