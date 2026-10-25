#!/system/bin/sh
# Attended D2: one fixed low-level earpiece tone, then verified codec reset and
# ADSP stop. D1 ownership/ledger containment is preserved. Only the frozen
# d2-tone helper opens the admitted ALSA control/PCM nodes; never an audio HAL.
# All paths rooted in R only for explicit offline rehearsal; production R=''.
set -eu
R= POST=10 DEAD=300 APRLIM=40 CARDLIM=20 SNDEVERY=1
if [ "${1:-}" = --rehearse ]; then
    [ "${ADSP_D2_REHEARSAL:-}" = 1 ] || exit 2
    R=$2; POST=1; APRLIM=4; CARDLIM=4; SNDEVERY=1; shift 2
    DEAD=${ADSP_D2_REHEARSAL_DEADLINE:-300}
fi
MODE=${1:-}; TUPLE=${2:-}
case "$TUPLE" in ''|*[!a-f0-9]*) exit 2;; esac
[ ${#TUPLE} = 64 ] || exit 2
B=$R/data/local/tmp/adsp-d2
S=$R/sys; P=$R/proc
RP= LOGPID= STREAMPID= DONE=0 VERIFIED=0 BASELINED=0 OWNED=0 OP=0 SNDN=0 PLAYING=0 KREADY=0
K=$R/sys/bus/platform/devices/a9-d2-codec
BOOT=${3:-}
O=$R/data/local/tmp/adsp-d2-ledger/$BOOT
SELF=$0
REHEARSE=
[ -z "$R" ] || REHEARSE="--rehearse $R"
fail() { echo "D2-FAIL $*" >&2; exit 1; }
request_stop() { : > "$B/stop.$BOOT.$TUPLE"; echo abort > "$B/abort.$BOOT.$TUPLE"; }
now() { IFS=' ' read -r uptime unused < "$P/uptime"; echo "${uptime%%.*}"; }
gate() {
    [ "$(cat "$S/module/qcom_q6v5_pas/parameters/a9_adsp_manual_start")" = Y ] || fail gate
    [ "$(cat "$RP/state")" = "$1" ] || fail state
    [ "$(cat "$RP/recovery")" = disabled ] || fail recovery
    [ "$(cat "$RP/coredump")" = disabled ] || fail coredump
    [ "$(cat "$RP/firmware")" = qcom/sm6115/hisense/hlte556n/adsp.mdt ] || fail firmware-name
}
identify() {
    count=0; RP=
    for p in "$S"/class/remoteproc/remoteproc*; do
        [ -e "$p/state" ] || continue
        # D0 admits exactly one published remoteproc, identified by OF identity.
        count=$((count+1)); RP=$p
        of=$(readlink -f "$p/device/of_node") || fail of-node
        [ "$of" = "$S/firmware/devicetree/base/soc@0/remoteproc@a400000" ] || fail wrong-node
        tr '\000' '\n' < "$of/compatible" | grep -qx qcom,sm6115-adsp-pas || fail compatible
        [ "$(cat "$of/status")" = okay ] || fail dt-status
    done
    [ "$count" = 1 ] || fail duplicate-or-missing
    VERIFIED=1
}
# Only the run executor dispatches load/start/normal-stop. Register BEFORE
# forking; a missing completion is permanently ambiguous, never PID evidence.
operation() {
    limit=$1; kind=$2; shift 2
    [ ! -e "$B/abort.$BOOT.$TUPLE" ] || return 1
    OP=$((OP+1)); job=$O/op.$OP
    mkdir "$job" || return 1
    # Fresh session and ignored signals avoid Android mksh quitenv() group TERM.
    # No timeout/kill: a syscall returning is the only completion evidence.
    setsid sh "$SELF" $REHEARSE writer "$TUPLE" "$BOOT" "$job" "$kind" "$@" </dev/null >> "$O/writers.log" 2>&1 &
    end=$(($(now)+limit))
    until [ -f "$job/done" ]; do
        [ "$(now)" -lt "$end" ] || { request_stop; return 1; }
        [ ! -e "$B/abort.$BOOT.$TUPLE" ] || { request_stop; return 1; }
        if [ "$kind" = tone ]; then
            sed -n "/$MARK/,$ p" "$B/kmsg.raw" > "$B/kmsg.log"
            gate running; ready || fail glink-rpmsg-lost
            apr_ready || fail apr-lost; card_ok || fail card-lost; health
        fi
        sleep 0.1
    done
    [ "$(cat "$job/done")" = 0 ]
}
quiescent() {
    # Only terminal run paths (success or EXIT) seal dispatch; never containment.
    [ -f "$O/sealed" ] || return 1
    for job in "$O"/op.*; do
        [ ! -d "$job" ] || [ -f "$job/done" ] || return 1
    done
}
stop_bounded() {
    # The guardian alone calls this, AFTER sealing and all writer completions.
    quiescent || return 1
    # Kernel containment is a registered operation AFTER all helper/ioctl
    # completions. Never race STOP against a helper or unresolved route write.
    if [ -d "$S/module/snd_soc_wcd937x" ] || [ -e "$K" ]; then
        [ -f "$K/d2_stop" ] || return 1
        [ "$(readlink -f "$K/of_node")" = "$S/firmware/devicetree/base/a9-d2-codec" ] || return 1
        [ "$(readlink -f "$K/driver")" = "$S/bus/platform/drivers/wcd937x_codec" ] || return 1
    fi
    if [ -e "$K/d2_stop" ] && [ ! -d "$O/kernel-stop" ]; then
        job=$O/kernel-stop; mkdir "$job" || return 1
        setsid sh "$SELF" $REHEARSE writer "$TUPLE" "$BOOT" "$job" kernel-stop "$K/d2_stop" </dev/null >> "$O/writers.log" 2>&1 &
        end=$(($(now)+5))
        until [ -f "$job/done" ]; do [ "$(now)" -lt "$end" ] || return 1; sleep 0.1; done
    fi
    if [ -d "$O/kernel-stop" ]; then
        [ -f "$O/kernel-stop/done" ] && [ "$(cat "$O/kernel-stop/done")" = 0 ] || return 1
        [ "$(cat "$K/d2_state")" = RESET ] && [ "$(cat "$K/d2_ready")" = 0 ] || return 1
    fi
    identify
    state=$(cat "$RP/state") || return 1
    case "$state" in
    offline) ;;
    running)
        job=$O/stop
        mkdir "$job" || return 1
        setsid sh "$SELF" $REHEARSE writer "$TUPLE" "$BOOT" "$job" stop "$RP/state" </dev/null >> "$O/writers.log" 2>&1 &
        end=$(($(now)+12))
        until [ -f "$job/done" ]; do
            [ "$(now)" -lt "$end" ] || return 1
            sleep 0.1
        done
        [ "$(cat "$job/done")" = 0 ] || return 1;;
    *) return 1;;
    esac
    gate offline
}
await_guard() {
    end=$(($(now)+16))
    until [ -f "$O/result" ]; do
        [ "$(now)" -lt "$end" ] || return 1
        sleep 0.1
    done
}
cleanup() {
    trap - EXIT
    trap '' HUP INT TERM
    if [ "$OWNED" = 1 ]; then
        # No path can return to dispatch after this seal, including signals.
        [ "$DONE" = 1 ] || request_stop
        echo sealed > "$O/sealed"
        if await_guard && [ "$(cat "$O/result")" = OFFLINE ]; then
            [ "$DONE" = 1 ] || echo D2-FAILED-OFFLINE
        else
            echo CONTAINMENT-UNVERIFIED
        fi
    fi
    # Readers are external processes. Never TERM a forked phone shell, and
    # cancel the independent guardian by protocol, never by killing it.
    for p in "$STREAMPID" "$LOGPID"; do
        [ -z "$p" ] || kill -KILL "$p" 2>/dev/null || :
    done
}
interrupted() {
    trap '' HUP INT TERM
    request_stop
    exit "$1"
}
# BEGIN-MATCHER
# Exclude ONE exact baseline record: Android's own E Ink driver asks for an optional
# template that is absent (ENOENT). The whole line must equal it (-x), so no variant
# is excluded. Its SUBSYSTEM=/DEVICE= continuation lines cannot match the fault
# pattern, so they need no exclusion. No awk (not a toybox applet here). Fail closed:
# the filtered output plus the excluded lines must account for every input line.
BENIGN_RE='^[0-9]+,[0-9]+,[0-9]+,[^;]*;a9-eink eink-display: Direct firmware load for hisense/a9-ctrl-template[.]bin failed with error -2$'
kernel_fault_in() {
    kf=$1.filtered
    grep -vxE "$BENIGN_RE" "$1" > "$kf" || [ $? = 1 ] || fail matcher-filter
    total=$(grep -c "" "$1") || { [ $? = 1 ] || fail matcher-count; }
    kept=$(grep -c "" "$kf") || { [ $? = 1 ] || fail matcher-count; }
    benign=$(grep -cxE "$BENIGN_RE" "$1") || { [ $? = 1 ] || fail matcher-count; }
    for kf_count in "$total" "$kept" "$benign"; do
        case "$kf_count" in *[!0-9]*|'') fail matcher-self-check;; esac
    done
    [ $((kept+benign)) = "$total" ] || fail matcher-self-check
    # The unchanged D0 pattern first, then D2's APR/PDR/Q6/ASoC/audio-PD tokens
    # (specific tokens only: no bare apr/snd word). Status 0 = a fault was found.
    grep -qiE 'panic|Oops:|BUG:|watchdog.*(bite|reset|crash)|remoteproc.*crash|PDM:.*crash|fatal|SSR|subsys.*restart|authentication.*fail|auth.*(fail|error)|(fail|error).*auth|SMMU.*(fault|error)|SMEM.*(fail|error)|glink.*(fail|error)|thermal.*(critical|emergency)|a9.eink.*(fault|abort|error|failed)|fp9929a.*(fault|expired)|reboot:|Linux version' "$kf" && return 0
    rc=$?; [ "$rc" = 1 ] || fail matcher-grep
    grep -qiE 'context fault|iommu.*(fault|error)|pdr.*(fail|error|timeout)|pd.mapper.*(fail|error)|servreg.*(fail|error|timeout)|Failed to add apr|Failed to init PDR|aprsvc.*(fail|error|timeout)|qcom-apr.*(fail|error)|apr_audio_svc.*(fail|error)|audio_pd.*(fail|error|crash|down)|q6(core|afe|asm|adm|routing)[a-z0-9_-]*.*(fail|error|timeout)|ASoC.*(fail|error)|snd-soc.*(fail|error)|sm8250.*(fail|error)|sysmon.*(fail|error)|intent request timed out|timed out|unable to find channel|invalid (channel|intent) id|channel not found|non-existing channel|Invalid open ack|unhandled rx cmd|Insufficient space in intent|failed to store remote intent|Unable to insert channel|Can.t remove GLINK|glink.*(timed out|timeout|invalid|unable|not found|unhandled|insufficient)' "$kf" && return 0
    rc=$?; [ "$rc" = 1 ] || fail matcher-grep
    grep -qiE 'soundwire.*(fail|error|timeout|clash|parity|underrun)|swrm.*(fail|error|timeout|clash|parity)|wcd937.*(fault|fail|error|timeout|over.current|short|thermal)|a9.d2.*(fault|fail|error|timeout)|RX.*(FIFO.*(error|under|over)|overflow)|WARNING:' "$kf" && return 0
    rc=$?; [ "$rc" = 1 ] || fail matcher-grep
    return 1
}
# END-MATCHER
health() {
    [ "$(cat "$P/sys/kernel/random/boot_id")" = "$BOOT" ] || fail reset
    kill -0 "$LOGPID" 2>/dev/null || fail log-reader
    kill -0 "$STREAMPID" 2>/dev/null || fail host-log-stream
    [ ! -e "$B/abort.$BOOT.$TUPLE" ] || fail host-abort
    [ "$(wc -c < "$B/kmsg.log")" -lt 16777216 ] || fail log-overflow
    # Use marked continuous logs. Any new crash/reset/SSR/auth/bus/thermal or
    # display fault is a stop condition, including faults in other subsystems.
    kernel_fault_in "$B/kmsg.log" && fail kernel-fault
    [ ! -e "$B/deadman-fired" ] || fail deadman
    # Baseline admission requires E Ink gates closed and stable thermal/display.
    for g in a9_eink/enable panel_hisense_hlte556n_eink/enable fp9929a/allow_power_on; do
        [ "$(cat "$S/module/${g%/*}/parameters/${g#*/}")" = N ] || fail display-gate
    done
    displays=0
    for p in "$S"/class/drm/card*-*/eink/status; do
        [ -f "$p" ] || continue
        displays=$((displays+1))
        cat "$p" >> "$B/display.log"
        set -- $(cat "$p")
        while [ "$#" -gt 1 ]; do
            case "$1" in rail_fault|stall_fault|commit_fail|aborts) [ "$2" = 0 ] || fail display-fault;; esac
            shift
        done
    done
    [ "$displays" = 1 ] || fail display-identity
    zones=0
    : > "$B/thermal.cur" || fail thermal-identity
    for z in "$S"/class/thermal/thermal_zone*; do
        [ -d "$z" ] || continue
        [ -f "$z/temp" ] && [ -r "$z/temp" ] || fail thermal-read
        printf '%s\n' "${z##*/}" >> "$B/thermal.cur" || fail thermal-identity
        zones=$((zones+1))
        temp=$(cat "$z/temp") || fail thermal-read
        case "$temp" in ''|*[!0-9]*) fail thermal-value;; esac
        [ "$temp" -lt 70000 ] || fail thermal-limit
        if [ "$BASELINED" = 0 ]; then
            printf '%s\n' "$temp" > "$B/baseline.${z##*/}" || fail thermal-identity
        else
            [ -f "$B/baseline.${z##*/}" ] && [ -r "$B/baseline.${z##*/}" ] || fail thermal-identity
            old=$(cat "$B/baseline.${z##*/}") || fail thermal-identity
            case "$old" in ''|*[!0-9]*) fail thermal-identity;; esac
            [ "$temp" -lt "$((old+15000))" ] || fail thermal-rise
        fi
    done
    [ "$zones" -gt 0 ] || fail missing-thermal
    if [ "$BASELINED" = 0 ]; then
        cp "$B/thermal.cur" "$B/thermal.baseline" || fail thermal-identity
    else
        # Do not accept a surviving subset. Both inventories must remain regular,
        # readable and equal, including when an entire baselined zone disappears.
        [ -f "$B/thermal.baseline" ] && [ -r "$B/thermal.baseline" ] || fail thermal-identity
        [ -f "$B/thermal.cur" ] && [ -r "$B/thermal.cur" ] || fail thermal-identity
        cmp -s "$B/thermal.cur" "$B/thermal.baseline" || fail thermal-identity
    fi
    BASELINED=1
    # D2: nothing outside the frozen baseline + modules.d2 may ever be loaded
    # (only this D2 profile admits WCD/RX/TX macros/SoundWire/LPI; no ESS/amp).
    cut -d ' ' -f 1 "$P/modules" | sort -u > modules.cur
    sort -u allowed.modules modules.cur > modules.union
    cmp -s modules.union allowed.modules || fail unexpected-module
    # Before APR/card probes complete, audio endpoints may be enumerating.
    # Once ready, compare exact frozen bus inventory (never accept extra slaves).
    if [ "$KREADY" = 1 ]; then
        topology
        [ "$(cat "$K/d2_fault")" = 0 ] || fail codec-fault
        [ "$(cat "$K/d2_ready")" = 1 ] || fail codec-not-ready
        case "$(cat "$K/d2_state")" in
            SAFE) ;;
            EAR) [ "$PLAYING" = 1 ] || fail unexpected-ear-power;;
            *) fail codec-state;;
        esac
    elif [ "$KREADY" = 2 ]; then
        [ "$(cat "$K/d2_fault")" = 0 ] || fail codec-fault
        [ "$(cat "$K/d2_ready")" = 0 ] && [ "$(cat "$K/d2_state")" = RESET ] || fail codec-reset-lost
    fi
    # Sound fd ownership is observed periodically: no process may hold any /dev/snd node open
    # (an Android audio HAL opening the new card would be an unplanned PCM/mixer user).
    # Every health call observes ownership, including while the helper runs: it cannot prove that
    # no transient open happened between samples. One ls per process (never one giant
    # glob). Fail closed: every surviving process's fd directory must be READ successfully
    # (3 tries for fds closing mid-read); a read failure for a process that still exists
    # refuses; only a process that has vanished is skipped; an empty scan refuses.
    SNDN=$((SNDN+1))
    if [ $((SNDN % SNDEVERY)) = $((1 % SNDEVERY)) ]; then
        scanned=0; : > snd.fd
        for d in "$P"/[0-9]*/fd; do
            [ -d "$d" ] || continue
            tries=0; got=0
            while [ "$tries" -lt 3 ]; do
                tries=$((tries+1))
                if ls -l "$d" > snd.one 2>/dev/null; then got=1; break; fi
                [ -d "$d" ] || { got=2; break; }
            done
            [ "$got" != 0 ] || fail snd-scan-unreadable
            [ "$got" = 1 ] || continue
            # Only the one frozen native helper may own sound descriptors.
            snd_rc=0
            grep -q '/dev/snd/' snd.one || snd_rc=$?
            case "$snd_rc" in 0|1) ;; *) fail snd-scan;; esac
            if [ "$snd_rc" = 0 ]; then
                [ "$PLAYING" = 1 ] || fail snd-device-open
                hp=$(cat "$O"/op.*/helper.pid 2>/dev/null) || fail helper-owner
                case "$hp" in ''|*[!0-9]*) fail helper-owner;; esac
                [ "${d%/fd}" = "$P/$hp" ] || fail foreign-snd-owner
                [ "$(readlink -f "$P/$hp/exe")" = "$B/d2-tone" ] || fail helper-executable
                grep '/dev/snd/' snd.one > snd.nodes || fail snd-scan
                grep -vE '/dev/snd/(controlC0|pcmC0D0p)$' snd.nodes > snd.unexpected || [ $? = 1 ] || fail snd-scan
                [ ! -s snd.unexpected ] || fail helper-snd-node
            fi
            cat snd.one >> snd.fd; scanned=$((scanned+1))
        done
        [ "$scanned" -gt 0 ] || fail snd-scan
        n=$(grep -c '/dev/snd/' snd.fd) || { [ $? = 1 ] || fail snd-scan; }
        case "$n" in ''|*[!0-9]*) fail snd-scan;; esac
        [ "$PLAYING" = 1 ] || [ "$n" = 0 ] || fail snd-device-open
    fi
}
ready() {
    # Both devices must descend from the identified ADSP, not RPM/another DSP.
    found=0
    for p in "$S"/bus/rpmsg/devices/*; do
        [ -e "$p" ] || continue
        real=$(readlink -f "$p") || fail rpmsg-path
        case "$real" in
            *a400000.remoteproc*glink*|*remoteproc@a400000*glink*)
                [ "$(readlink -f "$p/driver")" = "$S/bus/rpmsg/drivers/qcom_smd_qrtr" ] && found=1;;
        esac
    done
    [ "$found" = 1 ] || return 1
    [ -d "$S/module/qrtr_smd" ] && [ -d "$S/module/qcom_pd_mapper" ] || return 1
    count=0
    for p in "$S"/bus/auxiliary/devices/qcom_common.pd-mapper.*; do
        [ -e "$p" ] || continue
        real=$(readlink -f "$p") || return 1
        case "$real" in *a400000.remoteproc*|*remoteproc@a400000*) ;; *) return 1;; esac
        [ "$(readlink -f "$p/driver")" = "$S/bus/auxiliary/drivers/qcom_pd_mapper.qcom-pdm-mapper" ] || return 1
        count=$((count+1))
    done
    [ "$count" = 1 ]
}
# D2: the four APR services (aprbus devices "aprsvc:<node>:<domain>:<svc>",
# domain 4 = ADSP; service@3 q6core, @4 q6afe, @7 q6asm, @8 q6adm), each below the
# identified ADSP and bound to its q6 driver, plus the one "apr_audio_svc" rpmsg
# channel bound to qcom,apr. Any other service device is a hard stop (e.g. voice).
# Names come from drivers/soc/qcom/apr.c and the sm6115.dtsi tree, NOT from the D0
# log (APR was disabled in D0): first-run mismatch fails closed.
apr_ready() {
    found=0
    for p in "$S"/bus/rpmsg/devices/*; do
        [ -e "$p" ] || continue
        [ "$(cat "$p/name" 2>/dev/null)" = apr_audio_svc ] || continue
        real=$(readlink -f "$p") || return 1
        case "$real" in
            *a400000.remoteproc*glink*|*remoteproc@a400000*glink*) ;;
            *) fail apr-channel-owner;;
        esac
        [ "$(readlink -f "$p/driver")" = "$S/bus/rpmsg/drivers/qcom,apr" ] || return 1
        found=$((found+1))
    done
    [ "$found" = 1 ] || return 1
    count=0
    for p in "$S"/bus/aprbus/devices/*; do
        [ -e "$p" ] || continue
        case "${p##*/}" in
            aprsvc:service:4:3) want=qcom-q6core;;
            aprsvc:service:4:4) want=qcom-q6afe;;
            aprsvc:service:4:7) want=qcom-q6asm;;
            aprsvc:service:4:8) want=qcom-q6adm;;
            *) fail apr-unexpected-service;;
        esac
        real=$(readlink -f "$p") || return 1
        case "$real" in *a400000.remoteproc*|*remoteproc@a400000*) ;; *) fail apr-service-owner;; esac
        [ "$(readlink -f "$p/driver")" = "$S/bus/aprbus/drivers/$want" ] || return 1
        count=$((count+1))
    done
    [ "$count" = 4 ]
}
# Exactly one playback-only MM1 PCM. Outside the one owned native helper
# operation it must be closed. Extra capture/card/sound nodes refuse the run.
topology() {
    [ -f "$B/soundwire.devices" ] && [ -s "$B/soundwire.devices" ] || fail missing-soundwire-manifest
    : > "$B/soundwire.current"
    for p in "$S"/bus/soundwire/devices/*; do
        [ -e "$p" ] || continue
        dev=${p##*/}
        # sysfs embeds runtime controller/link IDs. Bind the stable manufacturer,
        # part/class/unique tuple and its exact controller OF ownership instead.
        echo "$dev" | grep -qE '^sdw:[0-9a-f]+:0:0217:010a:00:[34]$' || fail soundwire-identity
        stable=$(echo "$dev" | cut -d : -f 4-)
        echo "$stable" >> "$B/soundwire.current"
        case "$stable" in
            0217:010a:00:3) node=soundwire@a740000/codec@0,3;;
            0217:010a:00:4) node=soundwire@a610000/codec@0,4;;
            *) fail soundwire-identity;;
        esac
        [ "$(readlink -f "$p/of_node")" = "$S/firmware/devicetree/base/soc@0/$node" ] || fail soundwire-owner
        [ "$(readlink -f "$p/driver")" = "$S/bus/soundwire/drivers/wcd9370-codec" ] || fail soundwire-driver
    done
    sort "$B/soundwire.current" > "$B/soundwire.sorted"
    cmp -s "$B/soundwire.sorted" "$B/soundwire.devices" || fail soundwire-identity
    for dev in a610000.soundwire a740000.soundwire a600000.codec a730000.codec a6a9000.clock-controller a7ec000.clock-controller; do
        case "$dev" in
            a610000.soundwire|a740000.soundwire) drv=qcom-soundwire; node=soundwire@${dev%%.*};;
            a600000.codec) drv=rx_macro; node=codec@a600000;;
            a730000.codec) drv=va_macro; node=codec@a730000;;
            *) drv=lpasscc-sm6115; node=clock-controller@${dev%%.*};;
        esac
        devpath=$S/bus/platform/devices/$dev
        [ "$(readlink -f "$devpath/driver")" = "$S/bus/platform/drivers/$drv" ] || fail audio-platform-driver
        [ "$(readlink -f "$devpath/of_node")" = "$S/firmware/devicetree/base/soc@0/$node" ] || fail audio-platform-owner
    done
    [ -e "$S/bus/platform/devices/a7c0000.pinctrl" ] || fail lpi-missing
    [ "$(readlink -f "$S/bus/platform/devices/a7c0000.pinctrl/driver")" = "$S/bus/platform/drivers/qcom-sm6115-lpass-lpi-pinctrl" ] || fail lpi-driver
    [ "$(readlink -f "$K/of_node")" = "$S/firmware/devicetree/base/a9-d2-codec" ] || fail codec-of-identity
    [ "$(readlink -f "$K/driver")" = "$S/bus/platform/drivers/wcd937x_codec" ] || fail codec-driver
}
card_ok() {
    [ -d "$P/asound/card0" ] || return 1
    [ ! -e "$P/asound/card1" ] || fail extra-card
    [ "$(cat "$P/asound/card0/id")" = A9D2Earpiece ] || fail card-id
    [ -f "$P/asound/pcm" ] || return 1
    [ "$(grep -c '' "$P/asound/pcm")" = 1 ] || fail pcm-count
    grep -qE '^00-00: MultiMedia1.*playback 1' "$P/asound/pcm" || fail pcm-identity
    grep -q 'capture' "$P/asound/pcm" && fail capture-pcm
    n=0
    for p in "$S"/class/sound/*; do
        [ -e "$p" ] || continue
        case "${p##*/}" in card0|controlC0|timer|pcmC0D0p) n=$((n+1));; *) fail unexpected-sound-node;; esac
    done
    [ "$n" = 4 ] || return 1
    streams=0
    for p in "$P"/asound/card0/pcm*/sub*/status; do
        [ -f "$p" ] || continue
        streams=$((streams+1))
        [ "$p" = "$P/asound/card0/pcm0p/sub0/status" ] || fail unexpected-substream
        if [ "$PLAYING" = 0 ]; then [ "$(cat "$p")" = closed ] || fail stream-open
        else
            case "$(head -n 1 "$p")" in closed|'state: SETUP'|'state: PREPARED'|'state: RUNNING'|'state: DRAINING') ;; *) fail pcm-state;; esac
        fi
    done
    [ "$streams" = 1 ]
}
# D2: one-time evidence snapshot (reads only) into $B/audio.log and stdout.
audio_record() {
    {
        echo "D2-EVIDENCE $1"
        echo "-- /proc/asound/cards"; cat "$P/asound/cards"
        echo "-- /proc/asound/pcm"; cat "$P/asound/pcm"
        echo "-- /sys/class/sound"; ls "$S/class/sound"
        echo "-- aprbus"; ls "$S/bus/aprbus/devices"
        echo "-- rpmsg"; ls "$S/bus/rpmsg/devices"
        echo "-- modules"; cat "$P/modules"
    } > "$B/audio.log.$1" 2>&1 || :
    cat "$B/audio.log.$1"
    cat "$B/audio.log.$1" >> "$B/audio.log"
}
case "$MODE" in
boot-id) cat "$P/sys/kernel/random/boot_id"; exit;;
writer)
    # Private child entry: parent has already registered this operation.
    trap '' HUP INT TERM
    job=$4; kind=$5; shift 5
    [ "$(cat "$O/tuple")" = "$TUPLE" ] && [ -d "$job" ] || exit 1
    rc=1
    if [ "$(cat "$P/sys/kernel/random/boot_id")" = "$BOOT" ]; then
        if [ "$kind" = stop ] || [ "$kind" = kernel-stop ] || [ ! -e "$B/abort.$BOOT.$TUPLE" ]; then
            case "$kind" in
            load) if insmod "$@"; then rc=0; fi;;
            tone)
                "$B/d2-tone" --run "$B/stop.$BOOT.$TUPLE" > "$job/tone.log" 2>&1 &
                helper=$!; echo "$helper" > "$job/helper.pid"
                if wait "$helper"; then
                    if grep -qx D2-TONE-CLOSED-SAFE "$job/tone.log"; then rc=0; fi
                fi;;
            kernel-stop) if sh -c 'printf 1 > "$1"' sh "$1"; then rc=0; fi;;
            start|stop) if sh -c "printf $kind > \"\$1\"" sh "$1"; then rc=0; fi;;
            esac
        fi
    fi
    echo "$rc" > "$job/done.tmp"; mv "$job/done.tmp" "$job/done"
    exit "$rc";;
