# Sent on stdin to onb-root.py by onb.sh; never invoke directly on the host.
# onb-root.py supplies set -e and root/slot-B checks.
prop() {
    value=$(getprop "$2" 2>/dev/null || true)
    if [ "$1" = stock_count ]; then
        case "$value" in ''|*[!0-9]*) value=unknown ;; *) [ "$value" -le 134 ] || value=unknown ;; esac
        printf 'PROP %s %s\n' "$1" "$value"
        return
    fi
    case "$value" in
        '' ) value=unknown ;;
        failed*) value=failed ;;
        ended*) value=ended ;;
        idle|requested|preparing|gates|modules|sources|copying|database|labeling|verifying|metadata|complete|0|1|serving|awaiting-s2-audit|armed|starting|prepared|stopped|failed|ended|ending|running|restarting|blocked-kernel-abi) ;;
        *) value=unknown ;;
    esac
    printf 'PROP %s %s\n' "$1" "$value"
}
stock_pids() {
    for tag in qcrild qcrild2 imsqmidaemon imsdatadaemon ims_rtp_daemon imsrcsd audioserver audio_hal; do
        case "$tag" in
            qcrild) service=vendor.qcrild; binary=qcrild; path=/vendor/bin/hw/qcrild ;;
            qcrild2) service=vendor.qcrild2; binary=qcrild; path=/vendor/bin/hw/qcrild ;;
            imsrcsd) service=vendor.imsrcsservice; binary=imsrcsd; path=/vendor/bin/imsrcsd ;;
            audioserver) service=audioserver; binary=audioserver; path=/system/bin/audioserver ;;
            audio_hal) service=vendor.audio-hal-aidl; binary=android.hardware.audio.service-aidl.a9; path=/vendor/bin/hw/android.hardware.audio.service-aidl.a9 ;;
            *) service=vendor.$tag; binary=$tag; path=/vendor/bin/$tag ;;
        esac
        state=$(getprop "init.svc.$service" 2>/dev/null || true)
        case "$state" in running|stopped|restarting) ;; *) state=unknown ;; esac
        chosen=0; birth=0; count=0
        candidates=$(timeout 2 pidof "$binary" 2>/dev/null || true)
        for p in $candidates; do
            case "$p" in ''|*[!0-9]*) continue ;; esac
            args=$(timeout 1 head -c 4096 "/proc/$p/cmdline" 2>/dev/null | tr '\000' ' ' 2>/dev/null || true)
            if [ "$tag" = qcrild2 ]; then
                case "$args" in "$path -c 2"|"$path -c 2 ") ;; *) continue ;; esac
            else
                case "$args" in "$path"|"$path ") ;; *) continue ;; esac
            fi
            statline=$(timeout 1 cat "/proc/$p/stat" 2>/dev/null || true)
            statline=${statline##*) }
            ticks=$(printf '%s\n' "$statline" | awk '{print $20}' 2>/dev/null || true)
            case "$ticks" in ''|*[!0-9]*) continue ;; esac
            count=$((count + 1)); chosen=$p; birth=$ticks
        done
        [ "$count" -eq 1 ] || { chosen=0; birth=0; }
        printf 'PID %s %s %s %s %s\n' "$tag" "$chosen" "$birth" "$count" "$state"
    done
}
case "$phase" in
    start-owner)
        prop boot sys.boot_completed
        state=$(getprop vendor.a9ml.wifi.session.state 2>/dev/null || true)
        # Same one-shot trigger as scripts/mainline/poc/start.sh. No state spoof,
        # reset of a consumed attempt, direct remoteproc operation, or new owner.
        case "$state" in
            serving|armed|starting) printf 'ACTION owner_start 0\n' ;;
            ''|awaiting-s2-audit)
                if [ "$(getprop vendor.a9ml.wifi.s2.start 2>/dev/null || true)" = 1 ]; then
                    printf 'ACTION owner_start 2\n'
                else
                    setprop vendor.a9ml.wifi.s2.start 1
                    printf 'ACTION owner_start 1\n'
                fi ;;
            *) printf 'ACTION owner_start 2\n' ;;
        esac
        prop owner vendor.a9ml.wifi.session.state ;;
    wait-owner|wait-stock)
        n=0
        while [ "$n" -lt 20 ]; do
            owner=$(getprop vendor.a9ml.wifi.session.state 2>/dev/null || true)
            ready=$(getprop vendor.a9ml.cellular.stock.ready 2>/dev/null || true)
            state=$(getprop vendor.a9ml.cellular.stock.state 2>/dev/null || true)
            if [ "$phase" = wait-owner ]; then
                case "$owner" in serving|failed*|ended*|ending) break ;; esac
            else
                [ "$ready" != 1 ] || break
                case "$state:$owner" in failed:*|*:failed*|*:ended*|*:ending) break ;; esac
            fi
            sleep 1
            n=$((n + 1))
        done
        prop owner vendor.a9ml.wifi.session.state
        prop stock_ready vendor.a9ml.cellular.stock.ready
        prop stock_state vendor.a9ml.cellular.stock.state
        prop cellular vendor.a9ml.cellular
        prop owner_ready vendor.a9ml.wifi.android.ready
        prop stock_phase vendor.a9ml.cellular.stock.phase
        prop stock_count vendor.a9ml.cellular.stock.count
        prop prep_service init.svc.a9ml-stock-radio-prepare
        prop modules_service init.svc.a9ml-cellular-prepare
        ;;
    opt-in)
        # Preserve the existing integrated 20 dBm cap before enabling clients.
        cap_rc=0
        timeout 3 /mnt/a9-wifi/tools/iw dev wlan0 set txpower limit 2000 >/dev/null 2>&1 || cap_rc=$?
        printf 'RC txpower %s\n' "$cap_rc"
        if [ "$cap_rc" -eq 0 ]; then
            setprop vendor.a9ml.cellular 1
            prop cellular vendor.a9ml.cellular
            printf 'ACTION cellular_optin 1\n'
        else
            printf 'ACTION cellular_optin 0\n'
        fi ;;
    monitor|snapshot)
        prop boot sys.boot_completed
        prop owner vendor.a9ml.wifi.session.state
        prop stock_ready vendor.a9ml.cellular.stock.ready
        prop stock_state vendor.a9ml.cellular.stock.state
        prop netmgr vendor.a9ml.cellular.stock.netmgr
        prop cellular vendor.a9ml.cellular
        prop owner_ready vendor.a9ml.wifi.android.ready
        prop stock_phase vendor.a9ml.cellular.stock.phase
        prop stock_count vendor.a9ml.cellular.stock.count
        prop prep_service init.svc.a9ml-stock-radio-prepare
        prop modules_service init.svc.a9ml-cellular-prepare
        # Finite IPC diagnostics only; no directory listings or raw socket tables.
        ipc_dir=0; ipc_socket=0
        if [ -d /dev/socket/qmux_radio ] && [ ! -L /dev/socket/qmux_radio ]; then ipc_dir=1; fi
        if [ -S /dev/socket/qmux_radio/ril_ipc ] && [ ! -L /dev/socket/qmux_radio/ril_ipc ]; then ipc_socket=1; fi
        printf 'PROP qtibus_directory %s\nPROP qtibus_socket %s\n' "$ipc_dir" "$ipc_socket"
        stock_pids
        if [ "$phase" = snapshot ]; then
            sim=$(getprop gsm.sim.state 2>/dev/null || true)
            printf '%s\n' "$sim" | awk -F, '{for(i=1;i<=2;i++){v=$i; if(v !~ /^(ABSENT|READY|NOT_READY|PIN_REQUIRED|PUK_REQUIRED|UNKNOWN|CARD_IO_ERROR|CARD_RESTRICTED|PERM_DISABLED|LOADED|NETWORK_LOCKED|PRESENT|DISABLED)$/)v="UNKNOWN"; print "GSM",i,v}}'
            ok=1
            if crash=$(timeout 12 logcat -b crash -d -v brief 2>/dev/null); then :; else ok=0; fi
            # Native tombstone process headers only; no tombstone/log text leaves
            # this pipeline. Counts include all retained crash-buffer history.
            printf '%s\n' "$crash" | awk -v ok="$ok" '
            />>> \/vendor\/bin\/hw\/qcrild( | <)/ {n["qcrild"]++}
            />>> \/vendor\/bin\/imsqmidaemon( | <)/ {n["imsqmidaemon"]++}
            />>> \/vendor\/bin\/imsdatadaemon( | <)/ {n["imsdatadaemon"]++}
            />>> \/vendor\/bin\/ims_rtp_daemon( | <)/ {n["ims_rtp_daemon"]++}
            />>> \/vendor\/bin\/imsrcsd( | <)/ {n["imsrcsd"]++}
            />>> \/system\/bin\/audioserver( | <)/ {n["audioserver"]++}
            />>> \/vendor\/bin\/hw\/android.hardware.audio.service-aidl.a9( | <)/ {n["audio_hal"]++}
            END {split("qcrild imsqmidaemon imsdatadaemon ims_rtp_daemon imsrcsd audioserver audio_hal",t," ");for(i=1;i<=7;i++)print "CRASH",t[i],n[t[i]]+0,ok}'
            unset crash sim
        fi ;;
    cleanup)
        setprop vendor.a9ml.cellular 0
        n=0; stopped=0
        while [ "$n" -lt 20 ]; do
            stopped=1
            for s in vendor.qcrild vendor.qcrild2 vendor.qcrild3 vendor.imsqmidaemon vendor.imsdatadaemon vendor.ims_rtp_daemon vendor.imsrcsservice; do
                state=$(getprop "init.svc.$s" 2>/dev/null || true)
                case "$state" in stopped|'') ;; *) stopped=0 ;; esac
            done
            # Confirm process absence as well as init status; empty/denied
            # service properties alone must not certify stopped clients.
            remaining=$(pidof qcrild imsqmidaemon imsdatadaemon ims_rtp_daemon imsrcsd 2>/dev/null || true)
            [ -z "$remaining" ] || stopped=0
            [ "$stopped" -ne 1 ] || break
            sleep 1; n=$((n + 1))
        done
        printf 'ACTION clients_stopped %s\n' "$stopped"
        # Existing owner is left in charge. No kill, remoteproc stop, storage
        # teardown or protected-store action is performed by this hook.
        ;;
    *) exit 2 ;;
esac
