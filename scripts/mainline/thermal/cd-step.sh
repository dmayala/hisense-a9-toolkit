#!/system/bin/sh
# cd-step.sh -- step (a) of runs/sensors.md 11.2 ON THE PHONE (root): one
# cooling device to state 1 for 1 s, record the cpufreq policy it limits,
# back to 0. Self-resetting; run detached (thermal/host-step.sh cd_step).
# Pushed and invoked BY PATH so that no shell quoting has to survive the
# adb -> loopback root shell transport (review 41 F2).
#   sh cd-step.sh <cooling device number> <cpufreq policy number> <run id>
# Output: /data/local/tmp/cd-<run id>.txt; kmsg lines "a9-cd <run id>: ...".
# A9_SYS / A9_TMP / A9_KMSG: offline test roots only.
set -u
C=${1:?cooling device number}; P=${2:?policy number}; RID=${3:?run id}
SYS=${A9_SYS:-/sys}; TMP=${A9_TMP:-/data/local/tmp}; KMSG=${A9_KMSG:-/dev/kmsg}
case "$C$P" in ''|*[!0-9]*) echo "bad number" >&2; exit 2;; esac
case "$RID" in ''|*[!A-Za-z0-9._-]*) echo "bad run id" >&2; exit 2;; esac
D=$SYS/class/thermal/cooling_device$C; F=$SYS/devices/system/cpu/cpufreq/policy$P
O=$TMP/cd-$RID.txt
say() { echo "a9-cd $RID: $*" >> "$KMSG" 2>/dev/null; echo "$*" >> "$O"; }
: > "$O"
trap 'echo 0 > "$D/cur_state" 2>/dev/null; say "END state $(cat "$D/cur_state" 2>/dev/null)"' EXIT
[ "$(cat "$D/cur_state" 2>/dev/null)" = 0 ] || { say "REFUSED: $D/cur_state is not 0"; exit 2; }
say "type $(cat "$D/type" 2>/dev/null) before max_freq $(cat "$F/scaling_max_freq" 2>/dev/null)"
echo 1 > "$D/cur_state" || { say "FAIL: write 1"; exit 1; }
sleep 1
say "state $(cat "$D/cur_state" 2>/dev/null) max_freq $(cat "$F/scaling_max_freq" 2>/dev/null) available $(cat "$F/scaling_available_frequencies" 2>/dev/null)"
echo 0 > "$D/cur_state" || { say "FAIL: write 0"; exit 1; }
sleep 1
say "after reset state $(cat "$D/cur_state" 2>/dev/null) max_freq $(cat "$F/scaling_max_freq" 2>/dev/null)"
