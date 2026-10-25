#!/usr/bin/env bash
# Host tests of health-gate.py (offline). Synthetic captures for gates 3/4;
# gate 1/2 against private vendor builds when present (run-tests.sh --vendor).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); G="python3 -I $HERE/health-gate.py"; ls -l "$HERE/health-gate.py" | grep -q "^-rwx" || { echo "FAIL health-gate.py not executable"; exit 1; }
O=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}/health-gate-test; rm -rf "$O"; mkdir -p "$O"
fails=0
expect() { # expect <0|1> <name> cmd...
  local want=$1 name=$2; shift 2
  "$@" >"$O/out" 2>&1; local rc=$?
  if [ "$rc" = "$want" ]; then echo "ok   $name"; else echo "FAIL $name (rc $rc, want $want): $(tail -c 300 "$O/out")"; fails=$((fails+1)); fi
}
NOW=$(date +%s)
cap() { # cap <file> <n> [key=value overrides for sample 3]
  local f=$1 n=$2; shift 2; echo "boot_id=11111111-2222" > "$f"
  for i in $(seq 1 "$n"); do
    echo "t=$((100 + 2 * i)).5"; echo "epoch=$((NOW - 20 + 2 * i))"
    local kv="battery.present=1 battery.status=Discharging battery.health=Good battery.voltage_now=$((4100000 + i * 1000)) battery.current_now=-130000 battery.temp=310 battery.capacity=68 usb.present=1 usb.online=1"
    [ "$i" = 3 ] && for o in "$@"; do kv="$(echo "$kv" | sed "s/${o%%=*}=[^ ]*/$o/")"; done
    for x in $kv; do echo "$x"; done
  done >> "$f"
}
cap "$O/good" 6;                                  expect 0 "telemetry: 6 fresh samples pass" $G telemetry "$O/good"
cap "$O/few" 3;                                   expect 1 "telemetry: 3 samples stop" $G telemetry "$O/few"
cap "$O/enodata" 6 "battery.voltage_now=cat:_read_error:_No_data_available"; expect 1 "telemetry: ENODATA stops" $G telemetry "$O/enodata"
cap "$O/absent" 6 battery.present=0;              expect 1 "telemetry: absent battery stops" $G telemetry "$O/absent"
cap "$O/low" 6 battery.voltage_now=3700000;       expect 1 "telemetry: 3.70 V margin stops" $G telemetry "$O/low"
cap "$O/hot" 6 battery.temp=415;                  expect 1 "telemetry: 41.5 C stops" $G telemetry "$O/hot"
cap "$O/zero" 6 battery.capacity=0;               expect 1 "telemetry: 0 % stops" $G telemetry "$O/zero"
cap "$O/health" 6 battery.health=Overvoltage;     expect 1 "telemetry: health fault stops" $G telemetry "$O/health"
cap "$O/flap" 6 usb.online=0;                     expect 1 "telemetry: USB changing stops" $G telemetry "$O/flap"
sed 's/^t=.*/t=100.0/' "$O/good" > "$O/stale";    expect 1 "telemetry: non-increasing uptime stops" $G telemetry "$O/stale"
sed 's/^t=.*/t=nan/' "$O/good" > "$O/nan";        expect 1 "telemetry: nan uptime stops (review 28)" $G telemetry "$O/nan"
cap "$O/st" 6 battery.status=nonsense;            expect 1 "telemetry: unknown status stops (review 28)" $G telemetry "$O/st"
cap "$O/usbinv" 6 usb.present=0;                  expect 1 "telemetry: usb online without present stops (review 28)" $G telemetry "$O/usbinv"
grep -v '^epoch=' "$O/good" > "$O/noepoch";       expect 1 "telemetry: capture without epoch stops" $G telemetry "$O/noepoch"
expect 1 "telemetry: an hour-old capture stops (freshness)" $G telemetry --now $((NOW + 3600)) "$O/good"
grep -v '^boot_id=' "$O/good" > "$O/nobid";       expect 1 "telemetry: capture without boot_id stops" $G telemetry "$O/nobid"
ds() { printf 'boot_id=11111111-2222\nCurrent Battery Service state:\n  AC powered: false\n  USB powered: %s\n  Wireless powered: false\n  status: %s\n  health: %s\n  present: true\n  level: %s\n  scale: 100\n  voltage: %s\n  temperature: %s\n  technology: Li-ion\n%s' "$1" "$2" "${7:-2}" "$3" "$4" "$5" "${6:-}" > "$O/ds"; }
ds true 3 68 4103 310;    expect 0 "compare: matching values pass" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
ds true 2 85 3600 250;    expect 1 "compare: Cuttlefish fake values stop" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
ds true 3 68 4103000 310; expect 1 "compare: voltage in uV (unit error) stops" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
ds true 3 68 4103 31;     expect 1 "compare: temperature in C not deci-C stops" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
ds false 3 68 4103 310;   expect 1 "compare: USB powered disagreement stops" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
ds true 2 68 4103 310;    expect 1 "compare: status disagreement stops" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
ds true 3 68 4103 310 "" 4; expect 1 "compare: Android health 4 stops (review 28)" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
ds true 3 68 4103 310 "  current now: 130000"$'\n'; expect 1 "compare: current sign reversal stops (review 28)" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
ds true 3 68 4103 310 "  current now: -131000"$'\n'; expect 0 "compare: matching current passes" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
ds true 3 68 4103 310; sed -i 's/^boot_id=.*/boot_id=other/' "$O/ds"; expect 1 "compare: different boot stops" $G compare --dumpsys "$O/ds" --sysfs "$O/good"
cap "$O/possign" 6 battery.current_now=130000;  ds true 3 68 4103 310; expect 1 "compare: sysfs +current while Discharging stops" $G compare --dumpsys "$O/ds" --sysfs "$O/possign"
expect 0 "commands printed" $G commands
S=$O/../aw/out; B=$HOME/A9-build/mainline/android/out/a9-android-eink-boot.img; K=$HOME/A9-build/mainline/android/kernel-snap
if [ -f "$S/vendor_b-health.img" ] && [ -f "$B" ] && [ -d "$K" ]; then
  expect 0 "artifact: health-only vendor passes" $G artifact --vendor "$S/vendor_b-health.img" --boot "$B" --snapshot "$K" --record "$O/record.txt"
  [ -f "$S/vendor_b-health-thermal.img" ] && expect 1 "artifact: vendor with the A9 thermal HAL stops" $G artifact --vendor "$S/vendor_b-health-thermal.img" --boot "$B" --snapshot "$K"
  [ -f "$S/vendor_b.img" ] && expect 1 "artifact: baseline (fake health) vendor stops" $G artifact --vendor "$S/vendor_b.img" --boot "$B" --snapshot "$K"
  head -c 4096 "$B" > "$O/boot-other.img"; expect 1 "artifact: boot without the snapshot kernel stops" $G artifact --vendor "$S/vendor_b-health.img" --boot "$O/boot-other.img" --snapshot "$K"
  { printf 'not a boot image\n'; cat "$K/Image.gz"; } > "$O/boot-prefix.img"
  expect 1 "artifact: text prefix + real kernel stops (header parsed, review 28)" $G artifact --vendor "$S/vendor_b-health.img" --boot "$O/boot-prefix.img" --snapshot "$K"
  expect 1 "artifact: record under a9-assets refused" $G artifact --vendor "$S/vendor_b-health.img" --boot "$B" --snapshot "$K" --record "$HOME/a9-assets/x.txt"
  expect 1 "artifact: existing record not overwritten" $G artifact --vendor "$S/vendor_b-health.img" --boot "$B" --snapshot "$K" --record "$O/record.txt"
  [ -f "$S/vendor_b-eink-health.img" ] && expect 0 "artifact: eink health vendor, modules == snapshot" $G artifact --vendor "$S/vendor_b-eink-health.img" --boot "$B" --snapshot "$K"
