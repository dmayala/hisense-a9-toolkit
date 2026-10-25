#!/system/bin/sh
# a9ml-ctl.sh -- one command per TCP connection (tcp 2323 on NCM), DRAFT.
# Mirrors the diskless image's a9-ctl: ping | stay <s> | release | capture |
# bootloader. "capture" streams a tar.gz to stdout. Read-only extras for
# supervised runs (runs/sensors.md section 11): "kmsg" streams the kernel log
# live (dmesg -w) until the host disconnects; "irq" prints the tsens lines of
# /proc/interrupts.
STAY=/data/local/tmp/a9-stay
read -r cmd arg
case "$cmd" in
ping) echo "pong $(cat /proc/uptime) boot_completed=$(getprop sys.boot_completed)";;
stay)
	touch $STAY; echo "stay ${arg:-300}s"
	( sleep "${arg:-300}"; rm -f $STAY ) >/dev/null 2>&1 &;;
release) rm -f $STAY; echo released;;
capture)
	D=/data/local/tmp/a9cap; rm -rf $D; mkdir -p $D
	dmesg > $D/dmesg.txt 2>&1
	logcat -d -b all -v threadtime > $D/logcat.txt 2>&1
	getprop > $D/getprop.txt
	cat /proc/cmdline > $D/cmdline.txt
	cat /sys/module/qcom_pon/parameters/beacon > $D/beacon.txt 2>&1
	cat /proc/mounts > $D/mounts.txt
	cat /proc/meminfo > $D/meminfo.txt
	ls -l /dev/block/by-name/ > $D/by-name.txt 2>&1
	cat /sys/kernel/debug/devices_deferred > $D/devices_deferred.txt 2>&1
	ps -A -o PID,PPID,LABEL,USER,NAME > $D/ps.txt 2>&1
	getenforce > $D/getenforce.txt 2>&1
	dumpsys -l > $D/services.txt 2>&1
	tar -czf - -C /data/local/tmp a9cap;;
bootloader) echo "rebooting to bootloader"; setprop sys.powerctl reboot,bootloader;;
kmsg) exec dmesg -w;;
irq) grep -E 'CPU0|thermal-sensor' /proc/interrupts;;
*) echo "unknown: $cmd (ping|stay <s>|release|capture|bootloader|kmsg|irq)";;
esac
