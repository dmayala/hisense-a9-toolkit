#!/usr/bin/env bash
# Explicit, separate return to daily slot A.
set -euo pipefail
. "${A9_HOME:-/home/user/A9}/scripts/a9-env.sh"
timeout 10 "$ADB" reboot bootloader >/dev/null 2>&1 || true
until "$FB" devices 2>/dev/null | grep -q fastboot; do sleep 1; done
"$FB" set_active a
"$FB" reboot
"$ADB" wait-for-device
until [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; do sleep 2; done
"$ADB" shell input keyevent KEYCODE_WAKEUP
sleep 4
"$ADB" shell input keyevent KEYCODE_SLEEP
