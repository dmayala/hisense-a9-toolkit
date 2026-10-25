#!/usr/bin/env bash
# check-readonly.sh -- static proof that the thermal and health HALs cannot
# write thermal or charging state (offline; reads the repo only).
#  1. thermal HAL sources: no file-writing API at all; the only write() is the
#     binder dump() to the caller's fd. Its rc runs it as user system (sysfs
#     trip/emul_temp files are root-owned 0644/0200).
#  2. no vendor rc/script opens thermal or power_supply sysfs for writing
#     (emul_temp, trip_point_*, cur_state, policy, charge_*, input_current_*).
#  3. PMI632 power_supply driver (kernel patches 0008/0009/0031): no
#     set_property / property_is_writeable, so the power_supply core creates
#     every attribute 0444 and nothing in userspace can change charging through
#     it; no charging_policy / charge_behaviour / charge_control attribute
#     exists (the AOSP BatteryMonitor's only write, setChargingPolicy ->
#     <psy>/charging_policy, has no target).
#  4. health rc: user system, no write/chmod/chown lines.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
A=$(cd "$HERE/../.." && pwd)            # scripts/mainline/android
K=$(cd "$A/../kernel/patches" && pwd)
fails=0
ok() { echo "ok   $*"; }
bad() { echo "FAIL $*"; fails=$((fails+1)); }

T=$A/thermal-hal
src="$T/Thermal.cpp $T/thermal_core.cpp $T/thermal_core.h"
if grep -nE 'ofstream|fstream[^a-z]|fopen|fwrite|fputs|fprintf\(|O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|creat\(|truncate|unlink|rename\(|mkdir|chmod|chown|symlink|pwrite|sendfile|splice' $src | grep -v 'ifstream' | grep -v '#include'; then
  bad "thermal HAL sources use a file-writing API (above)"
else ok "thermal HAL sources: no file-writing API (only std::ifstream / opendir / readdir)"; fi
n=$(grep -c 'write(' $src | awk -F: '{s+=$NF} END{print s}')
w=$(grep -n 'write(' $src)
if [ "$n" = 1 ] && echo "$w" | grep -q 'write(fd, s.data(), s.size())'; then ok "thermal HAL: the only write() is dump() to the caller's fd"
else bad "thermal HAL write() calls: $w"; fi
grep -qx '    user system' "$T/android.hardware.thermal-service.a9.rc" && ! grep -qE '^\s*(write|chmod|chown|setprop)' "$T/android.hardware.thermal-service.a9.rc" \
  && ok "thermal rc: user system, no write/chmod/chown" || bad "thermal rc runs as root or writes"

if grep -rnE '(emul_temp|trip_point|cur_state|/policy|charge_|input_current|constant_charge|charging_policy|charge_behaviour|/sys/class/(thermal|power_supply))' \
     "$A/vendor/etc" "$A/vendor/bin" "$A/health"/*.rc "$T"/*.rc 2>/dev/null | grep -E '(^|[^a-z])(write|chmod|chown|echo|>)' ; then
  bad "a vendor rc/script writes thermal or power_supply sysfs (above)"
else ok "vendor rc/scripts: no write to thermal or power_supply sysfs"; fi

P="$K/0008-power-supply-Hisense-A9-PMI632-power-supply-charging.patch $K/0009-power-supply-observe-A9-with-one-stock-FV-probe-writ.patch $K/0031-power-supply-pmi632-a9-qualify-battery-OV-health-lik.patch"
for f in $P; do [ -f "$f" ] || { bad "missing $f"; }; done
if grep -nE '^\+.*(\.set_property|property_is_writeable)' $P; then bad "PMI632 driver has writable power_supply properties (above)"
else ok "PMI632 driver: no set_property / property_is_writeable -> every power_supply attribute is 0444"; fi
if grep -nE '^\+.*POWER_SUPPLY_PROP_(CHARGE_BEHAVIOUR|CHARGE_CONTROL|CHARGE_TYPE|INPUT_CURRENT_LIMIT.*=|USB_TYPE)' $P | grep -v 'case\|PROP_INPUT_CURRENT_LIMIT,'; then
  bad "PMI632 driver exposes a charge-control property (above)"
else ok "PMI632 driver: no charge_behaviour / charge_control / charge_type / charging_policy attribute"; fi

H=$A/health/android.hardware.health-service.a9.rc
grep -qx '    user system' "$H" && ! grep -qE '^\s*(write|chmod|chown)' "$H" && ok "health rc: user system, no write/chmod/chown" \
  || bad "health rc runs as root or writes"

[ $fails = 0 ] && echo "PASS: read-only audit" || echo "FAIL: $fails"
exit $((fails > 0))
