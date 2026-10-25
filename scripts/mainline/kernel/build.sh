#!/usr/bin/env bash
# Optional A9_BLUETOOTH=1: default-off BT variant; see docs/mainline-bluetooth.md.
# build.sh -- build the Hisense A9 diskless mainline recovery image, offline.
#
#   scripts/mainline/kernel/build.sh [all|kernel|image|check]
#
# Everything happens under $A9_MAINLINE (default ~/A9-build/mainline, LOCAL
# disk; never ~/a9-assets): a shallow linux clone, an out-of-tree build dir, the
# initramfs staging dir and out/. Toolchain: nix-shell (shell.nix next to this
# script: aarch64 cross gcc, dtc, mkbootimg comes from the user profile). No
# phone access. Steps:
#   kernel: clone v$KVER (shallow), apply patches/*.patch, add the A9 DTS,
#           defconfig + a9.config, build Image.gz, dtbs, modules
#   image:  initramfs (static busybox, a9-reboot, /init, E Ink modules),
#           null-overlay DTBO, mkbootimg header v2 -> out/a9-mainline-recovery.img
#   check:  scripts/abl-check.sh (the phone's ABL, emulated), dt-abl-check.sh,
#           header facts, size, config gates
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
KVER=${KVER:-7.2.10}
W=${A9_MAINLINE:-$HOME/A9-build/mainline}
SRC=$W/linux-$KVER
WIFI=${A9_WIFI:-0}
PD_MAPPER=${A9_WIFI_PD_MAPPER:-kernel}
case "$WIFI" in 0|1) ;; *) echo "A9_WIFI must be 0 or 1" >&2; exit 2 ;; esac
[ "$WIFI" = 0 ] || umask 077 # generated Wi-Fi DTBs carry private device identity
case "$PD_MAPPER" in kernel|userspace) ;; *) echo "A9_WIFI_PD_MAPPER must be kernel or userspace" >&2; exit 2 ;; esac
if [ "$WIFI" = 1 ] && [ "${A9_ANDROID:-0}" != 1 ]; then
	echo "A9_WIFI=1 requires A9_ANDROID=1 (Android-only draft)" >&2; exit 2
fi
# A9_ANDROID=1: add the opt-in a9-android.config fragment (Android GSI needs:
# binder, SELinux, dm, f2fs/erofs, cgroups/BPF, netfilter, ...) in its own build
# and output dirs. Not boot-tested; the default diskless image is unaffected.
FRAGS="$HERE/a9.config"
if [ "${A9_ANDROID:-0}" = 1 ]; then
	FRAGS="$FRAGS $HERE/a9-android.config"
	OBJ=$W/build-$KVER-android
	OUT=$W/out-android
else
	OBJ=$W/build-$KVER
	OUT=$W/out
fi
# A9_GPU=1 (needs A9_ANDROID=1; opt-in, never the default): the Adreno 610 GPU
# variant. Its OWN source tree (a --shared clone of linux-$KVER, so the GPU
# patches in patches-gpu/ never reach the shared tree), build and output dirs,
# and the GPU board DT (scripts/mainline/android/sm6115-hisense-hlte556n-
# android-gpu.dts). runs/gpu.md.
GPU=${A9_GPU:-0}
if [ "$GPU" = 1 ]; then
	[ "${A9_ANDROID:-0}" = 1 ] || { echo "A9_GPU=1 needs A9_ANDROID=1" >&2; exit 2; }
	SRC=$W/linux-$KVER-gpu OBJ=$W/build-$KVER-android-gpu OUT=$W/out-android-gpu
	FRAGS="$FRAGS $HERE/a9-gpu.config"   # lockup/hung-task panics (review 27)
fi
# A9_THERMAL=1 (sensors worktree, runs/sensors.md section 3): add the thermal
# fragment (critical-trip poweroff fix, generic-adc-thermal) and build the DT
# <board>-thermal.dts = the normal board DT + scripts/mainline/thermal/
# a9-thermal.dtsi (CPU cooling maps, PM6125 thermistor channels, quiet_therm
# skin zone), generated in the kernel tree; the repository DTS files and the
# normal DT in the tree stay untouched. Own build/out dirs, so the default
# images are never overwritten. mkramdisk.sh: A9_ANDROID_DTB=<out>/<board>-thermal.dtb.
if [ "${A9_THERMAL:-0}" = 1 ]; then
	FRAGS="$FRAGS $REPO/scripts/mainline/thermal/a9-thermal.config"
	OBJ=$OBJ-thermal
	OUT=$OUT-thermal
