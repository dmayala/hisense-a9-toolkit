#!/system/bin/sh
# Opt-in stock-client data preparation only. MPSS/storage remain externally owned.
set -efu
umask 027
stage=gates
phase() { stage=$1; setprop vendor.a9ml.cellular.stock.phase "$stage"; }
gates() {
    [ "$(getprop vendor.a9ml.cellular)" = 1 ] &&
    [ "$(getprop ro.a9ml.wifi.session)" = 1 ] &&
    [ "$(getprop ro.boot.slot_suffix)" = _b ] || return 1
}
fail() {
    trap - 0
    setprop vendor.a9ml.cellular.stock.ready 0 || :
    setprop vendor.a9ml.cellular.stock.state failed || :
    echo "A9_STOCK_RADIO preparation failed phase=$stage" >&2
    exit 1
}
trap 'fail' 0
setprop vendor.a9ml.cellular.stock.netmgr blocked-kernel-abi
phase gates
# Ordinary file preparation does not depend on WLAN. Init starts QCRIL after
# verified preparation, while IMS additionally needs the serving owner.
gates || fail
# QCRIL's framework endpoints must exist with empty SIMs or unavailable packet
# data. IPA/rmnet are data-plane prerequisites, not prerequisites for preparing
# the stock radio files or publishing IRadio. Their failed module load must
# leave netmgr blocked, without withholding both declared radio HALs.
phase clients
# Every helper call rechecks the opt-in/B-slot prerequisites. Repeated calls in
# one enabled interval do not rewrite a live QCRIL database. Opt-out clears ready.
if [ "$(getprop vendor.a9ml.cellular.stock.ready)" = 1 ]; then
    phase complete
    trap - 0
    exit 0
fi
for service in vendor.qcrild vendor.qcrild2 vendor.qcrild3 vendor.imsqmidaemon vendor.imsdatadaemon vendor.ims_rtp_daemon vendor.imsrcsservice; do
    case "$(getprop init.svc.$service)" in ''|stopped) ;; *) fail ;; esac
done

