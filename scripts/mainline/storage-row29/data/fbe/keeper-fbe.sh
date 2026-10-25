#!/usr/bin/env bash
# Host owner only. Same 60s feed/7200s requested hold, vendor cap <=21600s.
# Use instead of the legacy data-path keeper on the FBE vendor; never run both.
set -eu
[[ $# = 2 ]] || { echo 'Usage: keeper-fbe.sh ADB SERIAL' >&2;exit 2; }
while :;do
    timeout 10 "$1" -s "$2" shell -T '
        [ "$(getprop ro.boot.slot_suffix)" = _b ] &&
        [ -d /dev/a9-row29-hold ] &&
        touch /dev/a9-row29-hold/stay &&
        setprop vendor.a9ml.maxstay $(( $(cut -d. -f1 /proc/uptime) + 7200 ))
    ' >/dev/null 2>&1
    sleep 60
done