guard)
    # A fresh session, stdin /dev/null and file-only output survive the adb
    # transport/group. HUP/INT/TERM never cancel containment or its sleeper.
    trap '' HUP INT TERM
    [ "$(cat "$O/tuple")" = "$TUPLE" ] || exit 1
    deadline=$(($(now)+DEAD))
    echo $$ > "$O/guard.pid"
    echo ready > "$O/guard-ready"
    until [ -e "$B/abort.$BOOT.$TUPLE" ] || [ -f "$O/sealed" ]; do
        if [ "$(now)" -ge "$deadline" ]; then
            echo fired > "$B/deadman-fired"
            request_stop
            break
        fi
        sleep 0.1
    done
    # Bound the transfer to attended Power/PON2 when an executor was killed
    # without sealing, or a kernel operation has not returned. OFFLINE alone
    # is never evidence. A later completion cannot revise this failed outcome.
    deadline=$(($(now)+14))
    until quiescent; do
        [ "$(now)" -lt "$deadline" ] || break
        sleep 0.1
    done
    if quiescent && [ "$(cat "$P/sys/kernel/random/boot_id")" = "$BOOT" ] &&
        (stop_bounded); then
        echo OFFLINE > "$O/result.tmp"
    else
        echo 'REQUIRED, UNACKNOWLEDGED: attended PON2/Power then golden restore; no retry; guardian remains active' > "$O/fallback"
        echo CONTAINMENT-UNVERIFIED > "$O/result.tmp"
    fi
    mv "$O/result.tmp" "$O/result"
    if [ "$(cat "$O/result")" != OFFLINE ]; then
        # Report/handoff is bounded, but an unacknowledged fallback does not
        # disarm this owner. Keep watching late completions after transport or
        # executor death. The original result can NEVER be upgraded to success.
        while [ "$(cat "$P/sys/kernel/random/boot_id")" = "$BOOT" ]; do
            if quiescent && [ ! -d "$O/stop" ]; then
                if (stop_bounded); then echo OFFLINE > "$O/late-result"; exit; fi
            elif quiescent && [ -f "$O/stop/done" ]; then
                if (identify; gate offline); then echo OFFLINE > "$O/late-result"; exit; fi
            fi
            sleep 1
        done
    fi
    exit;;
