# Proposal insertion inside a9-ctl collect_safe(), after d=$1.
# Never executed by this agent. Coordinator integrates/runs capture.
cat /proc/cpuinfo > "$d/cpuinfo.txt" 2>&1
cat /proc/stat > "$d/cpu-stat.txt" 2>&1
cat /proc/sys/kernel/random/boot_id > "$d/boot-id.txt" 2>&1
for f in online possible present; do
    printf '%s=' "$f"
    cat "/sys/devices/system/cpu/$f"
done > "$d/cpus.txt" 2>&1
for p in /sys/devices/system/cpu/cpufreq/policy*; do
    [ -d "$p" ] || continue
    for f in scaling_driver scaling_governor related_cpus affected_cpus \
             scaling_available_frequencies scaling_boost_frequencies \
             cpuinfo_min_freq cpuinfo_max_freq scaling_min_freq scaling_max_freq \
             scaling_cur_freq cpuinfo_cur_freq stats/total_trans; do
        [ -r "$p/$f" ] || continue
        printf '%s/%s=' "${p##*/}" "$f"
        cat "$p/$f"
    done
done > "$d/cpufreq.txt" 2>&1
cat /sys/devices/system/cpu/cpuidle/current_driver > "$d/cpuidle-driver.txt" 2>&1
for p in /sys/devices/system/cpu/cpu[0-9]*/cpuidle/state*; do
    [ -d "$p" ] || continue
    for f in name usage time rejected disable; do
        [ -r "$p/$f" ] || continue
        printf '%s/%s=' "${p#/sys/devices/system/cpu/}" "$f"
        cat "$p/$f"
    done
done > "$d/cpuidle.txt" 2>&1
for p in /sys/class/thermal/thermal_zone*; do
    [ -d "$p" ] || continue
    for f in type temp; do
        [ -r "$p/$f" ] || continue
        printf '%s/%s=' "${p##*/}" "$f"
        cat "$p/$f"
    done
done > "$d/cpu-thermal.txt" 2>&1
