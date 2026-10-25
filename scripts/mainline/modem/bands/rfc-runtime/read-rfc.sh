#!/bin/sh
# Host launcher. Requires python3 and adb on the host; root su is set up by user.
# No device access occurs for --self-test, --help or --print-device-script.
exec python3 "$(dirname "$0")/read_rfc.py" "$@"
