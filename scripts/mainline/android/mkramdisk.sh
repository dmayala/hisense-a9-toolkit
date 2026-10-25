#!/usr/bin/env bash
# mkramdisk.sh -- build the boot ramdisk (and optionally boot_b image) for the
# A9 mainline-kernel Android boot. DRAFT 2026-10-15. Offline only: no adb,
# fastboot or edl; reads ~/a9-assets read-only, writes under ~/A9-build.
#
#   scripts/mainline/android/mkramdisk.sh [--bootimg] [--check]
#
# Ramdisk = the STOCK Android 11 first-stage ramdisk from the stock package
# boot.img (the golden boot_b is Magisk-patched), with:
#   - fstab.default / fstab.emmc REMOVED (they carry userdata/metadata/misc/
#     persist with formattable/check) and /fstab.hlte556n added;
#   - everything else unchanged: /init (A11 first stage, 1,524,736 bytes),
#     /system/bin/e2fsck, /avb/*.avbpubkey (unused: no avb flags), empty dirs.
#   - stock has no /lib/modules, no prop.default (MEASURED); asserted below.
# Packed newc, uid/gid 0, gzip -9n (stock is gzip; kernel has RD_GZIP=y).
#
# --bootimg: mkbootimg header v2 with the mainline kernel/DTB/recovery_dtbo
#   from $A9_MAINLINE/out and the Android cmdline below -> out/a9-android-boot.img
# --check:   scripts/abl-check.sh <img> boot --dtbo <null dtbo> (the ABL
#   emulator, offline) on that image.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
W=${A9_MAINLINE:-$HOME/A9-build/mainline}
A=$W/android
WORK=$A/work/ramdisk
OUT=$A/out
STOCK_BOOT=${STOCK_BOOT:-$HOME/a9-assets/HLTE556N/boot.img}
STOCK_SHA=03ae17607233034a8ee11eaa92deebef32b289d3921a7080fc7fc220044830bd
HW=hlte556n
case "$W" in *a9-assets*) echo "refusing to build on the NFS share" >&2; exit 2;; esac

# Android boot cmdline. Our items come BEFORE the ABL's appended ones (ABL
# order: DT /chosen/bootargs + header cmdline + its own items, MEASURED in the
# stock boot log), and Android init/fs_mgr take the FIRST occurrence of a key.
#  - safety params kept from the diskless image (beacon, *_ignore_unused,
#    cpuidle/cpufreq off, skip_qtee, panic=10); maxcpus=8 since boot 4
#    (sol-smp stage A: all 8 cores came up, boot_completed at t+257 s)
#  - androidboot.hardware/fstab_suffix=hlte556n: our rc + fstab only; phh qcom
#    branches stay off; ABL's fstab_suffix=default finds no file anyway
#  - boot_devices=soc@0/4804000.ufshc: mainline platform path (DT soc@0 has no
#    reg -> "soc@0"); the ABL's "soc/4804000.ufshc" is the 4.19 name
#  - slot_suffix=_b PINNED: only system_b/vendor_b can ever be mapped
#  - enforcing=0 selinux_force_permissive=1: kernel force-permissive patch
#    (GSI init is a USER build: androidboot.selinux is compiled out, MEASURED)
#  - brd: /data RAM disk, 3 GiB, lazily allocated
#  - loglevel 6, no ignore_loglevel: synchronous 115200 console vs Android's
#    printk volume (INFERRED risk of stalls)
# MEASURED 2026-10-15 (eink --hold run): the ABL passes ONLY the 512-byte
# header cmdline field; mkbootimg's spill-over into extra_cmdline never reached
# the kernel ("... androidboot.usbcontroller=4e00000.usb androidboo" then the
# ABL's own items), so everything after byte ~511 was lost, including
# androidboot.a9ml.eink=1. The cmdline must fit in 511 bytes: dropped
# console/earlycon (no UART access), rdinit=/init (default), brd.rd_nr
# (CONFIG_BLK_DEV_RAM_COUNT=1), fstab_suffix (falls back to hardware),
# usbcontroller (a9ml-usb.sh and the rc default), androidboot.selinux (ignored
# by the user-build init), androidboot.vendor.apex.* (vendor build.prop).
CMDLINE=${A9_ANDROID_CMDLINE:-"qcom_pon.beacon=2 clk_ignore_unused pd_ignore_unused regulator_ignore_unused cpuidle.off=1 maxcpus=8 cpufreq.off=1 qcom_scm.skip_qtee=1 panic=10 loglevel=6 log_buf_len=4M printk.devkmsg=on enforcing=0 selinux_force_permissive=1 brd.rd_size=3145728 androidboot.hardware=$HW androidboot.boot_devices=soc@0/4804000.ufshc androidboot.slot_suffix=_b androidboot.init_fatal_reboot_target=bootloader${A9_CMDLINE_EXTRA:+ $A9_CMDLINE_EXTRA}"}

