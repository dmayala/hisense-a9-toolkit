#!/system/bin/sh
# a9ml-usb.sh -- USB helper for the A9 mainline Android boot (DRAFT).
#  * corrects sys.usb.controller from /sys/class/udc;
#  * fallback bind: if adbd's functionfs is not ready after 120 s, bind the UDC
#    with only acm.gs9 + ncm.gs8 so the host still sees the phone; when adbd
#    becomes ready later, rebind so ffs.adb (f1) is included;
#  * root shell on /dev/ttyGS0 (ACM), respawned;
#  * NCM: 172.16.42.1/24 on the ncm.gs8 interface, a9ml-ctl on tcp 2323
#    (toybox nc -L), same commands as the diskless image's a9-ctl;
#  * optional logcat (E and above) to kmsg: a9ml.logcat=1 (default 0 since
#    boot 4: the sh `read` loop cost ~50 % of a core; sed -u is used now).
log() { echo "a9ml-usb: $*" > /dev/kmsg; }
flag() { for w in $(cat /proc/cmdline); do case "$w" in "$1="*) echo "${w#*=}"; return;; esac; done; echo "$2"; }
G=/config/usb_gadget/g1

i=0
while [ -z "$(ls /sys/class/udc 2>/dev/null)" ] && [ $i -lt 60 ]; do sleep 1; i=$((i+1)); done
UDC=$(ls /sys/class/udc 2>/dev/null | head -n1)
if [ -z "$UDC" ]; then
	log "no UDC after 60 s"
else
	[ "$UDC" != "$(getprop sys.usb.controller)" ] && { log "controller $(getprop sys.usb.controller) -> $UDC"; setprop sys.usb.controller "$UDC"; }
	log "UDC $UDC"
fi

# Same unconditional HAL handoff as row27-user-r22c-usbfix.
setprop sys.usb.configfs 2

# ACM root shell
(
	while :; do
		[ -c /dev/ttyGS0 ] && /system/bin/sh -i < /dev/ttyGS0 > /dev/ttyGS0 2>&1
		sleep 2
	done
) &

# logcat errors to kmsg
if [ "$(flag a9ml.logcat 0)" = 1 ]; then
	( /system/bin/logcat -b main,system,crash -v brief '*:E' 2>/dev/null |
		sed -u 's/^/a9ml-logcat: /' > /dev/kmsg ) &
fi

# The fallback may only act while adbd has NEVER been ready and the boot has
# not completed. Boot 3 (2026-10-15) lost adb right after boot_completed: an
# adbd restart / UsbDeviceManager reconfiguration unbinds the UDC for a moment
# (ffs.ready 0), the old loop then bound acm+ncm only, and init's own
# symlink/UDC writes failed with the gadget bound (configfs refuses link
# changes on a bound gadget) -> adb never came back. After the first adbd
# ready, this script never writes UDC again.
fallback=0 net=0 t=0 seen_ready=0
android_state_old=
android_generation_old=
while :; do
	ready=$(getprop sys.usb.ffs.ready)
	bound=$(cat $G/UDC 2>/dev/null)
	udc_state=$(cat /sys/class/udc/$UDC/state 2>/dev/null)
	case "$udc_state" in
		configured) android_state=CONFIGURED ;;
		"not attached") android_state=DISCONNECTED ;;
		attached|powered|default|addressed) android_state=CONNECTED ;;
		*) android_state= ;; # Preserve state through suspend or a failed read.
	esac
	# HAL composition switches need not change sys.usb.config, and a short
	# unbind/FFS restart can fall entirely between polls. Include the durable
	# configuration written by Gadget::apply so CONFIGURED is refreshed.
	composition=$(cat "$G/configs/b.1/strings/0x409/configuration" 2>/dev/null)
	android_generation="$(getprop sys.usb.config)|$bound|$ready|$composition"
	android_event=$android_state
	if [ "$android_state" = CONFIGURED ] && [ "$android_generation" != "$android_generation_old" ]; then
		android_event=CONFIGURED_REFRESH
	fi
	if [ -n "$android_state" ] && { [ "$android_state" != "$android_state_old" ] || [ "$android_event" = CONFIGURED_REFRESH ]; }; then
		if printf '%s\n' "$android_event" > /sys/class/android_usb/android0/state; then
			android_state_old=$android_state
			android_generation_old=$android_generation
		fi
	fi
	[ "$ready" = 1 ] && [ $seen_ready = 0 ] && { seen_ready=1; log "adbd ready: fallback disabled for good"; }
	[ "$(getprop sys.boot_completed)" = 1 ] && seen_ready=1
	if [ -z "$bound" ] && [ "$ready" != 1 ] && [ $t -ge 120 ] && [ -n "$UDC" ] && [ $fallback = 0 ] && [ $seen_ready = 0 ]; then
		echo "$UDC" > $G/UDC && { fallback=1; log "fallback bind: acm+ncm only (adbd not ready after 120 s)"; }
	fi
	if [ $fallback = 1 ] && [ "$ready" = 1 ] && [ -L $G/configs/b.1/f1 ]; then
		echo "" > $G/UDC; sleep 1; echo "$UDC" > $G/UDC && log "rebound with adb"
		fallback=2
	fi
	if [ "$(getprop sys.usb.configfs)" = 1 ] && [ "$ready" = 1 ] && [ -n "$bound" ] &&
	   [ "$(cat /sys/class/udc/$UDC/state 2>/dev/null)" = configured ]; then
		setprop sys.usb.configfs 2
		log "early USB configured; handing existing ADB gadget to HAL"
	fi
	if [ $net = 0 ]; then
		IF=$(cat $G/functions/ncm.gs8/ifname 2>/dev/null)
		if [ -n "$IF" ] && [ -e /sys/class/net/$IF ] && [ -n "$(cat $G/UDC 2>/dev/null)" ]; then
			ip addr add 172.16.42.1/24 dev "$IF" 2>/dev/null
			ip link set "$IF" up && {
				net=1; log "$IF 172.16.42.1 up, a9ml-ctl on tcp 2323"
				( while :; do nc -s 172.16.42.1 -p 2323 -L /system/bin/sh /vendor/bin/a9ml-ctl.sh; sleep 2; done ) &
			}
		fi
	fi
	sleep 2; t=$((t+2))
done
