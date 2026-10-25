# Sourced by native slot-B root commands, never by app_process.
clock_snapshot() {
    /data/local/tmp/a9-idle2-clock || echo CLOCK_UNAVAILABLE
}
battery_snapshot() {
    for p in /sys/class/power_supply/*; do
        [ -d "$p" ] || continue
        for f in type status online present current_now current_avg voltage_now charge_counter charge_now capacity model_name; do
            [ -r "$p/$f" ] || continue
            v=$(cat "$p/$f" 2>/dev/null) || v=UNAVAILABLE
            printf 'POWER\t%s\t%s\t%s\n' "${p##*/}" "$f" "$v"
        done
    done
}
snapshot() {
    printf 'START_'; clock_snapshot
    printf 'BOOT %s\n' "$(cat /proc/sys/kernel/random/boot_id)"
    printf 'CMDLINE %s\n' "$(cat /proc/cmdline)"
    printf 'STATE slot=%s sensors=%s\n' "$(getprop ro.boot.slot_suffix)" "$(getprop vendor.a9.sensors.state)"
    printf 'BOOT_REASON ro=%s sys=%s watchdogd=%s\n' "$(getprop ro.boot.bootreason)" "$(getprop sys.boot.reason)" "$(getprop init.svc.watchdogd-gsi)"
    for f in /sys/module/a9_boot_deadline/parameters/boot_completed /sys/class/watchdog/watchdog*/timeout /sys/class/watchdog/watchdog*/status; do
        [ ! -r "$f" ] || printf 'WATCHDOG\t%s\t%s\n' "$f" "$(cat "$f")"
    done
    battery_snapshot
    for f in /sys/devices/system/cpu/cpuidle/current_driver /sys/devices/system/cpu/cpuidle/current_governor_ro /sys/devices/system/cpu/online /sys/module/cpuidle/parameters/off /sys/power/mem_sleep; do
        [ ! -r "$f" ] || printf 'IDLE_CONTROL\t%s\t%s\n' "$f" "$(cat "$f")"
    done
    for c in /sys/devices/system/cpu/cpu[0-9]*; do
        for s in "$c"/cpuidle/state*; do
            [ -d "$s" ] || continue
            for f in name desc usage time rejected above below disable latency residency s2idle/usage s2idle/time; do
                [ ! -r "$s/$f" ] || printf 'CPUIDLE\t%s\t%s\t%s\t%s\n' "${c##*/}" "${s##*/}" "$f" "$(cat "$s/$f")"
            done
        done
    done
    found=0; scanned=0
    for f in /sys/kernel/debug/qcom_stats/* /sys/kernel/debug/rpmh_stats/* /sys/kernel/debug/rpm_stats/* /sys/kernel/debug/rpmh-stats/* /sys/power/soc_sleep_stats; do
        [ -f "$f" ] && [ -r "$f" ] || continue
        found=1; scanned=$((scanned+1)); [ "$scanned" -le 24 ] || break
        printf 'SOC_BEGIN\t%s\n' "$f"
        timeout 2 cat "$f" || echo SOC_READ_UNAVAILABLE
        echo SOC_END
    done
    [ "$found" -ne 0 ] || echo SOC_STATS_UNAVAILABLE
    for f in /sys/power/suspend_stats/*; do
        [ ! -f "$f" ] || printf 'STAT %s=%s\n' "${f##*/}" "$(cat "$f")"
    done
    printf 'WAKE_IRQ %s\n' "$(cat /sys/power/pm_wakeup_irq 2>/dev/null)"
    for p in /sys/class/wakeup/wakeup*; do
        [ ! -d "$p" ] || printf 'WAKE\t%s\t%s\t%s\t%s\t%s\n' "${p##*/}" "$(cat "$p/name")" "$(cat "$p/active_count")" "$(cat "$p/event_count")" "$(cat "$p/wakeup_count")"
    done
    echo WAKEUP_SOURCES
    cat /sys/kernel/debug/wakeup_sources 2>/dev/null || true
    echo PSCI_STARTUP
    dmesg | grep -iE 'psci|cpuidle|CPU PM domain|qcom.*stats' || true
    printf 'END_'; clock_snapshot
    :
}
