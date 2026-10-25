#!/system/bin/sh
# a9-bootlog: mount the Hisense kdebuginfo partition (ext4, 200 MiB, written by
# stock Hisense firmware until the GSI replaced /system on 2026-08-10) and keep
# per-boot logs there. Stock files (kmon_log/, power_reason.txt, edpd/, ...)
# are left untouched; everything of ours lives under a9/.
#
# Never formats. If the partition is not a mountable ext4, it does nothing.
MNT=/mnt/a9-kdebug
DEV=/dev/block/by-name/kdebuginfo
ERR=/data/adb/a9-bootlog.err
KEEP=8   # boots kept; each boot is capped at ~16 MiB (kmsg 2 x 4, logcat 4 x 2)

mkdir -p $MNT
if ! grep -q " $MNT " /proc/mounts; then
  mount -t ext4 -o rw,sync,nosuid,nodev,noexec,noatime $DEV $MNT 2>>$ERR || { echo "$(date) mount failed" >>$ERR; exit 0; }
fi
mkdir -p $MNT/a9 || exit 0

# boot number = last + 1
N=$(cat $MNT/a9/last 2>/dev/null); N=$(( ${N:-0} + 1 )); echo $N > $MNT/a9/last
B=$MNT/a9/boot-$(printf %05d $N); mkdir -p $B
ln -sfn boot-$(printf %05d $N) $MNT/a9/current

# drop the oldest boots beyond KEEP, and more while less than 24 MiB is free
prune() {
  set -- $(ls -d $MNT/a9/boot-* 2>/dev/null | sort)
  while [ $# -gt $KEEP ] || { [ $# -gt 1 ] && [ "$(df -k $MNT | awk 'NR==2{print $4}')" -lt 24576 ]; }; do
    rm -rf "$1"; shift
  done
}
prune

{
  echo "boot $N  post-fs-data $(date -u +%FT%TZ)  uptime $(cut -d' ' -f1 /proc/uptime)"
  echo "slot $(getprop ro.boot.slot_suffix)  bootreason $(getprop ro.boot.bootreason)  sys.boot.reason $(getprop sys.boot.reason)"
  echo "cmdline: $(cat /proc/cmdline)"
} > $B/info.txt
# this boot's XBL + ABL log (Hisense CONFIG_HISENSE_BOOTLOADER_LOG; what stock
# kernel_mond put at the top of each kmonlog): PON registers, devinfo/diag
# flags, slot choice, DTBO, AVB state, E Ink VCOM. docs/stock-boot-logs.md
# Early in boot this node never signals EOF: a read returns the same ~330-line
# log over and over (86 copies, 48 MiB, measured 2026-10-14). Keep the first
# copy only: stop where the first line appears again; strip NULs; cap 256 KiB.
tr -d '\000' < /proc/debug_control/bootloader_log 2>/dev/null | head -c 4194304 \
  | awk 'NR==1 { first = $0; print; next } index($0, first) { exit } { print }' \
  | head -c 262144 > $B/bootloader.txt

# whole kernel log from boot, then live; every reader of /dev/kmsg gets its own
# copy, so this does not steal lines from logd. The partition is mounted
# -o sync (toybox sync has no -f), so each line is on flash when written and a
# hard reset loses nothing already logged; no global sync, other filesystems
# are not touched.
# kmsg.txt is rotated to kmsg.prev.txt at 4 MiB (the restarted reader replays
# the kernel ring buffer once, so a few lines repeat after a rotation).
(
  cat /dev/kmsg > $B/kmsg.txt 2>/dev/null & C=$!
  while true; do
    sleep 60
    if [ "$(stat -c %s $B/kmsg.txt 2>/dev/null || echo 0)" -gt 4194304 ]; then
      kill $C; mv -f $B/kmsg.txt $B/kmsg.prev.txt
      cat /dev/kmsg > $B/kmsg.txt 2>/dev/null & C=$!
    fi
  done
) &
exit 0
