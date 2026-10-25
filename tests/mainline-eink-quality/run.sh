#!/usr/bin/env bash
# Host-only synthetic parity and benchmark; optional stock Unicorn replay.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
CC=${CC:-cc}
CORE=("$ROOT/scripts/mainline/eink/driver/a9_eink_engine.c" "$ROOT/scripts/mainline/eink/driver/a9_eink_damage.c" "$ROOT/scripts/mainline/tcon/a9tcon.c")
FLAGS=(-std=c11 -O2 -Wall -Wextra -Werror -I"$ROOT/scripts/mainline/eink/driver" -I"$ROOT/scripts/mainline/tcon")
"$CC" "${FLAGS[@]}" "$HERE/conversion.c" "${CORE[@]}" -o "$W/conversion"
"$CC" "${FLAGS[@]}" "$HERE/shadow.c" "${CORE[@]}" -o "$W/shadow"
"$W/shadow"
"$W/conversion"
if [[ $# -gt 0 ]]; then
    "$CC" "${FLAGS[@]}" -fPIC -shared "$HERE/fixture.c" "${CORE[@]}" \
        "$ROOT/scripts/mainline/tcon/a9tcon-load.c" -o "$W/fixture.so"
    "${PYTHON:-python3}" "$HERE/quality.py" "$1" "$W/fixture.so"
fi
