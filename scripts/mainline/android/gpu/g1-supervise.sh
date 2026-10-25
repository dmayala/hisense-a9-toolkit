#!/usr/bin/env bash
# g1-supervise.sh -- kept for the G1 command line: `gpu-supervise.sh --stage g1`.
exec "$(cd "$(dirname "$0")" && pwd)/gpu-supervise.sh" --stage g1 "$@"
