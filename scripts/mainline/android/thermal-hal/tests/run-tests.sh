#!/usr/bin/env bash
# Host tests of the thermal HAL (no phone, no NDK). ASan + UBSan.
#  1. test_core: parser, severities, faults, publication, cooling (fake sysfs)
#  2. test_fixture: the phone's MEASURED sysfs (tests/fixtures/) + the shipped
#     config: idle publication and replay of the supervised emulations
#  3. check-conf-vs-dtb.py: Android thresholds vs the kernel trips of a thermal
#     DTB (A9_THERMAL_DTB, default: the integ-1 snapshot's; skipped if absent)
#  4. check-readonly.sh: the HALs and vendor scripts cannot write thermal or
#     charging state
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
O=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}/thermal-hal-test
case "$O" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2;; esac
mkdir -p "$O"
CXX=${CXX:-g++}
command -v "$CXX" >/dev/null || { echo "no $CXX (try: nix-shell -p gcc --run $0)" >&2; exit 2; }
F="-std=c++17 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all"
"$CXX" $F -o "$O/test_core" "$HERE/test_core.cpp" "$HERE/../thermal_core.cpp"
"$CXX" $F -o "$O/test_fixture" "$HERE/test_fixture.cpp" "$HERE/../thermal_core.cpp"
A9_TEST_TMP=$O "$O/test_core" "$HERE/../thermal-a9.conf"
mkdir -p "$O/gate"; rm -f "$O/gate/"*
A9_TEST_TMP=$O A9_GATE_OUT=$O/gate "$O/test_fixture" "$HERE/../thermal-a9.conf" "$HERE/fixtures/phone-step1b-20261016.txt"
# thermal-gate.py on the HAL's real dump format: idle passes, the replayed 91 C state stops
TG="python3 -I $HERE/../thermal-gate.py"
$TG idle --hal "$O/gate/idle-dump.txt" --sysfs "$O/gate/idle-zones.txt"
if $TG idle --hal "$O/gate/hot-dump.txt" --sysfs "$O/gate/hot-zones.txt" 2>"$O/gate/hot.err"; then
  echo "FAIL: thermal-gate.py passed a throttling capture" >&2; exit 1; fi
grep -q 'cpu4 severity 1' "$O/gate/hot.err" && grep -q 'cooling cpufreq-cpu4 state 1' "$O/gate/hot.err" \
  || { echo "FAIL: thermal-gate.py hot reasons: $(cat "$O/gate/hot.err")" >&2; exit 1; }
echo "PASS: thermal-gate.py idle on the replayed phone state; STOP on the 91 C state"
"$HERE/test-thermal-gate.sh" "$O/gate"
DTB=${A9_THERMAL_DTB:-$HOME/A9-build/mainline/android/kernel-snap-gpu-thermal-bt/sm6115-hisense-hlte556n-android-gpu.dtb}
if [ -f "$DTB" ]; then
  python3 -I "$HERE/check-conf-vs-dtb.py" "$HERE/../thermal-a9.conf" "$DTB" | tail -n1
  # negative control: the default (non-thermal) Android DTB must FAIL
  ND=${A9_NONTHERMAL_DTB:-$HOME/A9-build/mainline/android/kernel-snap/sm6115-hisense-hlte556n-android.dtb}
  if [ -f "$ND" ]; then
    if python3 -I "$HERE/check-conf-vs-dtb.py" "$HERE/../thermal-a9.conf" "$ND" >/dev/null; then
      echo "FAIL: check-conf-vs-dtb.py passed on a non-thermal DTB" >&2; exit 1
    fi
    echo "PASS: non-thermal DTB refused (negative control)"
  fi
else
  echo "SKIP check-conf-vs-dtb.py: no thermal DTB at $DTB"
fi
"$HERE/check-readonly.sh" | tail -n1
"$HERE/check-readonly.sh" >/dev/null
