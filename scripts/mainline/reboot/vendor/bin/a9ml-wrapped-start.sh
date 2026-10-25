#!/system/bin/sh
# Real-data wrapped trial: storage work is bounded; no TEST mapper or GPT check.
log() { echo "a9ml-wrapped: $*" > /dev/kmsg; }
bounded() { /system/bin/timeout -s KILL 15 "$@"; }
trace() { setprop "vendor.a9.trace.$1" "$2" || :; }
fbe_failure_trace() {
    # The HAL trace emits only these fixed public numeric fields. Copy them to
    # the durable kernel logger before a long sweep rotates early logcat away.
    bounded /system/bin/logcat -b all -d -v raw -s A9FBE:I '*:S' |
        grep -E '^A9_FBE_KEY begin_rc=-[0-9]+ chars_rc=-?[0-9]+ requested_gcm=[01] hardware_aes=[01] hardware_gcm=[01] hardware_decrypt=[01] os_version=[0-9]+ os_patch=[0-9]+ vendor_patch=[0-9]+$|^A9_FBE_CONFIG configure_rc=-?[0-9]+ os_version=[0-9]+ os_patch=[0-9]+ vendor_patch=[0-9]+ boot_patch_present=[01] boot_patch=[0-9]+$|^A9_FBE_LOCAL local_rc=-?[0-9]+ verified=0 hardware_aes=[01] hardware_gcm=[01] hardware_decrypt=[01] os_version=[0-9]+ os_patch=[0-9]+ vendor_patch=[0-9]+ boot_patch_present=[01] boot_patch=[0-9]+$' |
        while IFS= read -r record; do log "$record"; done
}
case "$1" in
metadata)
    trace metadata mount
    bounded /system/bin/mount -t ext4 -o noatime,nosuid,nodev,discard /dev/block/by-name/metadata /metadata || { log "metadata mount failed/timed out"; exit 1; }
    grep -q ' /metadata ext4 ' /proc/mounts || exit 1
    trace metadata restorecon
    bounded /system/bin/restorecon /metadata || exit 1
    trace metadata complete
    log "real metadata mounted"
    ;;
persist)
    # Separate bounded init service: never mix physical persist with metadata.
    # Preserve every physical inode/xattr: no mutations on the mounted fs,
    # context override, fsck, journal replay, writable bind or fallback.
    persist_dev=/dev/block/by-name/persist
    persist_dir=/mnt/vendor/persist
    persist_phase=1
    persist_record() {
        record="A9_PERSIST_RO phase=$persist_phase rc=$1"
        log "$record"
        /system/bin/timeout -s KILL 1 /system/bin/log -p w -t A9_PERSIST_RO "$record" || :
    }
    persist_fail() { persist_record "$1"; exit 1; }
    persist_bounded() { /system/bin/timeout -s KILL 3 "$@"; }
    [ -b "$persist_dev" ] && [ ! -L "$persist_dir" ] && [ ! -L /mnt/vendor ] || persist_fail 1
    numbers=$(persist_bounded /system/bin/stat -Lc '%t %T' "$persist_dev") || persist_fail "$?"
    set -- $numbers
    [ "$#" = 2 ] || persist_fail 1
    case "$1$2" in ''|*[!0-9a-fA-F]*) persist_fail 1 ;; esac
    persist_id="$((16#$1)):$((16#$2))"
    # Do not stack a mount or share a preexisting mount of uncertain origin.
    # mountinfo supplies device identity without printing a path or identifier.
    mounted=$(awk -v id="$persist_id" -v target="$persist_dir" '$3==id || $5==target {found=1} END {print found+0}' /proc/self/mountinfo) || persist_fail "$?"
    [ "$mounted" = 0 ] || persist_fail 1
    if [ ! -d "$persist_dir" ]; then
        # The selected GSI does not create this mountpoint. Prepare only an
        # empty RAM directory BEFORE attaching persist, never on its filesystem.
        # Refuse intervening mounts and symlinks rather than touching a store.
        awk '
            $5=="/mnt/vendor" {bad=1}
            $5=="/mnt" {
                n++
                for(i=7;i<=NF;i++) if($i=="-") {ram=$(i+1)=="tmpfs"; break}
            }
            END {exit !(n==1 && ram && !bad)}' /proc/self/mountinfo || persist_fail 1
        (umask 022; persist_bounded /system/bin/mkdir -p "$persist_dir") || persist_fail "$?"
    fi
    contents=$(persist_bounded /system/bin/ls -A "$persist_dir") || persist_fail "$?"
    [ -z "$contents" ] || persist_fail 1
    persist_bounded /system/bin/blockdev --setro "$persist_dev" || persist_fail "$?"
    readonly=$(persist_bounded /system/bin/blockdev --getro "$persist_dev") || persist_fail "$?"
    [ "$readonly" = 1 ] || persist_fail 1
    persist_phase=2
    /system/bin/timeout -s KILL 10 /system/bin/mount -t ext4 -o ro,noload,nosuid,nodev,noexec "$persist_dev" "$persist_dir" || persist_fail "$?"
    persist_phase=3
    # Check VFS flags, ext4's displayed norecovery/noload option and the
    # read-only superblock. The block flag stays set for the rest of this boot.
    awk -v id="$persist_id" -v target="$persist_dir" '
        function has(options, key) {return index("," options ",", "," key ",") != 0}
        $3==id && $5==target {
            n++; good=has($6,"ro") && has($6,"nosuid") && has($6,"nodev") && has($6,"noexec")
            for(i=7;i<=NF;i++) if($i=="-") {
                separator=1
                good=good && $(i+1)=="ext4" && has($(i+3),"ro") &&
                    (has($(i+3),"norecovery") || has($(i+3),"noload"))
                break
            }
        }
        END {exit !(n==1 && separator && good)}' /proc/self/mountinfo || persist_fail 1
    readonly=$(persist_bounded /system/bin/blockdev --getro "$persist_dev") || persist_fail "$?"
    [ "$readonly" = 1 ] || persist_fail 1
    persist_record 0
    ;;
