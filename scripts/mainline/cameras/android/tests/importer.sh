#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT
${CXX:-g++} -std=c++17 -fPIC -shared -DFAKE_IMPORTER "$HERE/importer.cpp" -o "$WORK/camera.device@1.0-impl.so"
${CXX:-g++} -std=c++17 -fPIC -shared -DA9_IMPORT_HOST_TEST "$HERE/../import-guard.cpp" -ldl -o "$WORK/guard.so"
${CXX:-g++} -std=c++17 "$HERE/importer.cpp" -L"$WORK" -Wl,-rpath,"$WORK" -l:camera.device@1.0-impl.so -o "$WORK/test"
LD_PRELOAD="$WORK/guard.so" "$WORK/test"