# Check each path component before creating anything; never follow a symlink
# from an ordinary runtime directory into another filesystem or protected store.
new_mcfg_dirs=
ensure_dir() {
    path=$1
    case "$path" in /data/vendor|/data/vendor/*|/dev/socket/netmgr|/dev/socket/qmux_radio) ;; *) fail ;; esac
    walk=
    old_ifs=$IFS
    IFS=/
    set -- $path
    IFS=$old_ifs
    for component do
        [ -n "$component" ] || continue
        walk=$walk/$component
        [ ! -L "$walk" ] || fail
        if [ ! -e "$walk" ]; then
            mkdir "$walk" || fail
            case "$walk" in
                /data/vendor) chmod 0771 "$walk" ;;
                /data/vendor/modem_config/*)
                    # Names were restricted before ensure_dir; no whitespace or
                    # glob characters. Finalize newly created dirs in one batch.
                    new_mcfg_dirs="$new_mcfg_dirs $walk"
                    ;;
            esac
        fi
        [ -d "$walk" ] || fail
    done
}
plain_file() {
    [ ! -L "$1" ] || fail
    [ ! -e "$1" ] || [ -f "$1" ] || fail
}

setprop vendor.a9ml.cellular.stock.state preparing
phase sources
source=/vendor/firmware/modem_pr/mcfg/configs
files=/vendor/etc/a9-stock-radio/mcfg-files.sha256
[ -d "$source" ] && [ ! -L "$source" ] || fail
[ -f "$files" ] && [ ! -L "$files" ] || fail
(cd "$source" && sha256sum -c "$files" >/dev/null) || fail
(cd /vendor/radio/qcril_database && sha256sum -c /vendor/etc/a9-stock-radio/qcril-db.sha256 >/dev/null) || fail

phase copying
copied=0
setprop vendor.a9ml.cellular.stock.count 0
ensure_dir /data/vendor
ensure_dir /data/vendor/radio
chown system:radio /data/vendor/radio
chmod 0770 /data/vendor/radio
ensure_dir /data/vendor/modem_config
chown radio:root /data/vendor/modem_config
chmod 0570 /data/vendor/modem_config
set --
while read -r digest relative; do
    case "$relative" in mcfg_hw/*|mcfg_sw/*) ;; *) fail ;; esac
    case "$relative" in *..*|*[!a-zA-Z0-9_+./-]*) fail ;; esac
    [ -f "$source/$relative" ] && [ ! -L "$source/$relative" ] || fail
    dest=/data/vendor/modem_config/$relative
    ensure_dir "${dest%/*}"
    plain_file "$dest"
    cp "$source/$relative" "$dest" || fail
    # Preserve every per-path/symlink check and copy. Batch metadata work only;
    # no clients can start while ready remains zero.
    set -- "$@" "$dest"
    copied=$((copied + 1))
    if [ $((copied % 16)) -eq 0 ]; then
        setprop vendor.a9ml.cellular.stock.count "$copied"
    fi
done < "$files"
[ "$copied" -eq 134 ] || fail
setprop vendor.a9ml.cellular.stock.count "$copied"
phase metadata
# All positional arguments are the previously checked destinations. This avoids
# two new child processes for every individual profile.
chown radio:root "$@"
chmod 0640 "$@"
if [ -n "$new_mcfg_dirs" ]; then
    # Only fixed /data/vendor/modem_config + restricted manifest components.
    # Filename expansion is disabled above, so splitting cannot expand globs.
    set -- $new_mcfg_dirs
    chown radio:root "$@"
    chmod 0750 "$@"
fi

phase database
# Match stock's prebuilt seed destination; never replace its live qcril.db.
plain_file /data/vendor/radio/qcril_prebuilt.db
cp /vendor/radio/qcril_database/qcril.db /data/vendor/radio/qcril_prebuilt.db || fail
chown radio:radio /data/vendor/radio/qcril_prebuilt.db
chmod 0660 /data/vendor/radio/qcril_prebuilt.db
ensure_dir /data/vendor/netmgr
chown radio:radio /data/vendor/netmgr
chmod 0770 /data/vendor/netmgr
ensure_dir /data/vendor/netmgr/recovery
chown radio:radio /data/vendor/netmgr/recovery
chmod 0770 /data/vendor/netmgr/recovery
plain_file /data/vendor/netmgr/recovery/recovery_info
if [ ! -e /data/vendor/netmgr/recovery/recovery_info ]; then
    : > /data/vendor/netmgr/recovery/recovery_info
fi
chown radio:radio /data/vendor/netmgr/recovery/recovery_info
chmod 0660 /data/vendor/netmgr/recovery/recovery_info
ensure_dir /dev/socket/netmgr
chown radio:radio /dev/socket/netmgr
chmod 0750 /dev/socket/netmgr
# Stock init.qcom.rc creates this IPC parent before qcrild starts.
# QtiBus itself binds ril_ipc; do not create a substitute socket/server.
ensure_dir /dev/socket/qmux_radio
chown radio:radio /dev/socket/qmux_radio
chmod 2770 /dev/socket/qmux_radio
phase labeling
restorecon -RF /data/vendor/radio /data/vendor/modem_config /data/vendor/netmgr /dev/socket/netmgr /dev/socket/qmux_radio

phase verifying
# Verify the destination before publishing the stock completion marker.
(cd /data/vendor/modem_config && sha256sum -c "$files" >/dev/null) || fail
(cd /data/vendor/radio && sha256sum qcril_prebuilt.db) | {
    read -r actual ignored
    read -r expected ignored < /vendor/etc/a9-stock-radio/qcril-db.sha256
    [ "$actual" = "$expected" ]
} || fail
plain_file /data/vendor/radio/copy_complete
printf '1\n' > /data/vendor/radio/copy_complete
chown radio:radio /data/vendor/radio/copy_complete
chmod 0660 /data/vendor/radio/copy_complete
restorecon /data/vendor/radio/copy_complete
if [ "$(getprop ro.vendor.ril.mbn_copy_completed)" != 1 ]; then
    setprop ro.vendor.ril.mbn_copy_completed 1
fi
gates || fail
phase complete
setprop vendor.a9ml.cellular.stock.state prepared
setprop vendor.a9ml.cellular.stock.ready 1
trap - 0
exit 0