contain|stop)
    # Request only: the original detached guardian exclusively owns failure
    # stop. Never certify while an executor can still dispatch or finish start.
    [ "$(cat "$P/sys/kernel/random/boot_id")" = "$BOOT" ] || fail reset-or-unknown-boot
    # Both automatic containment and explicit STOP veto executor preflight.
    # Missing owner remains unverified; a delayed preflight can never mutate.
    [ "$(cat "$B/tuple")" = "$TUPLE" ] || fail tuple
    request_stop
    [ "$(cat "$O/tuple")" = "$TUPLE" ] || fail owner-tuple
    trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM
    request_stop
    await_guard || fail containment-owner-pending
    [ "$(cat "$O/result")" = OFFLINE ] || fail fallback-required
    quiescent || fail executor-pending
    identify; gate offline
    echo D2-CONTAINED-OFFLINE; exit;;
run) ;;
*) exit 2;;
esac
[ "$(id -u)" = 0 ] || fail root
[ "$(cat "$B/tuple")" = "$TUPLE" ] || fail tuple
cd "$B"
[ "$(sha256sum SHA256SUMS | cut -d ' ' -f 1)" = "$TUPLE" ] || fail frozen-manifest
sha256sum -c SHA256SUMS >/dev/null || fail bundle-hash
[ -x "$B/d2-tone" ] || fail missing-tone-helper
[ ! -e "$B/stop.$BOOT.$TUPLE" ] || fail prior-stop
# This immutable manifest also hashes the admitted live boot partition, kernel
# config, vendor loader/service files, DT properties and all firmware/module files.
sha256sum -c live.sha256 >/dev/null || fail live-tuple
while read -r path destination; do
    [ "$(readlink "$R$path")" = "$destination" ] || fail live-symlink
