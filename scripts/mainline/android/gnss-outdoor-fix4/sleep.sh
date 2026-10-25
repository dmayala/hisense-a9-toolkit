#!/system/bin/sh
# Detached root control/sleep logger. No GNSS/app-file/HAL reads.
# integ5 builds upstream qcom_stats in: RPM vlow/vmin Count + 19.2MHz ticks.
exec </dev/null >/dev/null 2>&1
session=$1; gnss_end=$2; duration=$3; rehearsal=$4; location_before=$5; networks=$6
case "$session" in *[!a-f0-9]*|'') exit 2;; esac
case "$gnss_end:$duration:$rehearsal:$networks" in *[!0-9:\ ]*) exit 2;; esac
case "$location_before" in true|false) ;; *) exit 2;; esac
[ "$(id -u)" = 0 ] && [ "$(getprop ro.boot.slot_suffix)" = _b ] || exit 2
up() { IFS=' ' read -r value rest </proc/uptime; printf '%s\n' "${value%%.*}"; }
emit() { printf 'A9_SLEEP_OUT session=%s %s\n' "$session" "$*" >/dev/kmsg; }
stats_debugfs() {
    # Android leaves debugfs unmounted. CONFIG_DEBUG_FS=y and a bound stats
    # driver alone do not expose its files in this mount namespace.
    debug_mounted=0; stats_bound=0; stats_files=0
    if ! awk '$2=="/sys/kernel/debug" && $3=="debugfs" {found=1} END {exit !found}' /proc/mounts; then
        timeout -s KILL 3 mount -t debugfs debugfs /sys/kernel/debug >/dev/null 2>&1 || :
    fi
    awk '$2=="/sys/kernel/debug" && $3=="debugfs" {found=1} END {exit !found}' /proc/mounts && debug_mounted=1
    [ ! -L /sys/bus/platform/devices/4690000.sram/driver ] || stats_bound=1
    for path in /sys/kernel/debug/qcom_stats/vlow /sys/kernel/debug/qcom_stats/vmin; do
        [ ! -r "$path" ] || stats_files=$((stats_files+1))
    done
    emit "kind=stats_debugfs sample=$sample debugfs_mounted=$debug_mounted stats_driver_bound=$stats_bound stats_files=$stats_files"
}
# Shared sanitizer used only for kernel labels, never arbitrary log lines.
NAME_AWK='function safe_name(s) {
    if(s=="4e00000.usb" || s=="4ef8800.usb" || s=="1613000.phy" || s=="4a8c000.serial") return s
    if(s ~ /([0-9a-fA-F][0-9a-fA-F][:-]){5}[0-9a-fA-F][0-9a-fA-F]/ || s ~ /[0-9][.][0-9]/) return "redacted"
    gsub(/[0-9a-fA-F]{7,}/,"redacted",s)
    gsub(/[^A-Za-z0-9_.:-]/,"_",s)
    return length(s) ? substr(s,1,64) : "unknown"
}'
previous_wake=''
wakeup_snapshot() {
    # Stable wakeup class index, fixed counter labels, numeric values only.
    # Emit kernel names only after removing private-looking identifiers.
    current_wake=$(
        n=0
        for dir in /sys/class/wakeup/wakeup*; do
            [ -d "$dir" ] || continue
            id=${dir##*/wakeup}
            case "$id" in ''|*[!0-9]*) continue;; esac
            n=$((n+1)); [ "$n" -le 256 ] || break
            IFS= read -r source <"$dir/name" || source=unknown
            case "$source" in ''|*[!A-Za-z0-9_.:-]*) source=redacted;; esac
            for key in active_count event_count wakeup_count expire_count active_time_ms total_time_ms max_time_ms last_change_ms prevent_suspend_time_ms; do
                [ -r "$dir/$key" ] || continue
                IFS= read -r value <"$dir/$key" || continue
                case "$value" in ''|*[!0-9]*) continue;; esac
                printf '%s %s %s %s\n' "$id" "$source" "$key" "$value"
            done
        done
    )
    # Android awk rejects literal newlines in -v values. Pass the prior numeric
    # snapshot on stdin, followed by a blank delimiter and the current snapshot.
    { printf '%s\n\n' "$previous_wake"; printf '%s\n' "$current_wake"; } |
    awk -v session="$session" -v sample="$sample" "$NAME_AWK"'
    function emit_record(line) {print line >"/dev/kmsg";close("/dev/kmsg")}
    NF==0 {current=1;next}
    !current {if(NF==4) old[$1 SUBSEP $3]=$4; next}
    NF==4 {
        key=$1 SUBSEP $3
        name=safe_name($2); alias="other"
        if(name ~ /^(alarmtimer|PowerManagerService.Display|PowerManagerService.WakeLocks|wlan|wlan_wow|qcom_rx_wakelock|pm8xxx_pwrkey|qpnp_rtc|rtc0|smp2p|glink|eventpoll|sync|timerfd)$/) alias=name
        line=sprintf("A9_SLEEP_OUT session=%s kind=wakeup sample=%s wakeup_id=%s wake_source=%s wake_name=%s key=%s value=%s",session,sample,$1,alias,name,$3,$4)
        if(key in old && $4+0>=old[key]+0) line=line sprintf(" delta=%.0f",$4-old[key])
        emit_record(line)
        fields++
    }
    END {emit_record(sprintf("A9_SLEEP_OUT session=%s kind=wakeup_availability sample=%s wakeup_fields=%d",session,sample,fields))}
    '
    previous_wake=$current_wake
    irq_available=0
    if [ -r /sys/power/pm_wakeup_irq ]; then
        IFS= read -r irq </sys/power/pm_wakeup_irq || irq=''
        case "$irq" in ''|*[!0-9]*) ;; *)
            irq_available=1
            action=unknown; chip=unknown; hwirq=0
            [ ! -r "/sys/kernel/irq/$irq/actions" ] || IFS= read -r action <"/sys/kernel/irq/$irq/actions"
            [ ! -r "/sys/kernel/irq/$irq/chip_name" ] || IFS= read -r chip <"/sys/kernel/irq/$irq/chip_name"
            [ ! -r "/sys/kernel/irq/$irq/hwirq" ] || IFS= read -r hwirq <"/sys/kernel/irq/$irq/hwirq"
            case "$hwirq" in ''|*[!0-9]*) hwirq=0;; esac
            printf '%s\n%s\n' "$action" "$chip" | awk -v session="$session" -v sample="$sample" -v irq="$irq" -v hwirq="$hwirq" "$NAME_AWK"'
            NR==1 {name=safe_name($0)}
            NR==2 {printf "A9_SLEEP_OUT session=%s kind=wakeup_irq sample=%s irq=%s irq_name=%s irq_chip=%s hwirq=%s\n",session,sample,irq,name,safe_name($0),hwirq}
            ' >/dev/kmsg;;
        esac
    fi
    emit "kind=wakeup_irq_availability sample=$sample irq_available=$irq_available"
}
emit "kind=armed duration_s=$duration rehearsal=$rehearsal"
now=$(up)
[ "$now" -ge "$gnss_end" ] || sleep "$((gnss_end-now))"
# The app's own timer has stopped GPS; only normal cleanup and screen control.
timeout -s KILL 10 cmd location set-location-enabled false --user 0 >/dev/null 2>&1 || :
setprop vendor.a9ml.cellular 0
timeout -s KILL 10 cmd wifi disconnect >/dev/null 2>&1 || :
timeout -s KILL 10 cmd wifi set-wifi-enabled disabled >/dev/null 2>&1 || :
forgot=1
for net in $networks; do
    timeout -s KILL 10 cmd wifi forget-network "$net" >/dev/null 2>&1 || forgot=0
