#!/usr/bin/env bash
# HOST hook for A9_BTRY_ONB. Written only; execution belongs to agent/b-try.
# Never invoke this file merely to test it. Native commands use onb-root.py;
# framework calls use ordinary adb shell, with device-side sanitization.
set -euo pipefail
# Preserve the runner output before any per-stage stdout redirection.
exec 3>&1
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
: "${ADB:?b-try must export ADB}"
: "${A9_BTRY_LOGS:?b-try must export A9_BTRY_LOGS}"
[[ ${A9_BTRY_IN_HOOK:-} == 1 ]] || { echo 'FAIL hook_context'; exit 2; }
LOGS=$A9_BTRY_LOGS/modem-stock
mkdir -m 700 "$LOGS"
ROOT=$HERE/../../boot-deadline/onb-root.py
REPORT=$HERE/onb-report.py
cellular_requested=0
finished=0
stage=initializing
step() { stage=$1; printf '%s\n' "$stage" >"$LOGS/hook-stage"; }
root_native() {
    local phase=$1
    [[ $phase =~ ^(start-owner|wait-owner|wait-stock|opt-in|monitor|snapshot|cleanup)$ ]] || return 2
    local statuses=()
    { printf 'phase=%s\n' "$phase"; cat "$HERE/onb-native.sh"; } |
        A9_BTRY_ROOT_STEP="modem-$phase" python3 "$ROOT" 2>/dev/null |
        python3 "$REPORT" filter native && statuses=("${PIPESTATUS[@]}") || statuses=("${PIPESTATUS[@]}")
    printf '%s %s %s %s\n' "$phase" "${statuses[0]}" "${statuses[1]}" "${statuses[2]}" >>"$LOGS/transport-status.tsv"
    [[ ${statuses[0]} == 0 && ${statuses[1]} == 0 && ${statuses[2]} == 0 ]]
}
framework_snapshot() {
    # Only finite interface names, booleans, numeric counts and SIM states
    # leave these device-side pipelines. Never retain raw framework dumps.
    local statuses=()
    timeout 40 "$ADB" shell -T '
        timeout 10 lshal 2>/dev/null | awk '\''
        /android\.hardware\.radio@1\.[4-9]::IRadio\/slot1([[:space:]]|$)/ {r1=1}
        /android\.hardware\.radio@1\.[4-9]::IRadio\/slot2([[:space:]]|$)/ {r2=1}
        /vendor\.qti\.hardware\.radio\.ims@1\.[0-9]+::IImsRadio\/imsradio0([[:space:]]|$)/ {i1=1}
        /vendor\.qti\.hardware\.radio\.ims@1\.[0-9]+::IImsRadio\/imsradio1([[:space:]]|$)/ {i2=1}
        END{print "FW radio_slot1",r1+0;print "FW radio_slot2",r2+0;print "FW ims_slot1",i1+0;print "FW ims_slot2",i2+0}'\''
        timeout 5 service list 2>/dev/null | awk '\''
        /android\.hardware\.radio\./{r++}
        /android\.hardware\.audio\.core\.IModule\/default/{a=1}
        END{print "FW binder_radio_count",r+0;print "FW audio_module",a+0}'\''
        timeout 5 cmd wifi status 2>/dev/null | awk '\''
        /^Wifi is enabled$/ || /^Wi-Fi is enabled$/ {v=1}
        /^Wifi is disabled$/ || /^Wi-Fi is disabled$/ {v=0;d=1}
        END {if(v)print "FW wifi_enabled 1";else if(d)print "FW wifi_enabled 0";else print "FW wifi_enabled -1"}'\''
        # LOS23 SubscriptionManagerService.dump exposes explicit logical slots.
        # Historical log lines, card numbers and unrelated dump fields cannot match.
        timeout 5 dumpsys isub 2>/dev/null | awk '\''
        /^[[:space:]]*mSimState\[[01]\]=[A-Z_]+[[:space:]]*$/ {
            v=$0; sub(/^[[:space:]]*mSimState\[/,"",v); slot=substr(v,1,1)
            sub(/^[01]\]=/,"",v);sub(/[[:space:]]*$/,"",v)
            seen[slot]++;state[slot]=v
        }
        END {
            a=0;o=0
            for(i=0;i<2;i++) {
                v=(seen[i]==1 ? state[i] : "UNKNOWN")
                if(v !~ /^(ABSENT|READY|NOT_READY|PIN_REQUIRED|PUK_REQUIRED|UNKNOWN|CARD_IO_ERROR|CARD_RESTRICTED|PERM_DISABLED|LOADED|NETWORK_LOCKED|PRESENT|DISABLED)$/)v="UNKNOWN"
                print "FW sim_slot" i,v
                if(v=="ABSENT")a++;else o++
            }
            print "FW sim_absent_evidence",a;print "FW sim_other_evidence",o
        }'\''
        # Registry reports radio/service state, not SIM presence. Keep only
        # phone-section booleans; OUT_OF_SERVICE is never evidence of ABSENT.
        timeout 5 dumpsys telephony.registry 2>/dev/null | awk '\''
        /^[[:space:]]*Phone Id=0[[:space:]]*$/ {p0=1}
        /^[[:space:]]*Phone Id=1[[:space:]]*$/ {p1=1}
        END{print "FW registry_phone0",p0+0;print "FW registry_phone1",p1+0}'\''
    ' 2>/dev/null | python3 "$REPORT" filter framework && statuses=("${PIPESTATUS[@]}") || statuses=("${PIPESTATUS[@]}")
    printf 'framework %s %s 0\n' "${statuses[0]}" "${statuses[1]}" >>"$LOGS/transport-status.tsv"
    [[ ${statuses[0]} == 0 && ${statuses[1]} == 0 ]]
}
audio_snapshot() {
    # AudioFlinger creates playback threads only after HAL output stream open.
    # This is a silent framework output-open check, not a voice/PCM/sound test.
    local statuses=()
    timeout 15 "$ADB" shell -T 'timeout 10 dumpsys media.audio_flinger 2>/dev/null | awk '\''
        /Output thread/ {n++}
        END {print "FW audio_output_threads",n+0}'\'' ' 2>/dev/null |
        python3 "$REPORT" filter framework && statuses=("${PIPESTATUS[@]}") || statuses=("${PIPESTATUS[@]}")
    printf 'audio %s %s 0\n' "${statuses[0]}" "${statuses[1]}" >>"$LOGS/transport-status.tsv"
    [[ ${statuses[0]} == 0 && ${statuses[1]} == 0 ]]
}
finish() {
    local rc=$? report_rc=0 output_rc=0
    trap - EXIT INT TERM
    # No failed optional diagnostic/cleanup command may skip the report.
    # The original runner output is FD3, even if EXIT came from a redirected
    # function call. All collections keep their own explicit destinations.
    set +e
    root_native snapshot >"$LOGS/final.native.tsv"
    framework_snapshot >"$LOGS/final.framework.tsv"
    audio_snapshot >"$LOGS/final.audio.tsv"
    if ((cellular_requested)); then
        root_native cleanup >"$LOGS/cleanup.tsv" || rc=1
    else
        printf 'ACTION clients_stopped 1\n' >"$LOGS/cleanup.tsv"
    fi
    printf '%s\n' "$rc" >"$LOGS/hook-status"
    python3 "$REPORT" summary "$LOGS" >"$LOGS/report.txt" 2>/dev/null
    report_rc=$?
    if [[ ! -s "$LOGS/report.txt" ]]; then
        printf 'FAIL report_generation rc=%s\n' "$report_rc" >"$LOGS/report.txt"
        report_rc=1
    fi
    cat "$LOGS/report.txt" >&3 || output_rc=1
    ((finished && rc==0 && report_rc==0 && output_rc==0)) && exit 0
    exit 1
}
trap finish EXIT
trap 'exit 130' INT TERM

