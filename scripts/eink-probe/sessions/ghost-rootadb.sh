#!/usr/bin/env bash
# Frozen root-shell transport. No embedded device identifier or settings writes.
set -eu
S=${A9_SERIAL:?A9_SERIAL is required}
if [ "${1:-}" = -s ]; then S=$2; shift 2; fi
if [ "${1:-}" = shell ]; then
  shift
  if [ $# = 0 ]; then exec adb -s "$S" shell nc 127.0.0.1 2324; fi
  printf '%s\n' "$*" | adb -s "$S" shell nc 127.0.0.1 2324
else
  exec adb -s "$S" "$@"
fi
