#!/usr/bin/env bash
# check-adsp-dt.sh -- offline compile check of a9-adsp-sensors.dtsi on top of
# the Android board DT (it is wired into no build). Run inside
# scripts/mainline/kernel/shell.nix (cpp). Output ~/A9-build/mainline/sensors/dt-adsp/.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../../.." && pwd)
L=${1:-$HOME/A9-build/mainline/linux-7.2.10}
DTC=${2:-$HOME/A9-build/mainline/build-7.2.10-android/scripts/dtc/dtc}
O=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}/dt-adsp
case "$O" in *a9-assets*) exit 2;; esac
rm -rf "$O"; mkdir -p "$O/src"
cp "$REPO/docs/mainline/sm6115-hisense-hlte556n.dts" "$REPO/scripts/mainline/android/sm6115-hisense-hlte556n-android.dts" \
   "$REPO/scripts/mainline/pmic/a9-pmi632.dtsi" "$HERE/a9-adsp-sensors.dtsi" "$O/src/"
printf '#include "sm6115-hisense-hlte556n-android.dts"\n#include "a9-adsp-sensors.dtsi"\n' > "$O/src/adsp.dts"
cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp -I "$O/src" -I "$L/include" -I "$L/arch/arm64/boot/dts/qcom" \
    -o "$O/adsp.pre.dts" "$O/src/adsp.dts"
"$DTC" -@ -q -I dts -O dtb -o "$O/adsp.dtb" "$O/adsp.pre.dts"
"$DTC" -q -I dtb -O dts -o "$O/adsp.out.dts" "$O/adsp.dtb"
python3 -I - "$O/adsp.out.dts" <<'PY'
import re, sys
s = open(sys.argv[1]).read()
f = 0
def ok(c, m):
    global f; print(('ok   ' if c else 'FAIL ') + m); f += not c
rp = s[s.index('remoteproc@a400000 {'):]
rp = rp[:rp.index('\n\t\t};')]
ok('status = "okay"' in rp.split('glink-edge')[0], 'remoteproc_adsp okay')
ok(len(re.findall(r'compatible = "qcom,fastrpc-compute-cb"', rp)) == 5, '5 adsp compute banks')
ok(all(f'<0x{x:02x} 0x1c{n} 0x00>' in rp or re.search(rf'iommus = <0x[0-9a-f]+ 0x1c{n} 0x00>', rp) for x, n in [(0, k) for k in range(3, 8)]), 'SIDs 0x1c3..0x1c7')
ok(re.search(r'apr \{[^}]*status = "disabled"', rp, re.S) is not None, 'APR (audio) disabled')
ok('memory@53800000' in s and re.search(r'memory@53800000 \{[^}]*reg = <0x00 0x53800000 0x00 0x1e00000>', s, re.S) is not None, 'ADSP region = stock 0x1e00000')
ok(re.search(r'memory@55600000 \{[^}]*reg = <0x00 0x55600000 0x00 0xa00000>;[^}]*no-map', s, re.S) is not None, 'tail 0x55600000-0x56000000 still no-map')
for n in ('remoteproc@b300000', 'remoteproc@6080000'):   # cdsp, mpss stay disabled
    m = s[s.index(n):]; ok('status = "disabled"' in m[:m.index('\n\t\t};')], f'{n} still disabled')
print('PASS' if not f else 'FAIL'); sys.exit(1 if f else 0)
PY
