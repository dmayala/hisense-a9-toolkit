#!/system/bin/sh
# STAGED ONLY. A human coordinator invokes each stage on an authorized slot-B
# test. This file has never been executed against a phone by the author.
# The R3 user exception permits this COORDINATOR ONLY to read four real stores
# before/after the trial into noswap RAM. It never writes/repairs/restores them.
# Companion processes retain the stricter no-real-block capability boundary.
set -eu
umask 077
D=/mnt/a9-wifi/trial
TOOLS=/mnt/a9-wifi/tools
SELF=/mnt/a9-wifi/tools/coordinator-target.sh
AUDIT=/mnt/a9-wifi/tools/audit-opens.awk
ROLES='rmtfs tqftpserv diag-router'
STRACE_SHA256=1d33daed173360a7d7726fc37ef1a2af958a8493ea36644f29c2b8f25bdbf82e
IW_SHA256=6a6951202b0e1120e7ba05fc57d7faec60790ede5614b5477c79fa386002703b
die() {
    echo "STOP: $*; no retry. Preserve $D; coordinator must stop MPSS before servers." >&2
    if [ -e "$D/start-attempted" ] && [ ! -e "$D/evidence-die" ]; then
        echo streamed > "$D/evidence-die" || :
        stream_evidence "die" || :
    fi
    exit 1
}
[ "$(id -u)" = 0 ] || die 'root shell required; never restart adbd to obtain it'
[ "$(getprop ro.a9ml.vendor)" = 1 ] && [ "$(getprop ro.a9ml.wifi)" = 1 ] || die 'not opt-in A9 mainline vendor'
[ "$(getprop ro.boot.slot_suffix)" = _b ] || die 'slot B required'
case "$(uname -r)" in 7.*) ;; *) die 'mainline kernel required';; esac
[ "$(getprop persist.vendor.a9ml.wifi.services)" != 1 ] || die 'clear the stale legacy persistent Wi-Fi opt-in before this coordinator trial'
case "$(getprop ro.a9ml.wifi.pd_mapper)" in
    kernel) ;; userspace) ROLES="$ROLES pd-mapper";; *) die 'unknown PD mapper mode';;
esac
role_ok() { case " $ROLES " in *" $1 "*) return 0;; *) die 'unknown role';; esac; }
modem() {
    selected=; count=0
    for p in /sys/class/remoteproc/remoteproc*; do
        [ -r "$p/device/of_node/compatible" ] || continue
        tr '\000' '\n' < "$p/device/of_node/compatible" | grep -qx 'qcom,sm6115-mpss-pas' || continue
        case "$(readlink -f "$p/device")" in */6080000.remoteproc) ;; *) die 'MPSS is not platform device 6080000.remoteproc';; esac
        [ "$(cat "$p/firmware")" = qcom/sm6115/hisense/hlte556n/modem.mdt ] || die 'unexpected MPSS firmware'
        selected=$p; count=$((count+1))
    done
    [ "$count" = 1 ] || die 'expected exactly one SM6115 PAS MPSS; do not guess remoteproc index'
    echo "$selected"
}
offline() { m=$(modem); [ "$(cat "$m/state")" = offline ] || die 'MPSS must be offline'; }
firmware_gate() (
    # prepare validated the root-owned bridge. Recheck after offline auditing:
    # an unrelated display hook used to replace this global parameter meanwhile.
    # Refuse before the sole start attempt and RMTFS release; never repair it here.
    fw_root=$(cat /sys/module/firmware_class/parameters/path) || return 1
    [ "$fw_root" = /mnt/a9-wifi/firmware ] || return 1
    [ -r "$fw_root/qcom/sm6115/hisense/hlte556n/modem.mdt" ] &&
        [ -s "$fw_root/qcom/sm6115/hisense/hlte556n/modem.mdt" ]
)
admission_file() {
    [ ! -L "$D/rmtfs.admission" ] && [ "$(stat -c '%u:%a:%h:%s' "$D/rmtfs.admission")" = 0:600:1:13 ] &&
        [ "$(stat -f -c '%T' "$D/rmtfs.admission")" = tmpfs ] || return 1
    IFS= read -r admission_record < "$D/rmtfs.admission" || return 1
    [ "$admission_record" = "A9RMTFS1:0:$1" ]
}
# ADSP runs sensors alongside Wi-Fi. Identify its GLINK edge by kernel/DT
# identity, never a remoteproc index. SMEM remote-pid is not a QRTR node ID.
adsp_edge() (
    selected=
    for p in /sys/class/remoteproc/remoteproc*; do
        [ -r "$p/device/of_node/compatible" ] || continue
        tr '\000' '\n' < "$p/device/of_node/compatible" | grep -qx 'qcom,sm6115-adsp-pas' || continue
        [ -z "$selected" ] && [ "$(cat "$p/name")" = adsp ] || return 1
        selected=$(readlink -f "$p/device/of_node/glink-edge") || return 1
        [ -d "$selected" ] && [ "$(tr -d '\000' < "$selected/label")" = lpass ] &&
            [ "$(od -An -tx1 "$selected/qcom,remote-pid" | tr -d ' \n')" = 00000002 ] || return 1
    done
    echo "$selected"
)
# This is the explicitly selected A9 MPSS node-0 policy, not MPSS discovery.
# ADSP's QRTR node is learned from SNS400/v1, as in sensors/s2-qrtr.c.
topology() (
    phase=$1; m=$(modem) || return 1
    adsp=$(adsp_edge) || return 1
    case "$phase:$(cat "$m/state")" in before:offline|after:running) ;; *) return 1;; esac
    edge=$(readlink -f "$m/device/of_node/glink-edge") || return 1
    [ -d "$edge" ] && [ "$(tr -d '\000' < "$edge/label")" = mpss ] &&
        [ "$(od -An -tx1 "$edge/qcom,remote-pid" | tr -d ' \n')" = 00000001 ] || return 1
    for p in /sys/class/remoteproc/remoteproc*; do
        [ -e "$p" ] || { [ ! -L "$p" ] && continue; return 1; }
        [ -d "$p" ] || return 1
        [ "$p" = "$m" ] && continue
        [ -z "$adsp" ] || [ ! "$p/device/of_node/glink-edge" -ef "$adsp" ] || continue
        [ -r "$p/state" ] && [ "$(cat "$p/state")" = offline ] || return 1
    done
    # These are the other transports compiled into the selected kernel. Even
    # an idle loaded TUN/MHI implementation is refused: NS rows alone cannot
    # enumerate service-less endpoints or open TUN instances.
    [ ! -e /sys/module/qrtr_tun ] && [ ! -e /sys/module/qrtr_mhi ] || return 1
    [ -d /sys/bus/rpmsg/devices ] && [ -d /sys/bus/rpmsg/drivers/qcom_smd_qrtr ] || return 1
    count=0; adsp_count=0; devices=' '
    for d in /sys/bus/rpmsg/devices/*IPCRTR*; do
        [ -e "$d" ] || { [ ! -L "$d" ] && continue; return 1; }
        [ -d "$d" ] && [ "$(readlink -f "$d/driver")" = /sys/bus/rpmsg/drivers/qcom_smd_qrtr ] || return 1
        target=$(readlink -f "$d") || return 1
        parent=${target%/*}
        transport_edge=$(readlink -f "$parent/of_node") || return 1
        if [ "$transport_edge" = "$edge" ]; then
            count=$((count+1))
        elif [ -n "$adsp" ] && [ "$transport_edge" = "$adsp" ]; then
            adsp_count=$((adsp_count+1))
        else
            return 1
        fi
        devices="$devices$target "
    done
    [ "$adsp_count" -le 1 ] || return 1
    for d in /sys/bus/rpmsg/drivers/qcom_smd_qrtr/*; do
        [ -L "$d" ] || continue
        [ "${d##*/}" != module ] || continue
        target=$(readlink -f "$d") || return 1
        case "$devices" in *" $target "*) ;; *) return 1;; esac
    done
    case "$phase:$count" in before:0|after:1) ;; after:0) return 2;; *) return 1;; esac
    local_node=$(sed -n 's/^A9_WIFI_LOCAL_NODE \([0-9][0-9]*\)$/\1/p' "$D/diag-router.log")
    case "$local_node" in ''|0|*[!0-9]*) return 1;; esac
    timeout --foreground -s KILL 1 /vendor/bin/qrtr-lookup > "$D/topology-$phase.qrtr" || return 1
    awk -v local_node="$local_node" -v phase="$phase" -v adsp="$adsp_count" '
        NR==1 {if ($0 !~ /^[ ]*Service Version Instance Node[ ]+Port$/) bad=1; next}
        {if (NF<5 || $1 !~ /^[0-9]+$/ || ($2!="N/A" && $2 !~ /^[0-9]+$/) ||
             $3 !~ /^[0-9]+$/ || $4 !~ /^[0-9]+$/ || $5 !~ /^[0-9]+$/ || $5<=0 || $5>=4294967294) bad=1
         if (adsp && $1==400 && $2==1 && $4!=local_node && $4!=0) {
             if (adsp_node!="" && adsp_node!=$4) bad=1
             adsp_node=$4
         }
         nodes[$4]=1; rows++}
        END {
            for (node in nodes)
                if (node!=local_node && !(phase=="after" && node==0) &&
                    !(adsp_node!="" && node==adsp_node)) bad=1
            exit !(NR>=2 && rows && !bad)
        }' "$D/topology-$phase.qrtr" || return 1
)
# Run 8 measured the post-start proof at 2.47 s against a modem request
# tolerance of ~56-70 ms. The release decision is therefore split. The
# MINIMAL check below uses shell builtins only (no command substitution, pipe,
# subshell or external command between the wake-up token and the gate byte)
# and runs in a watcher started BEFORE the MPSS start. The full census
# (topology after, forks, nameserver) runs after release and fails closed.
release_mark() {
    read -r rel_up rel_idle < /proc/uptime || return 0
    # echo, not printf: the phone's mksh has no printf builtin (a fork+exec).
    echo "A9_WIFI_TIMING $1 $rel_up" >> "$D/release-stamps" || :
}
# Forks allowed: runs before the sole start write.
release_prepare() {
    rel_modem=$(modem) || return 1
    rel_edge=$(readlink -f "$rel_modem/device/of_node/glink-edge") || return 1
    [ -d "$rel_edge" ] || return 1
    rel_adsp_edge=$(adsp_edge) || return 1
    rel_state_file=$rel_modem/state; rel_recovery_file=$rel_modem/recovery
    rel_others=
    for p in /sys/class/remoteproc/remoteproc*; do
        [ -e "$p" ] || { [ ! -L "$p" ] && continue; return 1; }
        [ -z "$rel_adsp_edge" ] || [ ! "$p/device/of_node/glink-edge" -ef "$rel_adsp_edge" ] || continue
        [ "$p" = "$rel_modem" ] || rel_others="$rel_others $p/state"
    done
    load_identity rmtfs || return 1
    rel_tracer=$owned_tracer; rel_pid=$owned_child; rel_pid_start=$owned_child_start
    rel_watchdog=$(cat "$D/watchdog.pid") || return 1
    rel_guardian=$(cat "$D/guardian.pid") || return 1
    case "$rel_watchdog$rel_guardian" in ''|*[!0-9]*) return 1;; esac
    rm -f "$D/release-stamps" "$D/release-watch.status" "$D/release.fifo"
    mkfifo "$D/release.fifo" || return 1
    exec 9<>"$D/release.fifo"
}
# Returns 0 release, 2 IPCRTR not yet probed (retry), 1 refuse. Builtins only.
release_check() {
    read -r rel_up rel_idle < /proc/uptime || return 1
    [ "${rel_up%%.*}" -lt "$rel_end" ] || return 1
    rel_value=; read -r rel_value < "$rel_state_file" || :
    # remoteproc state writes are synchronous: running by now, or crashed.
    # Anything else (a transient state) is retried until the shared deadline.
    case "$rel_value" in running) ;; crashed|invalid) return 1;; *) return 2;; esac
    read -r rel_value < "$rel_recovery_file" || return 1
    [ "$rel_value" = disabled ] || return 1
    for rel_path in $rel_others; do
        read -r rel_value < "$rel_path" || return 1
        [ "$rel_value" = offline ] || return 1
    done
    [ ! -e /sys/module/qrtr_tun ] && [ ! -e /sys/module/qrtr_mhi ] || return 1
    rel_count=0; rel_adsp_count=0
    for rel_dev in /sys/bus/rpmsg/devices/*IPCRTR*; do
        [ -e "$rel_dev" ] || { [ ! -L "$rel_dev" ] && continue; return 1; }
        [ -d "$rel_dev" ] || return 1
        rel_name=${rel_dev##*/}
        [ -L "/sys/bus/rpmsg/drivers/qcom_smd_qrtr/$rel_name" ] || return 1
        [ "$rel_dev/driver" -ef /sys/bus/rpmsg/drivers/qcom_smd_qrtr ] || return 1
        if [ "$rel_dev/../of_node" -ef "$rel_edge" ]; then
            rel_count=$((rel_count+1))
        elif [ -n "$rel_adsp_edge" ] && [ "$rel_dev/../of_node" -ef "$rel_adsp_edge" ]; then
            rel_adsp_count=$((rel_adsp_count+1))
        else
            return 1
        fi
    done
    for rel_dev in /sys/bus/rpmsg/drivers/qcom_smd_qrtr/*; do
        [ -L "$rel_dev" ] || continue
        rel_name=${rel_dev##*/}
        [ "$rel_name" != module ] || continue
        case "$rel_name" in *IPCRTR*) [ -e "/sys/bus/rpmsg/devices/$rel_name" ] || return 1;; *) return 1;; esac
    done
    [ "$rel_count" -le 1 ] && [ "$rel_adsp_count" -le 1 ] || return 1
    [ "$rel_count" = 1 ] || return 2
    # rmtfs must be alive (same lifetime as admitted) and holding THIS gate file.
    [ ! -e "$D/rmtfs.exit" ] || return 1
    read_process_record "$rel_pid" || return 1
    [ "$record_parent" = "$rel_tracer" ] && [ "$record_start" = "$rel_pid_start" ] || return 1
    case "$record_state" in Z|X|x) return 1;; esac
    [ "$D/rmtfs.admission" -ef "/proc/$rel_pid/fd/16" ] || return 1
    kill -0 "$rel_watchdog" 2>/dev/null && kill -0 "$rel_guardian" 2>/dev/null || return 1
    [ ! -e "$D/fault" ] && [ ! -e "$D/abort" ] && [ ! -e "$D/stop-requested" ] && [ ! -e "$D/complete" ] || return 1
    [ -e "$D/start-attempted" ] && [ -e "$D/start-write-pending" ] && [ -e "$D/start-write-returned" ] || return 1
    IFS= read -r rel_value < "$D/rmtfs.admission" || return 1
    [ "$rel_value" = A9RMTFS1:0:0 ] || return 1
    read -r rel_up rel_idle < /proc/uptime || return 1
    [ "${rel_up%%.*}" -lt "$rel_end" ] || return 1
    return 0
}
# Left side of: release_watch | dd of=rmtfs.admission bs=1 seek=11 count=1 conv=notrunc
# dd is already running, blocked on its stdin: printing the single byte IS the
# gate write. Any refusal prints nothing, so dd writes nothing.
release_watch() {
    IFS= read -r rel_token <&9 || return 1
    [ "$rel_token" = go ] || { echo refused-stopped > "$D/release-watch.status"; return 1; }
    release_mark release-begin
    rel_spins=0
    while :; do
        rel_rc=0; release_check || rel_rc=$?
        [ "$rel_rc" = 2 ] || break
        rel_spins=$((rel_spins+1))
        # Busy poll first (a probe delay is not on the modem's clock); then
        # back off with a 10 ms sleep until the shared startup deadline.
        [ "$rel_spins" -lt 50 ] || sleep 0.01
    done
    if [ "$rel_rc" != 0 ]; then
        echo refused-minimal-check > "$D/release-watch.status"
        release_mark release-refused
        return 1
    fi
    echo -n 1
    release_mark release-written
    echo released > "$D/release-watch.status"
}
# Best-effort revocation: rmtfs treats a gate that reverts after release as
# a fault and exits on its next packet.
release_revoke() {
    printf 0 | dd of="$D/rmtfs.admission" bs=1 seek=11 count=1 conv=notrunc 2>> "$D/rmtfs-admission.dd-log" || :
}
release_abort() {
    echo stop >&9 2>/dev/null || :
    [ -z "${rel_wrapper:-}" ] || kill "$rel_wrapper" 2>/dev/null || :
}
read_process_record() {
    # /proc/PID/stat is present without CONFIG_PROC_CHILDREN. comm may contain
    # spaces, newlines and ')'; strip through the LAST closing parenthesis.
    case "$1" in ''|*[!0-9]*) return 1;; esac
    record=
    # Builtin reads avoid one external cat for every entry in the census. Keep
    # embedded newlines in comm and still strip through the LAST ') ' below.
    while IFS= read -r part || [ -n "$part" ]; do
        record="$record$part
