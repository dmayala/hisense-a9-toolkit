#!/usr/bin/env bash
# build-d1-dtb.sh -- offline: the ADSP D1 DTB = the integ-1 board DT
# (sm6115-hisense-hlte556n-android-gpu-thermal.dts, from the integ-1 kernel tree)
# + a9-adsp-d1.dtsi, and every check that needs no phone (runs/adsp-plan.md):
#   1. the integ-1 DTB is rebuilt the same way and must decompile identically to
#      the snapshot's DTB (proves the tree still holds the integ-1 DT);
#   2. the ADSP DTB differs from it ONLY in remoteproc@a400000 status/firmware-name,
#      FastRPC/LPI status and the new /sound node (exact node/property delta,
#      check-d1-dt.py; APR stays enabled);
#   3. dt-reserved-audit.py vs the stock base DT with --baseline integ-1 and
#      --gpu-okay (no stock reservation lost, no overlap, nothing released);
#   4. CDSP/MPSS, FastRPC and LPASS LPI disabled; card has no backend/codec/pinctrl;
#   5. abl-check.sh (the phone's ABL, emulated) on a dummy-kernel boot image
#      with this DTB + the null dtbo, and as a recovery image.
# Output: unique dt-* directory under ${A9_ADSP_OUT:-~/A9-build/mainline/adsp}.
# Local disk only; the DTB
# contains the private BT address include of integ-1 -> never commit it).
# No device access, network fetch, or bootable image build. PAS auto-boot is
# unchanged: a PASS here is NOT a phone-trial GO.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../../.." && pwd)
W=${A9_MAINLINE:-$HOME/A9-build/mainline}
KT=${A9_ADSP_KTREE:-$W/linux-7.2.10-gpu}
KB=${A9_ADSP_KBUILD:-$W/build-7.2.10-android-gpu-thermal-bt}
SNAP=${A9_ADSP_SNAP:-$W/android/kernel-snap-gpu-thermal-bt}
STOCK=${A9_STOCK_BASE_DTS:-$W/sol-pmic/evidence/base_0.dts}
OUT_PARENT=$(realpath -m "${A9_ADSP_OUT:-$W/adsp}")
BOARD=sm6115-hisense-hlte556n-android-gpu-thermal
case "$OUT_PARENT" in
  *a9-assets*|/share/*) echo "refusing output on the NFS share" >&2; exit 2;;
  "$HOME/A9-build"/*|/tmp/*) ;;
  *) echo "output must be below ~/A9-build or /tmp" >&2; exit 2;;
esac
case "$OUT_PARENT" in "$REPO"|"$REPO"/*) echo "refusing output inside the checkout" >&2; exit 2;; esac
DTD=$KT/arch/arm64/boot/dts/qcom; DTC=$KB/scripts/dtc/dtc; BASE=$SNAP/sm6115-hisense-hlte556n-android-gpu.dtb
for f in "$DTD/$BOARD.dts" "$DTC" "$BASE" "$STOCK" "$SNAP/recovery_dtbo.img"; do [ -e "$f" ] || { echo "missing $f" >&2; exit 1; }; done
[ "$(cat "$SNAP/dtb.source")" = "$BOARD.dtb" ] || { echo "snapshot DTB is not $BOARD" >&2; exit 1; }
mkdir -p "$OUT_PARENT"
O=$(mktemp -d "$OUT_PARENT/dt-XXXXXXXX")
mkdir "$O/src"; cd "$O"
cp "$HERE/a9-adsp-d1.dtsi" src/
printf '#include "%s.dts"\n' "$BOARD" > src/base.dts
printf '#include "%s.dts"\n#include "a9-adsp-d1.dtsi"\n' "$BOARD" > src/adsp.dts
cc() {  # $1 = src/<x>.dts -> <x>.dtb
  local n
  n=$(basename "$1" .dts)
  nix-shell --option substitute false --option builders '' --option substituters '' -p gcc --run "cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp -I src -I '$DTD' -I '$KT/include' -I '$KT/scripts/dtc/include-prefixes' -o $n.pre.dts $1" >/dev/null
  "$DTC" -@ -q -I dts -O dtb -b 0 -o "$n.dtb" "$n.pre.dts"
  "$DTC" -q -s -I dtb -O dts -o "$n.out.dts" "$n.dtb"
}
cc src/base.dts; cc src/adsp.dts
"$DTC" -q -s -I dtb -O dts -o snap.out.dts "$BASE"
f=0
# Compare symbols as well: the ABL overlay fixups depend on them.
cmp -s snap.out.dts base.out.dts || { echo "FAIL rebuilt integ-1 != snapshot"; exit 1; }
echo "ok   rebuilt integ-1 DT == snapshot"
diff base.out.dts adsp.out.dts > adsp.diff || true
python3 -I "$HERE/check-d1-dt.py" base.dtb adsp.dtb
python3 -I "$HERE/test-d1-dt.py" base.dtb adsp.dtb
python3 -I "$REPO/scripts/mainline/android/gpu/dt-reserved-audit.py" adsp.dtb "$STOCK" --baseline "$BASE" --gpu-okay > audit.txt 2>&1 \
  && echo "ok   dt-reserved-audit vs stock, baseline integ-1, --gpu-okay" || { tail -20 audit.txt; echo "FAIL dt-reserved-audit"; f=1; }
# ABL emulation on a dummy-kernel image (abl-check does not run the kernel)
head -c 1048576 /dev/zero > kernel.bin; printf x | gzip > rd.gz
mk() { mkbootimg --header_version 2 --pagesize 4096 --base 0 --kernel_offset 0x8000 \
  --ramdisk_offset 0x1000000 --tags_offset 0x100 --dtb_offset 0x1f00000 \
  --kernel kernel.bin --ramdisk rd.gz --dtb adsp.dtb "$@"; }
mk --recovery_dtbo "$SNAP/recovery_dtbo.img" -o abl-boot.img
"$REPO/scripts/abl-check.sh" abl-boot.img boot --dtbo "$SNAP/recovery_dtbo.img" > abl.txt 2>&1 \
  && echo "ok   abl-check: boot + null dtbo" || { tail -20 abl.txt; echo "FAIL abl-check boot"; f=1; }
"$REPO/scripts/abl-check.sh" abl-boot.img recovery >> abl.txt 2>&1 \
  && echo "ok   abl-check: as recovery (embedded recovery_dtbo)" || { tail -20 abl.txt; echo "FAIL abl-check recovery"; f=1; }
rm -f kernel.bin rd.gz abl-boot.img
sha256sum adsp.dtb > adsp.dtb.sha256
echo "DTB $O/adsp.dtb $(cut -c1-64 adsp.dtb.sha256)"
[ $f = 0 ] && echo "ADSP-D1-DTB PASS" || { echo "ADSP-D1-DTB FAIL"; exit 1; }
