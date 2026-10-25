#!/usr/bin/env bash
# b-try owns slot B, timeout, logging and the checked return to A.
set -euo pipefail
P=/home/user/A9-build/mainline
minutes=${1:-${A9_PLAIN_IDLE_MINUTES:-10}}
[[ $minutes =~ ^[0-9]{1,3}$ ]] || exit 2
minutes=$((10#$minutes)); ((minutes>0)) || exit 2
duration=$((minutes*60)); assets=${A9_IDLE2_ASSETS:-$P/idle2-20261022}
umask 077
if [[ ${A9_BTRY_IN_HOOK:-0} != 1 ]]; then
    export A9_PLAIN_IDLE_MINUTES=$minutes A9_BTRY_ONB="$(realpath "$0")"
    export A9_BTRY_HOLD=${A9_BTRY_HOLD:-$((duration+300))}
    exec "$P/b-try.sh" "${A9_PLAIN_BOOT:-$assets/boot.img}" "${A9_PLAIN_VENDOR:-$P/plain-integ-20261022/vendor.img}"
fi
: "${ADB:?}" "${A9_BTRY_LOGS:?}"
out="$A9_BTRY_LOGS/idle"; mkdir -m 700 -p "$out"
current_step=initialization step_file=
step() {
    current_step=$1; step_file=${2:-}
    echo "IDLE2 step=$current_step${step_file:+ output=$step_file}" >&2
}
failure() {
    local rc=$1 line=$2
    trap - ERR
    echo "IDLE2 FAILED step=$current_step rc=$rc line=$line" >&2
    [[ -z $step_file || ! -f $step_file ]] || tail -n 60 "$step_file" >&2 || true
    exit "$rc"
}
trap 'failure "$?" "$LINENO"' ERR
[[ -x $assets/clock-sample && -f $assets/idle2-snapshot.sh && -f $assets/idle2-summary.py ]] || { echo "Missing idle2 assets: $assets" >&2; exit 2; }
root() { A9_BTRY_ROOT_STEP="$current_step" python3 "${A9_HOME}/scripts/mainline/boot-deadline/onb-root.py"; }
framework() { timeout 30 "$ADB" shell '[ "$(getprop ro.boot.slot_suffix)" = _b ] || exit 97; '"$1"; }
remote="/data/local/tmp/a9-idle2-state-$$"
t="/sys/kernel/tracing/instances/idle2-$$"
prolog() { printf 'state=%q\nt=%q\n' "$remote" "$t"; cat "$assets/idle2-snapshot.sh"; }
step save-framework-settings
saved_stayon=$(framework 'settings get global stay_on_while_plugged_in' | tr -d '\r\n')
[[ $saved_stayon =~ ^[0-9]+$ || $saved_stayon == null ]] || exit 2
cleanup() {
    local rc=$?
    trap - EXIT INT TERM ERR
    set +e
    step cleanup "$out/cleanup.txt"
    { prolog; cat <<'PHONE'
if [ -d "$t" ]; then echo 0 > "$t/tracing_on"; echo 0 > "$t/events/enable"; rmdir "$t"; fi
if [ -f "$state/printk" ]; then cat "$state/printk" > /proc/sys/kernel/printk; fi
if [ -f "$state/uart-wakeup" ]; then cat "$state/uart-wakeup" > /sys/bus/platform/devices/4a8c000.serial/power/wakeup; fi
echo a9ml_idle2_awake > /sys/power/wake_unlock
rm -rf "$state"
rm -f /data/local/tmp/a9-idle2-clock
exit
PHONE
    } | root >"$out/cleanup.txt" 2>&1
    framework 'svc power stayon false' >>"$out/cleanup.txt" 2>&1
    echo "IDLE2 cleanup finished; original_rc=$rc output=$out/cleanup.txt" >&2
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
step push-clock-helper
timeout 30 "$ADB" push "$assets/clock-sample" /data/local/tmp/a9-idle2-clock >&2
step native-setup "$out/setup.txt"
{ prolog; cat <<'PHONE'
chmod 700 /data/local/tmp/a9-idle2-clock
echo IDLE2_CLOCK_PREFLIGHT
ls -l /data/local/tmp/a9-idle2-clock
/data/local/tmp/a9-idle2-clock
mkdir -m 700 "$state"
cat /proc/sys/kernel/printk > "$state/printk"
if [ -r /sys/bus/platform/devices/4a8c000.serial/power/wakeup ]; then cat /sys/bus/platform/devices/4a8c000.serial/power/wakeup > "$state/uart-wakeup"; fi
mount -t tracefs tracefs /sys/kernel/tracing 2>/dev/null || true
mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null || true
mkdir "$t"
echo 0 > "$t/tracing_on"
echo boot > "$t/trace_clock"
cat "$t/trace_clock"
echo 4096 > "$t/buffer_size_kb"
echo 1 > "$t/events/power/suspend_resume/enable"
echo 1 > "$t/events/power/wakeup_source_activate/enable"
echo 1 > "$t/events/power/wakeup_source_deactivate/enable"
echo 1 > "$t/events/alarmtimer/enable"
echo 1 > /proc/sys/kernel/printk
if [ -w /sys/bus/platform/devices/4a8c000.serial/power/wakeup ]; then echo disabled > /sys/bus/platform/devices/4a8c000.serial/power/wakeup; fi
echo a9ml_idle2_awake > /sys/power/wake_lock
exit
PHONE
} | root >"$out/setup.txt" 2>&1
step framework-awake "$out/framework-awake.txt"
framework 'svc power stayon true && input keyevent KEYCODE_WAKEUP' >"$out/framework-awake.txt" 2>&1
step awake-baseline "$out/awake-before.txt"
{ prolog; printf '\nsnapshot\nexit\n'; } | root >"$out/awake-before.txt" 2>&1
# Sampling is confined to the explicitly awake control. No timed sampler or
# periodic host traffic is run in the natural suspend window.
step awake-current-control "$out/awake-current.txt"
{ prolog; cat <<'PHONE'
i=0
while [ "$i" -lt 9 ]; do
    clock_snapshot
    battery_snapshot
    i=$((i+1))
    sleep 5
done
exit
PHONE
} | root >"$out/awake-current.txt" 2>&1
step awake-final "$out/awake-after.txt"
{ prolog; printf '\nsnapshot\nexit\n'; } | root >"$out/awake-after.txt" 2>&1
# Complete baseline/trace setup while Android still holds the screen awake.
# A root-console response after screen-off can be lost as USB suspends.
step idle-baseline-and-trace "$out/before.txt"
{ prolog; cat <<'PHONE'
snapshot
echo 1 > "$t/tracing_on"
echo PLAIN_IDLE_BEGIN > "$t/trace_marker"
echo a9ml_idle2_awake > /sys/power/wake_unlock
echo a9ml_test_awake > /sys/power/wake_unlock || true
exit
PHONE
} | root >"$out/before.txt" 2>&1
step framework-screen-off "$out/framework-sleep.txt"
if framework 'svc power stayon false && input keyevent KEYCODE_SLEEP' >"$out/framework-sleep.txt" 2>&1; then
    echo 'IDLE2 screen-off acknowledged' >&2
else
    sleep_rc=$?
    # Only the explicit screen-off boundary permits a lost transport. The
    # subsequent same-boot snapshots and trace establish what actually ran.
    if ((sleep_rc==255 || sleep_rc==124)); then
        echo "IDLE2 screen-off transport rc=$sleep_rc; retain quiet window and verify on reconnect" >&2
    else
        failure "$sleep_rc" "$LINENO"
    fi
fi
step quiet-idle-window
echo "Idle2: ${minutes} minutes without adb polling or a sampling wake timer; USB cable remains attached. Logs: $out" >&2
sleep "$duration"
step reconnect-after-idle
timeout 60 "$ADB" wait-for-device >/dev/null
step framework-wake "$out/framework-wake.txt"
framework 'input keyevent KEYCODE_WAKEUP && svc power stayon true' >"$out/framework-wake.txt" 2>&1
step idle-final-and-trace "$out/after.txt"
{ prolog; cat <<'PHONE'
echo PLAIN_IDLE_END > "$t/trace_marker"
echo 0 > "$t/tracing_on"
snapshot
echo POST_KMSG
dmesg | tail -n 300
echo PSTORE
for f in /sys/fs/pstore/*; do [ ! -f "$f" ] || { echo "PSTORE_FILE $f"; cat "$f"; }; done
echo TRACE_BEGIN
cat "$t/trace"
echo TRACE_STATS
for f in "$t"/per_cpu/cpu*/stats; do echo "CPU $f"; cat "$f"; done
exit
PHONE
} | root >"$out/after.txt" 2>&1
step summarize "$out/summary.txt"
python3 "$assets/idle2-summary.py" "$out" | tee "$out/summary.txt"
echo 'IDLE2 measurements complete' >&2
