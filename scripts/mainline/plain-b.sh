#!/usr/bin/env bash
# Plain B boot; return to A is a separate plain-a.sh invocation.
set -euo pipefail
. "${A9_HOME:-/home/user/A9}/scripts/a9-env.sh"
BOOT=$1
VENDOR=$2
LPWRITE="$A9_HOME/scripts/mainline/android/lp-write.sh"
plan=$("$LPWRITE" "$VENDOR")
printf '%s\n' "$plan"
code=$(sed -n 's/^CHECK: PASS\. code: \([^ ]*\).*/\1/p' <<< "$plan")
"$LPWRITE" "$VENDOR" --write --code "$code"
"$ADB" reboot bootloader
until "$FB" devices 2>/dev/null | grep -q fastboot; do sleep 1; done
"$FB" flash boot_b "$BOOT"
"$FB" set_active b
"$FB" reboot
end=$((SECONDS+480))
while [ "$SECONDS" -lt "$end" ]; do
    state=$(timeout 5 "$ADB" shell 'getprop ro.boot.slot_suffix; getprop sys.boot_completed' 2>/dev/null | tr -d '\r') || state=
    if [ "$state" = $'_b\n1' ]; then
        "$ADB" shell 'echo slot=$(getprop ro.boot.slot_suffix); echo boot_completed=$(getprop sys.boot_completed); getenforce; grep " /data " /proc/mounts'
        exit 0
    fi
    sleep 2
done
echo 'B did not reach boot_completed within 8 minutes; use plain-a.sh to return.' >&2
timeout 5 "$ADB" shell 'echo slot=$(getprop ro.boot.slot_suffix); echo boot_completed=$(getprop sys.boot_completed); getenforce; grep " /data " /proc/mounts' || true
exit 1
