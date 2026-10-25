#!/usr/bin/env bash
#
# a9-bootlog.sh — the per-boot logs the a9-bootlog Magisk module keeps on the
# Hisense kdebuginfo partition (magisk/a9-bootlog/). pstore is useless here
# (no warm reset compiled in, a6l-harvest §1.7), so this is how a crashed or
# hung boot leaves evidence: kernel log from boot (written through, -o sync),
# power-on/off reasons, cmdline, the XBL+ABL bootloader log, logcat; last 8 boots.
#
# Usage:
#   a9-bootlog.sh install          # zip + magisk --install-module (active after next reboot)
#   a9-bootlog.sh status           # module state, mount, boots kept, free space
#   a9-bootlog.sh list             # boots on the phone with their info line (Android or TWRP)
#   a9-bootlog.sh pull [boot-NNNNN|all] [dir]   # copy logs to dir (default runs/<UTC>-bootlog);
#                                  # works from TWRP too: the way to read a bootlooping Android's logs
#   a9-bootlog.sh offline <kdebuginfo.bin> [dir] # read them from an EDL dump, no phone
#   a9-bootlog.sh uninstall        # remove the module (logs stay on kdebuginfo)
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
MOD="$A9_HOME/magisk/a9-bootlog"
MNT=/mnt/a9-kdebug

need_android() { [ "$(a9_adb_state)" = device ] || { echo "ERROR: phone not in Android" >&2; exit 1; }; }

# list/pull also work from TWRP: that is the case that matters (Android
# bootloops, /data stays encrypted, but kdebuginfo is plain ext4). In TWRP adb is
# already root and nothing mounts kdebuginfo, so mount it here (read-write, so
# ext4 replays its journal after a hard reset and the last lines are kept).
STATE=$(a9_adb_state)
TMPDIR_PHONE=/data/local/tmp
rsh() {  # root shell in Android (Magisk su) or TWRP (adb is root)
  if [ "$STATE" = recovery ]; then "$ADB" shell "$1" </dev/null 2>/dev/null | tr -d '\r'; else a9_su "$1"; fi
}
need_logs() {
  case "$STATE" in
    device) ;;
    recovery)
      MNT=/tmp/a9-kdebug; TMPDIR_PHONE=/tmp
      rsh "grep -q \" $MNT \" /proc/mounts || { mkdir -p $MNT && mount -t ext4 -o rw,noatime /dev/block/bootdevice/by-name/kdebuginfo $MNT; }; grep -c \" $MNT \" /proc/mounts" | grep -q 1 \
        || { echo "ERROR: could not mount kdebuginfo in recovery" >&2; exit 1; }
      echo "(reading kdebuginfo from TWRP)" >&2 ;;
    *) echo "ERROR: phone must be in Android or TWRP (adb state '$STATE')" >&2; exit 1 ;;
  esac
}

case "${1:-status}" in
  install)
    need_android
    z=$(mktemp -d)/a9-bootlog.zip
    ( cd "$MOD" && zip -qr "$z" . ) || exit 1
    "$ADB" push "$z" /data/local/tmp/a9-bootlog.zip >/dev/null || exit 1
    a9_su 'magisk --install-module /data/local/tmp/a9-bootlog.zip; rm -f /data/local/tmp/a9-bootlog.zip'
    echo "Installed. It starts logging at the next boot."
    ;;
  uninstall)
    need_android
    a9_su 'touch /data/adb/modules/a9-bootlog/remove' && echo "Module removed at next boot; logs stay in kdebuginfo:/a9."
    ;;
  status)
    need_android
    a9_su "ls -d /data/adb/modules/a9-bootlog 2>/dev/null && cat /data/adb/modules/a9-bootlog/module.prop | grep ^version=;
      ls /data/adb/modules/a9-bootlog/disable /data/adb/modules/a9-bootlog/remove 2>/dev/null;
      grep ' $MNT ' /proc/mounts || echo 'not mounted';
      df -h $MNT 2>/dev/null | tail -1; ls $MNT/a9 2>/dev/null | grep -c boot- ; cat /data/adb/a9-bootlog.err 2>/dev/null | tail -3"
    ;;
  list)
    need_logs
    # no single quotes inside: a9_su wraps the command in them
    rsh "for d in $MNT/a9/boot-*; do [ -d \$d ] || continue; echo == \${d##*/} \$(du -sh \$d | cut -f1); head -2 \$d/info.txt; grep -m1 boot_completed \$d/info.txt || echo \"   (never reached boot_completed)\"; done"
    ;;
  pull)
    need_logs
    what="${2:-all}"; out="${3:-}"
    if [ -z "$out" ]; then a9_run_dir bootlog "$what"; out="$A9_RUN"; fi
    mkdir -p "$out"
    src="$MNT/a9"; [ "$what" = all ] || src="$MNT/a9/$what"
    rsh "tar -C $(dirname "$src") -cf $TMPDIR_PHONE/a9-bootlog.tar $(basename "$src") && echo TAR_OK" | grep -q TAR_OK || exit 1
    "$ADB" pull $TMPDIR_PHONE/a9-bootlog.tar "$out/" >/dev/null && tar -C "$out" -xf "$out/a9-bootlog.tar" && rm -f "$out/a9-bootlog.tar"
    rsh "rm -f $TMPDIR_PHONE/a9-bootlog.tar"
    echo "logs in $out"
    ;;
  offline)
    img="${2:-}"; out="${3:-$PWD/kdebuginfo-a9}"
    [ -f "$img" ] || { echo "usage: $0 offline <kdebuginfo.bin> [dir]" >&2; exit 2; }
    mkdir -p "$out"
    debugfs -c -R "rdump /a9 $out" "$img" 2>/dev/null
    debugfs -c -R "rdump /kmon_log $out" "$img" 2>/dev/null   # stock Hisense boot logs (XBL+ABL+kernel)
    echo "logs in $out"; ls "$out"
    ;;
  *) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
