#!/usr/bin/env bash
# User-run, read existing crash logs. Never reboot or change SELinux policy.
set -euo pipefail
umask 077
here=$(cd -- "$(dirname -- "$0")" && pwd)
. "$here/common.sh"
out=${1:?usage: hvenc-pull.sh NEW_OUTPUT_DIR [--recovery]}
mode=${2:-}
[[ -z $mode || $mode == --recovery ]] || exit 2
mkdir "$out"
out=$(cd "$out" && pwd)

# Base64 keeps the nc root protocol's textual return marker out of tar bytes.
# Preserve the archive without unpacking arbitrary remote filenames on the host.
archive() {
    local name=$1 command=$2
    if root "$command" > "$out/$name.b64" 2> "$out/$name-transfer.log" &&
       base64 --decode "$out/$name.b64" > "$out/$name.tar" &&
       tar -tf "$out/$name.tar" > "$out/$name-files.txt"; then
        rm "$out/$name.b64"
        return 0
    fi
    return 1
}

if [[ $mode == --recovery ]]; then
    # This branch is only for an already-running TWRP. No automatic reboot.
    [[ $(shell 'id -u' | tr -d '\r') == 0 ]]
    [[ -n $(shell 'getprop ro.twrp.version' | tr -d '\r') ]]
    root() { shell "set -e; set -o pipefail; $1"; }
    # Same journal-replaying filesystem mount used by b-try pull_logs.
    root 'mkdir -p /tmp/a9-hvenc-kdebug
if ! grep -q " /tmp/a9-hvenc-kdebug " /proc/mounts; then
    mount -t ext4 -o rw,noatime /dev/block/bootdevice/by-name/kdebuginfo /tmp/a9-hvenc-kdebug
fi' > "$out/mount.log" 2>&1
    logroot=/tmp/a9-hvenc-kdebug/mainline
else
    # Logger runs in a private chroot + mount namespace. Reuse its existing
    # filesystem through proc; no block access, mount, nsenter or policy change.
    if ! logroot=$(root 'for p in /proc/[0-9]*/root/kdebuginfo/mainline; do
    if [ -r "$p/counter" ]; then printf "%s\n" "$p"; exit 0; fi
done
echo "Cannot read the early logger private mount; use --recovery in TWRP." >&2
exit 1' 2> "$out/logger-access.log"); then
        echo "B cannot expose the logger files. After the current owner releases the phone, enter TWRP and run:" >&2
        printf 'bash %q %q --recovery\n' "$here/hvenc-pull.sh" "$out-recovery" >&2
        exit 1
    fi
    [[ $logroot =~ ^/proc/[0-9]+/root/kdebuginfo/mainline$ ]] || exit 1
fi

# Keep the last three saved boots: the crash, its automatic reboot, and a
# possible intermediate boot. Counter ordering, not log timestamps, selects them.
printf -v quoted_root '%q' "$logroot"
if ! archive mainline "set -o pipefail
cd $quoted_root
boots=\$(ls -1d boot-* | sed -n '/^boot-[0-9][0-9]*\$/p' | sort | tail -3)
[ -n \"\$boots\" ]
set --
for b in \$boots; do
    for n in info.txt kernel.log kernel.log.prev kernel.early.log logcat.log logcat.log.prev pstore.previous.log; do
        [ ! -f \"\$b/\$n\" ] || set -- \"\$@\" \"\$b/\$n\"
    done
done
[ \"\$#\" -gt 0 ]
tar -cf - \"\$@\" | base64"; then
    echo "Saved-boot transfer failed; retain the transfer log and use --recovery in TWRP." >&2
    exit 1
fi

if [[ $mode == --recovery ]]; then
    root 'mkdir -p /sys/fs/pstore
grep -q " /sys/fs/pstore " /proc/mounts || mount -t pstore pstore /sys/fs/pstore' > "$out/pstore-mount.log" 2>&1 || true
fi
if ! archive pstore 'set -o pipefail; ls -la /sys/fs/pstore >&2; tar -C /sys/fs -cf - pstore | base64'; then
    echo "Live pstore unavailable in this domain. Saved pstore.previous.log is in mainline.tar; raw pstore requires TWRP --recovery." >&2
fi
root 'dmesg' > "$out/current-dmesg.log" 2> "$out/current-dmesg-error.log" || true
shell 'getprop ro.boot.bootreason; getprop sys.boot.reason; cat /proc/uptime' > "$out/current-boot.txt" 2>&1 || true
echo "Saved logs: $out/mainline.tar (last three boots, including previous-boot pstore copies)."
echo "List: $out/mainline-files.txt; current-boot logs are separate. No pstore records were removed."
