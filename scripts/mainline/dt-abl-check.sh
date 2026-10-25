#!/usr/bin/env bash
# Offline: compile docs/mainline/sm6115-hisense-hlte556n.dts against upstream
# sm6115.dtsi and run the phone's own ABL DT selection on it (scripts/abl-check.sh),
# as a recovery image (embedded recovery_dtbo) and as a boot image (--dtbo).
# usage: dt-abl-check.sh WORKDIR     (WORKDIR on local disk, never on ~/a9-assets)
# Fetches sm6115.dtsi, pm6125.dtsi, pmi632.dtsi and the dt-bindings headers from
# torvalds master on git.kernel.org on first use. The kernel in the test image is
# a 1 MiB dummy: abl-check does not run the kernel.
# The board DTS is always compiled from the current source with all of its local
# includes (a9-pmi632.dtsi); every output of an earlier run is deleted first, so a
# failed compile fails this check instead of testing a stale a9.dtb.
# DT_ABL_CHECK_DTS=<file> overrides the board DTS (tests/dt-abl-check-tests.sh).
set -euo pipefail
W=${1:?workdir}; R=$(cd "$(dirname "$0")/../.." && pwd)
DTS_SRC=${DT_ABL_CHECK_DTS:-$R/docs/mainline/sm6115-hisense-hlte556n.dts}
mkdir -p "$W/inc"
rm -f "$W"/a9.pre.dts "$W"/a9.dtb "$W"/overlay.dtbo "$W"/dtbo.img "$W"/rec.img "$W"/boot.img \
  "$W"/inc/sm6115-hisense-hlte556n.dts "$W"/inc/hlte556n-null-overlay.dts "$W"/inc/a9-pmi632.dtsi
cd "$W/inc"
B=https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain
get() { [ -s "$1" ] || { mkdir -p "$(dirname "$1")"; curl -sfL -A curl/8 "$B/$2" -o "$1"; }; }
for f in sm6115.dtsi pm6125.dtsi pmi632.dtsi; do get "$f" "arch/arm64/boot/dts/qcom/$f"; done
for h in clock/qcom,dsi-phy-28nm.h clock/qcom,gcc-sm6115.h clock/qcom,rpmcc.h \
  clock/qcom,sm6115-dispcc.h clock/qcom,sm6115-gpucc.h dma/qcom-gpi.h firmware/qcom,scm.h \
  gpio/gpio.h iio/qcom,spmi-vadc.h input/input.h interconnect/qcom,rpm-icc.h \
  interconnect/qcom,sm6115.h interrupt-controller/arm-gic.h interrupt-controller/irq.h \
  power/qcom-rpmpd.h power/qcom,rpmhpd.h soc/qcom,apr.h sound/qcom,q6asm.h \
  sound/qcom,q6dsp-lpass-ports.h spmi/spmi.h thermal/thermal.h; do
  get "dt-bindings/$h" "include/dt-bindings/$h"; done
get dt-bindings/input/linux-event-codes.h include/uapi/linux/input-event-codes.h
cp "$DTS_SRC" sm6115-hisense-hlte556n.dts
cp "$R/docs/mainline/hlte556n-null-overlay.dts" "$R/scripts/mainline/pmic/a9-pmi632.dtsi" .
nix-shell -p dtc gcc --run '
  set -e
  cpp -nostdinc -I . -undef -D__DTS__ -x assembler-with-cpp sm6115-hisense-hlte556n.dts -o ../a9.pre.dts
  dtc -@ -q -I dts -O dtb -o ../a9.dtb ../a9.pre.dts
  dtc -@ -q -I dts -O dtb -o ../overlay.dtbo hlte556n-null-overlay.dts'
cd "$W"
[ -s a9.dtb ] && [ -s overlay.dtbo ] || { echo "dt-abl-check: DT compile failed" >&2; exit 1; }
python3 -I "$R/scripts/mainline/mkdtbo.py" dtbo.img overlay.dtbo
head -c 1048576 /dev/zero > kernel.bin; printf x | gzip > rd.gz
mk() { mkbootimg --header_version 2 --pagesize 4096 --base 0 --kernel_offset 0x8000 \
  --ramdisk_offset 0x1000000 --tags_offset 0x100 --dtb_offset 0x1f00000 \
  --kernel kernel.bin --ramdisk rd.gz --dtb a9.dtb "$@"; }
mk --recovery_dtbo dtbo.img -o rec.img
mk -o boot.img
rc=0
"$R/scripts/abl-check.sh" rec.img recovery || rc=1
"$R/scripts/abl-check.sh" boot.img boot --dtbo dtbo.img || rc=1
echo "expected FAIL (stock dtbo partition on a mainline DTB):"
"$R/scripts/abl-check.sh" boot.img boot --quiet && rc=1 || true
exit $rc
