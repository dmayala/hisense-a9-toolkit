#!/usr/bin/env bash
# Deployed with execution.json + deployment.sha256; never resolves a device.
set -euo pipefail
D=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
(cd "$D" && sha256sum --quiet -c deployment.sha256) || { echo 'deployment seal mismatch' >&2; exit 2; }
exec "$D/python3" -B "$D/ghost_stock_runner.py" "$@"
