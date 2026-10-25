#!/system/bin/sh
# User-run diagnostic only. No playback, gates, scheduling, affinity or device writes.
# Uses an isolated trace instance; freezes it at the first starvation cutoff.
set -eu
umask 077
seconds=${2:-15}
case "$seconds" in 5|10|15|20|30) ;; *) echo 'duration must be 5,10,15,20,30 seconds' >&2; exit 2 ;; esac
out=${1:?usage: sh capture.sh NEW_PRIVATE_OUTPUT_DIR [15]}
[ ! -e "$out" ] || { echo 'output must be new' >&2; exit 2; }
tr=/sys/kernel/tracing
[ -d "$tr/instances" ] || tr=/sys/kernel/debug/tracing
[ -d "$tr/instances" ] || { echo 'tracefs instances unavailable' >&2; exit 1; }
consumer= producer=
for p in /proc/[0-9]*; do
    [ -r "$p/comm" ] || continue
    name=$(cat "$p/comm" 2>/dev/null) || continue
    case "$name" in a9_eink) consumer=${p##*/} ;; a9_eink_r) producer=${p##*/} ;; esac
done
[ -n "$consumer" ] && [ -n "$producer" ] || { echo 'E Ink threads not found' >&2; exit 1; }
mkdir "$out"
group=a9as_$$
inst=$tr/instances/$group
mkdir "$inst"
created=
cleanup() {
    echo 0 > "$inst/tracing_on" 2>/dev/null || :
    echo 0 > "$inst/events/enable" 2>/dev/null || :
    # Only our event group and trace instance; never reset global tracer/probes.
    # Removing the instance also removes its event triggers before probe deletion.
    rmdir "$inst" 2>/dev/null || echo "cleanup: instance retained: $inst" >&2
    for name in $created; do
        echo "-:$group/$name" >> "$tr/kprobe_events" 2>/dev/null || echo "cleanup: probe retained: $group/$name" >&2
    done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# A fresh instance has no existing user state to preserve.
echo 0 > "$inst/tracing_on"
echo nop > "$inst/current_tracer"
echo 512 > "$inst/buffer_size_kb" # per CPU; 4 MiB on the eight-core A9
if grep -qw mono "$inst/trace_clock"; then echo mono > "$inst/trace_clock"; fi
[ ! -e "$inst/options/overwrite" ] || echo 1 > "$inst/options/overwrite"
: > "$inst/trace"
{
    echo "consumer=$consumer producer=$producer seconds=$seconds"
    cat "$inst/trace_clock"
    for id in "$consumer" "$producer"; do
        cat "/proc/$id/comm"
        cat "/proc/$id/stat"
        cat "/proc/$id/sched" 2>/dev/null || :
        cat "/proc/$id/status"
    done
    cat /proc/sys/kernel/sched_rt_runtime_us /proc/sys/kernel/sched_rt_period_us
    cat /proc/sys/kernel/printk
    for name in prewarm prefill_frames enable hv_hold_ms; do
        echo "$name=$(cat /sys/module/a9_eink/parameters/$name 2>/dev/null)"
    done
} > "$out/setup-private.txt"
# Required scheduling and actual mutex contention, including optimistic spin.
echo "prev_pid == $consumer || next_pid == $consumer || prev_pid == $producer || next_pid == $producer" > "$inst/events/sched/sched_switch/filter"
echo "stacktrace if prev_pid == $consumer && prev_state != 0" > "$inst/events/sched/sched_switch/trigger"
echo 1 > "$inst/events/sched/sched_switch/enable"
for name in sched_waking sched_wakeup sched_migrate_task; do
    [ -d "$inst/events/sched/$name" ] || continue
    echo "pid == $consumer || pid == $producer" > "$inst/events/sched/$name/filter"
    echo 1 > "$inst/events/sched/$name/enable"
done
for name in contention_begin contention_end; do
    [ -d "$inst/events/lock/$name" ] || { echo "missing lock:$name" >&2; exit 1; }
    echo "common_pid == $consumer" > "$inst/events/lock/$name/filter"
    echo 1 > "$inst/events/lock/$name/enable"
done
echo stacktrace > "$inst/events/lock/contention_begin/trigger"
# Matching lock address + this release's common_pid identifies the contended
# unlocker; the blocking stack identifies whether it is clk/regulator/DRM/etc.
if [ -d "$inst/events/lock/contended_release" ]; then
    echo 1 > "$inst/events/lock/contended_release/enable"
fi
probe() {
    name=$1 symbol=$2 kind=${3:-p}
    if (echo "$kind:$group/$name $symbol" >> "$tr/kprobe_events") 2>> "$out/unavailable-probes.txt"; then
        created="$created $name"
        echo 1 > "$inst/events/$group/$name/enable"
        echo "$name $symbol $kind" >> "$out/probes.txt"
        return 0
    fi
    echo "$name $symbol unavailable" >> "$out/unavailable-probes.txt"
    return 1
}
# Mandatory: retain the first failure instead of overwriting it with recovery.
probe cutoff fp9929a_starvation_off
echo traceoff:1 > "$inst/events/$group/cutoff/trigger"
# Tracepoint support varies; record exactly which groups were enabled.
for subsystem in clk rpm regulator; do
    [ -d "$inst/events/$subsystem" ] || continue
    echo 1 > "$inst/events/$subsystem/enable"
    echo "$subsystem" >> "$out/event-groups.txt"
done
for name in msm_atomic_commit_tail_start msm_atomic_commit_tail_finish msm_atomic_wait_flush_start msm_atomic_wait_flush_finish; do
    [ -e "$inst/events/msm/$name/enable" ] || continue
    echo 1 > "$inst/events/msm/$name/enable"
done
for name in dpu_perf_crtc_update dpu_core_perf_update_clk dpu_crtc_complete_commit; do
    [ -e "$inst/events/dpu/$name/enable" ] || continue
    echo 1 > "$inst/events/dpu/$name/enable"
done
for name in irq_handler_entry irq_handler_exit softirq_entry softirq_exit; do
    [ -e "$inst/events/irq/$name/enable" ] || continue
    echo 1 > "$inst/events/irq/$name/enable"
done
# Entry probes bracket waits *before* clk tracepoints (which run after lock
# acquisition); returns include total elapsed call time. No arguments/payloads.
for symbol in clk_prepare clk_get_rate clk_set_rate clk_prepare_lock clk_prepare_unlock clk_enable_lock clk_enable_unlock msm_framebuffer_prepare arm_smmu_map_pages dpu_kms_enable_commit dpu_kms_wait_flush; do
    probe "${symbol}_in" "$symbol" p || continue
    probe "${symbol}_out" "$symbol" r || :
done
# Save read-only per-event layouts so offline interpretation never assumes fields.
for ev in "$inst"/events/lock/*/format "$inst"/events/sched/sched_switch/format; do
    [ -r "$ev" ] && cat "$ev" >> "$out/event-formats.txt"
done
cat /proc/interrupts > "$out/interrupts-before-private.txt"
echo 1 > "$inst/tracing_on"
echo 'READY: manually reproduce one ordinary audio start with screen updates now.'
sleep "$seconds"
cat "$inst/tracing_on" > "$out/tracing-on-before-stop.txt"
echo 0 > "$inst/tracing_on"
cat "$inst/trace" > "$out/trace-private.txt"
for cpu in "$inst"/per_cpu/cpu*; do
    [ -r "$cpu/stats" ] || continue
    echo "${cpu##*/}" >> "$out/buffer-stats.txt"
    cat "$cpu/stats" >> "$out/buffer-stats.txt"
done
cat /proc/interrupts > "$out/interrupts-after-private.txt"
for e in /sys/class/drm/card*-*/eink; do
    [ -d "$e" ] || continue
    for name in stall_snapshot stall_recovery status rail_diag prewarm_stats; do
        [ -r "$e/$name" ] && cat "$e/$name" > "$out/$name.txt"
    done
done
dmesg > "$out/dmesg-private.txt"
echo "Saved private trace and snapshots in $out; cleanup removes only this trace instance."
