#!/system/bin/sh
# s2c2-sampler.sh <seconds> -- S2c-2 sampler, run as the SHELL user (adb shell), read-only.
# Streams the lines s2c2-judge.py parses (S every 1 s, D every 5 s, T/E/W every 60 s) to stdout;
# the host saves them. Writes nothing on the phone. Ends after <seconds> or when killed.
# Read-only sources: getprop, the LED class brightness nodes, `settings get`, `dumpsys display`,
# pidof, thermal zones, the E Ink gate/fault properties, and the count of
# init's sys.linevibrator_short trigger lines in the kernel log buffer (logcat -b kernel).
SYS=${S2C2_SYS:-/sys}; PROC=${S2C2_PROC:-/proc}
dur=$1
case "$dur" in ''|*[!0-9]*) echo "usage: $0 <seconds>" >&2; exit 2;; esac
up() { read -r u rest < "$PROC/uptime"; echo "$u"; }
upi() { u=$(up); echo "${u%%.*}"; }
led() { cat "$SYS/class/leds/$1/brightness" 2>/dev/null || echo -; }
end=$(( $(upi) + dur )); i=0
while [ "$(upi)" -lt "$end" ]; do
	echo "S $(up) short=$(getprop sys.linevibrator_short) open=$(getprop sys.linevibrator_open) wc=$(led a9:white:center) ww=$(led a9:warm:center) state=$(getprop vendor.a9.sensors.state) hal=$(getprop init.svc_debug_pid.vendor.sensors-a9) ss=$(pidof system_server)"
	if [ $((i % 5)) = 0 ]; then
		d=$(dumpsys display 2>/dev/null)
		lux=$(echo "$d" | grep -m1 -oE 'mAmbientLux=[0-9.]+' | cut -d= -f2)
		strat=$(echo "$d" | grep -oE 'strat=[A-Za-z]+' | tail -n1 | cut -d= -f2)
		echo "D $(up) mode=$(settings get system screen_brightness_mode) brt=$(settings get system screen_brightness) lux=${lux:--} strat=${strat:--}"
	fi
	if [ $((i % 60)) = 0 ]; then
		t="T $(up)"
		for z in "$SYS"/class/thermal/thermal_zone*; do
			[ -r "$z/temp" ] && t="$t ${z##*/}=$(cat "$z/temp" 2>/dev/null)"
		done
		echo "$t"
		# E Ink fault state as the wrapper's own fault watch reads it (the a9_eink module has no fault
		# counter parameter: clear_fault is write-only); values have no spaces (else "-")
		g=$(getprop vendor.a9ml.eink.gate); f=$(getprop vendor.a9ml.eink.fault)
		case "$f" in *" "*) f=$(echo "$f" | tr ' ' _);; esac
		echo "E $(up) gate=${g:--} fault=${f:--}"
		echo "W $(up) trig=$(logcat -d -b kernel 2>/dev/null | grep -c 'processing action (sys.linevibrator_short=')"
	fi
	i=$((i + 1))
	sleep 1
done
echo "S2C2-SAMPLER-END $(up)"