BOOTIMG=0 CHECK=0 EINK=0 GPU=0
for a in "$@"; do case "$a" in --bootimg) BOOTIMG=1;; --check) CHECK=1; BOOTIMG=1;; --eink) EINK=1;; --gpu) GPU=1;; *) sed -n '2,20p' "$0"; exit 2;; esac; done
# --gpu (runs/gpu.md): the GPU kernel snapshot (kernel-snap-gpu: vkms.map_wc
# patch, GPU DTB). Same ramdisk and cmdline (firmware path and msm options are
# set by the GPU vendor at runtime); image name gets "-gpu". Pair ONLY with a
# build-vendor.sh --gpu vendor (its kernel-image.sha256 enforces that).
# --eink (phase 2): same ramdisk, cmdline + androidboot.a9ml.eink=1 (the vendor's
# init.a9ml-eink.rc then loads the E Ink modules and points drm_hwcomposer at
# the a9_eink card; gates stay CLOSED) and the waveform firmware path the test
# script fills at runtime (vendor data, never in an image). Output
# a9-android-eink-boot.img.
# A9_CPUFREQ=b1|off: sol-smp stage B1 (hardware cpufreq with schedutil instead
# of cpufreq.off=1), boot-tested in eink run 8
# Default since run 8 (2026-10-15: cpufreq 1.80/2.02 GHz schedutil, the eink
# image reached the goal): b1 for --eink, off for the vkms image.
[ -n "${A9_CPUFREQ:-}" ] || { [ $EINK = 1 ] && A9_CPUFREQ=b1 || A9_CPUFREQ=off; }
if [ "$A9_CPUFREQ" = b1 ]; then CMDLINE=${CMDLINE/ cpufreq.off=1/ cpufreq.default_governor=schedutil}; fi
if [ $EINK = 1 ]; then CMDLINE="$CMDLINE androidboot.a9ml.eink=1 firmware_class.path=/data/local/tmp/a9fw"
else CMDLINE="$CMDLINE androidboot.a9ml.eink=0"; fi   # the vkms image starts the composer in init.hlte556n.rc

echo "== stock boot.img"
[ "$(sha256sum "$STOCK_BOOT" | cut -c1-64)" = $STOCK_SHA ] || { echo "stock boot.img sha256 mismatch" >&2; exit 1; }
rm -rf "$WORK/unpacked" "$WORK/root"; mkdir -p "$WORK/root" "$OUT"
unpack_bootimg --boot_img "$STOCK_BOOT" --out "$WORK/unpacked" >/dev/null
gzip -dc "$WORK/unpacked/ramdisk" | (cd "$WORK/root" && cpio -idm --quiet)

