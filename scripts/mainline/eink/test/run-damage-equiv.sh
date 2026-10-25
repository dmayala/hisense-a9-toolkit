#!/usr/bin/env bash
# Bit-identity of the damage-limited prepare vs the reference full pass (host, offline).
#   run-damage-equiv.sh PNG_DIR [bench]
# PNG_DIR: 824x1648 screencaps (any *.png, up to 8 used). WAVEFORM / TEMPLATE default to
# ~/A9-build/mainline/firmware/hisense/{a9-waveform.bin,a9-ctrl-template.bin}.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
P=${1:?dir with 824x1648 PNG screencaps}
FW=${A9_FW_HISENSE:-$HOME/A9-build/mainline/firmware/hisense}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
i=0
for f in "$P"/*.png; do
  [ $i -ge 8 ] && break
  python3 -I "$HERE/tools/png2rgba.py" "$f" "$T/real$i.rgba" 2>/dev/null && i=$((i + 1))
done
nix-shell -p gcc gnumake --run "make -s -C '$HERE' O='$T/b' '$T/b/damage-equiv'"
"$T/b/damage-equiv" "${WAVEFORM:-$FW/a9-waveform.bin}" "${TEMPLATE:-$FW/a9-ctrl-template.bin}" "$T" "${2:-}"