done
setprop vendor.a9ml.wifi.session.stop 1
timeout -s KILL 10 svc power stayon false >/dev/null 2>&1 || :
off=0
timeout -s KILL 10 input keyevent KEYCODE_SLEEP >/dev/null 2>&1 && off=1
emit "kind=screen_off requested=$off wifi_forgotten=$forgot"
# The only observations below are sleep/stat counters and elapsed uptime.
snapshot() {
    emit "kind=sample sample=$sample elapsed_s=$(( $(up)-begin ))"
    stats_debugfs
    wakeup_snapshot
    soc=0; suspend=0
    for spec in \
        qcom:vlow:/sys/kernel/debug/qcom_stats/vlow \
        qcom:vmin:/sys/kernel/debug/qcom_stats/vmin \
        system_sleep:none:/sys/power/system_sleep/stats \
        soc_sleep:none:/sys/power/soc_sleep_stats \
        rpm:none:/sys/kernel/debug/rpm_stats \
        rpmh:vlow:/sys/kernel/debug/rpmh_stats/vlow \
        rpmh:vmin:/sys/kernel/debug/rpmh_stats/vmin; do
        src=${spec%%:*}; rest=${spec#*:}; mode=${rest%%:*}; path=${rest#*:}
        [ -f "$path" ] && [ -r "$path" ] || continue
        # Empty or failed reads must remain unavailable, rather than zero.
        raw=$(timeout -s KILL 3 cat "$path") || continue
        if printf '%s\n' "$raw" | awk -v session="$session" -v sample="$sample" -v src="$src" -v mode="$mode" '
        /RPM Mode:[[:space:]]*vlow/ {mode="vlow"; next}
        /RPM Mode:[[:space:]]*vmin/ {mode="vmin"; next}
        {s=$0; sub(/^[[:space:]]*/,"",s); key="";
         if(s ~ /^(Count|count):/) {key="count"; sub(/^[^:]*:[[:space:]]*/,"",s)}
         else if(s ~ /^Accumulated Duration:/) {key="duration_ticks"; sub(/^[^:]*:[[:space:]]*/,"",s)}
         else if(s ~ /^Last Entered At:/) {key="entered_ticks"; sub(/^[^:]*:[[:space:]]*/,"",s)}
         else if(s ~ /^Last Exited At:/) {key="exited_ticks"; sub(/^[^:]*:[[:space:]]*/,"",s)}
         else if(s ~ /^actual last sleep\(msec\):/) {key="duration_ms"; sub(/^[^:]*:[[:space:]]*/,"",s)}
         else if(s ~ /^(success|fail|failed_freeze|failed_prepare|failed_suspend|failed_suspend_late|failed_suspend_noirq|failed_resume|failed_resume_early|failed_resume_noirq):/) {
           key=s; sub(/:.*/,"",key); sub(/^[^:]*:[[:space:]]*/,"",s)
         }
         sub(/[[:space:]]*$/, "",s);
         if(key!="" && s ~ /^[0-9]+$/ && (mode=="vlow" || mode=="vmin")) {
           if(key=="count") counts++;
           printf "A9_SLEEP_OUT session=%s kind=counter sample=%s source=%s mode=%s key=%s value=%s\n",session,sample,src,mode,key,s >"/dev/kmsg"; close("/dev/kmsg")
         }
        }
        END {exit (counts ? 0 : 1)}' >/dev/kmsg; then
            soc=$((soc+1))
        fi
    done
    for dir in /sys/power/suspend_stats /sys/power/system_sleep/stats; do
        for key in success fail failed_freeze failed_prepare failed_suspend failed_suspend_late failed_suspend_noirq failed_resume failed_resume_early failed_resume_noirq; do
            [ -r "$dir/$key" ] || continue
            v=$(cat "$dir/$key")
            case "$v" in ''|*[!0-9]*) continue;; esac
            suspend=$((suspend+1))
            src=suspend; [ "$dir" != /sys/power/system_sleep/stats ] || src=system_sleep_suspend
            emit "kind=counter sample=$sample source=$src mode=none key=$key value=$v"
        done
    done
    emit "kind=availability sample=$sample soc_sources=$soc suspend_fields=$suspend"
}
begin=$(up); end=$((begin+duration)); sample=0
snapshot
if [ "$rehearsal" = 0 ]; then
    printf a9ml_idle2_awake >/sys/power/wake_unlock
    printf a9ml_test_awake >/sys/power/wake_unlock
fi
watch_result=$(/data/local/tmp/a9-resume-stats "$session" "$begin" "$end")
watch_rc=$?
case "$watch_result" in ''|*[!0-9]*) watch_rc=1;; *) sample=$watch_result;; esac
if [ "$watch_rc" != 0 ]; then
    emit "kind=resume_monitor state=unavailable"
    # Keep cleanup/reporting available if an old launcher lacks the helper.
    # This fallback makes no claim of observing each resume.
    while :; do
        now=$(up); [ "$now" -lt "$end" ] || break
        pause=$((end-now)); [ "$pause" -le 60 ] || pause=60
        sleep "$pause"
        sample=$((sample+1)); snapshot
    done
fi
printf a9ml_test_awake >/sys/power/wake_lock
sample=$((sample+1)); snapshot
emit "kind=final samples=$((sample+1)) elapsed_s=$(( $(up)-begin )) rehearsal=$rehearsal"
sync
timeout -s KILL 10 cmd location set-location-enabled "$location_before" --user 0 >/dev/null 2>&1 || :
