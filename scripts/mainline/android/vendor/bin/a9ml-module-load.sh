#!/system/bin/sh
# Shared by concurrent one-shot boot consumers. No unload, processor start or
# retry of a failed insertion: another consumer's successful load is reusable.
a9ml_module_load() {
    local ko=$1 sys=${2:-/sys} modules=${3:-/vendor/lib/modules}
    local name state n rc args
    case "$ko" in ''|*[!a-zA-Z0-9_.-]*|.*|*..*) return 1;; *.ko) ;; *) return 1;; esac
    name=$(echo "${ko%.ko}" | tr - _)
    rc=0
    if [ ! -d "$sys/module/$name" ]; then
        args=
        [ ! -f "$modules/modules.options" ] || args=$(sed -n "s/^$ko //p" "$modules/modules.options")
        insmod "$modules/$ko" $args || rc=$?
    fi
    # /sys/module appears while module_init is still running. EEXIST from a
    # competing finit_module is not failure if that module reaches LIVE. A
    # dependency/probe failure with no live winner must still fail this owner.
    n=0
    while [ "$n" -lt 50 ]; do
        [ -d "$sys/module/$name" ] || break
        state=$(cat "$sys/module/$name/initstate" 2>/dev/null) || break
        case "$state" in live) return 0;; coming) ;; *) break;; esac
        sleep 0.1
        n=$((n+1))
    done
    echo "A9_MODULE_LOAD failed name=$name rc=$rc" >&2
    return 1
}
