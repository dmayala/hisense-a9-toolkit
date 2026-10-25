#!/system/bin/sh
# Attended D0 only. No service, init trigger, autoload, recover or unload path.
# All paths rooted in R only for explicit offline rehearsal; production R=''.
set -eu
R= HOLD=180 POST=10 DEAD=250
if [ "${1:-}" = --rehearse ]; then
    [ "${ADSP_D0_REHEARSAL:-}" = 1 ] || exit 2
    R=$2; HOLD=2; POST=1; shift 2
    DEAD=${ADSP_D0_REHEARSAL_DEADLINE:-250}
fi
MODE=${1:-}; TUPLE=${2:-}
case "$TUPLE" in ''|*[!a-f0-9]*) exit 2;; esac
[ ${#TUPLE} = 64 ] || exit 2
B=$R/data/local/tmp/adsp-d0
S=$R/sys; P=$R/proc
RP= LOGPID= STREAMPID= DONE=0 VERIFIED=0 BASELINED=0 OWNED=0 OP=0
BOOT=${3:-}
O=$R/data/local/tmp/adsp-d0-ledger/$BOOT
SELF=$0
REHEARSE=
[ -z "$R" ] || REHEARSE="--rehearse $R"
fail() { echo "D0-FAIL $*" >&2; exit 1; }
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
        [ "$(now)" -lt "$end" ] || return 1
        [ ! -e "$B/abort.$BOOT.$TUPLE" ] || return 1
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
        [ "$DONE" = 1 ] || echo abort > "$B/abort.$BOOT.$TUPLE"
        echo sealed > "$O/sealed"
        if await_guard && [ "$(cat "$O/result")" = OFFLINE ]; then
            [ "$DONE" = 1 ] || echo D0-FAILED-OFFLINE
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
    echo abort > "$B/abort.$BOOT.$TUPLE"
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
    total=$(grep -c "" "$1" || :); kept=$(grep -c "" "$kf" || :); benign=$(grep -cxE "$BENIGN_RE" "$1" || :)
    case "$total$kept$benign" in *[!0-9]*|'') fail matcher-self-check;; esac
    [ $((kept+benign)) = "$total" ] || fail matcher-self-check
    grep -qiE 'panic|Oops:|BUG:|watchdog.*(bite|reset|crash)|remoteproc.*crash|PDM:.*crash|fatal|SSR|subsys.*restart|authentication.*fail|auth.*(fail|error)|(fail|error).*auth|SMMU.*(fault|error)|SMEM.*(fail|error)|glink.*(fail|error)|thermal.*(critical|emergency)|a9.eink.*(fault|abort|error|failed)|fp9929a.*(fault|expired)|reboot:|Linux version' "$kf"
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
    for z in "$S"/class/thermal/thermal_zone*; do
        [ -f "$z/temp" ] || continue
        zones=$((zones+1))
        temp=$(cat "$z/temp") || fail thermal-read
        case "$temp" in ''|*[!0-9]*) fail thermal-value;; esac
        [ "$temp" -lt 70000 ] || fail thermal-limit
        if [ "$BASELINED" = 0 ]; then echo "$temp" > "$B/baseline.${z##*/}"
        else
            old=$(cat "$B/baseline.${z##*/}") || fail thermal-identity
            [ "$temp" -lt "$((old+15000))" ] || fail thermal-rise
        fi
    done
    [ "$zones" -gt 0 ] || fail missing-thermal
    BASELINED=1
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
case "$MODE" in
boot-id) cat "$P/sys/kernel/random/boot_id"; exit;;
writer)
    # Private child entry: parent has already registered this operation.
    trap '' HUP INT TERM
    job=$4; kind=$5; shift 5
    [ "$(cat "$O/tuple")" = "$TUPLE" ] && [ -d "$job" ] || exit 1
    rc=1
    if [ "$(cat "$P/sys/kernel/random/boot_id")" = "$BOOT" ]; then
        if [ "$kind" = stop ] || [ ! -e "$B/abort.$BOOT.$TUPLE" ]; then
            case "$kind" in
            load) if insmod "$@"; then rc=0; fi;;
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
            echo abort > "$B/abort.$BOOT.$TUPLE"
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
contain)
    # Request only: the original detached guardian exclusively owns failure
    # stop. Never certify while an executor can still dispatch or finish start.
    [ "$(cat "$P/sys/kernel/random/boot_id")" = "$BOOT" ] || fail reset-or-unknown-boot
    [ "$(cat "$O/tuple")" = "$TUPLE" ] || fail owner-tuple
    trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM
    echo abort > "$B/abort.$BOOT.$TUPLE"
    await_guard || fail containment-owner-pending
    [ "$(cat "$O/result")" = OFFLINE ] || fail fallback-required
    quiescent || fail executor-pending
    identify; gate offline
    echo D0-CONTAINED-OFFLINE; exit;;
run) ;;
*) exit 2;;
esac
[ "$(id -u)" = 0 ] || fail root
[ "$(cat "$B/tuple")" = "$TUPLE" ] || fail tuple
cd "$B"
[ "$(sha256sum SHA256SUMS | cut -d ' ' -f 1)" = "$TUPLE" ] || fail frozen-manifest
sha256sum -c SHA256SUMS >/dev/null || fail bundle-hash
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
for p in "$S"/class/remoteproc/remoteproc*/state; do [ ! -e "$p" ] || fail prior-remoteproc; done
[ "$(cat "$P/sys/kernel/random/boot_id")" = "$BOOT" ] || fail original-boot
case "$BOOT" in ''|*[!a-f0-9-]*) fail boot-id;; esac
mkdir -p "$R/data/local/tmp/adsp-d0-ledger"
mkdir "$R/data/local/tmp/adsp-d0-ledger/$BOOT" || fail consumed-attempt
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
MARK="ADSP-D0-$BOOT-$TUPLE"
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
done < modules.d0
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
# +1 compensates for flooring uptime, guaranteeing at least HOLD seconds.
deadline=$(($(now)+HOLD+1))
while [ "$(now)" -lt "$deadline" ]; do
    sleep 1
    sed -n "/$MARK/,$ p" kmsg.raw > kmsg.log
    gate running; ready || fail glink-rpmsg-lost; health
done
operation 12 stop "$RP/state" || fail stop
gate offline
deadline=$(($(now)+POST+1))
while [ "$(now)" -lt "$deadline" ]; do
    sleep 1; sed -n "/$MARK/,$ p" kmsg.raw > kmsg.log
    gate offline; health
done
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
echo D0-PASS-ONE-START-180S-OFFLINE