fi
# integ-1: Bluetooth after GPU and thermal, so the variant suffixes compose
# in one order: A9_GPU=1 A9_THERMAL=1 A9_BLUETOOTH=1 builds in
# build-$KVER-android-gpu-thermal-bt / out-android-gpu-thermal-bt (GPU source tree)
# Bluetooth is a separate Android-only build; default config and output stay off.
BT=${A9_BLUETOOTH:-0}
case "$BT" in 0|1) ;; *) echo 'A9_BLUETOOTH must be 0 or 1' >&2; exit 2 ;; esac
BT_FW=${A9_BT_FIRMWARE:-$W/sol-bt/runtime}
if [ "$BT" = 1 ]; then
    [ "${A9_ANDROID:-0}" = 1 ] || { echo 'Bluetooth requires A9_ANDROID=1' >&2; exit 2; }
    FRAGS="$FRAGS $REPO/scripts/mainline/bluetooth/a9-bluetooth.config"
    OBJ=$OBJ-bt
    OUT=$OUT-bt
fi
RD=$W/initramfs
[ "$BT" = 1 ] && RD=$RD-bt
# E Ink asset images (vendor waveform inside) never share out/ with the
# default image: out-eink/ and their own initramfs staging dir
if [ "${A9_EINK_ASSETS:-0}" = 1 ]; then OUT=$OUT-eink; RD=$RD-eink; fi
IMG=$OUT/a9-mainline-recovery.img
if [ "$WIFI" = 1 ]; then
	FRAGS="$FRAGS $HERE/a9-wifi.config"
	OBJ=$OBJ-wifi; OUT=$OUT-wifi; RD=$RD-wifi
	IMG=$OUT/a9-mainline-recovery-wifi.img
fi
BASE_DTS=sm6115-hisense-hlte556n
DTS=$BASE_DTS
# A9_ANDROID=1 also builds the Android board DT: the diskless DT #included
# unchanged plus UFS enabled with the stock rails (read-only use; scripts/
# mainline/android/sm6115-hisense-hlte556n-android.dts), so first-stage init can
# map super. The default build is unaffected.
[ "${A9_ANDROID:-0}" = 1 ] && DTS=$BASE_DTS-android
[ "$WIFI" = 1 ] && DTS=$BASE_DTS-android-wifi
[ "$GPU" = 1 ] && DTS=$BASE_DTS-android-gpu
[ "${A9_THERMAL:-0}" = 1 ] && DTS=$DTS-thermal
CROSS=aarch64-unknown-linux-gnu-
J=${J:-$(nproc)}
case "$W" in *a9-assets*) echo "refusing to build on the NFS share ($W)" >&2; exit 2;; esac

# Kernel command line in the boot header. The ABL appends its own items
# (androidboot.*, eink_vcom, ...). First-boot hardening (2026-10-15, after the
# reset loop; docs/mainline-kernel-build.md section 3):
#   qcom_pon.beacon=2      PON reason "fastboot" armed at PON probe: every later
#                          reset (panic, watchdog, plain reboot) lands in
#                          bootloader fastboot, never in this image again. Safe
#                          whether the image sits in recovery_X or boot_X.
#   clk_ignore_unused pd_ignore_unused regulator_ignore_unused
#                          leave what the bootloader left on alone
#   cpuidle.off=1 maxcpus=1 cpufreq.off=1
#                          no PSCI idle states / OSI domains, no secondary CPU
#                          bring-up before the beacon is armed, no DCVS writes
#   qcom_scm.skip_qtee=1   no QTEE smcinvoke probe call into TZ (patch 0005)
#   panic=10               reboot (into fastboot, see above) 10 s after a panic
# A9_EINK_ASSETS=1 (E3-E5 images only): add the waveform blob, the DSI control
# template (eink/tools/a9ct-extract.py) and a9eink-pattern
# from local, NON-git paths to the initramfs, and make any oops a panic that
# reboots at once (the FP9929A panic notifier drops HV first). The resulting
# image contains vendor tables: keep it local, never commit or publish it.
EINK_ASSETS=${A9_EINK_ASSETS:-0}
EINK_FW=${A9_EINK_FW:-$W/firmware/hisense/a9-waveform.bin}
EINK_CT=${A9_EINK_CT:-$W/firmware/hisense/a9-ctrl-template.bin}
EINK_CT_SHA256=a88ae8efc5325e1ab52e4e48b73d2abb1a3c81a7989baa2a226513b07cebfbe7
EINK_PATTERN=${A9_EINK_PATTERN:-$W/eink-assets/a9eink-pattern}
[ "$EINK_ASSETS" = 1 ] && A9_CMDLINE_EXTRA="${A9_CMDLINE_EXTRA:+$A9_CMDLINE_EXTRA }oops=panic panic=1"
CMDLINE=${A9_CMDLINE:-"console=ttyMSM0,115200n8 earlycon=qcom_geni,0x4a90000 qcom_pon.beacon=2 clk_ignore_unused pd_ignore_unused regulator_ignore_unused cpuidle.off=1 maxcpus=1 cpufreq.off=1 qcom_scm.skip_qtee=1 panic=10 loglevel=8 ignore_loglevel printk.devkmsg=on rdinit=/init${A9_CMDLINE_EXTRA:+ $A9_CMDLINE_EXTRA}"}

