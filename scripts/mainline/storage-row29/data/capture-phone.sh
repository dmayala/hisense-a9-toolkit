#!/system/bin/sh
# Read-only snapshot via direct root adb shell or a9ml_rootsh; no boot-complete gate.
# Host wraps transport with a deadline. Save output privately; it can contain IDs.
echo ROW29_CAPTURE_BEGIN
id
cat /proc/self/attr/current
cat /proc/sys/kernel/random/boot_id
cat /proc/cmdline
getenforce
for p in ro.boot.slot_suffix sys.boot_completed init.svc.a9ml_row29_map init.svc.a9ml_rootsh init.svc.logd init.svc.vendor.a9ml_logd init.svc.adbd init.svc.vold init.svc.zygote init.svc.zygote_secondary ro.crypto.state; do
    printf '%s=' "$p"; getprop "$p"
done
echo ROW29_LABELS
ls -ldZ /vendor/bin/a9ml-row29-map /dev /dev/block /dev/block/mapper /dev/block/mapper/a9-row29-data /dev/device-mapper /mnt /mnt/row29-test /data /data/local /data/local/tmp 2>&1
ls -lZ /dev/block/dm-* 2>&1 | head -n 80
echo ROW29_MOUNTS
cat /proc/self/mountinfo
echo ROW29_TABLE
dmctl table a9-row29-data 2>&1
echo ROW29_KERNEL
dmesg 2>&1
echo ROW29_LOGCAT
logcat -b all -d -v threadtime -t 3000 2>&1
echo ROW29_CAPTURE_END