step owner_request
root_native start-owner >"$LOGS/owner-request.tsv"
[[ $(awk '$1=="ACTION" && $2=="owner_start" {print $3}' "$LOGS/owner-request.tsv") != 2 ]] || exit 1
# Each native chunk stays below onb-root.py/loopback transport timeouts.
step owner_wait
owner_end=$((SECONDS+300))
while :; do
    root_native wait-owner >"$LOGS/readiness.tsv"
    owner=$(awk '$1=="PROP" && $2=="owner" {print $3}' "$LOGS/readiness.tsv")
    [[ $owner != serving ]] || break
    case "$owner" in failed|ended|ending) exit 1 ;; esac
    ((SECONDS<owner_end)) || exit 1
done
step opt_in
cellular_requested=1
root_native opt-in >"$LOGS/opt-in.tsv"
grep -qx 'ACTION cellular_optin 1' "$LOGS/opt-in.tsv" || exit 1
# Same framework Wi-Fi enable operation as the integrated POC; no network,
# credentials, connection status details or setup-wizard changes are needed.
if timeout 15 "$ADB" shell 'cmd wifi set-wifi-enabled enabled >/dev/null 2>&1' >/dev/null 2>&1; then
    printf 'FW wifi_enable_request 1\n' >"$LOGS/wifi-request.tsv"
else
    printf 'FW wifi_enable_request 0\n' >"$LOGS/wifi-request.tsv"
    exit 1
fi
step preparation_diagnostics
root_native monitor >"$LOGS/preparation.native.tsv"
framework_snapshot >"$LOGS/preparation.framework.tsv"
audio_snapshot >"$LOGS/preparation.audio.tsv"
step stock_wait
stock_end=$((SECONDS+240))
while :; do
    root_native wait-stock >"$LOGS/readiness.tsv"
    ready=$(awk '$1=="PROP" && $2=="stock_ready" {print $3}' "$LOGS/readiness.tsv")
    state=$(awk '$1=="PROP" && $2=="stock_state" {print $3}' "$LOGS/readiness.tsv")
    [[ $state != failed ]] || exit 1
    [[ $ready != 1 ]] || break
    ((SECONDS<stock_end)) || exit 1
done
# Allow gated IMS clients time to publish before taking the starting PID set.
step client_warmup
warm_end=$((SECONDS+30))
while :; do
    root_native monitor >"$LOGS/warmup.tsv"
    if python3 "$REPORT" processes-ready "$LOGS/warmup.tsv"; then break; fi
    ((SECONDS<warm_end)) || break
    sleep 2
done
step observation
root_native snapshot >"$LOGS/start.native.tsv"
framework_snapshot >"$LOGS/start.framework.tsv"
audio_snapshot >"$LOGS/start.audio.tsv"
python3 "$REPORT" stamp >"$LOGS/window.start"
# Six full sleeps guarantee >=180s; integer SECONDS deadlines can truncate
# the fractional starting second. Native sampling overhead adds to this window.
for sample in 1 2 3 4 5 6; do
    sleep 30
    if ((sample<6)); then
        root_native monitor >"$LOGS/monitor-$sample.tsv"
    fi
done
root_native snapshot >"$LOGS/end.native.tsv"
framework_snapshot >"$LOGS/end.framework.tsv"
audio_snapshot >"$LOGS/end.audio.tsv"
python3 "$REPORT" stamp >"$LOGS/window.end"
finished=1
step complete
