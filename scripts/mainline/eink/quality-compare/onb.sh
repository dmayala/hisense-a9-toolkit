#!/usr/bin/env bash
# User-executed b-try hook. The worker only builds this file, never runs it.
set -euo pipefail
[[ ${A9_BTRY_IN_HOOK:-0} == 1 ]] || { echo 'Run through b-try using A9_BTRY_ONB' >&2; exit 2; }
: "${A9_BTRY_LOGS:?}" "${ADB:?}"
HERE=$(cd "$(dirname "$0")" && pwd)
exec "${A9_EINK_PYTHON:-/nix/store/ykripfs7ljz7i7ibwsfzy6d4hpjmcddk-python3-3.13.7-env/bin/python3}" \
  "$HERE/capture.py" --backend mainline --out "$A9_BTRY_LOGS/quality" \
  --build "${A9_EINK_BUILD:-/home/user/A9-build/mainline/eink-quality-20261023}"