done < live.symlinks
[ "$(zcat "$P/config.gz" | sha256sum | cut -d ' ' -f 1)" = "$(cat config.sha256)" ] || fail live-config
bytes=$(cat boot.bytes)
case "$bytes" in ''|*[!0-9]*) fail boot-length;; esac
[ "$bytes" -gt 4096 ] && [ "$bytes" -le 100663296 ] || fail boot-length
[ "$(head -c "$bytes" "$R/dev/block/by-name/boot_b" | sha256sum | cut -d ' ' -f 1)" = "$(cat boot.sha256)" ] || fail live-boot
[ "$(uname -r)" = "$(cat kernel.release)" ] || fail kernel-release
[ -z "$(cat "$P/sys/kernel/modprobe")" ] || fail kernel-autoload-helper
data_mounts=0
while read -r device mountpoint filesystem options unused; do
    [ "$mountpoint" = /data ] || continue
    case "$device" in /dev/block/ram0|/dev/ram0) ;; *) fail persistent-data;; esac
    [ "$filesystem" = ext4 ] || fail data-filesystem
    data_mounts=$((data_mounts+1))
done < "$P/mounts"
[ "$data_mounts" = 1 ] || fail data-mount
for directory in /data/apex/active /data/adb/modules /data/adb/modules_update; do
    [ ! -d "$R$directory" ] || [ -z "$(ls -A "$R$directory")" ] || fail unadmitted-overlay
