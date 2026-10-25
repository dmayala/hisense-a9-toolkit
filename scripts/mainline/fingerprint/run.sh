#!/usr/bin/env bash
# USER/coordinator execution only; preparing/building this file is host-only.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
image=${A9_INTEG_IMAGE:-/home/user/A9-build/mainline/integ-20261024-fp10}
export A9_BTRY_HOLD=240 A9_BTRY_ONB="$here/onb.py"
exec "$here/../boot-deadline/b-try.sh" \
  "$image/boot.img" \
  "$image/vendor.img"