else
  echo "skip artifact cases (no private vendor builds / boot image / snapshot)"
fi
# android-thermal (2026-10-16): MEASURED phone values (healthd kmsg of the 2026-10-16 bundles,
# thermal-hal/tests/fixtures/phone-step1b-20261016.txt: l=96 v=4408 t=31.9 st=3 c=-229644 chg=u)
cap "$O/meas" 6; sed -i 's/battery.voltage_now=.*/battery.voltage_now=4408000/; s/battery.current_now=.*/battery.current_now=-229644/; s/battery.temp=.*/battery.temp=319/; s/battery.capacity=.*/battery.capacity=96/' "$O/meas"
expect 0 "telemetry: measured phone values pass" $G telemetry "$O/meas"
ds true 3 96 4408 319 "  current now: -229644"$'\n'; expect 0 "compare: Android = measured phone values passes" $G compare --dumpsys "$O/ds" --sysfs "$O/meas"
ds true 3 85 3600 250;    expect 1 "compare: Cuttlefish fake vs measured values stops" $G compare --dumpsys "$O/ds" --sysfs "$O/meas"
# the combined kernel (integ-1: gated thermal kernel 0043-0046) with the private android-thermal vendors
IB=${A9_INTEG_BOOT:-$HOME/A9-build/mainline/integ-1/out/a9-android-eink-boot-integ1.img}
IK=${A9_INTEG_SNAP:-$HOME/A9-build/mainline/android/kernel-snap-gpu-thermal-bt}
IV=${A9_INTEG_VENDOR:-$HOME/A9-build/mainline/integ-1/out/vendor_b-eink-integ1.img}
VH=$S/vendor_b-eink-gpu-bt-health.img VT=$S/vendor_b-eink-gpu-bt-health-thermal.img
if [ -f "$IB" ] && [ -d "$IK" ] && [ -f "$VH" ] && [ -f "$VT" ]; then
  expect 1 "artifact: thermal kernel without --thermal-kernel stops" $G artifact --vendor "$VH" --boot "$IB" --snapshot "$IK"
  expect 0 "artifact --thermal-kernel: health-only vendor on the thermal kernel passes" $G artifact --vendor "$VH" --boot "$IB" --snapshot "$IK" --thermal-kernel
  expect 1 "artifact --thermal-kernel: vendor with the thermal HAL stops" $G artifact --vendor "$VT" --boot "$IB" --snapshot "$IK" --thermal-kernel
  expect 0 "artifact --thermal-hal: health + thermal HAL vendor passes" $G artifact --vendor "$VT" --boot "$IB" --snapshot "$IK" --thermal-hal
  expect 1 "artifact --thermal-hal: health-only vendor stops" $G artifact --vendor "$VH" --boot "$IB" --snapshot "$IK" --thermal-hal
  [ -f "$IV" ] && expect 1 "artifact --thermal-hal: integ-1 (fake health) vendor stops" $G artifact --vendor "$IV" --boot "$IB" --snapshot "$IK" --thermal-hal
  printf 'changed\n' > "$O/other.conf"
  expect 1 "artifact --thermal-hal: config differing from the reviewed one stops" $G artifact --vendor "$VT" --boot "$IB" --snapshot "$IK" --thermal-hal --thermal-conf "$O/other.conf"
  K0=$HOME/A9-build/mainline/android/kernel-snap
  [ -d "$K0" ] && expect 1 "artifact --thermal-hal: non-thermal snapshot stops (pairing + gates)" $G artifact --vendor "$VT" --boot "$IB" --snapshot "$K0" --thermal-hal
else
  echo "skip thermal-kernel artifact cases (no integ-1 boot/snapshot or android-thermal vendors under $S)"
fi
[ $fails = 0 ] && echo PASS || echo "FAIL ($fails)"; exit $((fails > 0))