R=$WORK/root
[ -f "$R/init" ] || { echo "no /init in the stock ramdisk" >&2; exit 1; }
[ ! -e "$R/lib/modules" ] || { echo "unexpected /lib/modules in the stock ramdisk" >&2; exit 1; }
[ ! -e "$R/prop.default" ] || echo "  note: stock prop.default present (unexpected), kept"
rm -f "$R/fstab.default" "$R/fstab.emmc"
install -m 0640 "$HERE/ramdisk/fstab.$HW" "$R/fstab.$HW"
# no fstab other than ours may remain, and ours names only system + vendor
left=$(cd "$R" && find . -name 'fstab*' | sort | tr '\n' ' ')
[ "$left" = "./fstab.$HW " ] || { echo "unexpected fstab files: $left" >&2; exit 1; }
if grep -v '^\s*#' "$R/fstab.$HW" | grep -Eqi 'userdata|metadata|misc|persist|modem|fsg|by-name|formattable|/data'; then
	echo "fstab.$HW names a forbidden partition/flag" >&2; exit 1
fi

(cd "$R" && find . | LC_ALL=C sort | cpio -o -H newc -R 0:0 --quiet | gzip -9n) > "$OUT/ramdisk-android.cpio.gz"
echo "  ramdisk: $OUT/ramdisk-android.cpio.gz ($(stat -c %s "$OUT/ramdisk-android.cpio.gz") bytes)"
(cd "$R" && find . -printf '%M %s %p\n' | LC_ALL=C sort -k3) > "$OUT/ramdisk-android.list"

[ $BOOTIMG = 1 ] || exit 0
echo "== boot image"
# kernel: A9_ANDROID=1 scripts/mainline/kernel/build.sh (a9-android.config: binder, SELinux + patch 0014 force-permissive,
# DM/UFS/brd built in, vkms; Android DTS with UFS); null-overlay DTBO from the same out dir
K=${A9_ANDROID_KERNEL:-$A/kernel-snap}   # snapshot-kernel.sh (kernel + matching modules)
KDTB=${A9_ANDROID_DTB:-$K/sm6115-hisense-hlte556n-android.dtb}
if [ $GPU = 1 ]; then
	K=${A9_ANDROID_KERNEL:-$A/kernel-snap-gpu}
	KDTB=${A9_ANDROID_DTB:-$K/sm6115-hisense-hlte556n-android-gpu.dtb}
fi
M=$K
for f in "$K/Image.gz" "$KDTB" "$M/recovery_dtbo.img"; do [ -f "$f" ] || { echo "missing $f (run A9_ANDROID=1 scripts/mainline/kernel/build.sh)" >&2; exit 1; }; done
[ ${#CMDLINE} -le 511 ] || { echo "cmdline ${#CMDLINE} > 511: the ABL passes only the 512-byte header field" >&2; exit 1; }
IMG=$OUT/a9-android-boot.img; [ $EINK = 1 ] && IMG=$OUT/a9-android-eink-boot.img
# a non-default cpufreq choice gets its own file name
[ $EINK = 1 ] && [ "$A9_CPUFREQ" = off ] && IMG=${IMG%.img}-nocpufreq.img
[ $EINK = 0 ] && [ "$A9_CPUFREQ" = b1 ] && IMG=${IMG%.img}-b1.img
[ $GPU = 1 ] && IMG=${IMG%.img}-gpu.img
mkbootimg --header_version 2 --pagesize 4096 --base 0 --kernel_offset 0x8000 \
	--ramdisk_offset 0x1000000 --tags_offset 0x100 --second_offset 0 \
	--dtb_offset 0x1f00000 --os_version 11.0.0 --os_patch_level 2022-04 \
	--kernel "$K/Image.gz" --ramdisk "$OUT/ramdisk-android.cpio.gz" \
	--dtb "$KDTB" --recovery_dtbo "$M/recovery_dtbo.img" \
	--cmdline "$CMDLINE" -o "$IMG"
(cd "$OUT" && sha256sum "$(basename "$IMG")" > "$(basename "$IMG").sha256")
echo "  image: $IMG ($(stat -c %s "$IMG") bytes, cmdline ${#CMDLINE} chars)"
[ "$(stat -c %s "$IMG")" -le $((24576 * 4096)) ] || { echo "image larger than boot_b (96 MiB)" >&2; exit 1; }
[ $CHECK = 1 ] && "$REPO/scripts/abl-check.sh" "$IMG" boot --dtbo "$M/recovery_dtbo.img"
exit 0