nixrun() { nix-shell "$HERE/shell.nix" --run "$*"; }
mk() { nixrun "make -C '$SRC' O='$OBJ' ARCH=arm64 CROSS_COMPILE=$CROSS -j$J $*"; }

kernel() {
    if [ "$BT" = 1 ]; then
        python3 -I "$REPO/scripts/mainline/bluetooth/firmware.py" check --directory "$BT_FW"
        BT_FW=$(realpath "$BT_FW")
        # Quoted CPP include paths must not allow directive injection.
        python3 -I - "$BT_FW" <<'PYBT'
import sys
assert not any(c in sys.argv[1] for c in ('"', '\\', '\n', '\r')), 'unsafe private include path'
PYBT
    fi
	mkdir -p "$W"
	if [ "$GPU" = 1 ] && [ ! -d "$SRC/.git" ]; then
		[ -d "$W/linux-$KVER/.git" ] || { echo "A9_GPU=1: base tree $W/linux-$KVER missing (run a default build first)" >&2; exit 1; }
		git clone -q --shared "$W/linux-$KVER" "$SRC"
	fi
	if [ ! -d "$SRC/.git" ]; then
		if [ "${A9_OFFLINE:-0}" = 1 ] || [ "$WIFI" = 1 ]; then
			echo "offline source missing: $SRC" >&2; exit 2
		fi
		git clone -q --depth 1 --branch "v$KVER" \
			https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git "$SRC"
	fi
	# a patch counts as applied when a commit since v$KVER has the same patch-id
	# (robust when a later patch touches the same file)
	local have; have=$(git -C "$SRC" log --format=%H "v$KVER..HEAD" 2>/dev/null |
		while read -r c; do git -C "$SRC" show "$c" | git -C "$SRC" patch-id --stable; done | awk '{print $1}')
	local pl="$HERE/patches/*.patch"; [ "$GPU" = 1 ] && pl="$pl $HERE/patches-gpu/*.patch"
	for p in $pl; do
		id=$(git -C "$SRC" patch-id --stable < "$p" | awk '{print $1}')
		if printf '%s\n' "$have" | grep -qx "$id"; then
			echo "  applied already: $(basename "$p")"
		else
			git -C "$SRC" am -q --committer-date-is-author-date "$p" && echo "  applied: $(basename "$p")"
		fi
	done
    if [ "$BT" = 1 ]; then
        # Feature-local patches avoid collisions with the main numbered series.
        # They are board-scoped; default-off builds do not apply this series.
        for p in "$REPO"/scripts/mainline/bluetooth/patches/*.patch; do
            id=$(git -C "$SRC" patch-id --stable < "$p" | awk '{print $1}')
            if printf '%s\n' "$have" | grep -qx "$id"; then
                echo "  Bluetooth patch applied already: $(basename "$p")"
            else
                git -C "$SRC" am -q --committer-date-is-author-date "$p"
                echo "  Bluetooth patch applied: $(basename "$p")"
            fi
        done
    fi
	# A9_THERMAL=1 needs reviews 24/28's two gates in the tree: patches 0043
	# (critical-trip confirmation) and 0044 (TSENS VALID-only). They are applied
	# to every build above, but both are default-off Kconfig options
	# (THERMAL_CRITICAL_CONFIRM_SAMPLES=1 = upstream, QCOM_TSENS_REQUIRE_VALID=n);
	# only the A9_THERMAL=1 fragment turns them on. Belt and braces: refuse a
	# thermal build on a tree that does not carry them.
	if [ "${A9_THERMAL:-0}" = 1 ] && ! { grep -q '^config THERMAL_CRITICAL_CONFIRM_SAMPLES$' "$SRC/drivers/thermal/Kconfig" &&
	     grep -q '^config QCOM_TSENS_REQUIRE_VALID$' "$SRC/drivers/thermal/qcom/Kconfig"; }; then
		echo "A9_THERMAL=1: kernel tree lacks patches 0043/0044 (critical confirmation, TSENS VALID-only); refusing" >&2
		exit 1
	fi
	cp "$REPO/docs/mainline/$BASE_DTS.dts" "$SRC/arch/arm64/boot/dts/qcom/"
	# Wi-Fi: its DTS is generated below from the Android board DT (no checked-in <board>-android-wifi.dts)
	[ "$WIFI" = 1 ] && cp "$REPO/scripts/mainline/android/$BASE_DTS-android.dts" "$SRC/arch/arm64/boot/dts/qcom/"
	[ "$WIFI" = 1 ] || [ "${DTS%-thermal}" = "$BASE_DTS" ] || cp "$REPO/scripts/mainline/android/${DTS%-thermal}.dts" "$SRC/arch/arm64/boot/dts/qcom/"
	[ "$GPU" = 1 ] && cp "$REPO/scripts/mainline/android/$BASE_DTS-android.dts" "$SRC/arch/arm64/boot/dts/qcom/"
    if [ "$BT" = 1 ]; then
        cp "$REPO/scripts/mainline/bluetooth/a9-bluetooth.dtsi" "$SRC/arch/arm64/boot/dts/qcom/"
        printf '\n#include "a9-bluetooth.dtsi"\n#include "%s/address.dtsi"\n' "$BT_FW" >> "$SRC/arch/arm64/boot/dts/qcom/${DTS%-thermal}.dts"   # integ-1: the board DTS (the A9_THERMAL wrapper #includes it)
    fi
	cp "$REPO/scripts/mainline/pmic/a9-pmi632.dtsi" "$SRC/arch/arm64/boot/dts/qcom/"
	if [ "${A9_THERMAL:-0}" = 1 ]; then
		cp "$REPO/scripts/mainline/thermal/a9-thermal.dtsi" "$SRC/arch/arm64/boot/dts/qcom/"
		printf '// generated by build.sh (A9_THERMAL=1)\n#include "%s.dts"\n#include "a9-thermal.dtsi"\n' \
			"${DTS%-thermal}" > "$SRC/arch/arm64/boot/dts/qcom/$DTS.dts"
	fi
	grep -q "$DTS.dtb" "$SRC/arch/arm64/boot/dts/qcom/Makefile" ||
		echo "dtb-\$(CONFIG_ARCH_QCOM)	+= $DTS.dtb" >> "$SRC/arch/arm64/boot/dts/qcom/Makefile"
	mkdir -p "$OBJ"
	if [ "$WIFI" = 1 ]; then
		local dtroot="$SRC/arch/arm64/boot/dts/qcom"
		cp "$REPO/scripts/mainline/android/a9-wifi.dtsi" "$dtroot/"
		# The private MAC exists only in the local object directory and DTB.
		# Nothing private is copied into this project's source checkout.
		python3 -I "$REPO/scripts/mainline/wifi/mac-dtsi.py" \
			--assets "${A9_WIFI_ASSETS:-$HOME/A9-build/mainline/sol-wifi}" \
			--output "$OBJ/a9-wifi-private.dtsi"
		python3 -I - "$dtroot/$DTS.dts" "$OBJ/a9-wifi-private.dtsi" <<'PY'
import json, pathlib, sys
wrapper, private = map(pathlib.Path, sys.argv[1:])
wrapper.write_text('#include "sm6115-hisense-hlte556n-android.dts"\n'
                   '#include "a9-wifi.dtsi"\n'
                   '/include/ ' + json.dumps(str(private.resolve())) + '\n')
PY
		if [ "$PD_MAPPER" = userspace ]; then
			printf '%s\n' '# CONFIG_QCOM_PD_MAPPER is not set' > "$OBJ/a9-wifi-pd.config"
			FRAGS="$FRAGS $OBJ/a9-wifi-pd.config"
		fi
	fi
	mk defconfig
	nixrun "cd '$SRC' && ARCH=arm64 scripts/kconfig/merge_config.sh -m -O '$OBJ' '$OBJ/.config' $FRAGS >/dev/null"
	mk olddefconfig
	# every option the fragments ask for must have survived olddefconfig; when
	# fragments disagree the later one wins (as in merge_config.sh)
	local bad=0
	while IFS= read -r l; do
		case "$l" in
		CONFIG_*=*) grep -qx "$l" "$OBJ/.config" || { echo "  config: wanted $l, got: $(grep "^${l%%=*}=\|# ${l%%=*} " "$OBJ/.config" || echo unset)"; bad=1; } ;;
		"# CONFIG_"*" is not set") s=${l#\# }; s=${s%% *}; grep -q "^$s=" "$OBJ/.config" && { echo "  config: wanted $s off, got $(grep "^$s=" "$OBJ/.config")"; bad=1; } ;;
		esac
	done < <(python3 -I - $FRAGS <<'PY'
import re, sys
want = {}
for f in sys.argv[1:]:
    for l in open(f):
        l = l.rstrip('\n')
        m = re.match(r'(CONFIG_\w+)=', l) or re.match(r'# (CONFIG_\w+) is not set', l)
        if m:
            want[m.group(1)] = l
print('\n'.join(want.values()))
PY
)
	[ $bad = 0 ] || { echo "config fragment not honoured" >&2; exit 1; }
	mk Image.gz modules
	mk DTC_FLAGS=-@ "qcom/$DTS.dtb"
    if [ "$BT" = 1 ]; then
        python3 -I "$REPO/scripts/mainline/bluetooth/kernel-assets.py" \
            --config "$OBJ/.config" --dtb "$OBJ/arch/arm64/boot/dts/qcom/$DTS.dtb" --firmware "$BT_FW" \
            --driver "$OBJ/drivers/bluetooth/hci_uart.ko"
    fi
}

image() {
    if [ "$BT" = 1 ]; then
        python3 -I "$REPO/scripts/mainline/bluetooth/kernel-assets.py" \
            --config "$OBJ/.config" --dtb "$OBJ/arch/arm64/boot/dts/qcom/$DTS.dtb" --firmware "$BT_FW" \
            --driver "$OBJ/drivers/bluetooth/hci_uart.ko"
    fi
	local KREL; KREL=$(cat "$OBJ/include/config/kernel.release")
	rm -rf "$RD" "$W/modstage"; mkdir -p "$RD"/{bin,sbin,dev,proc,sys,tmp,run,mnt,etc,lib/modules} "$OUT"
	# static busybox (nixpkgs pkgsCross aarch64 musl)
	local BB; BB=$(nix-build '<nixpkgs>' -A pkgsCross.aarch64-multiplatform.pkgsStatic.busybox --no-out-link)
	cp "$BB/bin/busybox" "$RD/bin/busybox"; chmod 755 "$RD/bin/busybox"
	nixrun "${CROSS}gcc -Os -static -nostdlib -ffreestanding -fno-stack-protector -fno-pie -no-pie -Wl,--build-id=none -o '$RD/bin/a9-reboot' '$HERE/initramfs/a9-reboot.c'"
	install -m 755 "$HERE/initramfs/init" "$RD/init"
	install -m 755 "$HERE/initramfs/a9-eink" "$RD/bin/a9-eink"
	install -m 755 "$HERE/initramfs/a9-ctl" "$RD/bin/a9-ctl"
	install -m 755 "$HERE/initramfs/a9-eink-seq" "$RD/bin/a9-eink-seq"
	if [ "$EINK_ASSETS" = 1 ]; then
		[ -s "$EINK_FW" ] && [ -s "$EINK_CT" ] && [ -x "$EINK_PATTERN" ] || { echo "A9_EINK_ASSETS=1 but $EINK_FW, $EINK_CT or $EINK_PATTERN missing" >&2; exit 1; }
		mkdir -p "$RD/lib/firmware/hisense"
		install -m 644 "$EINK_FW" "$RD/lib/firmware/hisense/a9-waveform.bin"
		install -m 644 "$EINK_CT" "$RD/lib/firmware/hisense/a9-ctrl-template.bin"
		install -m 755 "$EINK_PATTERN" "$RD/bin/a9eink-pattern"
		echo "  E Ink assets: waveform $(sha256sum "$EINK_FW" | cut -c1-16)…, control template $(sha256sum "$EINK_CT" | cut -c1-16)…, a9eink-pattern (image is LOCAL ONLY: vendor data)"
	fi
	# E Ink modules + their dependencies only (modules.dep closure)
	mk INSTALL_MOD_PATH="$W/modstage" INSTALL_MOD_STRIP=1 modules_install >/dev/null
	local MD="$W/modstage/lib/modules/$KREL"
	python3 -I - "$MD" "$RD/lib/modules/$KREL" dispcc-sm6115 msm tc358767 panel-hisense-hlte556n-eink a9_eink <<'PY'
import os, shutil, sys
md, dst, want = sys.argv[1], sys.argv[2], sys.argv[3:]
dep = {}
for l in open(os.path.join(md, 'modules.dep')):
    k, v = l.split(':', 1); dep[k] = v.split()
byname = {os.path.basename(k)[:-3].replace('-', '_'): k for k in dep}
need, todo = set(), [byname[w.replace('-', '_')] for w in want]
while todo:
    k = todo.pop()
    if k in need: continue
    need.add(k); todo += dep[k]
os.makedirs(dst, exist_ok=True)
for k in need:
    os.makedirs(os.path.join(dst, os.path.dirname(k)), exist_ok=True)
    shutil.copy2(os.path.join(md, k), os.path.join(dst, k))
with open(os.path.join(dst, 'modules.dep'), 'w') as f:
    for k in sorted(need): f.write(f"{k}: {' '.join(dep[k])}\n")
for f in ('modules.order', 'modules.builtin', 'modules.builtin.modinfo'):
    if os.path.exists(os.path.join(md, f)): shutil.copy2(os.path.join(md, f), dst)
print(f"  initramfs modules: {len(need)} ({', '.join(sorted(os.path.basename(k) for k in need))})")
PY
	rm -rf "$W/modstage"
	(cd "$RD" && find . | LC_ALL=C sort | cpio -o -H newc -R 0:0 --quiet | gzip -9n) > "$OUT/initramfs.cpio.gz"
	# DTB (with __symbols__) and the null board overlay as recovery_dtbo
	cp "$OBJ/arch/arm64/boot/dts/qcom/$DTS.dtb" "$OUT/$DTS.dtb"
	"$OBJ/scripts/dtc/dtc" -@ -q -I dts -O dtb -o "$OUT/null-overlay.dtbo" "$REPO/docs/mainline/hlte556n-null-overlay.dts"
	python3 -I "$REPO/scripts/mainline/mkdtbo.py" "$OUT/recovery_dtbo.img" "$OUT/null-overlay.dtbo"
	cp "$OBJ/arch/arm64/boot/Image.gz" "$OUT/Image.gz"
	# boot header v2, page 4096, base 0, offsets as stock/TWRP; DTB in the
	# v2 dtb section (stock has no appended DTB); os version as TWRP
	mkbootimg --header_version 2 --pagesize 4096 --base 0 --kernel_offset 0x8000 \
		--ramdisk_offset 0x1000000 --tags_offset 0x100 --second_offset 0 \
		--dtb_offset 0x1f00000 --os_version 11.0.0 --os_patch_level 2022-04 \
		--kernel "$OUT/Image.gz" --ramdisk "$OUT/initramfs.cpio.gz" \
		--dtb "$OUT/$DTS.dtb" --recovery_dtbo "$OUT/recovery_dtbo.img" \
		--cmdline "$CMDLINE" -o "$IMG"
	(cd "$OUT" && sha256sum "$(basename "$IMG")" > "$(basename "$IMG").sha256")
	echo "  image: $IMG ($(stat -c %s "$IMG") bytes) sha256 $(cut -c1-16 "$IMG.sha256")…"
}

check() {
	local rc=0
	if grep -qx 'CONFIG_CHARGER_PMI632_A9=y' "$OBJ/.config" &&
	   grep -qx 'CONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY=y' "$OBJ/.config" &&
	   grep -qx 'CONFIG_CHARGER_PMI632_A9_OBSERVE=y' "$OBJ/.config" &&
	   grep -qx 'CONFIG_CHARGER_PMI632_A9_FV_STOCK=y' "$OBJ/.config"; then
		echo "  ok   PMI632 built in, observe with one stock-FV probe write"
	else
		echo "  FAIL PMI632 observe configuration"; rc=1
	fi
	local pmic_status
	pmic_status=$(nixrun "fdtget -t s '$OUT/$DTS.dtb' '/soc@0/spmi@1c40000/pmic@2/charger@1000' status" 2>/dev/null) || rc=1
	[ "$pmic_status" = okay ] && echo "  ok   PMI632 telemetry DT node okay, no charging-validated" || { echo "  FAIL PMI632 telemetry DT node disabled/missing"; rc=1; }
	nixrun "fdtget '$OUT/$DTS.dtb' '/soc@0/spmi@1c40000/pmic@2/charger@1000' hisense,charging-validated" >/dev/null 2>&1 &&
		{ echo "  FAIL hisense,charging-validated present"; rc=1; }
	python3 -I - "$IMG" "$OUT/Image.gz" <<'PY' || rc=1
import gzip, struct, sys, os
img, kgz = sys.argv[1], sys.argv[2]
h = open(img, 'rb').read(4096)
ok = lambda c, m: print(("  ok   " if c else "  FAIL ") + m) or c
r = [ok(h[:8] == b'ANDROID!', 'magic ANDROID!'),
     ok(struct.unpack_from('<I', h, 40)[0] == 2, 'header version 2'),
     ok(struct.unpack_from('<I', h, 36)[0] == 4096, 'page size 4096'),
     ok(struct.unpack_from('<I', h, 12)[0] == 0x8000, 'kernel addr 0x8000'),
     ok(struct.unpack_from('<I', h, 20)[0] == 0x1000000, 'ramdisk addr 0x1000000'),
     ok(struct.unpack_from('<I', h, 32)[0] == 0x100, 'tags addr 0x100'),
     ok(struct.unpack_from('<I', h, 1632)[0] > 0, f'recovery_dtbo {struct.unpack_from("<I", h, 1632)[0]} bytes'),
     ok(struct.unpack_from('<I', h, 1648)[0] > 0, f'dtb {struct.unpack_from("<I", h, 1648)[0]} bytes'),
     ok(os.path.getsize(img) <= 100663296, f'size {os.path.getsize(img)} <= 96 MiB (recovery_a/_b)')]
k = gzip.decompress(open(kgz, 'rb').read())
isz = struct.unpack_from('<Q', k, 16)[0]
r.append(ok(k[56:60] == b'ARMd', 'arm64 Image magic'))
# stock kernel (TWRP/recovery_b): 34,476,544 raw, image_size 36,024,320
r.append(ok(isz <= 36024320, f'runtime image_size {isz} <= stock 36,024,320 (ABL load window, INFERRED)'))
sys.exit(0 if all(r) else 1)
PY
	grep -q "^CONFIG_REGULATOR_FP9929A=y" "$OBJ/.config" && grep -q "^CONFIG_DRM_MSM=m" "$OBJ/.config" &&
		grep -q "^# CONFIG_DRM_FBDEV_EMULATION is not set" "$OBJ/.config" && echo "  ok   E Ink gates: FP9929A built in, msm modular, no fbdev" || { echo "  FAIL config gates"; rc=1; }
	# E1 needs the display clock controller in the initramfs (G1)
	zcat "$OUT/initramfs.cpio.gz" | cpio -t --quiet 2>/dev/null | grep -q 'dispcc-sm6115.ko$' \
		&& echo "  ok   initramfs has dispcc-sm6115.ko" || { echo "  FAIL dispcc-sm6115.ko missing from the initramfs"; rc=1; }
	zcat "$OUT/initramfs.cpio.gz" | cpio -t --quiet 2>/dev/null | grep -q 'bin/a9-eink-seq$' \
		&& echo "  ok   initramfs has the E3-E5 sequencer" || { echo "  FAIL bin/a9-eink-seq missing"; rc=1; }
	if [ "$EINK_ASSETS" = 1 ]; then
		zcat "$OUT/initramfs.cpio.gz" | cpio -t --quiet 2>/dev/null | grep -c -e 'lib/firmware/hisense/a9-waveform.bin$' -e 'lib/firmware/hisense/a9-ctrl-template.bin$' -e 'bin/a9eink-pattern$' -e 'a9_eink.ko$' | grep -qx 4 \
			&& echo "  ok   E Ink assets: waveform, control template, a9eink-pattern, a9_eink.ko (LOCAL image)" || { echo "  FAIL E Ink assets incomplete"; rc=1; }
		# control template: header (A9CT v1, 80, 1485568, payload sha256) + payload sha256 == a88ae8ef...
		zcat "$OUT/initramfs.cpio.gz" | cpio -i --quiet --to-stdout '*lib/firmware/hisense/a9-ctrl-template.bin' 2>/dev/null |
			python3 -I -c "import sys,struct,hashlib;d=sys.stdin.buffer.read();w=bytes.fromhex(sys.argv[1]);p=d[80:];sys.exit(0 if len(d)==80+1485568 and struct.unpack('<4I',d[:16])==(0x54433941,1,80,1485568) and d[48:80]==w and hashlib.sha256(p).digest()==w else 1)" "$EINK_CT_SHA256" \
			&& echo "  ok   E Ink control template: header + payload sha256 ${EINK_CT_SHA256:0:16}…" || { echo "  FAIL E Ink control template missing or wrong (header/payload sha256)"; rc=1; }
		python3 -I -c "import sys;h=open(sys.argv[1],'rb').read(4096);c=(h[64:576].split(b'\\0')[0]+h[608:1632].split(b'\\0')[0]).decode().split();p=[w for w in c if w.startswith('panic=')];sys.exit(0 if 'oops=panic' in c and p and p[-1]=='panic=1' else 1)" "$IMG" \
			&& echo "  ok   E Ink image cmdline: oops=panic, last panic=1" || { echo "  FAIL E Ink image needs oops=panic panic=1"; rc=1; }
	fi
	# the power gates must never be on the command line
	if python3 -I -c "import sys;h=open(sys.argv[1],'rb').read(4096);c=(h[64:576].split(b'\\0')[0]+h[608:1632].split(b'\\0')[0]).decode();sys.exit(1 if any(g in c for g in ('allow_power_on','hlte556n_eink.enable','eink_power','a9.eink=1','a9_eink.enable')) else 0)" "$IMG"; then
		echo "  ok   no E Ink power gate on the command line"
	else echo "  FAIL an E Ink power gate is on the command line"; rc=1; fi
	"$REPO/scripts/abl-check.sh" "$IMG" recovery || rc=1
	# the same image as boot_b with the null overlay as dtbo_b (first-boot route, docs section 4)
	"$REPO/scripts/abl-check.sh" "$IMG" boot --dtbo "$OUT/recovery_dtbo.img" || rc=1
	python3 -I - "$IMG" <<'PY' || rc=1
import sys
h = open(sys.argv[1], 'rb').read(4096)
cmd = (h[64:64+512].split(b'\0')[0] + h[608:608+1024].split(b'\0')[0]).decode()
need = ['qcom_pon.beacon=2', 'rdinit=/init', 'cpuidle.off=1', 'maxcpus=1', 'regulator_ignore_unused', 'qcom_scm.skip_qtee=1']
miss = [n for n in need if n not in cmd.split()]
print(("  ok   " if not miss else "  FAIL ") + f"cmdline has {', '.join(need)}" + (f" (missing {miss})" if miss else ''))
bad = [w for w in cmd.split() if w.startswith(('qcom_pon.beacon=',)) and w != 'qcom_pon.beacon=2']
sys.exit(1 if miss or bad else 0)
PY
	"$REPO/scripts/mainline/dt-abl-check.sh" "$W/dtcheck" >"$W/dtcheck.log" 2>&1 && echo "  ok   dt-abl-check (current DTS compiled from source vs torvalds master)" || { echo "  FAIL dt-abl-check (log: $W/dtcheck.log)"; rc=1; }
	[ $rc = 0 ] && echo "CHECK: PASS" || echo "CHECK: FAIL"
	return $rc
}

case "${1:-all}" in
	kernel) kernel ;;
	image) image ;;
	check) check ;;
	all) kernel; image; check ;;
	*) sed -n '2,17p' "$0"; exit 2 ;;
esac
