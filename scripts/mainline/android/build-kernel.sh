#!/usr/bin/env bash
# Offline Android kernel wrapper. Wi-Fi defaults OFF, uses separate artifacts.
# Usage: build-kernel.sh [--wifi] [kernel|image|check|all]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WIFI=${A9_WIFI:-0}
STEP=kernel
while [ $# -gt 0 ]; do
	case "$1" in
		--wifi) WIFI=1 ;;
		kernel|image|check|all) STEP=$1 ;;
		*) echo "usage: $0 [--wifi] [kernel|image|check|all]" >&2; exit 2 ;;
	esac
	shift
done
case "$WIFI" in 0|1) ;; *) echo "A9_WIFI must be 0 or 1" >&2; exit 2 ;; esac
# No download or dependency installation here. The source must exist locally.
export A9_ANDROID=1 A9_WIFI=$WIFI A9_OFFLINE=1
exec "$HERE/../kernel/build.sh" "$STEP"
