#!/system/bin/sh
# Only installed in A9_BLUETOOTH=1 vendors. Never opens a UART or block device.
# HCI firmware/setup runs in Linux QCA; Android takes HCI_CHANNEL_USER later.
set -eu
fail() { setprop vendor.a9.bt.ready 0; log -t a9-bt "$1"; exit 1; }
[ "$(getprop ro.vendor.a9.bluetooth)" = 1 ] || exit 0
setprop vendor.a9.bt.ready 0
M=/vendor/lib/modules
[ -s "$M/modules.bluetooth" ] || fail 'BT module list absent'
[ -f /vendor/etc/bluetooth/local-bd-address ] || fail 'private identity absent'
# The kernel looks for qca/* only under firmware_class.path, which the E Ink
# cmdline sets to the RAM-/data staging directory (waveform/template live in
# its hisense/ subdirectory). Copy the vendor's pinned QCA files there before
# any module insertion and check every byte against the vendor manifest; no
# file may be missing, extra or altered. /data is the fresh RAM /data.
# Integrated Wi-Fi images point firmware_class.path at the Wi-Fi link farm
# (/mnt/a9-wifi/firmware); there the bridge links qca -> /vendor/firmware/qca.
FWD=/data/local/tmp/a9fw Q=/vendor/firmware/qca S=/vendor/etc/bluetooth/firmware.sha256
FWP=$(cat /sys/module/firmware_class/parameters/path 2>/dev/null)
if [ "$FWP" = /mnt/a9-wifi/firmware ]; then BRIDGE=1; FWD=$FWP; else BRIDGE=0; fi
[ "$FWP" = "$FWD" ] || fail 'firmware search path is not the staging directory'
[ -f "$S" ] || fail 'firmware manifest absent'
fw_ok() {  # $1: directory that must hold exactly the manifest's files
    [ "$(ls -A "$1" | wc -l)" = "$(grep -c . "$S")" ] || return 1
    (cd "$1" && sha256sum -c "$S") >/dev/null 2>&1
}
fw_ok "$Q" || fail 'vendor firmware differs from manifest'
if [ "$BRIDGE" = 1 ]; then
    /vendor/bin/a9ml-wifi-firmware.sh >/dev/null || fail 'Wi-Fi firmware bridge'
else
    [ -d /data/local/tmp ] && [ ! -L /data/local/tmp ] || fail 'staging parent absent'
    # integ-1 (review 44): a9fw is ROOT-owned 0755 on every vendor. The GPU
    # vendor's a9ml-gpu.sh setup (exec_start at post-fs-data, concurrent with this
    # service) may create it first; the E Ink blobs are installed later as root by
    # android-boot.sh/eink-fw-install.sh, never pushed into a9fw as shell. A
    # shell-writable firmware directory would be a TOCTOU path (review 43).
    [ -e "$FWD" ] || [ -L "$FWD" ] || mkdir "$FWD" 2>/dev/null || [ -d "$FWD" ] || fail 'staging directory'
    [ -d "$FWD" ] && [ ! -L "$FWD" ] || fail 'staging directory is not a directory'
    chown root:root "$FWD" && chmod 0755 "$FWD" || fail 'staging directory ownership'
    [ "$(stat -c %a "$FWD" 2>/dev/null)" = 755 ] || fail 'staging directory mode'
    [ ! -e "$FWD/qca" ] && [ ! -L "$FWD/qca" ] || fail 'stale firmware staging directory'
    mkdir "$FWD/qca" && chmod 0755 "$FWD/qca" || fail 'firmware staging directory'
    while read -r sum name; do
        case "$name" in ''|*[!a-zA-Z0-9_.-]*) fail 'invalid firmware manifest entry' ;; esac
        cp "$Q/$name" "$FWD/qca/$name" && chmod 0644 "$FWD/qca/$name" || fail 'firmware copy failed'
    done < "$S"
fi
fw_ok "$FWD/qca" || fail 'staged firmware differs from manifest'
# The display loader reads modules.load only; BT has its own dependency order.
while IFS= read -r ko; do
    case "$ko" in ''|*[!a-zA-Z0-9_.-]*) fail 'invalid BT module entry' ;; esac
    module=${ko%.ko}; module=$(echo "$module" | tr '-' '_')
    if [ ! -d "/sys/module/$module" ]; then
        insmod "$M/$ko" || fail 'BT module insertion failed'
    fi
done < "$M/modules.bluetooth"
seconds() { read -r uptime rest < /proc/uptime; echo "${uptime%.*}"; }
# A monotonic boot-time deadline includes subprocess/poll time, unlike counting
# sleeps. A single read-only management query has at most a 100 ms poll wait.
deadline=$(($(seconds)+30))
while [ "$(seconds)" -lt "$deadline" ]; do
    # Reject any additional controller: default HAL controller selection must
    # not accidentally claim a USB/vhci device with a different identity.
    for h in /sys/class/bluetooth/hci*; do
        [ -e "$h" ] || continue
        [ "${h##*/}" = hci0 ] || fail 'unexpected additional HCI controller'
    done
    path=$(readlink -f /sys/class/bluetooth/hci0 2>/dev/null || true)
    case "$path" in
        */4a8c000.serial/*)
            if /vendor/bin/a9-bt-identity \
                /vendor/etc/bluetooth/local-bd-address \
                /sys/class/bluetooth/hci0/device/of_node/local-bd-address 2>/dev/null; then
                # The download read these files; prove they were still the pinned ones.
                fw_ok "$FWD/qca" || fail 'staged firmware changed during setup'
                setprop vendor.a9.bt.ready 1
                log -t a9-bt 'UART controller and identity verified; HAL gate open'
                exit 0
            fi ;;
    esac
    sleep 0.1
done
fail 'controller setup/identity deadline; HAL remains stopped'
