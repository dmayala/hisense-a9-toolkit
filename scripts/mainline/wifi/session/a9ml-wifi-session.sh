#!/system/bin/sh
# Boot-time starter of the integrated Wi-Fi demo session (opt-in image only:
# build-vendor.sh --wifi, A9_WIFI_ANDROID=1, A9_WIFI_SESSION=1).
# It adds NO confinement of its own: it installs the pinned tools, then runs the
# SAME reviewed controller stages a human coordinator runs (prepare, run) with
# `run --session`. One attempt per boot, no retry, no restart: any failure leaves
# Wi-Fi off and the phone otherwise unchanged (the controller's own bounded
# fallback may reset to the bootloader, exactly as in a trial).
#
#   a9ml-wifi-session.sh start    init service entry (once per boot)
#   a9ml-wifi-session.sh stop     ask a running session to end and wait (<=75 s)
#
# Reads only /vendor/etc/a9-wifi/session/{tools/*,session.conf}. Opens no partition.
set -u
umask 077
CONF=/vendor/etc/a9-wifi/session
TOOLS=/mnt/a9-wifi/tools
STATE=/mnt/a9-wifi/session
SELF_CTL=$TOOLS/coordinator-target.sh
note() {
    echo "A9_WIFI_SESSION $*"
    echo "a9wifi-session $*" > /dev/kmsg 2>/dev/null || :
    # Init discards stdout and an AVC-only dmesg export omits these messages.
    # Also retain native stage failures in the ordinary private logcat capture.
    /system/bin/log -p i -t a9wifi-session "$*" 2>/dev/null || :
}
state() { setprop vendor.a9ml.wifi.session.state "$1" || :; }
fail() { note "FAILED: $*"; state "failed"; exit 1; }
uptime_s() { read -r up idle < /proc/uptime || return 1; echo "${up%%.*}"; }

case "${1:-}" in
    stop)
        [ "$(getprop vendor.a9ml.wifi.session.state)" = serving ] || [ "$(getprop vendor.a9ml.wifi.session.state)" = starting ] || {
            echo "session not active: $(getprop vendor.a9ml.wifi.session.state)"; exit 0; }
        setprop vendor.a9ml.wifi.session.stop 1
        end=$(( $(uptime_s) + 75 ))
        while :; do
            case "$(getprop vendor.a9ml.wifi.session.state)" in ended*|failed*) break;; esac
            [ "$(uptime_s)" -lt "$end" ] || { echo "session did not end within 75 s"; exit 1; }
            sleep 1
        done
        echo "session $(getprop vendor.a9ml.wifi.session.state)"
        exit 0 ;;
    start) ;;
    *) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac

# ---- gates: build flags, identity of the environment, one attempt per boot
[ "$(id -u)" = 0 ] || fail 'root required'
[ "$(getprop ro.a9ml.vendor)" = 1 ] && [ "$(getprop ro.a9ml.wifi)" = 1 ] && [ "$(getprop ro.a9ml.wifi.android)" = 1 ] \
    && [ "$(getprop ro.a9ml.wifi.session)" = 1 ] || fail 'not a Wi-Fi session image'
[ "$(getprop ro.boot.slot_suffix)" = _b ] || fail 'slot B required'
case "$(uname -r)" in 7.*) ;; *) fail 'mainline kernel required';; esac
[ -d /mnt/a9-wifi ] && [ ! -L /mnt/a9-wifi ] && [ "$(stat -c %u:%a /mnt/a9-wifi)" = 0:700 ] || fail 'root-only /mnt/a9-wifi required'
[ ! -e "$STATE" ] || fail 'one session attempt per boot'
mkdir -m 700 "$STATE" || fail 'state directory'
[ "$(getprop vendor.a9ml.wifi.session.stop)" != 1 ] || fail 'stop already requested'
state starting

# ---- configuration (root-owned image file, strict keys only)
[ -f "$CONF/session.conf" ] && [ ! -L "$CONF/session.conf" ] || fail 'session.conf missing'
seconds= identity= preliminary= ack_start= ack_risk= strace_sha= iw_sha= awk_sha=
while IFS='=' read -r key value; do
    case "$key" in ''|'#'*) continue;; esac
    case "$value" in ''|*[!A-Za-z0-9_]*) fail "bad value for $key";; esac
    case "$key" in
        seconds) seconds=$value;; identity_sha256) identity=$value;; preliminary_sha256) preliminary=$value;;
        ack_start) ack_start=$value;; ack_risk) ack_risk=$value;;
        *) fail "unknown key $key";;
    esac
done < "$CONF/session.conf"
case "$seconds" in ''|*[!0-9]*) fail 'bad session length';; esac
[ "$seconds" -ge 600 ] && [ "$seconds" -le 14400 ] || fail 'session length out of range'
for digest in "$identity" "$preliminary"; do
    case "$digest" in ''|*[!0-9a-f]*) fail 'bad digest';; esac
    [ "${#digest}" = 64 ] || fail 'bad digest length'
