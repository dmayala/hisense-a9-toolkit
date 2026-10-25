# Active S1 checks, sourced into the pinned D1-derived target. The immutable
# seed remains separate from the listener's mutable, capability-rooted copy.
# Review 79i: never inherit the seed-check state from the caller's environment.
unset S1_REG_READY S1_REG_LIST
s1_registry_check() {
    mounts=0
    while read -r dev point fs opts rest; do
        case "$dev" in *by-name/persist*|*/persist|*/persist_a|*/persist_b) fail s1-real-persist-mounted;; esac
        case "$point" in
            /mnt/vendor/persist)
                [ "$dev" = tmpfs ] && [ "$fs" = tmpfs ] || fail s1-persist-not-tmpfs
                mounts=$((mounts+1));;
            /mnt/vendor/persist/*) fail s1-persist-nested-mount;;
        esac
    done < "$P/mounts"
    [ "$mounts" = 1 ] || fail s1-persist-mount-count
    [ "$(readlink -f "$R/mnt/vendor/persist")" = "$R/mnt/vendor/persist" ] || fail s1-persist-alias
    find "$R/mnt/vendor/persist" -type l > "$B/s1-registry.links" || fail s1-registry-scan
    [ ! -s "$B/s1-registry.links" ] || fail s1-registry-symlink
    # S1 attempt 6: the per-file loop forked ~5 processes per seed file (148 files), about 20 s
    # per health() call, which pushed the run past the guardian deadline and the host's 400 s
    # bound. The full per-line validation now runs once per executor and writes a private
    # check list; every later call re-hashes all seed files with ONE sha256sum -c. No symlink
    # can exist under persist (find above), so the per-file alias check cannot change between calls.
    if [ "${S1_REG_READY:-0}" != 1 ]; then
        : > "$B/s1-registry.check" || fail s1-registry-scan
        n=0
        while read -r digest path extra; do
            case "$digest" in ''|*[!a-f0-9]*) fail s1-registry-digest;; esac
            [ "${#digest}" = 64 ] && [ -z "$extra" ] || fail s1-registry-manifest
            case "$path" in /mnt/vendor/persist/sensors/*) ;; *) fail s1-registry-path;; esac
            case "$path" in *'/../'*|*'/./'*|*'//'*|*'\'*|*'/..'|*'/.'|*' '*|*'\t'*) fail s1-registry-path;; esac
            [ "$(readlink -f "$R$path")" = "$R$path" ] || fail s1-registry-alias
            [ -f "$R$path" ] || fail s1-registry-file
            [ "$(sha256sum "$R$path" | cut -d ' ' -f 1)" = "$digest" ] || fail s1-registry-hash
            printf '%s  %s\n' "$digest" "$R$path" >> "$B/s1-registry.check" || fail s1-registry-scan
            n=$((n+1))
        done < "$B/registry.sha256"
        [ "$n" -gt 0 ] || fail s1-empty-registry
        [ "$(grep -c '' "$B/s1-registry.check")" = "$n" ] || fail s1-registry-manifest
        S1_REG_LIST=$(sha256sum "$B/s1-registry.check" | cut -d ' ' -f 1)
        S1_REG_READY=1
    fi
    # The list itself is pinned in this executor's memory: a changed list is a failure, not a new baseline.
    [ "$(sha256sum "$B/s1-registry.check" | cut -d ' ' -f 1)" = "$S1_REG_LIST" ] || fail s1-registry-manifest
    sha256sum -c "$B/s1-registry.check" > /dev/null 2>&1 || fail s1-registry-hash
    root=$R/mnt/vendor/persist/s1-listener-root
    [ -d "$root/persist/sensors" ] && [ -d "$root/vendor/etc/sensors" ] || fail s1-listener-root
    [ "$(readlink -f "$root")" = "$root" ] || fail s1-listener-root-alias
}
# Failure evidence printed by the target itself (stdout reaches the host run.log, which survives; the files
# under /data do not, and they are root-only). Bounded: at most 50 log lines. The listener log carries only
# markers, counters and (since fix6) the request trace: one line per path-bearing request with its
# sanitized path and open mode. Never file contents, payloads or handles.
s1_listener_dump() {
    echo "S1-LISTENER-DIAG why=$1"
    if [ -f "$O/listener/done" ]; then echo "S1-LISTENER-DIAG worker-done-rc=$(cat "$O/listener/done")"; else echo 'S1-LISTENER-DIAG worker-done-rc=none'; fi
    [ ! -f "$O/listener/launched" ] || echo 'S1-LISTENER-DIAG launched=yes'
    echo "S1-LISTENER-DIAG rpc-state=$(cat "$RP/state" 2>/dev/null || echo unknown) log-bytes=$(wc -c 2>/dev/null < "$B/s1-listener.log" || echo 0)"
    if [ -f "$B/s1-listener.log" ]; then tail -n 50 "$B/s1-listener.log" | sed 's/^/S1-LISTENER-LOG-TAIL /'; fi
    ps -A -o PID,STAT,ARGS 2>/dev/null | grep -E 's1-listener|sensorspd|sscrpcd' | grep -v grep | sed 's/^/S1-LISTENER-DIAG ps /' || :
    return 0
}
s1_health() {
    # One progress line per health call (uptime, seconds): attempt 6 timed out with no output to show where.
    echo "S1-HEALTH uptime=$(now)"
    s1_registry_check
    if [ -d "$S/module/fastrpc" ]; then
        [ "$OWNED" = 1 ] || fail s1-prior-fastrpc
        [ "$(cat "$S/module/fastrpc/parameters/a9_sensor_fastrpc")" = Y ] || fail s1-fastrpc-gate
    fi
    ps -A -o ARGS > "$B/s1-processes" || fail s1-process-scan
    if grep -iE 'sscrpcd|(^|[ /])sensorspd([ /]|$)|sensors[.]qti|android[.]hardware[.]sensors.*service|sensorscalibrate' "$B/s1-processes"; then
        fail s1-competing-sensor-service
    fi
    if [ -f "$B/s1-listener.log" ]; then
        [ "$(wc -c < "$B/s1-listener.log")" -lt 4194304 ] || { s1_listener_dump log-overflow; fail s1-listener-log-overflow; }
        if grep -q 'S1-LISTENER-FAULT' "$B/s1-listener.log"; then s1_listener_dump fault; fail s1-listener-fault; fi
    fi
    if [ -d "$O/listener" ] && [ -n "$RP" ] && [ "$(cat "$RP/state")" = running ]; then
        [ ! -f "$O/listener/done" ] || { s1_listener_dump exited-before-stop; fail s1-listener-exited-before-stop; }
    fi
}
# Review 79 F3: a crashed ADSP (recovery disabled) is still a failure, but it is
# classified distinctly so the documented response is the adb return to slot A.
s1_note_crashed() {
    [ ! -f "$RP/state" ] || [ "$(cat "$RP/state")" != crashed ] || echo crashed > "$O/adsp-crashed"
    return 0
}
# Called only by the detached guardian, never by run/contain or a writer.
# The same guardian seals launch dispatch before it can stop ADSP.
s1_listener_maybe_launch() {
    [ -f "$O/listener.request" ] || return 0
    [ ! -d "$O/listener" ] || return 0
    [ ! -e "$B/abort.$BOOT.$TUPLE" ] && [ ! -f "$O/sealed" ] &&
        [ ! -f "$O/listener.launch.sealed" ] || return 0
    mkdir "$O/listener" || return 1
    echo armed > "$O/listener/armed"
    [ ! -e "$B/s1-listener.log" ] || return 1
    : > "$B/s1-listener.log"
    setsid sh "$SELF" $REHEARSE listener "$TUPLE" "$BOOT" </dev/null > "$O/listener/worker.log" 2>&1 &
    echo $! > "$O/listener/pid"
    echo launched > "$O/listener/launched"
}
s1_listener_join() {
    limit=$1
    [ -d "$O/listener" ] || return 0
    end=$(($(now)+limit))
    until [ -f "$O/listener/done" ]; do
        [ "$(now)" -lt "$end" ] || return 1
        sleep 0.1
    done
    rc=$(cat "$O/listener/done") || return 1
    case "$rc" in ''|*[!0-9]*) return 1;; esac
    [ "${#rc}" -le 3 ] && [ "$rc" -le 255 ]
}
# After the process has actually returned, kernel DMA/context-bank ownership
# must also have drained. Unknown or orphaned DMA can never certify OFFLINE.
s1_fastrpc_drained() {
    limit=$1
    [ -d "$S/module/fastrpc" ] || return 0
    [ "$(cat "$S/module/fastrpc/parameters/a9_sensor_fastrpc")" = Y ] || return 1
    end=$(($(now)+limit))
    while :; do
        [ -f "$S/module/fastrpc/parameters/a9_sensor_drain_pending" ] || return 1
        pending=$(cat "$S/module/fastrpc/parameters/a9_sensor_drain_pending") || return 1
        case "$pending" in N) return 0;; Y) ;; *) return 1;; esac
        [ "$(now)" -lt "$end" ] || return 1
        sleep 0.1
    done
}
s1_listener_delivery() {
    grep -qE '^S1-REGISTRY-DELIVERED reads=[1-9][0-9]* writes=[0-9]+$|^S1-REGISTRY-DELIVERED reads=0 writes=[1-9][0-9]*$' "$B/s1-listener.log"
}
s1_listener_wait_ready() {
    [ -d "$S/module/fastrpc" ] || fail s1-missing-fastrpc
    [ -x "$B/s1-listener" ] || fail s1-missing-listener
    [ ! -e "$O/listener.request" ] && [ ! -d "$O/listener" ] || fail s1-listener-request-consumed
    echo requested > "$O/listener.request"
    limit=30; [ -z "$R" ] || limit=6
    wait_start=$(now); deadline=$((wait_start+limit)); shown=0
    while :; do
        sed -n "/$MARK/,$ p" kmsg.raw > kmsg.log
        gate running; ready || fail glink-rpmsg-lost; apr_ready || fail apr-lost; card_ok || fail card-lost
        # progress first, so the host log shows the listener's markers and the loop timing even if health fails
        echo "S1-WAIT t=$(($(now)-wait_start)) limit=$limit"
        if [ -f "$B/s1-listener.log" ]; then
            total=$(wc -l < "$B/s1-listener.log")
            if [ "$total" -gt "$shown" ] && [ "$shown" -lt 200 ]; then
                sed -n "$((shown+1)),${total}p" "$B/s1-listener.log" | head -n 20 | sed 's/^/S1-LISTENER-LOG /'
                shown=$total
            fi
        fi
        health
        if [ -f "$O/listener/launched" ] && [ -f "$B/s1-listener.log" ] &&
            grep -qx 'S1-LISTENER-READY' "$B/s1-listener.log" && s1_listener_delivery; then
            echo S1-STEP RAM-REGISTRY-DELIVERED
            break
        fi
        [ "$(now)" -lt "$deadline" ] || { s1_listener_dump ready-timeout; fail s1-listener-ready-timeout; }
        sleep 1
    done
}
s1_observe() {
    s1_health
    s1_listener_wait_ready
    [ -x "$B/s1-observe" ] || fail s1-missing-observer
    [ "$(cat "$B/sns-service-id")" = 400 ] || fail s1-SNS-id
    # The observer polls the QRTR namespace for up to ~25 s (review 79 F1) and
    # then needs at most ~5 s for the subscription; 40 s is the outer bound.
    if timeout 40 "$B/s1-observe" 400 > "$B/s1-observation.log" 2>&1; then
        cat "$B/s1-observation.log"
        grep -qx 'S1-ENUMERATION-PASS registry_transport=NOT_IMPLEMENTED' "$B/s1-observation.log" || fail s1-incomplete-observer
        grep -qE '^S1-POLL-RESULT found=1 attempts=[1-9][0-9]* elapsed_ms=[0-9]+$' "$B/s1-observation.log" || fail s1-incomplete-observer
    else
        cat "$B/s1-observation.log"; fail s1-no-enumeration-proof
    fi
    s1_health
}
