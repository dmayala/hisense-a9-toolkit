#!/system/bin/sh
# a9-bootlog: after Android is up, add power-on/off reasons and logcat.
MNT=/mnt/a9-kdebug
B=$MNT/a9/current
[ -d $B ] || exit 0
until [ "$(getprop sys.boot_completed)" = 1 ]; do sleep 5; done
{
  echo "boot_completed $(date -u +%FT%TZ)  uptime $(cut -d' ' -f1 /proc/uptime)"
  echo "build $(getprop ro.lineage.version) $(getprop ro.build.display.id)"
  dmesg | grep -E 'Power-(on|off) reason|PON|powerup_reason|set_dload_mode' | head -20
} >> $B/info.txt
# logcat, warnings and errors only (the partition is mounted -o sync; full
# logcat measured ~57 KB/min of synchronous writes, 2026-10-14), rotated: at
# most 4 x 2 MiB per boot. The kernel log above stays complete.
logcat -b all -v threadtime -f $B/logcat.txt -r 2048 -n 3 '*:W' &
exit 0
