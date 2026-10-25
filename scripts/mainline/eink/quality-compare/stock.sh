#!/usr/bin/env bash
# User-executed slot-A capture, no flashing/reboot. Root is su -c.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
export A9_HOME=${A9_HOME:-/home/user/A9}
. "$HERE/../../../a9-env.sh"
a9_usb_lock
exec "${A9_EINK_PYTHON:-/nix/store/ykripfs7ljz7i7ibwsfzy6d4hpjmcddk-python3-3.13.7-env/bin/python3}" \
  "$HERE/capture.py" --backend stock --out "${1:?usage: stock.sh NEW_OUTDIR}" \
  --build "${A9_EINK_BUILD:-/home/user/A9-build/mainline/eink-quality-20261023}"
