#!/usr/bin/env bash
# One hour on booted B, Wi-Fi on, screen off; kernel + host restore charging.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
. "$here/common.sh"
control=/sys/class/power_supply/a9-usb/input_suspend
if [[ ${1:-} == --watchdog ]]; then
    parent=$2; report=$3; end=$((SECONDS + 3900))
    while kill -0 "$parent" 2>/dev/null && ((SECONDS < end)); do sleep 10; done
    # Cover a late completion of any bounded in-flight pause command too.
    for ((i=0; i<10; i++)); do
        root "printf '0\n' > $control; cat $control" >> "$report" 2>&1 || true
        sleep 10
    done
    exit
fi
image=${1:?usage: drain.sh IMAGE_DIR [OUTPUT_DIR]}
out=${2:-"$PWD/manual-drain-$(date -u +%Y%m%dT%H%M%SZ)"}
mkdir -p "$out"; out=$(cd "$out" && pwd)
[[ $(shell 'getprop ro.boot.slot_suffix' | tr -d '\r') == _b ]]
# Use the suspend-safe counter, not the old sampled charge_counter attribute.
[[ $(root 'cat /sys/class/power_supply/battery/drain_counter_supported') == 1 ]]
capacity=$(root 'cat /sys/class/power_supply/battery/drain_capacity')
((capacity > 35))
stayon=$(shell 'settings get global stay_on_while_plugged_in' | tr -d '\r')
printf -v stayon_q '%q' "$stayon"
remote=$(shell 'mktemp -d /data/local/tmp/manual-drain.XXXXXX' | tr -d '\r')
"$ADB" push "$image/acceptance-assets/drain-clock" "$remote/clock"
shell "chmod 755 $remote/clock"
watchdog=; pause_ack=0
cleanup() {
    local rc=$? restored=0
    root "printf '0\n' > $control; test \"\$(cat $control)\" = 0" > "$out/restore.log" 2>&1 && restored=1 || rc=1
    # Stop our detached watchdog only after charging is confirmed restored and
    # the original pause command was acknowledged; otherwise leave it alive.
    if ((restored && pause_ack)) && [[ -n $watchdog ]]; then kill -- "-$watchdog" 2>/dev/null || true; fi
    if [[ $stayon == null ]]; then setting='settings delete global stay_on_while_plugged_in'; else setting="settings put global stay_on_while_plugged_in $stayon_q"; fi
    shell "$setting; input keyevent KEYCODE_WAKEUP" >> "$out/restore.log" 2>&1 || rc=1
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
setsid "$here/drain.sh" --watchdog "$$" "$out/watchdog.log" </dev/null >/dev/null 2>&1 &
watchdog=$!
shell 'svc wifi enable; cmd wifi status' > "$out/wifi-before.txt"
shell 'svc power stayon false; input keyevent KEYCODE_SLEEP'
root "printf '1\n' > $control; test \"\$(cat $control)\" = 1"
pause_ack=1
snapshot() {
    root "$remote/clock
cat /sys/class/power_supply/battery/drain_snapshot
test \"\$(cat $control)\" = 1
test \"\$(cat /sys/class/power_supply/battery/status)\" != Charging
printf 'input_suspend '; cat $control"
}
wakeups() {
    root 'cat /sys/kernel/debug/wakeup_sources' > "$1" 2>/dev/null ||
        root 'for w in /sys/class/wakeup/wakeup*; do echo "$w"; cat "$w/name" "$w/event_count" "$w/total_time_ms" 2>/dev/null || true; done' > "$1" 2>/dev/null || true
}
snapshot > "$out/start.txt"
wakeups "$out/wakeup-before.txt"
shell 'dumpsys power' > "$out/power-before.txt"
end=$((SECONDS + 3600))
while ((SECONDS < end)); do
    delay=$((end - SECONDS)); ((delay <= 60)) || delay=60
    sleep "$delay"
    capacity=$(root 'cat /sys/class/power_supply/battery/drain_capacity')
    printf '%s %s\n' "$SECONDS" "$capacity" >> "$out/capacity.txt"
    ((capacity > 35)) || { echo 'Stopped at safety margin; charging will resume.' >&2; exit 1; }
done
snapshot > "$out/end.txt"
wakeups "$out/wakeup-after.txt"
shell 'cmd wifi status; dumpsys power' > "$out/framework-after.txt"
awk 'FNR==NR {if($1=="DRAIN_CLOCK"){b=$2;m=$3} if($1=="charge_counter")q=$2; next}
$1=="DRAIN_CLOCK" {seconds=($2-b)/1e9; suspend=(($2-b)-($3-m))/1e9}
$1=="charge_counter" {delta=$2-q}
END {printf "elapsed_s %.3f\nsuspend_s %.3f\ndelta_uAh %.0f\ndrain_uAh %.0f\naverage_mA %.3f\n",seconds,suspend,delta,-delta,-delta*3.6/seconds}' \
    "$out/start.txt" "$out/end.txt" > "$out/result.txt"
cat "$out/result.txt"