data)
    setprop vendor.a9.data.ready 0
    trace data entered
    trace data_casefold "$(getprop external_storage.casefold.enabled)"
    grep -q ' /metadata ext4 ' /proc/mounts || { log "real metadata missing; no RAM keys"; exit 1; }
    # early-fs starts adbd on a bootstrap tmpfs. Retire that mount before vold
    # installs the real one, rather than leaving a hidden /data underlay.
    # Lazy detach preserves existing adbd descriptors until its normal restart.
    bootstrap=0
    mounts=$(awk '$2=="/data" {n++; if($3=="tmpfs") t++} END {print n+0,t+0}' /proc/mounts)
    case "$mounts" in
        '1 1') bounded /system/bin/umount -l /data || exit 1; bootstrap=1 ;;
        '0 0') ;;
        *) trace data unexpected_mount; log "unexpected initial data mount; refusing another layer"; exit 1 ;;
    esac
    log "A9_FBE_MOUNT bootstrap_detached=$bootstrap bootstrap_restored=0"
    # Existing keys: mount only. Fresh TWRP-formatted trial: format through the
    # newly created dm-default-key mapping, avoiding whole-partition inplace crypto.
    if [ -d /metadata/vold/metadata_encryption/key ]; then
        trace data mount_request
        /system/bin/timeout -s KILL 60 /system/bin/vdc cryptfs mountFstab /dev/block/by-name/userdata /data false ""
    else
        trace data encrypt_request
        /system/bin/timeout -s KILL 60 /system/bin/vdc cryptfs encryptFstab /dev/block/by-name/userdata /data true f2fs false 0 "" ""
    fi
    status=$?
    trace data_rc "$status"
    log "wrapped vold request rc=$status"
    [ "$status" = 0 ] || fbe_failure_trace
    if [ "$status" != 0 ]; then
        # Keep the existing diagnostic RAM fallback only when vold mounted
        # nothing. Never cover a partially successful real mount on an error.
        if [ "$bootstrap" = 1 ] && ! awk '$2=="/data" {found=1} END {exit !found}' /proc/mounts; then
            if bounded /system/bin/mount -t tmpfs -o mode=0771,uid=1000,gid=1000,nosuid,nodev tmpfs /data; then
                bounded /system/bin/restorecon /data
                mkdir -p /data/misc/adb
                chown 1000:2000 /data/misc/adb
                chmod 2750 /data/misc/adb
                cp /vendor/etc/adb_keys /data/misc/adb/adb_keys
                chown 1000:2000 /data/misc/adb/adb_keys
                chmod 0640 /data/misc/adb/adb_keys
                bounded /system/bin/restorecon -R /data/misc/adb
                log "A9_FBE_MOUNT bootstrap_detached=1 bootstrap_restored=1"
            fi
        fi
        exit "$status"
    fi
    awk '$2=="/data" {n++; if($3=="f2fs") f++} END {exit !(n==1 && f==1)}' /proc/mounts || { trace data f2fs_missing; log "vold returned without one real f2fs data mount"; exit 1; }
    trace data f2fs_mounted
    # These describe the verified real wrapped mount. The diagnostic worker
    # needs the same narrow status-property permission as the normal owner.
    [ "$(getprop ro.crypto.state)" = encrypted ] || setprop ro.crypto.state encrypted || exit 1
    [ "$(getprop ro.crypto.type)" = file ] || setprop ro.crypto.type file || exit 1
    [ "$(getprop ro.crypto.state)" = encrypted ] && [ "$(getprop ro.crypto.type)" = file ] || exit 1
    # The existing init on-boot action owns persistent ADB directory/key setup
    # after platform post-fs-data. Doing it here hits system_data_file denials
    # and made a successful mount exit 1; do not add wider rootsh permissions.
    setprop vendor.a9.data.ready 1 || exit 1
    ;;
*) exit 2 ;;
esac
