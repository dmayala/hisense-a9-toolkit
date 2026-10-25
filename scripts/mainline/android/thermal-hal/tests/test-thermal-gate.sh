#!/usr/bin/env bash
# Host tests of thermal-gate.py (offline). $1 = dir with idle-dump.txt /
# idle-zones.txt written by test_fixture (the HAL's real dump format).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); G="python3 -I $HERE/../thermal-gate.py"; D=$1
fails=0
expect() { local want=$1 name=$2; shift 2; "$@" >"$D/out" 2>&1; local rc=$?
  if [ "$rc" = "$want" ]; then echo "ok   $name"; else echo "FAIL $name (rc $rc, want $want): $(tail -c 300 "$D/out")"; fails=$((fails+1)); fi; }
H=$D/idle-dump.txt Z=$D/idle-zones.txt
expect 0 "idle: replayed phone state passes" $G idle --hal "$H" --sysfs "$Z"
sed 's/^\(  skin type 3 \).*/\1FAULT out of range 125.000000 C/' "$H" > "$D/h1"; expect 1 "idle: skin FAULT stops" $G idle --hal "$D/h1" --sysfs "$Z"
sed '/^  gpu /d' "$H" > "$D/h2";                     expect 1 "idle: missing GPU sensor stops" $G idle --hal "$D/h2" --sysfs "$Z"
sed 's/^zone cpu4-thermal .*/zone cpu4-thermal 45000/' "$Z" > "$D/z3"; expect 1 "idle: HAL vs sysfs > 3 C stops" $G idle --hal "$H" --sysfs "$D/z3"
sed 's/^zone quiet-therm-thermal .*/zone quiet-therm-thermal 43000/' "$Z" > "$D/z4"; expect 1 "idle: warm skin (43 C) stops" $G idle --hal "$H" --sysfs "$D/z4"
sed 's/from adczone:quiet-therm-thermal/from iio:quiet_therm/' "$H" > "$D/h5"; expect 1 "idle: skin not from the zone stops" $G idle --hal "$D/h5" --sysfs "$Z"
sed 's/^cdev cpufreq-cpu0 0/cdev cpufreq-cpu0 2/' "$Z" > "$D/z6"; expect 1 "idle: sysfs cooling active stops" $G idle --hal "$H" --sysfs "$D/z6"
grep -v '^cdev cpufreq-cpu4' "$Z" > "$D/z7";          expect 1 "idle: cooling list mismatch stops" $G idle --hal "$H" --sysfs "$D/z7"
: > "$D/h8";                                          expect 1 "idle: empty dump (HAL not running) stops" $G idle --hal "$D/h8" --sysfs "$Z"
ts() { printf 'IsStatusOverride: %s\nThermal Status: %s\nCached temperatures:\nHAL Ready: %s\nHAL connection:\n\tThermalHAL AIDL 3  connected: yes\nCurrent temperatures from HAL:\n%s\nCurrent cooling devices from HAL:\n' "$1" "$2" "$3" "$4" > "$D/ts"; }
T=""; for n in cpu4 cpu5 cpu0123 gpu skin battery; do T="$T	Temperature{mValue=38.0, mType=0, mName=$n, mStatus=0}"$'\n'; done
ts false 0 true "$T";  expect 0 "service: ready, status 0, sensors listed" $G service --dumpsys "$D/ts"
ts true 0 true "$T";   expect 1 "service: override active stops" $G service --dumpsys "$D/ts"
ts false 1 true "$T";  expect 1 "service: status 1 stops" $G service --dumpsys "$D/ts"
ts false 0 false "$T"; expect 1 "service: HAL not ready stops" $G service --dumpsys "$D/ts"
ts false 0 true "${T//mName=skin,/mName=other,}"; expect 1 "service: skin missing stops" $G service --dumpsys "$D/ts"
expect 0 "commands printed" $G commands
! $G commands | grep -v '^#' | grep -qE 'override|emul_temp|setprop|echo [0-9]+ >' && echo "ok   commands are read-only" || { echo "FAIL commands not read-only"; fails=$((fails+1)); }
[ $fails = 0 ] && echo "PASS: thermal-gate.py" || echo "FAIL ($fails)"; exit $((fails > 0))
