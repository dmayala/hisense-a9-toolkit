#!/system/bin/sh
# User-run on current N-e. Own trace instance/probes only; no panel/audio controls.
set -eu
umask 077
out=${1:?usage: sh phase5-capture.sh NEW_PRIVATE_DIR [SECONDS=600] [THRESHOLD_US=30000]}
seconds=${2:-600}
threshold=${3:-30000}
case "$seconds:$threshold" in *[!0-9:]*|:*|*:) echo 'integer duration/threshold required' >&2; exit 2 ;; esac
[ "$seconds" -ge 1 ] && [ "$seconds" -le 900 ] && [ "$threshold" -ge 1 ] && [ "$threshold" -le 1000000 ] || exit 2
[ ! -e "$out" ] || { echo 'output must be new' >&2; exit 2; }
base=${0%/*}
[ "$base" != "$0" ] || base=.
[ -x "$base/phase5-watch" ] || { echo 'phase5-watch binary must be beside this script' >&2; exit 1; }
tr=/sys/kernel/tracing
[ -d "$tr/instances" ] || tr=/sys/kernel/debug/tracing
[ -d "$tr/instances" ] || { echo 'tracefs instances unavailable' >&2; exit 1; }
consumer=
for p in /proc/[0-9]*; do
    [ -r "$p/comm" ] || continue
    name=$(cat "$p/comm" 2>/dev/null) || continue
    [ "$name" = a9_eink ] || continue
    [ -z "$consumer" ] || { echo 'multiple a9_eink waiters; refuse ambiguous capture' >&2; exit 1; }
    consumer=${p##*/}
done
[ -n "$consumer" ] || { echo 'a9_eink waiter absent' >&2; exit 1; }
mkdir "$out"
group=a9p5_$$
inst=$tr/instances/$group
[ ! -e "$tr/events/$group" ] || { echo "probe group already exists: $group" >&2; exit 1; }
created=
watchpid=
mkdir "$inst"
cleanup() {
    if [ -n "$watchpid" ]; then
        kill -TERM "$watchpid" 2>/dev/null || :
        wait "$watchpid" 2>/dev/null || :
    fi
    echo 0 > "$inst/tracing_on" 2>/dev/null || :
    echo 0 > "$inst/events/enable" 2>/dev/null || :
    rmdir "$inst" 2>/dev/null || echo "cleanup: own instance retained: $inst" >&2
    for name in $created; do
        echo "-:$group/$name" >> "$tr/kprobe_events" 2>/dev/null || echo "cleanup: own probe retained: $group/$name" >&2
    done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Never clear/control another instance or the global tracing state.
echo 0 > "$inst/tracing_on"
echo nop > "$inst/current_tracer"
echo 512 > "$inst/buffer_size_kb"
grep -qw mono "$inst/trace_clock" || { echo 'mono trace clock unavailable' >&2; exit 1; }
echo mono > "$inst/trace_clock"
echo 1 > "$inst/options/overwrite"
: > "$inst/trace"
need_event() {
    path=$inst/events/$1/$2
    [ -e "$path/enable" ] || { echo "required event unavailable: $1:$2" >&2; exit 1; }
    if [ -n "${3:-}" ]; then echo "$3" > "$path/filter"; fi
    echo 1 > "$path/enable"
    cat "$path/format" >> "$out/event-formats.txt"
}
for name in msm_atomic_commit_tail_start msm_atomic_commit_tail_finish msm_atomic_flush_commit msm_atomic_wait_flush_start msm_atomic_wait_flush_finish; do
    need_event msm "$name" "common_pid == $consumer"
done
need_event sched sched_switch "prev_pid == $consumer || next_pid == $consumer"
for name in sched_wakeup sched_waking sched_migrate_task; do
    need_event sched "$name" "pid == $consumer"
done
need_event power cpu_idle
for name in irq_handler_entry irq_handler_exit softirq_entry softirq_exit; do
    need_event irq "$name"
done
# Entry+return pair gives spacing and the existing MMIO result, no extra reads.
probe() {
    name=${group}_$1 kind=$2 symbol=$3 fetch=${4:-} filter=${5:-}
    echo "$kind:$group/$name $symbol $fetch" >> "$tr/kprobe_events"
    created="$created $name"
    need_event "$group" "$name" "$filter"
    echo "$name $kind $symbol $fetch filter=$filter" >> "$out/probes.txt"
}
probe flush_in p dpu_hw_ctl_get_flush_register
probe flush_value r64 dpu_hw_ctl_get_flush_register 'value=$retval:x32'
probe vsync_in p dpu_encoder_phys_vid_vblank_irq
probe vsync_out r64 dpu_encoder_phys_vid_vblank_irq
probe wait_in p dpu_encoder_phys_vid_wait_for_commit_done '' "common_pid == $consumer"
probe wait_out r64 dpu_encoder_phys_vid_wait_for_commit_done 'rc=$retval:s32' "common_pid == $consumer"
# Pending mask assists interpretation of whole CTL_FLUSH versus IRQ mask tests.
if [ -e "$inst/events/dpu/dpu_enc_trigger_flush/enable" ]; then
    need_event dpu dpu_enc_trigger_flush
fi
{
    echo "waiter=$consumer seconds=$seconds threshold_us=$threshold clock=mono buffer_kb_per_cpu=512"
    date -u
    cat /proc/uptime
    cat "$inst/trace_clock"
    cat "/proc/$consumer/comm" "/proc/$consumer/status" "/proc/$consumer/sched"
    cat /proc/sys/kernel/sched_rt_runtime_us /proc/sys/kernel/sched_rt_period_us
} > "$out/setup-private.txt"
cat /proc/interrupts > "$out/interrupts-before-private.txt"
"$base/phase5-watch" capture "$inst" "$out" "$seconds" "$threshold" &
watchpid=$!
rc=0
wait "$watchpid" || rc=$?
watchpid=
for cpu in "$inst"/per_cpu/cpu*; do
    [ -r "$cpu/stats" ] || continue
    echo "${cpu##*/}" >> "$out/buffer-stats.txt"
    cat "$cpu/stats" >> "$out/buffer-stats.txt"
done
# nmissed must be checked before relying on a probe's absence.
if [ -r "$tr/kprobe_profile" ]; then
    grep "$group" "$tr/kprobe_profile" > "$out/kprobe-profile.txt" || :
fi
cat /proc/interrupts > "$out/interrupts-after-private.txt"
for e in /sys/class/drm/card*-*/eink; do
    [ -d "$e" ] || continue
    for name in stall_snapshot stall_recovery status rail_diag; do
        [ -r "$e/$name" ] && cat "$e/$name" > "$out/$name.txt"
    done
done
dmesg > "$out/dmesg-private.txt" || :
echo "Saved private capture in $out (watcher rc=$rc); cleanup now removes own tracing objects."
exit "$rc"
