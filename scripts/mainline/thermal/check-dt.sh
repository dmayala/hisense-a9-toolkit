#!/usr/bin/env bash
# check-dt.sh -- offline: compile the Android board DT with a9-thermal.dtsi
# appended (what A9_THERMAL=1 build.sh does) and check the result. No phone.
#   scripts/mainline/thermal/check-dt.sh [linux_src] [dtc]
# Output: ~/A9-build/mainline/sensors/dt/ (dtb + decompiled dts).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
L=${1:-$HOME/A9-build/mainline/linux-7.2.10}
DTC=${2:-$HOME/A9-build/mainline/build-7.2.10-android/scripts/dtc/dtc}
O=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}/dt
case "$O" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2;; esac
rm -rf "$O"; mkdir -p "$O/src"
cp "$REPO/docs/mainline/sm6115-hisense-hlte556n.dts" "$REPO/scripts/mainline/android/sm6115-hisense-hlte556n-android.dts" \
   "$REPO/scripts/mainline/pmic/a9-pmi632.dtsi" "$HERE/a9-thermal.dtsi" "$O/src/"
for v in base thermal; do
  cp "$O/src/sm6115-hisense-hlte556n-android.dts" "$O/src/$v.dts"
  [ $v = thermal ] && printf '\n#include "a9-thermal.dtsi"\n' >> "$O/src/$v.dts"
  cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp -I "$O/src" -I "$L/include" \
      -I "$L/arch/arm64/boot/dts/qcom" -I "$L/arch/arm64/boot/dts" -o "$O/$v.pre.dts" "$O/src/$v.dts"
  "$DTC" -@ -q -I dts -O dtb -o "$O/$v.dtb" "$O/$v.pre.dts"
  "$DTC" -q -I dtb -O dts -o "$O/$v.out.dts" "$O/$v.dtb"
done
python3 -I "$HERE/check-dt.py" "$O/base.out.dts" "$O/thermal.out.dts"
