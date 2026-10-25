#!/usr/bin/env bash
# Future owning coordinator/user execution only. Host preparation never runs this.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
image=${1:-${A9_SWEEP_IMAGE:-${A9_INTEG_IMAGE:-}}}
[[ -n "$image" ]] || { echo "usage: run.sh INTEG_IMAGE_DIR" >&2; exit 2; }
export A9_SWEEP_HVENC_QUALIFY=${A9_SWEEP_HVENC_QUALIFY:-0}
case "$A9_SWEEP_HVENC_QUALIFY" in 0|1) ;; *) echo "A9_SWEEP_HVENC_QUALIFY must be 0 or 1" >&2; exit 2;; esac
# Bind SYSTEM delivery to this package, rather than an inherited test image.
if python3 - "$image/integrated-build.json" <<'PY_SYSTEM'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists():
    sys.exit(1)
try:
    hashes = json.loads(p.read_text())['sha256']
    if not isinstance(hashes, dict):
        raise ValueError('sha256 must be an object')
except (OSError, ValueError, KeyError, TypeError) as e:
    print('Invalid integrated-build.json: ' + str(e), file=sys.stderr)
    sys.exit(2)
sys.exit(0 if 'system.img' in hashes else 1)
PY_SYSTEM
then
  export A9_BTRY_SYSTEM="$image/system.img"
else
  status=$?
  [[ $status == 1 ]] || exit "$status"
  unset A9_BTRY_SYSTEM
fi
export A9_SWEEP_IMAGE="$image"
export A9_SWEEP_ASSETS=${A9_SWEEP_ASSETS:-$image/acceptance-assets}
export A9_BTRY_SOUND=0 A9_BTRY_ONB="$here/onb.py"
export A9_BTRY_HOLD=${A9_BTRY_HOLD:-3600}
exec "$here/../boot-deadline/b-try.sh" "$image/boot.img" "$image/vendor.img"