done
[ "$(cat "$S/module/firmware_class/parameters/path")" = /data/local/tmp/a9fw ] || fail firmware-path
# force_sysfs_fallback is a SYSCTL (kernel.firmware_config), present only when
# CONFIG_FW_LOADER_USER_HELPER=y; it is never a firmware_class module parameter.
# Present: must be 0. Absent: accepted only if the live config (hash-bound to the
# frozen snapshot above) proves the user helper is not built. Anything else fails.
FWF=$P/sys/kernel/firmware_config/force_sysfs_fallback
if [ -e "$FWF" ]; then
    [ "$(cat "$FWF")" = 0 ] || fail firmware-fallback
else
    [ "$(zcat "$P/config.gz" | grep -c '^# CONFIG_FW_LOADER_USER_HELPER is not set$')" = 1 ] || fail firmware-fallback
fi
[ ! -e "$S/module/firmware_class/parameters/force_sysfs_fallback" ] || [ "$(cat "$S/module/firmware_class/parameters/force_sysfs_fallback")" = N ] || fail firmware-fallback
# Only the frozen admitted baseline may have loaded modules before publication.
cut -d ' ' -f 1 "$P/modules" | sort > modules.now
cmp modules.now baseline.modules || fail loaded-modules
ps -A -o ARGS > processes
grep -iE 'pd.mapper|protection.domain|rmtfs|adsprpc|fastrpc|remoteproc.*(start|recover)' processes && fail competing-service
[ ! -d "$S/module/qcom_q6v5_pas" ] || fail prior-binding
# D2: no audio stack of any kind before the attempt.
[ ! -e "$P/asound" ] || fail prior-sound
[ ! -d "$S/bus/aprbus" ] || fail prior-apr
# D2: allowed module set = admitted baseline + the frozen D2 sidecar, normalised.
{ cat baseline.modules; while read -r ko; do echo "${ko%.ko}" | tr '-' '_'; done < modules.d2; } | sort -u > allowed.modules
for p in "$S"/class/remoteproc/remoteproc*/state; do [ ! -e "$p" ] || fail prior-remoteproc; done
[ "$(cat "$P/sys/kernel/random/boot_id")" = "$BOOT" ] || fail original-boot
case "$BOOT" in ''|*[!a-f0-9-]*) fail boot-id;; esac
mkdir -p "$R/data/local/tmp/adsp-d2-ledger"
mkdir "$R/data/local/tmp/adsp-d2-ledger/$BOOT" || fail consumed-attempt
echo "$TUPLE" > "$O/tuple"
sync
OWNED=1
trap 'status=$?; cleanup; exit "$status"' EXIT
trap 'interrupted 129' HUP; trap 'interrupted 130' INT; trap 'interrupted 143' TERM
setsid sh "$SELF" $REHEARSE guard "$TUPLE" "$BOOT" </dev/null > "$O/guard.log" 2>&1 &
deadline=$(($(now)+5))
until [ -f "$O/guard-ready" ]; do
    [ "$(now)" -lt "$deadline" ] || fail guardian-start
    sleep 0.1
