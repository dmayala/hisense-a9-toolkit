# Sourced into the derived D1 target. Every check is observational. Mounting and
# copying the private registry seed are an earlier, separately admitted action.
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
    # Refuse links anywhere in the private tree, not just manifest members.
    find "$R/mnt/vendor/persist" -type l > "$B/s1-registry.links" || fail s1-registry-scan
    [ ! -s "$B/s1-registry.links" ] || fail s1-registry-symlink
    n=0
    while read -r digest path extra; do
        case "$digest" in ''|*[!a-f0-9]*) fail s1-registry-digest;; esac
        [ "${#digest}" = 64 ] && [ -z "$extra" ] || fail s1-registry-manifest
        case "$path" in /mnt/vendor/persist/sensors/*) ;; *) fail s1-registry-path;; esac
        case "$path" in *'/../'*|*'/./'*|*'//'*|*'\'*|*'/..'|*'/.'|*' '*|*'\t'*) fail s1-registry-path;; esac
        [ "$(readlink -f "$R$path")" = "$R$path" ] || fail s1-registry-alias
        [ -f "$R$path" ] || fail s1-registry-file
        [ "$(sha256sum "$R$path" | cut -d ' ' -f 1)" = "$digest" ] || fail s1-registry-hash
        n=$((n+1))
    done < "$B/registry.sha256"
    [ "$n" -gt 0 ] || fail s1-empty-registry
}
s1_health() {
    s1_registry_check
    # Exact D1 has FastRPC disabled. Any active RPC stack/stock sensor daemon is
    # outside this passive tuple; no device node is ever opened by this harness.
    [ ! -d "$S/module/fastrpc" ] || fail s1-unadmitted-fastrpc
    ps -A -o ARGS > "$B/s1-processes" || fail s1-process-scan
    if grep -iE 'sscrpcd|(^|[ /])sensorspd([ /]|$)|sensors[.]qti|android[.]hardware[.]sensors.*service|sensorscalibrate' "$B/s1-processes"; then
        fail s1-competing-sensor-service
    fi
}
s1_observe() {
    s1_health
    [ -x "$B/s1-observe" ] || fail s1-missing-observer
    # Numeric ID is independently measured from the stock IDL object, and is
    # frozen in this bundle. A plausible name or PD mapping is never proof.
    id=$(cat "$B/sns-service-id")
    case "$id" in ''|*[!0-9]*) fail s1-SNS-id;; esac
    # Android mksh arithmetic is signed 32-bit; avoid a UINT32_MAX comparison.
    [ "$id" = 400 ] || fail s1-SNS-id
    if timeout 40 "$B/s1-observe" "$id" > "$B/s1-observation.log" 2>&1; then
        cat "$B/s1-observation.log"
        grep -qx 'S1-ENUMERATION-PASS registry_transport=NOT_IMPLEMENTED' "$B/s1-observation.log" || fail s1-incomplete-observer
    else
        cat "$B/s1-observation.log"
        fail s1-no-enumeration-proof
    fi
    s1_health
}
