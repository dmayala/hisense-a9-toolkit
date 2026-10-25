#!/usr/bin/env bash
# Host-only integrated R4 boot/vendor plus freshly built feature payloads.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
exec python3 -I "$HERE/build-integrated.py" "$@"