done
# cat /dev/kmsg starts at oldest record. Drain into a baseline file first, then
# use a unique acknowledged marker to select the new observation interval.
cat "$R/dev/kmsg" > "$B/kmsg.raw" & LOGPID=$!
tail -n +1 -f "$B/kmsg.raw" & STREAMPID=$!
MARK="ADSP-D2-$BOOT-$TUPLE"
printf '<6>%s\n' "$MARK" > "$R/dev/kmsg"
i=0
deadline=$(($(now)+5))
until grep -q "$MARK" "$B/kmsg.raw"; do
    i=$((i+1)); [ "$i" -lt 5 ] && [ "$(now)" -lt "$deadline" ] || fail marker; sleep 1
done
while read -r ko; do
    [ ! -e "$B/abort.$BOOT.$TUPLE" ] || fail host-abort
    case "$ko" in ''|*/*|*[!a-zA-Z0-9_.-]*) fail module-name;; esac
    module=$(echo "${ko%.ko}" | tr '-' '_')
    if [ -d "$S/module/$module" ]; then
        # GPU and ADSP share MDT. Reuse only an exact admitted baseline
        # provider; PAS was required absent before any D0 operation.
        [ "$ko" != qcom_q6v5_pas.ko ] || fail prior-binding
        grep -qx "$module" baseline.modules || fail unexpected-provider
        continue
    fi
    args=
    [ "$ko" != qcom_q6v5_pas.ko ] || args=a9_adsp_manual_start=1
    operation 8 load "$B/modules/$ko" $args || fail module-load
done < modules.d2
identify
gate offline
# Probe completion = sysfs publication + correct driver binding, in addition
# to the diagnostic emitted before publication. Exactly one diagnostic required.
[ "$(readlink -f "$RP/device/driver")" = "$S/bus/platform/drivers/qcom_q6v5_pas" ] || fail probe
i=0
deadline=$(($(now)+5))
until [ "$(grep -c 'A9 ADSP manual-start: auto_boot=0 recovery=disabled coredump=disabled' kmsg.raw)" = 1 ]; do
    i=$((i+1)); [ "$i" -lt 5 ] && [ "$(now)" -lt "$deadline" ] || fail diagnostic; sleep 1
done
printf disabled > "$RP/recovery"; printf disabled > "$RP/coredump"; gate offline
sed -n "/$MARK/,$ p" kmsg.raw > kmsg.log
health
operation 12 start "$RP/state" || fail start
gate running
i=0
deadline=$(($(now)+10))
until ready; do
    gate running
    i=$((i+1)); [ "$i" -lt 10 ] && [ "$(now)" -lt "$deadline" ] || fail glink-rpmsg-not-ready; sleep 1
done
# D2: bounded waits for the Q6 services (40 s) and then the card (20 s). Every
# iteration re-checks state, ADSP readiness, logs and health, so an SSR/PDR/GLINK/
# SMMU fault stops the wait immediately instead of running the timeout out.
for step in apr card; do
    case "$step" in apr) limit=$APRLIM;; card) limit=$CARDLIM;; esac
    deadline=$(($(now)+limit))
    until if [ "$step" = apr ]; then apr_ready; else card_ok; fi; do
        sleep 1
        sed -n "/$MARK/,$ p" kmsg.raw > kmsg.log
        gate running; ready || fail glink-rpmsg-lost; health
        [ "$(now)" -lt "$deadline" ] || fail "$step-timeout"
    done
    echo "D2-STEP $step-ready uptime=$(now)"
done
KREADY=1
topology
[ "$(cat "$K/d2_fault")" = 0 ] && [ "$(cat "$K/d2_ready")" = 1 ] && [ "$(cat "$K/d2_state")" = SAFE ] || fail codec-not-safe
health
audio_record up
# One and only one native helper owns route/control/PCM ioctls. The kernel also
# enforces its independent 1.5 s PA bound. Parent monitors every 100 ms.
PLAYING=1
operation 8 tone || fail tone
PLAYING=0
cat "$O/op.$OP/tone.log"
[ "$(cat "$K/d2_state")" = SAFE ] || fail tone-not-safe
card_ok || fail pcm-not-closed
health
# Kernel stop is irreversible until reboot, after helper completion and SAFE.
operation 5 kernel-stop "$K/d2_stop" || fail codec-stop
[ "$(cat "$K/d2_state")" = RESET ] && [ "$(cat "$K/d2_ready")" = 0 ] && [ "$(cat "$K/d2_fault")" = 0 ] || fail codec-reset
# Guardian does not issue a second kernel stop when this normal operation has
# already completed. Presence records the operation's completion directory.
mkdir "$O/kernel-stop"; cp "$O/op.$OP/done" "$O/kernel-stop/done"
KREADY=2
operation 12 stop "$RP/state" || fail stop
gate offline
deadline=$(($(now)+POST+1))
while [ "$(now)" -lt "$deadline" ]; do
    sleep 1; sed -n "/$MARK/,$ p" kmsg.raw > kmsg.log
    gate offline; health
done
# D2: observation only (never a failure by itself): did the card/APR devices
# unregister with the ADSP? A stale card is recorded; the phone is rebooted anyway.
if [ -e "$P/asound/card0" ]; then echo "D2-AFTER-STOP card=present"; else echo "D2-AFTER-STOP card=gone"; fi
audio_record down
# Seal dispatch before the guardian can acknowledge success or abort. Any
# concurrent containment must wait for this seal and every registered writer.
echo sealed > "$O/sealed"
await_guard || fail guardian-completion
[ "$(cat "$O/result")" = OFFLINE ] || fail fallback-required
[ ! -e "$B/abort.$BOOT.$TUPLE" ] || fail host-abort
DONE=1
# Finish the host stream before printing the terminal result. No pipe or name
# based cleanup: both readers have their own recorded child PID.
kill -KILL "$STREAMPID" 2>/dev/null || :
STREAMPID=
echo D2-PASS-ONE-TONE-CLOSED-RESET-OFFLINE