"
    done 2>/dev/null < "/proc/$1/stat" || return 1
    case "$record" in "$1 ("*') '*) ;; *) return 1;; esac
    record=${record##*) }
    case $- in *f*) record_noglob=1;; *) record_noglob=0;; esac
    set -f
    set -- $record
    [ "$record_noglob" = 1 ] || set +f
    [ "$#" -ge 20 ] || return 1
    case "$2" in ''|*[!0-9]*) return 1;; esac
    case "${20}" in ''|*[!0-9]*) return 1;; esac
    record_parent=$2; record_start=${20}; record_state=$1
}
process_record() (
    read_process_record "$1" || exit 1
    printf '%s %s %s\n' "$record_parent" "$record_start" "$record_state"
)
trace_children() (
    parent=$1
    for entry in /proc/[0-9]*; do
        child=${entry##*/}
        read_process_record "$child" || { [ ! -d "$entry" ] && continue; exit 1; }
        [ "$record_parent" != "$parent" ] || printf '%s\n' "$child"
    done
)
load_identity() {
    # Published only by a full offline observe, in the root-owned trial tmpfs.
    read -r owned_tracer owned_start owned_child owned_child_start extra < "$D/$1.identity" || return 1
    [ -z "$extra" ] || return 1
    for value in "$owned_tracer" "$owned_start" "$owned_child" "$owned_child_start"; do
        case "$value" in ''|*[!0-9]*) return 1;; esac
    done
}
freshness() {
    # Immutable confinement is bound to these lifetimes. Mutable descriptors
    # and trace acceptance are checked separately by mutable_gate.
    for role in $ROLES; do
        load_identity "$role" || return 1
        [ "$(cat "$D/$role.tracer")" = "$owned_tracer" ] && [ "$(cat "$D/$role.tracer-start")" = "$owned_start" ] || return 1
        read_process_record "$owned_tracer" || return 1
        [ "$record_start" = "$owned_start" ] || return 1
        case "$record_state" in Z|X|x) return 1;; esac
        [ "$(readlink "/proc/$owned_tracer/exe")" = "$TOOLS/strace" ] || return 1
        read_process_record "$owned_child" || return 1
        [ "$record_parent" = "$owned_tracer" ] && [ "$record_start" = "$owned_child_start" ] || return 1
        case "$record_state" in Z|X|x) return 1;; esac
        [ "$(readlink "/proc/$owned_child/exe")" = "/vendor/bin/$role" ] || return 1
        [ -s "$D/$role.trace" ] && [ ! -e "$D/$role.exit" ] || return 1
        awk -v role="$role" '$0=="A9_WIFI_READY " role {ready=1} /A9_WIFI_FAULT|A9_WIFI_FATAL/ {bad=1} END {exit !(ready && !bad)}' "$D/$role.log" || return 1
    done
}
stamp() {
    read -r stamp_up stamp_idle < /proc/uptime || die 'uptime unavailable'
    printf 'A9_WIFI_TIMING %s %s\n' "$1" "$stamp_up"
}
# MPSS must be proved offline before arm+235; all evidence before arm+265.
# These leave five seconds before the independent 240 s stop / 270 s reset.
# Cleanup shares a fixed 35 s reserve: calibrated evidence/helper tail,
# <=20 s modem stop, and >=1 s handoff. A larger tail SHORTENS modem stop;
# it never extends cleanup or either guard. Evidence after a proved stop may
# use the reset window; charging ALL cleanup to the stop window wasted 15 s.
clock_now() {
    read -r clock_up clock_idle < /proc/uptime || return 1
    clock_value=${clock_up%%.*}
    case "$clock_value" in ''|*[!0-9]*) return 1;; esac
}
# Total-trial length. 240 s (guardian reset 270 s) for every existing stage. Only
# `run --session N` publishes another value (RAM file, written once before the
# guards arm; every guard re-reads and re-validates it). Bounds: 240..14400.
total_load() {
    TOTAL=240
    [ -e "$D/session-total" ] || return 0
    [ ! -L "$D/session-total" ] || return 1
    read -r TOTAL total_extra < "$D/session-total" || return 1
    case "$TOTAL" in ''|*[!0-9]*) return 1;; esac
    [ -z "$total_extra" ] && [ "$TOTAL" -ge 240 ] && [ "$TOTAL" -le 14400 ]
}
anchor() {
    read -r guard_start < "$D/guard-armed.uptime" || return 1
    case "$guard_start" in ''|*[!0-9]*) return 1;; esac
    clock_now || return 1
    [ "$clock_value" -ge "$guard_start" ] || return 1
}
active_end() {
    anchor || return 1
    total_load || return 1
    # Reserve the whole 20 s stop phase before the MPSS deadline and the whole
    # 35 s cleanup before the evidence deadline. Take the earlier constraint.
    active_stop=$((guard_start + TOTAL - 5 - 20))
    active_evidence=$((guard_start + TOTAL + 30 - 5 - 35))
    [ "$active_stop" -le "$active_evidence" ] || active_stop=$active_evidence
    echo "$active_stop"
}
activation_end() {
    active=$(active_end) || return 1
    # Readiness/identity work overlaps the running observation; it does not
    # get another 180 s. Running is anchored upward by one second below.
    echo "$((active - 181 - 5))"
}
start_budget() {
    running_end=$(activation_end) || return 1
    [ -e "$D/cleanup-budget-verified" ] || return 1
    # Strict inequality covers integer truncation and the activation write.
    clock_now || return 1
    [ "$((clock_value + 10))" -lt "$running_end" ]
}
cleanup_limits() {
    # Before calibration (including failed preflight), retain the old bounds.
    tail_limit=15; stop_limit=20
    if [ -e "$D/cleanup-budget-verified" ]; then
        read -r tail_limit extra < "$D/cleanup-budget-verified" || return 1
        case "$tail_limit" in ''|*[!0-9]*) return 1;; esac
        [ -z "$extra" ] && [ "$tail_limit" -gt 0 ] && [ "$tail_limit" -lt 34 ] || return 1
        stop_limit=$((35 - tail_limit - 1))
        [ "$stop_limit" -le 20 ] || stop_limit=20
    fi
}
bounded_until() (
    deadline=$1; shift
    group_option=
    if [ "${1:-}" = --foreground ]; then group_option=--foreground; shift; fi
    clock_now || exit 1
    remaining=$((deadline - clock_value))
    [ "$remaining" -gt 0 ] || exit 1
    # Only the outer bounded stage owns a new process group. Inner timeouts
    # must inherit it, so killing that group cannot leave a late start writer.
    timeout $group_option -s KILL "$remaining" /system/bin/sh "$SELF" "$@" || exit 1
    clock_now && [ "$clock_value" -lt "$deadline" ]
)
mutable_gate() {
    freshness || return 1
    for role in $ROLES; do
        load_identity "$role" || return 1
        pid=$owned_child
        descriptors || return 1
    done
    audit && freshness
}
run_exit() {
    run_status=$1
    trap - EXIT
    trap '' HUP INT TERM # repeated interruption must not kill ordered cleanup
    if [ ! -e "$D/complete" ]; then
        echo run-cancelled >> "$D/fault" || :
        echo run-cancelled > "$D/abort" || :
        echo cancelled > "$D/stage" || :
        # stop is itself bounded, proves offline first, and publishes complete
        # only after the required teardown/evidence. Partial monitor launches
        # do not disable a surviving guardian.
        # A failed/timed-out stop has already consumed its reserve. Never
        # retry it with a fresh timeout or re-enter partially written evidence.
        [ ! -e "$D/stop-requested" ] || fallback run_exit-1
        /system/bin/sh "$SELF" stop || :
        [ -e "$D/complete" ] || fallback run_exit-2
        run_status=1
    fi
    exit "$run_status"
}
tools_mount() {
    # /mnt itself is noexec on Android. Require a separate executable mount,
    # installed BEFORE copying tools; never remount the Android parent.
    awk -v path="$TOOLS" '$2==path {n++; split($4,a,","); for(i in opt)delete opt[i]; for(i in a)opt[a[i]]=1; if($3!="tmpfs" || !opt["rw"] || !opt["nosuid"] || !opt["nodev"] || opt["noexec"])bad=1} END {exit !(n==1 && !bad)}' /proc/mounts || die 'tools require a dedicated executable nosuid,nodev tmpfs; mount before copying'
}
stop_helpers() (
    # SIGKILL the owned tracer: pinned --kill-on-exit uses PTRACE_O_EXITKILL.
    # SIGTERM can let strace detach; never rely on it to remove tracees.
    failed=0
    for role in $ROLES; do
        [ -e "$D/$role.tracer" ] || continue # partial prepare launch
        tracer=$(cat "$D/$role.tracer") || { failed=1; continue; }
        expected=$(cat "$D/$role.tracer-start") || { failed=1; continue; }
        case "$expected" in ''|*[!0-9]*) failed=1; continue;; esac
        record=$(process_record "$tracer") || { [ ! -d "/proc/$tracer" ] || failed=1; continue; }
        set -- $record
        [ "$2" = "$expected" ] || { failed=1; continue; } # recycled PID
        [ "$3" != Z ] || continue
        # Freeze before enumerating: even an abort during strace startup must
        # not race its fork/EXITKILL setup and leave an untraced helper alive.
        kill -STOP "$tracer" 2>/dev/null || { failed=1; continue; }
        stopped=0
        for attempt in 1 2 3 4 5; do
            record=$(process_record "$tracer") || break
            set -- $record
            [ "$2" = "$expected" ] || break
            case "$3" in T|t|Z) stopped=1; break;; esac
            sleep 1
        done
        [ "$stopped" = 1 ] || { failed=1; continue; }
        if [ -e "$D/$role.identity" ]; then
            # A verified, post-exec tracee cannot create another process. Avoid
            # the host-wide scan during normal stop; never trust a recycled PID.
            load_identity "$role" || { failed=1; continue; }
            [ "$owned_tracer" = "$tracer" ] && [ "$owned_start" = "$expected" ] || { failed=1; continue; }
            children=
            if record=$(process_record "$owned_child"); then
                set -- $record
                [ "$1" = "$tracer" ] && [ "$2" = "$owned_child_start" ] || { failed=1; continue; }
                children=$owned_child
            else
                [ ! -d "/proc/$owned_child" ] || { failed=1; continue; }
            fi
        else
            # Partial prepare: EXITKILL may not yet be installed. Preserve the
            # frozen-tracer/full-enumeration cleanup until observe has succeeded.
            children=$(trace_children "$tracer") || { failed=1; children=; }
        fi
        for child in $children; do
            record=$(process_record "$child") || continue
            set -- $record
            [ "$1" != "$tracer" ] || kill -KILL "$child" 2>/dev/null || failed=1
        done
        kill -KILL "$tracer" 2>/dev/null || failed=1
    done
    [ "$failed" = 0 ]
)
prepare_exit() {
    prepare_status=$1
    trap - EXIT HUP INT TERM
    [ "$prepare_status" = 0 ] && return 0
    echo prepare-failed >> "$D/fault" || :
    echo failed > "$D/prepare-failed" || :
    stop_modem # do not dismantle storage until MPSS is proved offline
    # Cover workers still between fork and publishing their tracer identity.
    # They reject the cancellation marker before launch; an in-flight launch
    # is caught on the next pass. Wait only for this prepare's own children.
    for attempt in 1 2 3 4 5; do
        stop_helpers || :
        live=0
        for worker in $prepare_workers; do
            record=$(process_record "$worker") || continue
            set -- $record
            [ "$3" = Z ] || live=1
        done
        [ "$live" = 1 ] || break
        sleep 1
    done
    if [ "$live" = 1 ] || ! stop_helpers; then
        echo 'prepare cleanup incomplete' >> "$D/fault" || :
        fallback prepare_exit-1
    fi
    echo stopped > "$D/stage"
    echo cleaned > "$D/prepare-cleaned"
    exit "$prepare_status"
}
monitors() {
    for monitor in watchdog guardian; do
        mpid=$(cat "$D/$monitor.pid") || return 1
        case "$mpid" in ''|*[!0-9]*) return 1;; esac
        kill -0 "$mpid" 2>/dev/null || return 1
        tr '\000' '\n' < "/proc/$mpid/cmdline" | grep -Fx "$SELF" >/dev/null || return 1
        tr '\000' '\n' < "/proc/$mpid/cmdline" | grep -Fx -- "--$monitor" >/dev/null || return 1
    done
    [ ! -e "$D/fault" ] && [ ! -e "$D/abort" ] && [ ! -e "$D/complete" ]
}
services() {
    for service in 64 69; do
        timeout --foreground -s KILL 5 /vendor/bin/qrtr-lookup "$service" > "$D/service-$service-$$.txt" || return 1
        awk -v s="$service" '$1==s && $2~/^[0-9]+$/ && $3~/^[0-9]+$/ && $4~/^[0-9]+$/ && $5~/^[1-9][0-9]*$/ {n++} END {exit !(n>0)}' "$D/service-$service-$$.txt" || return 1
    done
}
diag_lookup() {
    timeout --foreground -s KILL 5 /vendor/bin/qrtr-lookup 4097 > "$D/diag-lookup.txt" || die 'local QRTR nameserver lookup failed'
    node=$(sed -n 's/^A9_WIFI_LOCAL_NODE \([0-9][0-9]*\)$/\1/p' "$D/diag-router.log")
    case "$node" in ''|*[!0-9]*) die 'one runtime local QRTR node required';; esac
    awk -v node="$node" '$1==4097 { if ($2!="N/A" || $4!=node || $5<=0 || ($3!=0 && $3!=2 && $3!=4) || seen[$3]++) bad=1; n++ } END {exit !(n==3 && !bad && seen[0] && seen[2] && seen[4])}' "$D/diag-lookup.txt" || die 'DIAG4097/0,2,4 not independently visible on actual local node'
}
# Read-only WLAN observation (run 12 follow-up). Never prints the MAC (every iw
# line is sanitised); the link is ALWAYS taken down again, and the normal stop
# also downs it if this stage was killed.
wlan_mac_filter() { sed -E 's/([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}/xx:xx:xx:xx:xx:xx/g'; }
# wlan_factory_mac: give wlan0 this phone's factory MAC and prove it. The session
# starter reads persist:wlan_mac.bin (Intf0, read-only mount) into FACTORY_MAC, so
# images carry no device MAC. A private DT MAC (older images) is still accepted.
# Never prints the address.
FACTORY_MAC=/mnt/a9-wifi/session/factory-mac
wlan_factory_mac() {
    local want cur dt
    if [ -f "$FACTORY_MAC" ] && [ ! -L "$FACTORY_MAC" ]; then
        want=$(cat "$FACTORY_MAC") || return 1
    else
        dt=/sys/class/net/wlan0/device/of_node/local-mac-address
        [ -r "$dt" ] && [ "$(stat -L -c '%s' "$dt")" = 6 ] || return 1
        want=$(od -An -v -tx1 "$dt" | tr -d ' \n')
    fi
    case "$want" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) return 1;; esac
    [ $((0x${want%??????????} & 1)) = 0 ] || return 1
    case "$want" in 000000000000|ffffffffffff) return 1;; esac
    cur=$(tr -d ':\n' < /sys/class/net/wlan0/address)
    if [ "$cur" != "$want" ]; then
        timeout --foreground -s KILL 5 ip link set wlan0 down || return 1
        timeout --foreground -s KILL 5 ip link set wlan0 address "$(echo "$want" | sed 's/../&:/g; s/:$//')" || return 1
        cur=$(tr -d ':\n' < /sys/class/net/wlan0/address)
    fi
    [ "$cur" = "$want" ]
}
wlan_link_down() {
    [ -e "$D/wlan-up-requested" ] && [ ! -e "$D/wlan-down-done" ] || return 0
    if [ -e /sys/class/net/wlan0 ]; then
        timeout --foreground -s KILL 5 ip link set wlan0 down || { echo link-down-failed >> "$D/wlan-observe-errors" || :; return 0; }
    fi
    echo down > "$D/wlan-down-done" || :
}
wlan_step() {
    echo "A9_WIFI_WLAN step=$1" >> "$D/wlan-observe.txt"
}
wlan_sanitised() {
    # wlan_sanitised STEP COMMAND...: run bounded, keep sanitised output, never fail the stage here
    ws_step=$1; shift
    wlan_step "$ws_step"
    ws_rc=0; "$@" > "$D/wlan-raw.txt" 2>&1 || ws_rc=$?
    head -n 400 "$D/wlan-raw.txt" | wlan_mac_filter >> "$D/wlan-observe.txt" || :
    echo "A9_WIFI_WLAN rc=$ws_rc" >> "$D/wlan-observe.txt"
    return "$ws_rc"
}
wlan_survey() {
    wo_failed=
    : > "$D/wlan-observe.txt"; rm -f "$D/wlan-down-done" "$D/wlan-up-requested"
    wo_end=$(( $(cut -d. -f1 /proc/uptime) + 40 ))
    while [ ! -e /sys/class/net/wlan0 ] || [ -z "$(ls /sys/class/ieee80211 2>/dev/null)" ]; do
        monitors || { wo_failed='monitor/fault loss while waiting for phy and wlan0'; break; }
        [ "$(cat "$m/state")" = running ] || { wo_failed='MPSS stopped while waiting for WLAN'; break; }
        [ "$(cut -d. -f1 /proc/uptime)" -lt "$wo_end" ] || { wo_failed='40-second phy/wlan0 timeout'; break; }
        sleep 1
    done
    wlan_step sysfs
    { echo "ieee80211: $(ls /sys/class/ieee80211 2>&1)"; echo "net: $(ls /sys/class/net 2>&1)"; } >> "$D/wlan-observe.txt" || :
    for wo_attr in operstate type flags carrier; do
        wo_val=n/a; [ ! -r "/sys/class/net/wlan0/$wo_attr" ] || read -r wo_val < "/sys/class/net/wlan0/$wo_attr" 2>/dev/null || :
        echo "wlan0.$wo_attr=$wo_val" >> "$D/wlan-observe.txt"
    done
    if [ -z "$wo_failed" ]; then
        wlan_sanitised iw-dev timeout --foreground -s KILL 10 "$TOOLS/iw" dev || wo_failed='iw dev'
        wlan_sanitised iw-phy timeout --foreground -s KILL 10 "$TOOLS/iw" phy || wo_failed="${wo_failed:+$wo_failed; }iw phy"
    fi
    wlan_step dmesg-pre
    dmesg 2>/dev/null | grep -E 'ath10k|cfg80211|ieee80211' | tail -n 60 | wlan_mac_filter >> "$D/wlan-observe.txt" || :
    # Passive scan only if everything so far is healthy; the MPSS must still run.
    if [ -z "$wo_failed" ] && monitors && [ "$(cat "$m/state")" = running ]; then
        echo requested > "$D/wlan-up-requested"
        if timeout --foreground -s KILL 10 ip link set wlan0 up; then
            sleep 2
            wlan_step scan-passive
            wo_rc=0
            timeout --foreground -s KILL 30 "$TOOLS/iw" dev wlan0 scan passive > "$D/scan-passive.txt" 2> "$D/scan-passive.err" || wo_rc=$?
            echo "A9_WIFI_WLAN rc=$wo_rc" >> "$D/wlan-observe.txt"
            [ "$wo_rc" = 0 ] || wo_failed="${wo_failed:+$wo_failed; }passive scan rc=$wo_rc"
            wo_bss=$(grep -c '^BSS ' "$D/scan-passive.txt" 2>/dev/null) || wo_bss=0
            echo "bss_count=$wo_bss" >> "$D/wlan-observe.txt"
            grep -E '^[[:space:]]*freq:' "$D/scan-passive.txt" 2>/dev/null | sort | uniq -c | head -n 40 >> "$D/wlan-observe.txt" || :
            head -n 5 "$D/scan-passive.err" 2>/dev/null | wlan_mac_filter >> "$D/wlan-observe.txt" || :
        else
            wo_failed="${wo_failed:+$wo_failed; }ip link up"
        fi
    elif [ -z "$wo_failed" ]; then
        wo_failed='unhealthy before scan'
    fi
    wlan_link_down
    wlan_step dmesg-post
    dmesg 2>/dev/null | grep -E 'ath10k|cfg80211|ieee80211' | tail -n 60 | wlan_mac_filter >> "$D/wlan-observe.txt" || :
    cat "$D/wlan-observe.txt" || :
    echo "$wo_failed" > "$D/wlan-observe-result" || :
}
# Integrated session (run --session): regulatory state before the framework may transmit.
# cfg80211 is built in and asked for regulatory.db at kernel init, before the firmware
# bridge existed (run 14: "failed to load regulatory.db" at t+1.5 s), so reload it now.
# The image MUST carry a country (/vendor/etc/a9-wifi/session/country, user decision US):
# no country, a failed reload, a refused hint or a domain other than the requested one
# fails the session before the Android gate. Never a silent fallback to the driver's EEPROM
# hint (CN on this phone) or to the world domain. Metadata only.
session_regulatory() {
    [ -s /vendor/etc/a9-wifi/session/country ] || die 'session image carries no country'
    read -r sr_country sr_extra < /vendor/etc/a9-wifi/session/country || die 'country file unreadable'
    [ -z "$sr_extra" ] || die 'country file malformed'
    case "$sr_country" in [A-Z][A-Z]) ;; *) die 'country must be two capital letters';; esac
    sr_rc=0; timeout --foreground -s KILL 10 "$TOOLS/iw" reg reload > "$D/reg-reload.txt" 2>&1 || sr_rc=$?
    echo "A9_WIFI_SESSION regdb-reload rc=$sr_rc"
    [ "$sr_rc" = 0 ] || die 'regulatory.db reload failed; no country can be applied'
    sleep 1
    timeout --foreground -s KILL 10 "$TOOLS/iw" reg set "$sr_country" > "$D/reg-set.txt" 2>&1 || die 'regulatory country hint refused'
    sleep 1
    timeout --foreground -s KILL 10 "$TOOLS/iw" reg get > "$D/reg-get.txt" 2>&1 || die 'regulatory state unreadable'
    echo 'A9_WIFI_SESSION regulatory begin'
    head -n 40 "$D/reg-get.txt" | wlan_mac_filter || :
    echo 'A9_WIFI_SESSION regulatory end'
    dmesg 2>/dev/null | grep -E 'regulatory\.db|cfg80211: (Loaded|failed|Direct firmware|Loading)' | tail -n 6 | wlan_mac_filter || :
    awk -v c="$sr_country" '$1=="country" && $2==c":" {ok=1} END {exit !ok}' "$D/reg-get.txt" || die 'requested country is not an active regulatory domain'
}
# Evidence that must survive a reset (run 12: the tmpfs trial directory was lost
# with the reset): everything below goes to stdout (the host's ctl capture) and
# /dev/kmsg (the host's dmesg capture), bounded, metadata only. The companion
# logs hold no payload bytes by construction.
kmsg_note() { echo "a9wifi-evidence $*" >> /dev/kmsg 2>/dev/null || :; }
stream_evidence() {
    se_label=$1
    [ -d "$D" ] || return 0
    first_fault || :
    echo "A9_WIFI_EVIDENCE begin label=$se_label"
    se_up=?; read -r se_up se_idle < /proc/uptime || :
    echo "A9_WIFI_EVIDENCE uptime=$se_up"
    kmsg_note "label=$se_label uptime=$se_up"
    for se_file in stage fault teardown-signalled release-watch.status; do
        [ -r "$D/$se_file" ] || continue
        echo "A9_WIFI_EVIDENCE file=$se_file"
        tail -n 20 "$D/$se_file" || :
        [ "$se_file" != fault ] || while IFS= read -r se_line; do kmsg_note "fault: $se_line"; done < "$D/fault" || :
    done
    for se_role in $ROLES; do
        se_code=none; [ ! -r "$D/$se_role.exit" ] || read -r se_code < "$D/$se_role.exit" || :
        echo "A9_WIFI_EVIDENCE exit $se_role=$se_code"
        kmsg_note "exit $se_role=$se_code"
        [ -r "$D/$se_role.log" ] || continue
        echo "A9_WIFI_EVIDENCE log=$se_role"
        tail -n 40 "$D/$se_role.log" || :
        grep -E 'A9_WIFI_FAULT|A9_WIFI_DIAG_FAULT|A9_WIFI_TEARDOWN|_NOTE|A9_WIFI_DIAG_HANDSHAKE' "$D/$se_role.log" 2>/dev/null | tail -n 5 | while IFS= read -r se_line; do kmsg_note "$se_role: $se_line"; done || :
    done
    if [ -r "$D/ramcopy-sha256.txt" ]; then echo "A9_WIFI_EVIDENCE file=ramcopy-sha256.txt"; cat "$D/ramcopy-sha256.txt" || :
    else grep -h '^A9_WIFI_RMTFS_STORE_SHA256 ' "$D/rmtfs.log" 2>/dev/null | tail -n 1 || :; fi
    echo "A9_WIFI_EVIDENCE end label=$se_label"
}
# Every call names its site. The label, remoteproc state and uptime reach stdout,
# /dev/kmsg and the fault file BEFORE the reset is requested.
fallback() {
    fb_site=${1:-unlabelled}
    if [ -z "${fb_active:-}" ]; then
        fb_active=1
        fb_state=unknown
        fb_m=$(modem 2>/dev/null) && read -r fb_state < "$fb_m/state" || :
        fb_up=?; read -r fb_up fb_idle < /proc/uptime || :
        fb_line="a9wifi-fallback site=$fb_site state=$fb_state uptime=$fb_up"
        echo "$fb_line"
        echo "$fb_line" >> "$D/fault" || :
        kmsg_note "$fb_line"
        stream_evidence "fallback-$fb_site" || :
    fi
    echo 'HARD STOP: safe bounded trial completion cannot be proved; requesting bootloader reset. No retry or EFS restore.' >> "$D/fault" || :
    timeout --foreground -s KILL 5 setprop sys.powerctl reboot,bootloader || :
    sleep 5 || :
    timeout --foreground -s KILL 5 /system/bin/reboot bootloader || :
    exit 1
}
stop_modem() {
    m=$(modem) || fallback stop_modem-1
    state=$(cat "$m/state") || fallback stop_modem-2
    [ "$state" = offline ] && return 0
    # An independent guardian still requests reset if this helper/timeout hangs.
    cleanup_limits || fallback stop_modem-3
    timeout --foreground -s KILL "$stop_limit" /system/bin/sh "$SELF" --stop-state || fallback stop_modem-4
    state=$(cat "$m/state") || fallback stop_modem-5
    [ "$state" = offline ] || fallback stop_modem-6
}
stop_proven_offline() {
    [ -e "$D/stop-requested" ] || return 1
    [ ! -e "$D/start-write-pending" ] || [ -e "$D/start-write-returned" ] || return 1
    m=$(modem) || fallback stop_proven_offline-1
    state=$(cat "$m/state") || fallback stop_proven_offline-2
    [ "$state" = offline ] || return 1
}
retire_after_offline() {
    stop_proven_offline || return 1
    # Deliberate companion teardown is permitted only after this state read.
    # Preserve existing faults and leave the independent guardian armed until
    # the main controller completes hashes and teardown and writes complete.
    echo offline > "$D/watchdog-retired"
}
stores() {
    phase=$1
    changed=0
    [ "$(cat "$(modem)/state")" = offline ] || die 'store comparison requires stopped MPSS'
    mkdir -m 700 "$D/stores/$phase"
    for part in modemst1 modemst2 fsg fsc; do
        size=2097152; count=512
        [ "$part" != fsc ] || { size=131072; count=32; }
        device=/dev/block/by-name/$part
        case "$(stat -L -c '%F' "$device")" in
            'block special file'|'block device') ;;
            *) die 'readonly audit source is not a block partition';;
        esac
        resolved=$(readlink -f "$device"); leaf=${resolved##*/}
        case "$leaf" in ''|*[!a-zA-Z0-9]*) die 'unexpected block partition identity';; esac
        sectors=$(cat "/sys/class/block/$leaf/size")
        case "$sectors" in ''|*[!0-9]*) die 'invalid block partition size';; esac
        [ "$sectors" = "$((size / 512))" ] || die 'real store size differs from exact audited size; never hash a prefix'
        # This is the sole intentional live-store open in the controller.
        # Fixed names only, READ-ONLY dd input; private output on noswap RAM.
        timeout --foreground -s KILL 10 dd if="$device" of="$D/stores/$phase/$part.bin" bs=4096 count="$count" 2>"$D/stores/$phase/$part.dd-log" || die 'readonly store snapshot failed'
        [ "$(stat -c '%s' "$D/stores/$phase/$part.bin")" = "$size" ] || die 'short readonly store snapshot'
        record=$(sha256sum "$D/stores/$phase/$part.bin") || die 'readonly store snapshot hashing failed'
        digest=${record%% *}
        case "$digest" in ''|*[!0-9a-f]*) die 'invalid readonly store snapshot digest';; esac
        [ "${#digest}" = 64 ] || die 'invalid readonly store snapshot digest length'
        printf '%s\n' "$digest" > "$D/stores/$phase/$part.sha256"
        if [ "$phase" = post ]; then
            cmp -s "$D/stores/pre/$part.sha256" "$D/stores/post/$part.sha256" || {
                echo "REAL_STORE_CHANGED $part; NEVER restore or repair as part of this trial" >> "$D/fault"
                changed=1
            }
        fi
    done
    if [ "$phase" = pre ]; then
        # Coordinator MUST stream this stdout to a private host log before
        # activation; RAM evidence cannot survive the emergency reset.
        for part in modemst1 modemst2 fsg fsc; do
            printf 'PRE_STORE_SHA256 %s %s\n' "$part" "$(cat "$D/stores/pre/$part.sha256")"
        done
    fi
    [ "$changed" = 0 ]
}
access_flags() {
    awk -v mode="$3" '$1=="flags:" {n=0; for(i=1;i<=length($2);i++){d=substr($2,i,1); if(d!~/[0-7]/)exit 1; n=n*8+d} if(n%4==mode && int(n/1024)%2==0 && int(n/2097152)%2==0)ok=1} END {exit !ok}' "/proc/$1/fdinfo/$2"
}
kernel_evidence() {
    case "$1" in before-scan|identity|after) ;; *) die 'unknown kernel evidence phase';; esac
    timeout --foreground -s KILL 10 dmesg > "$D/kernel-$1.txt" || die 'kernel evidence unavailable'
    rc=0
    grep -Eiq 'dog_hb|task starvation|fatal error|remoteproc.*crash|ath10k.*(crash|failed|timeout|timed out|capability req rejected|found invalid board magic|could not fetch firmware files|could not init core)' "$D/kernel-$1.txt" || rc=$?
    case "$rc" in
        0) echo kernel-radio-fault >> "$D/fault"; die 'radio/kernel fault before further radio work; review private evidence';;
        1) ;;
        *) die 'kernel evidence could not be assessed';;
    esac
}
capture_identity() {
    # qmi.c emits these records before first registration. Exclude printk time.
    # board_id 0xff is "no board id reported", the normal value for WCN3990 on this phone
    # (run 14: chip_id 0x150, board_id 0xff). Only a session run (ID_BOARD_FF=1, set by
    # --session-body) accepts it; every older stage keeps the strict refusal.
    awk -v board_ff="${ID_BOARD_FF:-0}" '
    function trim(v) {gsub(/[[:space:]]+/," ",v); sub(/^ /,"",v); sub(/ $/,"",v); return v}
    function hex(v) {
        if(v !~ /^0x[0-9a-fA-F]+$/ || length(v)>10) {bad=1; return ""}
        v=tolower(v); sub(/^0x0*/,"",v); return "0x" (v==""?"0":v)
    }
    function device(v, result) {
        result=""
        while(match(v,/ath10k[^:]*:/)) {result=substr(v,RSTART,RLENGTH); v=substr(v,RSTART+RLENGTH)}
        if(!result || length(result)>128) bad=1
        sub(/:$/,"",result); return trim(result)
    }
    {
        p=index($0,"qmi chip_id ")
        if(p) {
            chips++; cd=device(substr($0,1,p-1)); body=substr($0,p)
            if(index(substr(body,2),"qmi chip_id ")) bad=1
            split(body,a,/[[:space:]]+/)
            if(a[1]!="qmi" || a[2]!="chip_id" || a[4]!="chip_family" || a[6]!="board_id" || a[8]!="soc_id") bad=1
            chip=hex(a[3]); family=hex(a[5]); board=hex(a[7]); soc=hex(a[9])
        }
        p=index($0,"qmi fw_version ")
        if(p) {
            firmwares++; fd=device(substr($0,1,p-1)); body=substr($0,p+length("qmi fw_version "))
            if(index(body,"qmi fw_version ")) bad=1
            split(body,a,/[[:space:]]+/); version=hex(a[1])
            if(!match(body,/^[^[:space:]]+[[:space:]]+fw_build_timestamp[[:space:]]+/)) {bad=1; next}
            body=substr(body,RLENGTH+1); divider=index(body," fw_build_id ")
            if(!divider) {bad=1; next}
            stamp=trim(substr(body,1,divider-1)); build=trim(substr(body,divider+length(" fw_build_id ")))
            if(!length(stamp) || length(stamp)>32 || !length(build) || length(build)>128 || stamp ~ /[^ -~]/ || build ~ /[^ -~]/) bad=1
        }
    }
    END {
        if(bad || chips!=1 || firmwares!=1 || cd!=fd || chip=="0xff" || (board=="0xff" && board_ff!=1)) exit 1
        print "device=" cd
        print "chip_id=" chip
        print "chip_family=" family
        print "board_id=" board
        print "soc_id=" soc
        print "fw_version=" version
        print "fw_build_timestamp=" stamp
        print "fw_build_id=" build
    }' "$D/kernel-identity.txt" > "$D/identity.txt" || die 'missing, ambiguous, malformed or default QMI identity; no scan'
    record=$(sha256sum "$D/identity.txt") || die 'canonical identity hashing failed'
    digest=${record%% *}
    case "$digest" in ''|*[!0-9a-f]*) die 'invalid canonical identity digest';; esac
    [ "${#digest}" = 64 ] || die 'invalid canonical identity digest length'
    printf '%s\n' "$digest" > "$D/identity.sha256"
    printf 'A9_WIFI_IDENTITY_SHA256 %s\n' "$digest"
}
scan_if_reviewed() {
    # Only run writes this RAM gate; standalone check accepts no digest arg.
    [ -e "$D/scan-reviewed.sha256" ] || return 0
    [ ! -e "$D/scan-attempted" ] || die 'one scan attempt per fresh trial'
    reviewed=$(cat "$D/scan-reviewed.sha256") || die 'reviewed scan gate unreadable'
    actual=$(cat "$D/identity.sha256") || die 'actual identity digest unavailable'
    case "$reviewed" in ''|*[!0-9a-f]*) die 'invalid reviewed scan digest';; esac
    [ "${#reviewed}" = 64 ] && [ "$reviewed" = "$actual" ] || die 'actual QMI identity differs from private reviewed scan identity'
    kernel_evidence before-scan
    monitors || die 'monitor/fault loss before reviewed scan'
    echo attempted > "$D/scan-attempted"
    timeout --foreground 10 "$TOOLS/iw" dev wlan0 info > "$D/wlan-info.txt" || die 'nl80211 wlan0 info'
    timeout --foreground -s KILL 10 ip link set wlan0 up || die 'wlan0 up'
    timeout --foreground 25 "$TOOLS/iw" dev wlan0 scan > "$D/scan.txt" 2> "$D/scan-error.txt" || die 'one bounded nl80211 scan failed'
    grep -c '^BSS ' "$D/scan.txt" > "$D/bss-count.txt" || :
    echo complete > "$D/scan-completed"
}
tool() {
    name=$1; expected=$2
    case "$expected" in ''|*[!0-9a-f]*) die 'reviewed tool SHA256 required';; esac
    [ "${#expected}" = 64 ] || die 'tool SHA256 length'
    case "$name" in
        strace) [ "$expected" = "$STRACE_SHA256" ] || die 'strace argument does not match the pinned static tool';;
        iw) [ "$expected" = "$IW_SHA256" ] || die 'iw argument does not match the pinned static tool';;
        audit-opens.awk) ;;
        *) die 'unknown coordinator tool';;
    esac
    [ ! -L "$TOOLS/$name" ] && [ "$(stat -c '%u:%h' "$TOOLS/$name")" = 0:1 ] || die 'unsafe tool ownership/link'
    [ "$(stat -c '%F' "$TOOLS/$name")" = 'regular file' ] || die 'coordinator tool must be a regular file'
    record=$(sha256sum "$TOOLS/$name") || die 'coordinator tool hashing failed'
    actual=${record%% *}
    [ "$actual" = "$expected" ] || die 'tool differs from pinned/reviewed artifact'
}
# Expected teardown (run 10): a helper that exited ONLY because the controller
# stopped MPSS. Strictly after stop-request AND the controller's own SIGUSR1
# for that role; exit code 0; the daemon's own A9_WIFI_TEARDOWN marker; no
# fault line. Anything else (earlier, nonzero, faulted, unsignalled) stays a
# failure. Only rmtfs and diag-router have a teardown mode.
expected_teardown() {
    case "$1" in rmtfs|diag-router|tqftpserv) ;; *) return 1;; esac
    # Either stop path counts, each with its OWN marker: the controller's stop-requested
    # or the watchdog's watchdog-stop (never reuse stop-requested for the watchdog: run_exit
    # treats an existing stop-requested as an interrupted stop and falls back).
    [ -e "$D/stop-requested" ] || [ -e "$D/watchdog-stop" ] || return 1
    [ -e "$D/teardown-signalled" ] || return 1
    grep -qx "$1" "$D/teardown-signalled" || return 1
    td_code=; read -r td_code < "$D/$1.exit" 2>/dev/null || return 1
    [ "$td_code" = 0 ] || return 1
    grep -q "^A9_WIFI_TEARDOWN $1 " "$D/$1.log" || return 1
    ! grep -Eq 'A9_WIFI_FAULT|A9_WIFI_FATAL' "$D/$1.log"
}
# Called right after stop-request, BEFORE the modem stop write.
teardown_signal() {
    for td_role in rmtfs diag-router tqftpserv; do
        case " $ROLES " in *" $td_role "*) ;; *) continue;; esac
        # A helper that already exited before the stop gets no teardown credit.
        [ ! -e "$D/$td_role.exit" ] || continue
        load_identity "$td_role" 2>/dev/null || continue
        td_record=$(process_record "$owned_child") || continue
        set -- $td_record
        [ "$1" = "$owned_tracer" ] && [ "$2" = "$owned_child_start" ] || continue
        case "$3" in Z|X|x) continue;; esac
        kill -USR1 "$owned_child" 2>/dev/null && echo "$td_role" >> "$D/teardown-signalled" || :
    done
    stamp teardown-signalled
}
# The watchdog's own stop (run 14): same SIGUSR1 teardown phase, separate marker.
watchdog_teardown() {
    echo requested > "$D/watchdog-stop" || return 0
    teardown_signal
}
# The earliest faulting role (by exit-file mtime; ROLES order breaks ties) and its first
# fault line, written once to $D/first-fault, printed ahead of the cleanup evidence.
first_fault() {
    [ -d "$D" ] || return 0
    if [ ! -e "$D/first-fault" ]; then
        ff_role=none
        for ff_r in $ROLES; do
            [ -e "$D/$ff_r.exit" ] || continue
            if expected_teardown "$ff_r"; then continue; fi
            if [ "$ff_role" = none ] || [ "$D/$ff_role.exit" -nt "$D/$ff_r.exit" ]; then ff_role=$ff_r; fi
        done
        ff_code=none; ff_reason=
        if [ "$ff_role" != none ]; then
            read -r ff_code < "$D/$ff_role.exit" || ff_code=none
            ff_reason=$(grep -E 'A9_WIFI_FAULT|A9_WIFI_DIAG_FAULT' "$D/$ff_role.log" 2>/dev/null | head -n 1) || ff_reason=
        else
            [ ! -r "$D/fault" ] || read -r ff_reason < "$D/fault" || ff_reason=
        fi
        [ "$ff_role" != none ] || [ -n "$ff_reason" ] || return 0   # nothing faulted yet
        echo "A9_WIFI_FIRST_FAULT role=$ff_role exit=$ff_code reason=${ff_reason:-none}" > "$D/first-fault" || return 0
    fi
    read -r ff_line < "$D/first-fault" || return 0
    echo "$ff_line"
    kmsg_note "$ff_line"
}
# Best-effort evidence: SHA-256 of rmtfs's four RAM copies after the modem is
# offline, compared with the PRE hashes of the real stores they were seeded
# from. Hashes only; never fails the stop.
ramcopy_evidence() {
    : > "$D/ramcopy-sha256.txt" || return 0
    rc_live=0
    if load_identity rmtfs 2>/dev/null && rc_record=$(process_record "$owned_child"); then
        set -- $rc_record
        [ "$2" != "$owned_child_start" ] || rc_live=1
    fi
    # rmtfs already gone (clean teardown exit): use its own hash line from its
    # log (written at exit, hashes only); the file always exists (run 11).
    rc_line=
    [ "$rc_live" = 1 ] || rc_line=$(grep '^A9_WIFI_RMTFS_STORE_SHA256 ' "$D/rmtfs.log" 2>/dev/null | tail -n 1) || rc_line=
    for rc_pair in 10:modemst1 11:modemst2 12:fsg 15:fsc; do
        rc_fd=${rc_pair%%:*}; rc_name=${rc_pair#*:}
        rc_sum=unreadable
        if [ "$rc_live" = 1 ]; then
            rc_sum=$(sha256sum "/proc/$owned_child/fd/$rc_fd" 2>/dev/null) || rc_sum=unreadable
            rc_sum=${rc_sum%% *}
        else
            for rc_word in $rc_line; do
                case "$rc_word" in "$rc_name="*) rc_sum=${rc_word#*=};; esac
            done
        fi
        # Label against the SEED the copy was loaded from (vendor assets.sha256); the PRE
        # hash of the real partition is kept as a separate field (they can differ: the seeds
        # are an older snapshot, run 13).
        rc_seed=unknown
        if [ -r /vendor/etc/a9-wifi/assets.sha256 ]; then
            while read -r rc_n rc_b rc_h rc_x; do [ "$rc_n" != "$rc_name" ] || rc_seed=$rc_h; done < /vendor/etc/a9-wifi/assets.sha256 || :
        fi
        rc_pre=unknown; [ ! -r "$D/stores/pre/$rc_name.sha256" ] || read -r rc_pre rc_rest < "$D/stores/pre/$rc_name.sha256" || :
        if [ "$rc_sum" = unreadable ] || [ "$rc_seed" = unknown ]; then rc_cmp=unknown
        elif [ "$rc_sum" = "$rc_seed" ]; then rc_cmp=same; else rc_cmp=changed; fi
        if [ "$rc_sum" = unreadable ] || [ "$rc_pre" = unknown ]; then rc_prec=unknown
        elif [ "$rc_sum" = "$rc_pre" ]; then rc_prec=same; else rc_prec=differs; fi
        echo "A9_WIFI_RAMCOPY $rc_name $rc_sum $rc_cmp vs=seed pre-store=$rc_prec source=$([ "$rc_live" = 1 ] && echo live-fd || echo rmtfs-log)" >> "$D/ramcopy-sha256.txt" || :
    done
    cat "$D/ramcopy-sha256.txt" || :
}
audit() {
    audit_failed_role=
    for role in $ROLES; do
        audit_failed_role=$role
        [ -s "$D/$role.trace" ] || return 1
        awk -f "$AUDIT" "$D/$role.trace" > "$D/$role.audit" || return 1
        grep -qx "A9_WIFI_READY $role" "$D/$role.log" || return 1
        # A helper that already exited on its own (run 8: rmtfs on a QRTR
        # control packet) is a failed audit, and the fault names the role.
        [ ! -e "$D/$role.exit" ] || expected_teardown "$role" || return 1
    done
    audit_failed_role=
}
# Read one inventory per gate, rather than exec stat repeatedly for the same
# FD. Snapshots are local to this call, never reused across gates/DIAG lookup.
# A vanished FD makes stat fail closed. All checks still sample live processes;
# freshness before/after the gate binds the snapshot to admitted lifetimes.
fd_record() {
    # String parsing avoids mksh here-document temporary files per lookup.
    fd_remaining=$fd_inventory
    while [ -n "$fd_remaining" ]; do
        fd_line=${fd_remaining%%'
'*}
        if [ "$fd_remaining" = "$fd_line" ]; then fd_remaining=;
        else fd_remaining=${fd_remaining#*'
'}; fi
        fd_path=${fd_line%%'|'*}; fd_line=${fd_line#*'|'}
        [ "$fd_path" = "$1" ] || continue
        fd_kind=${fd_line%%'|'*}; fd_line=${fd_line#*'|'}
        fd_size=${fd_line%%'|'*}; fd_identity=${fd_line#*'|'}
        return 0
    done
    return 1
}
descriptor_inventory() {
    # Keep the original complete snapshot and terminal failure. Classify its
    # stderr in memory: a vanished FD and denied proc access need different
    # repairs, and neither may silently admit a partial inventory.
    inventory_rc=0
    fd_inventory=$(stat -L -c '%n|%F|%s|%d:%i' /proc/$pid/fd/* 2>&1) || inventory_rc=$?
    [ "$inventory_rc" != 0 ] || return 0
    inventory_error=0
    case "$fd_inventory" in
        *'Permission denied'*|*'Operation not permitted'*) inventory_error=13;;
        *'No such file or directory'*) inventory_error=2;;
    esac
    fd_inventory=
    kmsg_note "descriptor-inventory-failed role=$role rc=$inventory_rc error_class=$inventory_error"
    return 1
}
descriptors() {
        descriptor_inventory || die "$role descriptor inventory unavailable"
        [ ! -e "/proc/$pid/fd/30" ] || die "$role retained executable capability"
        case "$role" in
            rmtfs) fixed='10 11 12 13 14 15 16';;
            tqftpserv) fixed='20 21 22';;
            *) fixed='';;
        esac
        for required in $fixed; do [ -e "/proc/$pid/fd/$required" ] || die "$role missing mandatory admitted capability"; done
        if [ "$role" = rmtfs ]; then
            seen=''
            for required in 10 11 12 15; do
                expected=2097152; [ "$required" != 15 ] || expected=131072
                object=/proc/$pid/fd/$required
                fd_record "$object" || die 'rmtfs fixed RAM descriptor missing'
                [ "$fd_kind:$fd_size" = "regular file:$expected" ] && [ "$(stat -L -f -c '%T' "$object")" = tmpfs ] && access_flags "$pid" "$required" 2 || die 'rmtfs fixed RAM shape/access'
                identity=$fd_identity
                case " $seen " in *" $identity "*) die 'rmtfs seed roles alias';; esac
                seen="$seen $identity"
            done
            fd_record "/proc/$pid/fd/13" || die 'rmtfs RAM transport missing'
            case "$fd_kind" in
                'character special file'|'character device') ;;
                *) die 'rmtfs RAM transport type/access';;
            esac
            access_flags "$pid" 13 2 || die 'rmtfs RAM transport type/access'
            fd_record "/proc/$pid/fd/14" || die 'rmtfs QRTR socket missing'
            [ "$fd_kind" = socket ] || die 'rmtfs fixed QRTR socket missing'
            fd_record "/proc/$pid/fd/16" || die 'rmtfs admission descriptor missing'
            admission_identity=$fd_identity
            [ "$fd_kind:$fd_size" = 'regular file:13' ] && access_flags "$pid" 16 0 &&
                [ "$(stat -L -f -c '%T' "/proc/$pid/fd/16")" = tmpfs ] &&
                [ "$admission_identity" = "$(stat -c '%d:%i' "$D/rmtfs.admission")" ] || die 'rmtfs admission capability differs from controller gate'
        fi
        if [ "$role" = tqftpserv ]; then
            fd_record "/proc/$pid/fd/20" || die 'tqftp firmware missing'
            [ "$fd_kind:$fd_size" = 'regular file:3814580' ] && [ "$(readlink "/proc/$pid/fd/20")" = /vendor/firmware/qcom/sm6115/hisense/hlte556n/wlanmdsp.mbn ] && access_flags "$pid" 20 0 || die 'tqftp immutable firmware capability'
            for required in 21 22; do
                object=/proc/$pid/fd/$required
                fd_record "$object" || die 'tqftp RAM scratch missing'
                case "$fd_kind" in
                    'regular file'|'regular empty file') ;;
                    *) die 'tqftp fixed RAM shape/access';;
                esac
                [ "$fd_size" -le 1048576 ] && [ "$(stat -L -f -c '%T' "$object")" = tmpfs ] && access_flags "$pid" "$required" 2 || die 'tqftp fixed RAM shape/access'
            done
            for pair in 20:21 20:22 21:22; do
                left=${pair%:*}; right=${pair#*:}
                fd_record "/proc/$pid/fd/$left" || die 'tqftp alias check missing left FD'
                left_identity=$fd_identity
                fd_record "/proc/$pid/fd/$right" || die 'tqftp alias check missing right FD'
                [ "$left_identity" != "$fd_identity" ] || die 'tqftp admitted roles alias'
            done
        fi
        for fd in /proc/$pid/fd/*; do
            n=${fd##*/}
            fd_record "$fd" || die "$role descriptor changed during inventory"
            kind=$fd_kind
            case "$kind" in
                'block special file'|'block device') die "$role has block FD";;
                'regular file'|'regular empty file')
                    case "$role" in
                        rmtfs)
                            case "$n" in 10|11|12|15|16) ;; *) die 'rmtfs extra regular capability';; esac
                            [ "$(stat -L -f -c '%T' "$fd")" = tmpfs ] || die "$role non-RAM writable backing";;
                        tqftpserv)
                            admitted=0; identity=$fd_identity
                            for anchor in 20 21 22; do
                                fd_record "/proc/$pid/fd/$anchor" || die 'tqftp session anchor missing'
                                [ "$identity" = "$fd_identity" ] || continue
                                if [ "$anchor" = 20 ]; then access_flags "$pid" "$n" 0 || die 'firmware session is not readonly';
                                else
                                    access_flags "$pid" "$n" 2 || die 'scratch session access flags'
                                    [ "$(stat -L -f -c '%T' "$fd")" = tmpfs ] || die 'scratch session not RAM'
                                fi
                                admitted=1; break
                            done
                            [ "$admitted" = 1 ] || die 'TFTP session capability outside fixed admitted inodes';;
                        *) die "$role unexpected regular-file capability";;
                    esac;;
                'character special file'|'character device')
                    case "$role:$n" in
                        rmtfs:13)
                            [ "$(readlink "$fd")" = /dev/qcom_rmtfs_mem1 ] || die 'unexpected RAM character capability';;
                        *:0) [ "$(readlink "$fd")" = /dev/null ] || die 'unexpected stdin device';;
                        *) die "$role unexpected device capability";;
                    esac;;
                socket|fifo|'FIFO (named pipe)') ;; *) die "$role unexpected descriptor type";;
            esac
        done
}
observe() {
    for role in $ROLES; do
        tracer=$(cat "$D/$role.tracer")
        # strace owns exactly one launched helper. No pidof-by-name ambiguity.
        record=$(process_record "$tracer") || die "$role tracer identity unavailable"
        set -- $record
        tracer_start=$2
        [ "$2" = "$(cat "$D/$role.tracer-start")" ] && [ "$(readlink "/proc/$tracer/exe")" = "$TOOLS/strace" ] || die "$role tracer identity changed"
        children=$(trace_children "$tracer") || die "$role proc topology unavailable"
        set -- $children
        [ "$#" = 1 ] || die "$role trace process topology changed"
        pid=$1
        [ "$(readlink "/proc/$pid/exe")" = "/vendor/bin/$role" ] || die "$role executable identity"
        record=$(process_record "$pid") || die "$role process identity unavailable"
        set -- $record
        [ "$1" = "$tracer" ] || die "$role parent changed"
        child_start=$2
        s=/proc/$pid/status
        grep -Eq '^Uid:[[:space:]]+65534[[:space:]]+65534[[:space:]]+65534[[:space:]]+65534$' "$s" || die "$role UID"
        grep -Eq '^Gid:[[:space:]]+65534[[:space:]]+65534[[:space:]]+65534[[:space:]]+65534$' "$s" || die "$role GID"
        grep -Eq '^Groups:[[:space:]]*$' "$s" || die "$role supplementary groups"
        for cap in CapInh CapPrm CapEff CapBnd CapAmb; do grep -Eq "^$cap:[[:space:]]+0+$" "$s" || die "$role capability $cap"; done
        grep -Eq '^NoNewPrivs:[[:space:]]+1$' "$s" && grep -Eq '^Seccomp:[[:space:]]+2$' "$s" || die "$role seccomp/NNP"
        [ "$(cat "/proc/$pid/attr/current")" = u:r:a9_wifi_radio:s0 ] || die "$role production SELinux domain"
        [ ! -e "/proc/$pid/root/dev" ] && [ ! -e "/proc/$pid/root/proc" ] && [ ! -e "/proc/$pid/root/sys" ] || die "$role old filesystem reachable"
        # Root mount must be the isolated noswap/noexec/nodev/nosuid tmpfs.
        awk '$5=="/" { if ($0 ~ / - tmpfs / && $6 ~ /noexec/ && $6 ~ /nodev/ && $6 ~ /nosuid/ && $0 ~ /noswap/) good=1; else bad=1 } END {exit !(good && !bad)}' "/proc/$pid/mountinfo" || die "$role RAM root options"
        descriptors
        # Bind the expensive proof to these exact process lifetimes. The final
        # guarded freshness check detects any exit/reparent/exec since this read.
        printf '%s %s %s %s\n' "$tracer" "$tracer_start" "$pid" "$child_start" > "$D/$role.identity"
        echo "$role $pid confined" >> "$D/observations.txt"
    done
}
case "${1:-}" in
    --offline-proof)
        offline; admission_file 0 || die 'static RMTFS admission must be pending'
        topology before || die 'ambiguous/non-MPSS remote QRTR topology before arming'
        observe
        stamp calibration-audit-begin
        clock_now; audit_begin=$clock_value
        audit || die 'offline trace audit failed'
        clock_now; echo "$((clock_value - audit_begin + 1))" > "$D/audit-cost"
        stamp calibration-audit-end
        offline
        ;;
    --stores-pre)
        stamp stores-pre-begin
        clock_now; store_begin=$clock_value
        stores pre
        clock_now; echo "$((clock_value - store_begin + 1))" > "$D/store-cost"
        stamp stores-pre-end
        ;;
    --stop-state)
        m=$(modem)
        case "$(cat "$m/state")" in offline) exit 0;; running|crashed) ;; *) exit 1;; esac
        # ONE writer (watchdog, controller stop and guardian all arrive here): the
        # mkdir winner writes; the others wait for offline instead of writing a
        # second `stop` (run 12: the loser's write failed on the already-stopped
        # modem and the failure became a hard reset). A failed write is only an
        # error if the modem is still not offline afterwards. A lock whose owner
        # died while the modem is still up (stale) is taken over, never waited on.
        have_lock=0; waited=0
        stop_write() {
            have_lock=1; echo "$$" > "$D/stop-writer/pid" || :
            echo stop > "$m/state" || [ "$(cat "$m/state")" = offline ] || exit 1
        }
        if mkdir "$D/stop-writer" 2>/dev/null; then stop_write; fi
        while [ "$(cat "$m/state")" != offline ]; do
            if [ "$have_lock" = 0 ]; then
                waited=$((waited + 1)); wpid=
                read -r wpid < "$D/stop-writer/pid" 2>/dev/null || :
                if { [ -n "$wpid" ] && ! kill -0 "$wpid" 2>/dev/null; } || { [ -z "$wpid" ] && [ "$waited" -ge 3 ]; }; then
                    rm -rf "$D/stop-writer"
                    if mkdir "$D/stop-writer" 2>/dev/null; then stop_write; fi
                fi
            fi
            sleep 1
        done
        ;;
    --stop-modem)
        setprop vendor.a9ml.wifi.android.ready 0 || {
            echo failed > "$D/android-stop-failed" || fallback stagestop_modem-1
            echo Android-gate-stop-failed >> "$D/fault" || :
        }
        stop_modem
        ;;
    --guardian)
        clock_now || fallback stageguardian-1
        guard_origin=$clock_value
        if [ -e "$D/guard-armed.uptime" ]; then anchor || fallback stageguardian-2; guard_origin=$guard_start; fi
        total_load || fallback stageguardian-5
        hard_end=$((guard_origin + TOTAL + 30))
        echo ready > "$D/guardian-ready"
        # Independent of the request/death watcher: even a blocked lookup or
        # dead controller cannot postpone the total-trial stop deadline.
        clock_now || fallback stageguardian-3
        remaining=$((guard_origin + TOTAL - clock_value))
        [ "$remaining" -le 0 ] || sleep "$remaining"
        if [ ! -e "$D/complete" ]; then
            # Never wait for the orderly stop here: a blocked sysfs write or
            # timeout wait must not suppress this process's hard reset timer.
            /system/bin/sh "$SELF" --guardian-stop </dev/null >"$D/guardian-stop.log" 2>&1 &
            echo "$!" > "$D/guardian-stop.pid" || :
        fi
        remaining=$(( hard_end - $(cut -d. -f1 /proc/uptime) ))
        [ "$remaining" -le 0 ] || sleep "$remaining"
        [ -e "$D/complete" ] || fallback stageguardian-4
        ;;
    --guardian-stop)
        # Evidence may still be running after a confirmed orderly stop. The
        # independent 240 s stop still executes; only a missed stop is a fault.
        # Keep all potentially blocking proof/stop I/O in this child so the
        # parent's 270 s reset cannot be postponed by it.
        stop_proven_offline || { echo independent-total-240s-deadline >> "$D/fault" || :; }
        stop_modem
        ;;
    --watchdog)
        clock_now || fallback stagewatchdog-1
        guard_origin=$clock_value
        if [ -e "$D/guard-armed.uptime" ]; then anchor || fallback stagewatchdog-2; guard_origin=$guard_start; fi
        total_load || fallback stagewatchdog-5
        end=$((guard_origin + TOTAL))
        echo ready > "$D/watchdog-ready"
        while [ ! -e "$D/complete" ]; do
            if retire_after_offline; then exit 0; fi
            why=
            [ ! -e "$D/abort" ] || why=operator-abort
            [ ! -e "$D/fault" ] || why=companion-or-controller-fault
            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || why=total-trial-240s-deadline
            for role in $ROLES; do
                # A helper that exited cleanly BECAUSE we stopped MPSS is not a fault.
                if [ -e "$D/$role.exit" ] && expected_teardown "$role"; then continue; fi
                [ ! -e "$D/$role.exit" ] || why=companion-death
                if [ -e "$D/$role.tracer" ]; then
                    tracer=$(cat "$D/$role.tracer")
                    kill -0 "$tracer" 2>/dev/null || why=tracer-or-logging-loss
                    [ "$(readlink "/proc/$tracer/exe" 2>/dev/null || :)" = "$TOOLS/strace" ] || why=tracer-identity-loss
                else why=missing-tracer; fi
                grep -Eq 'A9_WIFI_FAULT|A9_WIFI_FATAL' "$D/$role.log" && why=companion-reported-fault || :
            done
            if [ -e "$D/start-attempted" ]; then
                m=$(modem) || fallback stagewatchdog-3
                state=$(cat "$m/state") || fallback stagewatchdog-4
                case "$state" in
                    running) ;;
                    offline) [ ! -e "$D/started.uptime" ] || [ -e "$D/stop-requested" ] || why=unexpected-remoteproc-offline;;
                    *) why=remoteproc-crash;;
                esac
            fi
            if [ -e "$D/services-ready" ] && [ ! -e "$D/stop-requested" ]; then
                services || { [ -e "$D/stop-requested" ] || why=PD-or-WLFW-service-loss; }
            fi
            if [ -n "$why" ]; then
                # Stop may have proven offline and begun intentional teardown
                # while the checks above were observing companion processes.
                if retire_after_offline; then exit 0; fi
                echo "$why" >> "$D/fault" || :
                stream_evidence "watchdog-$why" || :
                watchdog_teardown || :
                stop_modem
                echo stopped > "$D/watchdog-stopped"
                # Leave surviving companions/RAM alive for main stop/evidence.
                exit 1
            fi
            sleep 1
        done
        ;;
    --worker)
        role=${2:-}; role_ok "$role"
        [ ! -e "$D/prepare-failed" ] && [ ! -e "$D/abort" ] && [ ! -e "$D/stop-requested" ] || exit 1
        cd /
        # Both daemon stdout/stderr inherit a write-only pipe, as required by
        # the native launcher. strace output is a separate root-only file.
        { /system/bin/runcon u:r:a9_wifi_radio:s0 "$TOOLS/strace" --kill-on-exit -f -qq -yy -s 256 \
              -e 'trace=%file,%process,mount,umount2,pivot_root,prctl,seccomp,fcntl,close,close_range,socket,bind,connect,fstat' \
              -o "$D/$role.trace" /vendor/bin/a9-wifi-sandbox "$role" &
          tracer=$!
          record=$(process_record "$tracer") || { kill -KILL "$tracer" 2>/dev/null || :; exit 1; }
          set -- $record
          echo "$2" > "$D/$role.tracer-start"
          echo "$tracer" > "$D/$role.tracer"
          rc=0; wait "$tracer" || rc=$?; echo "$rc" > "$D/$role.exit"
        } 2>&1 | cat > "$D/$role.log"
        ;;
    prepare)
        [ "$#" = 4 ] || die 'prepare STRACE_SHA256 IW_SHA256 AUDIT_AWK_SHA256'
        tool strace "$2"; tool iw "$3"; tool audit-opens.awk "$4"
        for parent in /mnt/a9-wifi "$TOOLS"; do
            [ -d "$parent" ] && [ ! -L "$parent" ] && [ "$(stat -c '%u:%a' "$parent")" = 0:700 ] || die 'root-only nonreplaceable coordinator parents required'
        done
        tools_mount
        [ ! -e "$D" ] || die 'one prepare attempt per fresh boot; preserve previous evidence'
        mkdir -m 700 "$D"
        mount -t tmpfs -o noswap,nodev,nosuid,noexec,size=32m,mode=0700 a9-trial "$D" || die 'private noswap trial evidence mount'
        [ "$(stat -f -c '%T' "$D")" = tmpfs ] || die 'trial evidence must stay in RAM'
        . /vendor/bin/a9ml-test-data.sh || die 'test /data guard missing'
        a9ml_test_data || die 'RAM ext4 or checked ROW29 f2fs /data required'
        "$TOOLS/strace" --help | grep -q -- --kill-on-exit || die 'strace must support kill-on-exit'
        [ -x /system/bin/runcon ] || die 'runcon required to preserve production radio SELinux credentials'
        for role in $ROLES; do
            case "$role" in pd-mapper) svc=vendor.pd_mapper;; diag-router) svc=vendor.diag_router;; *) svc=vendor.$role;; esac
            [ "$(getprop "init.svc.$svc")" != running ] || die 'untraced companion already running'
            mkdir -p "/mnt/a9-wifi/$role"; chmod 700 "/mnt/a9-wifi/$role"
        done
        # Fixed reviewed module list. Current init has disabled service
        # definitions only; the stale legacy persistent opt-in is rejected.
        /vendor/bin/a9ml-wifi-firmware.sh || die 'root-owned firmware bridge setup'
        [ "$(cat /sys/module/firmware_class/parameters/path)" = /mnt/a9-wifi/firmware ] || die 'root-owned radio/E Ink firmware bridge not configured before probe'
        for path in qcom ath10k regulatory.db regulatory.db.p7s hisense; do
            [ -L "/mnt/a9-wifi/firmware/$path" ] || die 'firmware bridge incomplete'
        done
        . /vendor/bin/a9ml-module-load.sh || die 'module loader'
        while IFS= read -r module; do
            case "$module" in ''|'#'*) continue;; *[!a-zA-Z0-9_.-]*|.*|*..*) die 'invalid module name';; *.ko) ;; *) die 'invalid module suffix';; esac
            a9ml_module_load "$module" || die 'module load'
        done < /vendor/lib/modules/modules.wifi
        offline
        m=$(modem)
        echo disabled > "$m/recovery" || die 'cannot disable remoteproc crash recovery'
        [ "$(cat "$m/recovery")" = disabled ] || die 'remoteproc recovery-disable readback failed'
        (set -C; printf 'A9RMTFS1:0:0\n' > "$D/rmtfs.admission") || die 'fresh static RMTFS admission file required'
        chmod 600 "$D/rmtfs.admission"
        admission_file 0 || die 'static RMTFS admission file invalid'
        prepare_workers=
        trap 'prepare_exit $?' EXIT
        trap 'exit 1' HUP INT TERM
        record=$(process_record "$$") || die 'prepare process identity unavailable'
        set -- $record
        printf '%s %s\n' "$$" "$2" > "$D/prepare-owner"
        for role in $ROLES; do
            nohup /system/bin/sh "$SELF" --worker "$role" </dev/null >"$D/$role.worker-log" 2>&1 &
            prepare_workers="$prepare_workers $!"
        done
        end=$(( $(cut -d. -f1 /proc/uptime) + 20 ))
        while :; do
            [ ! -e "$D/abort" ] || die 'prepare aborted'
            ready=1
            for role in $ROLES; do
                [ ! -e "$D/$role.exit" ] || die "$role exited before readiness"
                grep -qx "A9_WIFI_READY $role" "$D/$role.log" 2>/dev/null || ready=0
            done
            [ "$ready" = 1 ] && break
            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || die '20-second readiness timeout'
            sleep 1
        done
        observe; audit; offline; diag_lookup
        echo prepared > "$D/stage"
        echo finished > "$D/prepare-finished"
        trap - EXIT HUP INT TERM
        echo 'PREPARED: confined companions ready; MPSS is still offline. Review evidence before start.'
        ;;
    --start-guarded)
        [ "$#" = 2 ] && [ "$2" = I_AUTHORIZE_ONE_MPSS_START ] || die 'explicit one-start acknowledgement required'
        [ -e "$D/watchdog-ready" ] && [ -e "$D/guardian-ready" ] || die 'independent abort channels not armed'
        monitors || die 'monitor readiness is stale or a fault/abort is pending'
        [ "$(cat "$D/stage")" = prepared ] && [ ! -e "$D/start-attempted" ] || die 'not fresh prepared state'
        m=$(modem)
        [ "$(cat "$m/state")" = offline ] || die 'MPSS must be offline'
        firmware_gate || die 'firmware bridge changed or modem firmware missing before sole MPSS start'
        stamp start-gate-before-begin
        mutable_gate || die 'mutable companion gate changed since offline proof'
        stamp start-gate-before-end
        stamp start-diag-begin
        diag_lookup
        stamp start-diag-end
        stamp start-gate-after-begin
        mutable_gate || die 'mutable companion gate lost during DIAG lookup'
        stamp start-gate-after-end
        stamp start-freshness-end
        [ "$(cat "$m/state")" = offline ] || die 'MPSS changed before sole start'
        [ "$(cat "$m/recovery")" = disabled ] || die 'remoteproc recovery was re-enabled'
        monitors || die 'independent abort channel lost before sole start'
        start_budget || die 'cumulative start/observation/cleanup budget exhausted'
        # Everything with forks happens before the start write. The watcher is
        # armed (blocked on its wake-up token) and dd is already running.
        # The watcher's deadline is the shared ten-second startup budget,
        # anchored (slightly early, never late) before the start write.
        clock_now; rel_end=$((clock_value + 10))
        stamp release-prepare-begin
        release_prepare || die 'release watcher preparation failed'
        rel_wrapper=
        trap release_abort EXIT
        ( release_watch | dd of="$D/rmtfs.admission" bs=1 seek=11 count=1 conv=notrunc 2>> "$D/rmtfs-admission.dd-log" ) &
        rel_wrapper=$!
        stamp release-prepare-end
        stamp start-attempt
        echo attempted > "$D/start-attempted" # no retry even if state write fails
        echo pending > "$D/start-write-pending"
        clock_now; end=$((clock_value + 10))
        # The sysfs write itself can block in firmware startup. Bound it too;
        # the remaining running wait shares this deadline, never a fresh ten.
        timeout --foreground -s KILL 10 /system/bin/sh -c 'echo start > "$1" && echo returned > "$2/start-write-returned"' sh "$m/state" "$D" || die 'explicit MPSS start failed or timed out'
        # Wake the watcher: one builtin write, no fork. The first OPEN may be
        # queued ~15 ms after MPSS up; it cannot touch RAM stores until the
        # minimal check below releases the inherited gate.
        clock_now; [ "$clock_value" -lt "$end" ] || die 'MPSS startup budget exhausted'
        echo go >&9
        stamp start-returned
        rel_status=0
        wait "$rel_wrapper" || rel_status=$?
        rel_wrapper=
        while IFS= read -r rel_line; do echo "$rel_line"; done < "$D/release-stamps" || :
        read -r rel_result < "$D/release-watch.status" || rel_result=missing
        [ "$rel_result" = released ] && [ "$rel_status" = 0 ] || die "minimal MPSS-only release check refused ($rel_result)"
        admission_file 1 || die 'RMTFS gate publication readback failed'
        stamp rmtfs-admitted
        # Full census AFTER release (forks, nameserver). Disagreement revokes
        # the gate, faults and stops: same terminal outcome as a pre-release
        # refusal, minus the modem-visible delay.
        stamp census-begin
        rc=0; topology after || rc=$?
        stamp census-end
        if [ "$rc" != 0 ]; then
            release_revoke
            # topology() requires running: a modem that already crashed (run 9)
            # makes the census fail as a CONSEQUENCE. Name the first cause.
            if [ "$(cat "$m/state")" != running ]; then
                echo 'modem already left running before the post-release census (consequence of an earlier rmtfs/modem fault; see those lines)' >> "$D/fault" || :
                die 'post-release census skipped as inconclusive: modem no longer running'
            fi
            echo 'post-release QRTR topology census disagrees with the minimal check' >> "$D/fault" || :
            die 'post-release ambiguous/non-MPSS remote QRTR topology'
        fi
        if ! monitors || [ -e "$D/stop-requested" ]; then
            release_revoke
            die 'monitor/fault loss after RMTFS admission'
        fi
        [ "$(cat "$m/state")" = running ] && [ "$(cat "$m/recovery")" = disabled ] || { release_revoke; die 'MPSS state/recovery changed after RMTFS admission'; }
        admission_file 1 || die 'RMTFS gate changed after publication'
        clock_now; [ "$clock_value" -lt "$end" ] || { release_revoke; die 'MPSS startup budget exhausted'; }
        trap - EXIT
        exec 9>&-
        clock_now
        # Round the observation anchor UP: integer truncation must not shorten
        # the promised 180 healthy seconds. Admission reserves this extra second.
        echo "$((clock_value + 1))" > "$D/started.uptime"
        stamp running
        echo started > "$D/stage"
        ;;
    --start)
        [ "$#" = 2 ] && [ "$2" = I_AUTHORIZE_ONE_MPSS_START ] || die 'explicit one-start acknowledgement required'
        end=$(activation_end) || die 'guard anchor unavailable'
        bounded_until "$end" --start-guarded "$2" || die 'bounded activation failed'
        end=$(active_end) || die 'active deadline unavailable'
        bounded_until "$end" --post-start || die 'bounded post-start readiness failed'
        ;;
    --post-start)
        m=$(modem)
        end=$(( $(cut -d. -f1 /proc/uptime) + 30 ))
        # Run 13: the DIAG handshake is not needed for WLAN (runs 11-13 reached ath10k core init
        # without it). ONLY under --observe-wlan (marker written by `run`), a missing handshake is
        # accepted when diag-router is alive and fault-free, monitors are healthy, MPSS runs and
        # WLFW 69 and PD 64 are visible; a note records it. The 30 s bound and every other check
        # are unchanged; the plain 180 s check path keeps requiring the handshake.
        while ! grep -Eq '^A9_WIFI_DIAG_HANDSHAKE($| )' "$D/diag-router.log"; do
            monitors || die 'monitor/fault loss during DIAG handshake'
            [ ! -e "$D/fault" ] || die 'watchdog fault during DIAG handshake'
            [ "$(cat "$m/state")" = running ] || die 'MPSS stopped before DIAG control handshake'
            if [ -e "$D/observe-wlan-mode" ] && [ ! -e "$D/diag-router.exit" ] &&
               ! grep -Eq 'A9_WIFI_FAULT|A9_WIFI_FATAL' "$D/diag-router.log" && services; then
                echo 'A9_WIFI_NOTE diag-handshake-absent (observe-wlan: diag-router alive, no fault, services 64 and 69 visible)'
                grep -E 'A9_WIFI_DIAG_NOTE|A9_WIFI_DIAG first-|A9_WIFI_DIAG command-peer' "$D/diag-router.log" | tail -n 5 || :
                echo absent > "$D/diag-handshake-absent" || :
                break
            fi
            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || die '30-second post-start DIAG control-handshake timeout'
            sleep 1
        done
        end=$(( $(cut -d. -f1 /proc/uptime) + 30 ))
        until services; do
            monitors || die 'monitor/fault loss while waiting for PD/WLFW'
            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || die '30-second PD64/WLFW69 readiness timeout'
            sleep 1
        done
        echo ready > "$D/services-ready"
        if [ -e "$D/diag-handshake-absent" ]; then echo 'STARTED: DIAG handshake absent (noted); services 64 and 69 visible; monitor continues under independent abort deadlines.'
        else echo 'STARTED: actual DIAG control handshake observed; monitor continues under independent abort deadlines.'; fi
        ;;
    run)
        [ "$#" -ge 4 ] || die 'run requires single-start, residual-risk and preliminary-evidence arguments'
        [ "$2" = I_AUTHORIZE_ONE_MPSS_START ] && [ "$3" = I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF ] || die 'single-start and residual-risk acknowledgements required'
        case "$4" in ''|*[!0-9a-f]*) die 'reviewed preliminary production-offline evidence SHA256 required';; esac
        [ "${#4}" = 64 ] || die 'preliminary evidence digest length'
        start_ack=$2; preliminary=$4; shift 4
        scan_reviewed=; android=0; observe_wlan=0; session_seconds=; identity_reviewed=
        while [ "$#" -gt 0 ]; do
            case "$1" in
                --session)
                    [ "$#" -ge 2 ] && [ -z "$session_seconds" ] || die 'one --session length required'
                    session_seconds=$2; shift 2
                    case "$session_seconds" in ''|*[!0-9]*) die 'invalid session length';; esac
                    [ "$session_seconds" -ge 600 ] && [ "$session_seconds" -le 14400 ] || die 'session length must be 600..14400 s';;
                --identity-reviewed)
                    [ "$#" -ge 2 ] && [ -z "$identity_reviewed" ] || die 'one --identity-reviewed digest required'
                    identity_reviewed=$2; shift 2
                    case "$identity_reviewed" in ''|*[!0-9a-f]*) die 'invalid reviewed identity digest';; esac
                    [ "${#identity_reviewed}" = 64 ] || die 'reviewed identity digest length';;
                --scan-reviewed)
                    [ "$#" -ge 2 ] && [ -z "$scan_reviewed" ] || die 'one --scan-reviewed digest required'
                    scan_reviewed=$2; shift 2
                    case "$scan_reviewed" in ''|*[!0-9a-f]*) die 'invalid reviewed scan digest';; esac
                    [ "${#scan_reviewed}" = 64 ] || die 'reviewed scan digest length';;
                --android) [ "$android" = 0 ] || die 'duplicate Android stage'; android=1; shift;;
                --observe-wlan) [ "$observe_wlan" = 0 ] || die 'duplicate WLAN observation'; observe_wlan=1; shift;;
                *) die 'unknown run option';;
            esac
        done
        [ "$observe_wlan" = 0 ] || { [ "$android" = 0 ] && [ -z "$scan_reviewed" ] || die '--observe-wlan is exclusive with --android and --scan-reviewed'; }
        if [ -n "$session_seconds" ] || [ -n "$identity_reviewed" ]; then
            # Integrated demo session (docs: runs/wifi-android-plan.md). Exclusive with every
            # other stage option; both session options are required together; no real-store
            # audit (no live-store open at all); Android HAL gate opens only after the QMI
            # identity equals the reviewed one.
            [ -n "$session_seconds" ] && [ -n "$identity_reviewed" ] || die '--session and --identity-reviewed go together'
            [ "$android" = 0 ] && [ "$observe_wlan" = 0 ] && [ -z "$scan_reviewed" ] || die '--session is exclusive with --android, --observe-wlan and --scan-reviewed'
            [ -s /vendor/etc/a9-wifi/android-bundle.json ] || die 'reviewed Android integration absent'
        fi
        if [ "$android" = 1 ]; then
            [ -n "$scan_reviewed" ] || die '--android requires --scan-reviewed before MPSS start'
            [ -s /vendor/etc/a9-wifi/android-bundle.json ] || die 'reviewed Android integration absent'
        fi
        [ "$(cat "$D/stage")" = prepared ] || die 'prepare and review offline confinement first'
        [ ! -e "$D/start-attempted" ] && [ ! -e "$D/watchdog-ready" ] || die 'one trial per fresh boot'
        [ ! -e "$D/scan-reviewed.sha256" ] || die 'stale scan eligibility gate'
        [ ! -e "$D/fault" ] && [ ! -e "$D/abort" ] && [ ! -e "$D/stop-requested" ] || die 'trial already cancelled'
        trap 'run_exit $?' EXIT
        trap 'exit 1' HUP INT TERM
        if [ -n "$session_seconds" ]; then
            [ ! -e "$D/session-total" ] || die 'stale session length'
            printf '%s\n' "$session_seconds" > "$D/session-total" || die 'session length unwritable'
            printf '%s\n' "$identity_reviewed" > "$D/identity-reviewed.sha256" || die 'reviewed identity unwritable'
            total_load || die 'session length invalid'
        fi
        stamp offline-audit-begin
        clock_now
        bounded_until "$((clock_value + 180))" --offline-proof || die 'offline confinement/trace audit failed or timed out'
        offline
        stamp offline-audit-end
        echo "$preliminary" > "$D/preliminary-production-evidence.sha256"
        [ -z "$scan_reviewed" ] || printf '%s\n' "$scan_reviewed" > "$D/scan-reviewed.sha256"
        if [ -n "$session_seconds" ]; then
            # No live-store open in a session: nothing to calibrate, nothing to read after.
            echo 0 > "$D/store-cost" || die 'store cost unwritable'
            echo skipped > "$D/stores-post-complete" || die 'store marker unwritable'
        else
            mkdir -m 700 "$D/stores"
            mount -t tmpfs -o noswap,nodev,nosuid,noexec,size=16m,mode=0700 a9-store-audit "$D/stores" || die 'noswap real-store audit RAM mount'
            clock_now
            bounded_until "$((clock_value + 15))" --stores-pre || die 'offline store preparation failed or timed out'
        fi
        # Keep the measured costs rounded UP and doubled, plus six seconds
        # for cached freeze/kill. Allocate inside the SAME 35 s reserve:
        # tail + min(20, 35-tail-1) + handoff <=35. Slow stop fails sooner;
        # never borrow from the 180 s observation or the 240/270 s guards.
        read -r audit_cost < "$D/audit-cost"
        read -r store_cost < "$D/store-cost"
        case "$audit_cost:$store_cost" in *[!0-9:]*|:*|*:) die 'invalid cleanup calibration';; esac
        cleanup_estimate=$((6 + 2 * (audit_cost + store_cost)))
        [ "$cleanup_estimate" -lt 34 ] || die 'measured cleanup cannot fit conservative reserve'
        echo "$cleanup_estimate" > "$D/cleanup-budget-verified"
        cleanup_limits || die 'invalid cleanup allocation'
        printf 'A9_WIFI_BUDGET cleanup_estimate=%s tail_limit=%s stop_limit=%s handoff=1 total_limit=35 slack=5 startup=10 observation=180 final=5 active_end=215 running_end=29 offline_end=235 evidence_end=265\n' "$cleanup_estimate" "$tail_limit" "$stop_limit"
        offline
        cut -d. -f1 /proc/uptime > "$D/guard-armed.uptime"
        stamp guards-arm
        nohup /system/bin/sh "$SELF" --guardian </dev/null >"$D/guardian.log" 2>&1 &
        echo "$!" > "$D/guardian.pid"
        nohup /system/bin/sh "$SELF" --watchdog </dev/null >"$D/watchdog.log" 2>&1 &
        echo "$!" > "$D/watchdog.pid"
        end=$(( $(cut -d. -f1 /proc/uptime) + 5 ))
        while [ ! -e "$D/watchdog-ready" ] || [ ! -e "$D/guardian-ready" ]; do
            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || die 'independent abort readiness timeout'
            sleep 1
        done
        rc=0
        if [ "$observe_wlan" = 1 ]; then
            echo observe-wlan > "$D/observe-wlan-mode" || die 'observe-wlan marker unwritable'
            /system/bin/sh "$SELF" --start "$start_ack" && /system/bin/sh "$SELF" observe-wlan || rc=$?
        elif [ -n "$session_seconds" ]; then
            /system/bin/sh "$SELF" --start "$start_ack" && /system/bin/sh "$SELF" session || rc=$?
        else
            /system/bin/sh "$SELF" --start "$start_ack" && /system/bin/sh "$SELF" check || rc=$?
        fi
        if [ "$rc" = 0 ] && [ "$android" = 1 ]; then /system/bin/sh "$SELF" android || rc=$?; fi
        [ "$rc" = 0 ] || { echo controller-stage-failed >> "$D/fault" || :; }
        /system/bin/sh "$SELF" stop || rc=1
        exit "$rc"
        ;;
    check)
        [ "$#" = 1 ] || die 'check accepts no scan eligibility argument; only run arms the RAM gate'
        end=$(active_end) || die 'active deadline unavailable'
        bounded_until "$end" --check-body || die 'bounded active check failed; stop required'
        ;;
    --check-body)
        [ "$#" = 1 ] || die 'check accepts no scan eligibility argument; only run arms the RAM gate'
        [ "$(cat "$D/stage")" = started ] || die 'MPSS not explicitly started'
        m=$(modem); [ "$(cat "$m/state")" = running ] || die 'MPSS not running'
        mutable_gate || die 'mutable companion gate lost during check'
        end=$(( $(cut -d. -f1 /proc/uptime) + 30 ))
        while [ ! -e /sys/class/net/wlan0 ]; do
            monitors || die 'monitor/fault loss while waiting for wlan0'
            [ "$(cat "$m/state")" = running ] || die 'MPSS stopped while waiting for WLAN'
            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || die '30-second wlan0 timeout'
            sleep 1
        done
        # Do not log the factory MAC (persist file or older private DT MAC).
        wlan_factory_mac || die 'wlan0 does not carry the factory MAC'
        echo true > "$D/mac-matches-factory.txt"
        kernel_evidence identity
        capture_identity
        scan_if_reviewed
        mutable_gate || die 'mutable companion gate lost after identity/scan'
        # Observe >=180 healthy seconds since running, crossing the A6L ~100s
        # starvation horizon. Total-trial watchdog remains independently 240s.
        end=$(( $(cat "$D/started.uptime") + 180 ))
        while [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ]; do
            monitors || die 'independent abort monitor disappeared'
            [ ! -e "$D/fault" ] || die 'independent monitor fault'
            [ "$(cat "$m/state")" = running ] || die 'MPSS stopped during stability window'
            [ "$(cat "$m/recovery")" = disabled ] || die 'crash recovery changed'
            for role in $ROLES; do [ ! -e "$D/$role.exit" ] || die 'companion died'; done
            clock_now
            step=$((end - clock_value))
            [ "$step" -gt 0 ] || break
            [ "$step" -le 3 ] || step=3
            sleep "$step"
        done
        clock_now
        final_end=$((clock_value + 5))
        cutoff=$(active_end) || die 'active deadline unavailable'
        [ "$final_end" -le "$cutoff" ] || die 'insufficient final-check reserve'
        bounded_until "$final_end" --foreground --check-final || die 'bounded final checks failed'
        if [ -e "$D/scan-completed" ]; then
            echo checked > "$D/stage"
            echo 'CHECKED: one reviewed scan completed. Stop MPSS and companions now.'
        else
            echo captured > "$D/stage"
            echo 'CAPTURED: private QMI identity and healthy observation; no linkup/scan requested. Stop and review offline before a fresh reviewed-scan trial.'
        fi
        ;;
    observe-wlan)
        [ "$#" = 1 ] || die 'observe-wlan accepts no arguments'
        end=$(active_end) || die 'active deadline unavailable'
        bounded_until "$end" --wlan-observe-body || die 'bounded WLAN observation failed; stop required'
        ;;
    --wlan-observe-body)
        [ "$(cat "$D/stage")" = started ] || die 'MPSS not explicitly started'
        m=$(modem); [ "$(cat "$m/state")" = running ] || die 'MPSS not running'
        mutable_gate || die 'mutable companion gate lost before WLAN observation'
        wlan_survey
        read -r wo_result < "$D/wlan-observe-result" || wo_result='result missing'
        [ -z "$wo_result" ] || die "WLAN observation failed: $wo_result"
        echo observed > "$D/stage"
        echo 'OBSERVED: read-only WLAN listing and one bounded passive scan; link taken down. Stop MPSS and companions now.'
        ;;
    session)
        [ "$#" = 1 ] || die 'session accepts no arguments'
        end=$(active_end) || die 'active deadline unavailable'
        bounded_until "$end" --session-body || die 'bounded session failed; stop required'
        ;;
    --session-body)
        [ "$(cat "$D/stage")" = started ] || die 'MPSS not explicitly started'
        [ -e "$D/session-total" ] && [ -e "$D/identity-reviewed.sha256" ] || die 'not a session run'
        m=$(modem); [ "$(cat "$m/state")" = running ] || die 'MPSS not running'
        mutable_gate || die 'mutable companion gate lost before the session'
        end=$(( $(cut -d. -f1 /proc/uptime) + 30 ))
        while [ ! -e /sys/class/net/wlan0 ]; do
            monitors || die 'monitor/fault loss while waiting for wlan0'
            [ "$(cat "$m/state")" = running ] || die 'MPSS stopped while waiting for WLAN'
            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || die '30-second wlan0 timeout'
            sleep 1
        done
        # Same identity gate as check. The address is never printed.
        wlan_factory_mac || die 'wlan0 does not carry the factory MAC'
        echo true > "$D/mac-matches-factory.txt"
        kernel_evidence identity
        ID_BOARD_FF=1 capture_identity
        reviewed=$(cat "$D/identity-reviewed.sha256") || die 'reviewed identity unavailable'
        actual=$(cat "$D/identity.sha256") || die 'actual identity unavailable'
        [ "$reviewed" = "$actual" ] || die 'actual QMI identity differs from the reviewed one; no Android gate'
        session_regulatory
        mutable_gate || die 'mutable companion gate lost before the Android gate'
        [ -s /vendor/etc/a9-wifi/android-bundle.json ] || die 'reviewed matching Android HAL bundle absent'
        [ "$(cat "$m/state")" = running ] || die 'MPSS not running'
        setprop vendor.a9ml.wifi.android.ready 1
        end=$(( $(cut -d. -f1 /proc/uptime) + 15 ))
        while [ "$(getprop init.svc.vendor.a9ml_wifi_hal)" != running ] || [ "$(getprop init.svc.wpa_supplicant)" != running ]; do
            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || die 'Android HAL/supplicant readiness timeout'
            sleep 1
        done
        echo session > "$D/stage"
        # Lets the normal stop take wlan0 down even after a fault (wlan_link_down).
        echo requested > "$D/wlan-up-requested" || :
        setprop vendor.a9ml.wifi.session.state serving
        echo 'A9_WIFI_SESSION serving: Android Wi-Fi stack released; monitors and deadlines unchanged.'
        # Hold. Orderly end (exit 0, session-end marker): stop request property, session length
        # reached, evidence RAM nearly full. Any fault or loss is a die (failed session).
        session_stop_at=$(active_end) || die 'active deadline unavailable'
        while :; do
            monitors || die 'independent abort monitor disappeared'
            [ ! -e "$D/fault" ] || die 'independent monitor fault'
            [ "$(cat "$m/state")" = running ] || die 'MPSS stopped during the session'
            [ "$(cat "$m/recovery")" = disabled ] || die 'crash recovery changed'
            for role in $ROLES; do [ ! -e "$D/$role.exit" ] || die 'companion died'; done
            if [ "$(getprop vendor.a9ml.wifi.session.stop)" = 1 ]; then echo requested > "$D/session-end"; break; fi
            set -- $(stat -f -c '%f %S' "$D")
            case "${1:-}:${2:-}" in *[!0-9:]*|:*|*:) die 'evidence RAM free space unreadable';; esac
            if [ "$(($1 * $2))" -lt 4194304 ]; then echo evidence-ram-low > "$D/session-end"; break; fi
            clock_now || die 'clock unavailable'
            [ "$clock_value" -lt "$((session_stop_at - 16))" ] || { echo session-length > "$D/session-end"; break; }
            sleep 2
        done
        read -r session_end_reason < "$D/session-end" || session_end_reason=unknown
        echo "A9_WIFI_SESSION ending reason=$session_end_reason"
        setprop vendor.a9ml.wifi.session.state ending
        # Ask the framework to turn Wi-Fi off first (best effort, bounded) so wlan0 is
        # disassociated before the modem goes; the stop path downs the link regardless.
        timeout --foreground -s KILL 8 /system/bin/cmd wifi set-wifi-enabled disabled > /dev/null 2>&1 || :
        sleep 2
        ;;
    --check-final)
        services || die 'PD/WLFW QRTR service disappeared'
        mutable_gate || die 'mutable companion gate lost after stability window'
        [ "$(cat "$(modem)/state")" = running ] || die 'MPSS stopped during identity/scan observation'
        kernel_evidence after
        ;;
    android)
        [ "$(cat "$D/stage")" = checked ] || die 'direct nl80211 scan must pass first'
        [ -e "$D/scan-completed" ] && [ -e "$D/scan-reviewed.sha256" ] || die 'Android requires this trial reviewed scan completion'
        end=$(active_end) || die 'active deadline unavailable'
        bounded_until "$end" --android-body || die 'bounded Android stage failed'
        ;;
    --android-body)
        [ "$(cat "$D/stage")" = checked ] || die 'direct nl80211 scan must pass first'
        [ -e "$D/scan-completed" ] && [ -e "$D/scan-reviewed.sha256" ] || die 'Android requires this trial reviewed scan completion'
        reviewed=$(cat "$D/scan-reviewed.sha256") || die 'Android reviewed scan digest unavailable'
        actual=$(cat "$D/identity.sha256") || die 'Android identity digest unavailable'
        case "$actual" in ''|*[!0-9a-f]*) die 'Android identity digest invalid';; esac
        [ "${#actual}" = 64 ] && [ "$actual" = "$reviewed" ] || die 'Android requires this trial reviewed scan identity'
        [ -s /vendor/etc/a9-wifi/android-bundle.json ] || die 'reviewed matching Android HAL bundle absent'
        [ "$(cat "$(modem)/state")" = running ] || die 'MPSS not running'
        mutable_gate || die 'mutable companion gate lost before Android gate'
        setprop vendor.a9ml.wifi.android.ready 1
        end=$(( $(cut -d. -f1 /proc/uptime) + 15 ))
        while [ "$(getprop init.svc.vendor.a9ml_wifi_hal)" != running ] || [ "$(getprop init.svc.wpa_supplicant)" != running ]; do
            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || die 'Android HAL/supplicant readiness timeout'
            sleep 1
        done
        timeout --foreground -s KILL 10 dumpsys wifi > "$D/android-wifi.txt" || die 'Android Wi-Fi status timeout'
        echo 'ANDROID SERVICES STARTED: inspect framework/Settings separately; this is not proof of association.'
        ;;
    stop)
        clock_now || fallback stagestop-1
        stop_end=$((clock_value + 35))
        if [ -e "$D/guard-armed.uptime" ]; then
            anchor || fallback stagestop-2
            total_load || fallback stagestop-4
            last_end=$((guard_start + TOTAL + 30 - 5))
            [ "$stop_end" -le "$last_end" ] || stop_end=$last_end
        fi
        bounded_until "$stop_end" --stop-body "$stop_end" || {
            [ -e "$D/complete" ] || fallback stagestop-3
            exit 1
        }
        ;;
    --stop-body)
        rc=0
        echo requested > "$D/stop-requested" || rc=1
        stamp stop-request
        stream_evidence stop || :
        wlan_link_down || :
        teardown_signal
        # Bound lookup, property stop, timeout and readback together, not just
        # the sysfs writer. Evidence may use the reset window ONLY after this
        # earlier, independent MPSS-offline deadline has been met.
        clock_now || fallback stagestop_body-1
        offline_end=$((clock_value + 20))
        if [ -e "$D/guard-armed.uptime" ]; then
            anchor || fallback stagestop_body-2
            total_load || fallback stagestop_body-10
            last_offline=$((guard_start + TOTAL - 5))
            [ "$offline_end" -le "$last_offline" ] || offline_end=$last_offline
        fi
        bounded_until "$offline_end" --foreground --stop-modem || fallback stagestop_body-3
        [ ! -e "$D/android-stop-failed" ] || rc=1
        # A killed sysfs writer can remain in an uninterruptible kernel boot
        # while state still reads offline. No helper teardown until the write
        # actually returned; an uncertain writer requires reset, never a retry.
        [ ! -e "$D/start-write-pending" ] || [ -e "$D/start-write-returned" ] || fallback stagestop_body-4
        stamp modem-offline
        cleanup_limits || fallback stagestop_body-5
        clock_now
        tail_end=$((clock_value + tail_limit))
        [ "$tail_end" -le "$2" ] || tail_end=$2
        bounded_until "$tail_end" --foreground --stop-tail || {
            [ -e "$D/cleanup-finished" ] || fallback stagestop_body-6
            clock_now && [ "$clock_value" -lt "$tail_end" ] || fallback stagestop_body-7
            rc=1
        }
        [ -e "$D/cleanup-finished" ] || fallback stagestop_body-8
        clock_now && [ "$clock_value" -lt "$2" ] || fallback stagestop_body-9
        stamp stop-evidence-complete
        echo complete > "$D/complete"
        echo stopped > "$D/stage"
        echo 'STOPPED: MPSS offline before tracer/companion teardown. Preserve private evidence; never restore EFS stores.'
        [ ! -e "$D/fault" ] && [ "$rc" = 0 ] || die 'single trial failed; preserve all private evidence'
        ;;
    --stop-tail)
        offline
        rc=0
        stamp stop-audit-begin
        audit || { echo "stop-audit-failed ${audit_failed_role:-unknown}" >> "$D/fault" || :; rc=1; }
        stamp stop-audit-end
        ramcopy_evidence
        stamp helpers-stop-begin
        stop_helpers || die 'helper cleanup incomplete'
        stamp helpers-stopped
        if [ -e "$D/start-attempted" ] && [ ! -e "$D/stores-post-complete" ]; then
            stamp stores-post-begin
            stores post || die 'post-store evidence incomplete'
            stamp stores-post-end
            echo complete > "$D/stores-post-complete"
        fi
        # Android's stop request precedes offline; check its outcome afterwards.
        for svc in vendor.a9ml_wifi_hal wpa_supplicant hostapd; do
            [ "$(getprop "init.svc.$svc")" != running ] || { echo Android-stop-incomplete >> "$D/fault" || :; rc=1; }
        done
        echo finished > "$D/cleanup-finished"
        exit "$rc"
        ;;
    abort)
        if ! echo operator-abort > "$D/abort"; then
            echo abort-marker-failed >> "$D/fault" || :
            stop_modem
            die 'abort marker unavailable; MPSS stopped directly'
        fi
        if [ ! -e "$D/start-attempted" ]; then
            # No watchdog exists during prepare. Cancel late launches and
            # synchronously stop its helpers after proving MPSS offline.
            echo failed > "$D/prepare-failed" || :
            stop_modem
            # If prepare is still launching, let its EXIT trap join all of
            # its workers before reporting that the abort is complete.
            if [ -r "$D/prepare-owner" ]; then
                owner=$(cat "$D/prepare-owner") || die 'prepare owner unreadable'
                set -- $owner
                [ "$#" = 2 ] || die 'invalid prepare owner'
                owner_pid=$1; owner_start=$2
                record=$(process_record "$owner_pid") || record=
                if [ -n "$record" ]; then
                    set -- $record
                    if [ "$2" = "$owner_start" ] && [ "$3" != Z ]; then
                        kill -TERM "$owner_pid" || die 'cannot cancel preparing controller'
                        end=$(( $(cut -d. -f1 /proc/uptime) + 10 ))
                        while [ ! -e "$D/prepare-cleaned" ] && [ ! -e "$D/prepare-finished" ]; do
                            [ "$(cut -d. -f1 /proc/uptime)" -lt "$end" ] || die 'prepare cleanup timeout'
                            sleep 1
                        done
                    fi
                fi
            fi
            stop_helpers || die 'offline abort helper teardown failed'
            echo 'ABORTED: MPSS offline; prepared helpers stopped.'
            exit 0
        fi
        echo 'Abort requested through independent watchdog. Confirm offline; if USB is lost, the target guardian still has its finite reset deadline.'
        ;;
    *) die 'stages: prepare <three reviewed hashes> | run <two acknowledgements> <preliminary-evidence-sha256> [--scan-reviewed <identity-sha256>] [--android] [--session <s> --identity-reviewed <identity-sha256>] | abort | stop';;
esac
