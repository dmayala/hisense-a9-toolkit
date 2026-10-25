#!/usr/bin/env bash
# USER/AGENT execution only. Preparing this launcher does not access a device.
set -euo pipefail
case "${1:-}" in
  rehearsal) export A9_GNSS_REHEARSAL=1 ;;
  outing) export A9_GNSS_REHEARSAL=0 ;;
  *) echo 'usage: run.sh rehearsal|outing' >&2; exit 2 ;;
esac
task_dir=$(cd -- "$(dirname -- "$0")" && pwd)
export A9_BTRY_HOLD=4200 A9_GNSS_STANDALONE=1
export A9_BTRY_ONB="$task_dir/onb.py"
image=${A9_INTEG_IMAGE:-/home/user/A9-build/mainline/integ-20261024-9}
exec "$task_dir/b-try-outdoor.sh" "$image/boot.img" "$image/vendor.img"
