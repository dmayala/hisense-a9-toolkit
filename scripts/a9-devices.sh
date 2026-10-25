#!/usr/bin/env bash
# a9-devices.sh — which Hisense A9 the scripts talk to. Thin front end for
# a9_devices.py (registry in local/device.json, gitignored).
#   a9-devices.sh [list]              known A9s (* default, + attached)
#   a9-devices.sh scan                record every attached A9
#   a9-devices.sh use <name|serial>   pick the default
#   a9-devices.sh name <serial|name> <new>
#   a9-devices.sh forget <name|serial>
# Per command instead: A9_DEVICE=<name|serial> scripts/... or A9_SERIAL=<serial>.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export A9_HOME="${A9_HOME:-$(dirname "$HERE")}"
exec python3 -I "$HERE/a9_devices.py" "${@:-list}"