done
# the country is mandatory and checked BEFORE any modem start (no silent EEPROM/world fallback)
[ -f "$CONF/country" ] && [ ! -L "$CONF/country" ] || fail 'no country in the image'
read -r country extra < "$CONF/country" || fail 'country unreadable'
[ -z "$extra" ] || fail 'country malformed'
case "$country" in [A-Z][A-Z]) ;; *) fail 'country must be two capital letters';; esac
[ "$ack_start" = I_AUTHORIZE_ONE_MPSS_START ] && [ "$ack_risk" = I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF ] \
    || fail 'acknowledgements absent: this image was not built as a pre-authorised session'

# ---- tools: dedicated exec tmpfs, copied from the image, verified, then pinned by prepare
mkdir -m 700 "$TOOLS" || fail 'tools directory (one session per boot)'
mount -t tmpfs -o rw,nosuid,nodev,mode=0700,size=8m a9-tools "$TOOLS" || fail 'tools tmpfs'
for f in strace iw audit-opens.awk coordinator-target.sh coordinator-tools.lock.json SHA256SUMS; do
    [ -f "$CONF/tools/$f" ] && [ ! -L "$CONF/tools/$f" ] || fail "tool $f missing"
    cat "$CONF/tools/$f" > "$TOOLS/$f" || fail "copy $f"
done
chmod 0700 "$TOOLS/strace" "$TOOLS/iw" "$TOOLS/coordinator-target.sh" || fail chmod
chmod 0600 "$TOOLS/audit-opens.awk" "$TOOLS/SHA256SUMS" || fail chmod
(cd "$TOOLS" && sha256sum -c SHA256SUMS > /dev/null) || fail 'tool hashes differ from the image manifest'
sums() { while read -r hash name; do [ "$name" != "$1" ] || { echo "$hash"; return; }; done < "$TOOLS/SHA256SUMS"; }
strace_sha=$(sums strace); iw_sha=$(sums iw); awk_sha=$(sums audit-opens.awk)
[ -n "$strace_sha" ] && [ -n "$iw_sha" ] && [ -n "$awk_sha" ] || fail 'SHA256SUMS incomplete'

# ---- this phone's factory WLAN MAC (persist:wlan_mac.bin Intf0), so images carry none.
# Read-only, journal not replayed (noload), unmounted at once; never printed.
PERSIST=/dev/block/by-name/persist
mkdir -m 700 "$STATE/persist" || fail 'persist mount point'
mount -t ext4 -o ro,noload,nosuid,nodev,noexec "$PERSIST" "$STATE/persist" || fail 'persist read-only mount'
mac=$(sed -n 's/^Intf0MacAddress=\([0-9A-Fa-f]\{12\}\)\r\{0,1\}$/\1/p' "$STATE/persist/wlan_mac.bin" 2>/dev/null | tr 'A-F' 'a-f')
umount "$STATE/persist" || fail 'persist unmount'
rmdir "$STATE/persist"
case "$mac" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) fail 'no unique Intf0 MAC in persist wlan_mac.bin';; esac
( umask 077; printf '%s' "$mac" > "$STATE/factory-mac" ) || fail 'factory MAC unwritable'
unset mac
note "factory MAC read from persist"

# ---- the reviewed stages, unchanged order
note "prepare begin"
/system/bin/sh "$SELF_CTL" prepare "$strace_sha" "$iw_sha" "$awk_sha" > "$STATE/prepare.out" 2>&1
rc=$?
sed -n '/^STOP: /p' "$STATE/prepare.out" | head -n 3 | while IFS= read -r line; do note "prepare STOP: $line"; done
tail -n 5 "$STATE/prepare.out" | while IFS= read -r line; do note "prepare: $line"; done
[ "$rc" = 0 ] || fail "prepare rc=$rc (modem never started)"

# A kill of this script (init stop, shutdown) asks the controller to end orderly and
# waits for it; it never kills the controller's process group.
terminated() { setprop vendor.a9ml.wifi.session.stop 1; }
trap terminated HUP INT TERM
note "run begin seconds=$seconds"
/system/bin/sh "$SELF_CTL" run "$ack_start" "$ack_risk" "$preliminary" --session "$seconds" --identity-reviewed "$identity" > "$STATE/run.out" 2>&1 &
ctl=$!
# `wait` is interrupted by the trap above; keep waiting until the controller is really gone.
rc=0
while :; do
    wait "$ctl"; rc=$?
    kill -0 "$ctl" 2>/dev/null || break
done
sed -n '/^STOP: /p' "$STATE/run.out" | head -n 3 | while IFS= read -r line; do note "run STOP: $line"; done
tail -n 12 "$STATE/run.out" | while IFS= read -r line; do note "run: $line"; done
if [ "$rc" = 0 ]; then
    state ended; note "ended orderly"
else
    state "failed"; note "ended with failure rc=$rc; Wi-Fi stays off until reboot; no retry"
fi
exit "$rc"
