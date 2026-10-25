#!/usr/bin/env bash
# Coordinator-only, within the existing exclusive slot-B phone session.
# The worker must never execute this script. Does not flash or restore images.
set -euo pipefail
mode=${1:-rtc}
if [[ $mode == pmtest ]]; then
    shift
    exec python3 -I "$(dirname "$0")/pmtest-host.py" "$@"
fi
case "$mode" in rtc|key|rtc-awake|watchdog-awake|watchdog-s2idle) ;; *) exit 2;; esac
ADB=${ADB:-adb}
root() { timeout 5 "$ADB" shell 'timeout 4 nc 127.0.0.1 2324'; }
if [[ $mode == key ]]; then
    echo 'Launching now: press the physical power key in about 3 s.'
fi
# a9ml_test_awake remains held: direct freeze exercises one suspend without
# enabling Android autosuspend or depending on an adb connection surviving it.
root <<R
rm -f /data/local/tmp/a9-pm.log /data/local/tmp/a9-pm.rc
input keyevent KEYCODE_SLEEP
nohup /system/bin/sh -c '/vendor/bin/a9ml-suspend $mode > /data/local/tmp/a9-pm.log 2>&1; echo \$? > /data/local/tmp/a9-pm.rc' < /dev/null > /dev/null 2>&1 &
R
if [[ $mode == watchdog-* ]]; then
    if [[ $mode == watchdog-s2idle ]]; then
        echo 'Deliberate watchdog expiry at +15 s; RTC backup +20 s.'
        echo 'A reset only after RTC wake does not prove asleep watchdog counting.'
    else
        echo 'Deliberate watchdog expiry: expect bootloader within 30-40 s.'
    fi
    echo 'Use the existing owner restore path after bootloader appears. No automatic retry.'
    exit 0
fi
# Keep USB quiet through the +10 RTC deadline. Polling every second while
# asleep would itself exercise USB wake and invalidate the RTC experiment.
sleep 12
# Poll over the existing adb route. A returned helper still has the WDT armed;
# only this successful host round trip grants the ACK that permits disarming.
deadline=$((SECONDS + 25))
while (( SECONDS < deadline )); do
    result=$(root <<'R' 2>/dev/null || true
if grep -q '^PM waiting=host-adb-ack' /data/local/tmp/a9-pm.log 2>/dev/null; then
    touch /data/local/tmp/a9-pm-ack
fi
cat /data/local/tmp/a9-pm.log 2>/dev/null
if [ -f /data/local/tmp/a9-pm.rc ]; then
    echo PM_RC=$(cat /data/local/tmp/a9-pm.rc)
fi
R
)
    result=${result//$'\r'/}
    if [[ $result == *PM_RC=* ]]; then
        printf '%s\n' "$result"
        [[ $result == *PM_RC=0* ]] || exit 1
        exit 0
    fi
    sleep 1
done
echo 'No completed result. Check bootloader, then use the existing owner restore path.' >&2
exit 1
