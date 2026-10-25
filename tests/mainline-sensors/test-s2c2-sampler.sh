#!/usr/bin/env bash
# Host test of s2c2-sampler.sh against stubs (getprop/settings/dumpsys/pidof/logcat) and fake /sys,/proc;
# then s2c2-judge.py must parse its stream (sampler and judge agree on the line format). Offline.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
R=$(mktemp -d); trap 'kill $UP 2>/dev/null; rm -rf "$R"' EXIT
mkdir -p "$R/stubs" "$R/sys/class/leds/a9:white:center" "$R/sys/class/leds/a9:warm:center" "$R/sys/class/thermal/thermal_zone0" "$R/proc"
echo 480 > "$R/sys/class/leds/a9:white:center/brightness"; echo 0 > "$R/sys/class/leds/a9:warm:center/brightness"
echo 41000 > "$R/sys/class/thermal/thermal_zone0/temp"; echo "100.00 1.00" > "$R/proc/uptime"
cat > "$R/stubs/getprop" <<'S'
#!/bin/sh
case "$1" in sys.linevibrator_short) echo 640;; sys.linevibrator_open) echo 0;; vendor.a9.sensors.state) echo up;;
  init.svc_debug_pid.vendor.sensors-a9) echo 2002;; vendor.a9ml.eink.gate) echo closed;; vendor.a9ml.eink.fault) echo "";; esac
S
printf '#!/bin/sh\ncase "$3" in screen_brightness_mode) echo 1;; *) echo 104;; esac\n' > "$R/stubs/settings"
printf '#!/bin/sh\necho "  mAmbientLux=80.0"; echo "  mAmbientLux=79.0"; echo "BrightnessEvent: brt=0.4, lux=80.0, reason=automatic, strat=AutomaticBrightnessStrategy, state=ON"\n' > "$R/stubs/dumpsys"
printf '#!/bin/sh\necho 1515\n' > "$R/stubs/pidof"
printf '#!/bin/sh\necho "init: processing action (sys.linevibrator_short=*) from (/system/etc/init/vndk.rc:275)"\n' > "$R/stubs/logcat"
chmod +x "$R"/stubs/*
( s=0; while :; do s=$((s+1)); echo "$((100+s)).00 1.00" > "$R/proc/uptime"; sleep 1; done ) & UP=$!
PATH="$R/stubs:$PATH" S2C2_SYS="$R/sys" S2C2_PROC="$R/proc" timeout 30 sh "$ROOT/scripts/mainline/sensors/s2c2-sampler.sh" 6 > "$R/out.txt" 2>&1
cat "$R/out.txt"
fails=0
grep -qE '^S [0-9.]+ short=640 open=0 wc=480 ww=0 state=up hal=2002 ss=1515$' "$R/out.txt" || { echo FAIL S-line; fails=1; }
grep -qE '^D [0-9.]+ mode=1 brt=104 lux=80.0 strat=AutomaticBrightnessStrategy$' "$R/out.txt" || { echo FAIL D-line; fails=1; }
grep -qE '^T [0-9.]+ thermal_zone0=41000$' "$R/out.txt" || { echo FAIL T-line; fails=1; }
grep -qE '^E [0-9.]+ gate=closed fault=-$' "$R/out.txt" || { echo FAIL E-line; fails=1; }
grep -qE '^W [0-9.]+ trig=1$' "$R/out.txt" || { echo FAIL W-line; fails=1; }
grep -q '^S2C2-SAMPLER-END' "$R/out.txt" || { echo FAIL end; fails=1; }
printf '{"hal":2002,"ss":1515,"switch_uptime":0,"short_before":300,"thermal":{"thermal_zone0":41000},"eink":{"gate":"closed","fault":"-"}}' > "$R/b.json"
python3 -I "$ROOT/scripts/mainline/sensors/s2c2-judge.py" final "$R/out.txt" "$R/b.json" | tee "$R/j.txt"
grep -qx 'S2C2-RESULT PASS' "$R/j.txt" || { echo FAIL judge; fails=1; }
[ "$fails" = 0 ] && echo "S2C2-SAMPLER HOST TEST PASS"
exit $fails
