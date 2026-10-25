#!/usr/bin/env bash
#
# abl-check.sh — check a boot or recovery image with the phone's own ABL code
# (LinuxLoader from the golden abl_a dump) in Unicorn, before flashing it.
# Offline: no adb/fastboot/edl. Thin wrapper: provides python unicorn through
# nix-shell and runs scripts/abl-check.py. See docs/abl-check.md.
#
# Usage: scripts/abl-check.sh <image> recovery|boot [--dtbo <dtbo.img>] [--json] [--quiet] [--save-dtb <f>]
#        (--target recovery|boot is accepted too)
# Exit:  0 = all three stages PASS, 1 = a stage FAILED, 2 = usage/harness error
#
set -euo pipefail
S="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if python3 -c 'import unicorn' 2>/dev/null; then
  exec python3 -P "$S/abl-check.py" "$@"
fi
args=$(printf '%q ' "$@")
exec nix-shell -p python3Packages.unicorn --run "python3 -P $(printf '%q' "$S/abl-check.py") $args"
