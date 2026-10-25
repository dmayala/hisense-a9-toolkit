# Sourced by the two manual host scripts. Never sourced by a sweep hook.
ADB=${ADB:-adb}
shell() { timeout --kill-after=2 75 "$ADB" shell -T "$1"; }
root() {
    local reply rc quoted
    printf -v quoted '%q' "set -e"$'\n'"$1"
    reply=$(printf 'sh -c %s; r=$?; printf "\\nA9_MANUAL_RC=%%s\\n" "$r"; exit\n' "$quoted" |
        timeout --kill-after=2 75 "$ADB" shell -T 'timeout 65 nc 127.0.0.1 2324') || return $?
    reply=${reply//$'\r'/}
    printf '%s\n' "$reply" | sed '/^A9_MANUAL_RC=/d'
    rc=$(printf '%s\n' "$reply" | sed -n 's/^A9_MANUAL_RC=//p')
    [[ $rc == 0 ]]
}
