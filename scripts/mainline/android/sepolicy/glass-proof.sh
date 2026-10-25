# Manual coordinator-only snippet for the existing admitted phone root console.
# Never opens gates, changes waveform/power parameters or restarts a compositor.
set -eu
SYS=${A9ML_SYS:-/sys}
[ "$(getenforce)" = Enforcing ] || { echo 'GLASS FAIL: require Enforcing'; exit 1; }
echo "GLASS gate_property=$(getprop vendor.a9ml.eink.gate) (diagnostic; module gates checked below)"
[ -z "$(getprop vendor.a9ml.eink.fault)" ] || { echo 'GLASS FAIL: existing E Ink fault'; exit 1; }
for parameter in a9_eink/enable fp9929a/allow_power_on panel_hisense_hlte556n_eink/enable; do
    case "$(cat "$SYS"/module/${parameter%/*}/parameters/${parameter#*/})" in
        Y|y|1) ;; *) echo "GLASS FAIL: closed gate $parameter"; exit 1 ;;
    esac
done
card=$(getprop vendor.a9ml.eink.card)
case "$card" in card[0-9]*) ;; *) echo 'GLASS FAIL: missing E Ink card'; exit 1 ;; esac
set -- "$SYS"/class/drm/"$card"-*/eink/status
[ "$#" = 1 ] && [ -r "$1" ] || { echo 'GLASS FAIL: no unique readable E Ink status'; exit 1; }
status=$1
log_parameter="$SYS"/module/a9_eink/parameters/log_updates
old=$(cat "$log_parameter")
trap 'printf "%s\n" "$old" > "$log_parameter"' EXIT
printf 'Y\n' > "$log_parameter"
case "$(cat "$log_parameter")" in Y|y|1) ;; *) echo 'GLASS FAIL: log_updates readback'; exit 1 ;; esac
count_logs() { dmesg | grep -cE 'a9[-_]eink.*update done:' || :; }
count_updates() { awk '$1 == "updates" {print $2; exit}' "$status"; }
fault_counts() { awk '{for(i=1;i<NF;i++) if($i=="aborts" || $i=="commit_fail" || $i=="stall_fault" || $i=="rail_fault" || $i=="rail_faults") print $i,$(i+1)}' "$status"; }
before_faults=$(fault_counts)
before_logs=$(count_logs)
before_updates=$(count_updates)
case "$before_updates" in ''|*[!0-9]*) echo 'GLASS FAIL: no updates counter'; exit 1 ;; esac
echo "GLASS before logs=$before_logs updates=$before_updates card=$card"
cat "$status"
# Existing open/admitted session only: produce an awake HOME redraw and swipe.
input keyevent KEYCODE_WAKEUP
input keyevent KEYCODE_HOME
input swipe 650 400 150 400 350
input keyevent KEYCODE_HOME
i=0
while [ "$i" -lt 10 ]; do
    sleep 1
    after_logs=$(count_logs)
    after_updates=$(count_updates)
    [ "$after_logs" -le "$before_logs" ] || break
    i=$((i+1))
done
echo "GLASS after logs=$after_logs updates=$after_updates log_delta=$((after_logs-before_logs)) update_delta=$((after_updates-before_updates))"
cat "$status"
after_faults=$(fault_counts)
if [ "$after_logs" -gt "$before_logs" ] && [ "$after_updates" -gt "$before_updates" ] && [ "$before_faults" = "$after_faults" ] && [ -z "$(getprop vendor.a9ml.eink.fault)" ]; then
    echo 'GLASS DRIVER PASS: completed real updates observed; user must still confirm actual glass changes and no new faults'
else
    echo 'GLASS FAIL: no positive update delta or new fault counters; preserve full early-boot dmesg/composer dump'
    exit 1
fi
