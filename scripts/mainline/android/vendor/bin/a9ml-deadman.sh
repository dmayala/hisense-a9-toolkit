#!/system/bin/sh
# a9ml-deadman.sh -- Android-side dead-man for the A9 mainline boot (DRAFT).
# Phase 1: no sys.boot_completed within a9ml.deadman s (default 2400; maxcpus=1
#          + first-boot dexopt is slow) -> reboot to the bootloader.
# Phase 2: after boot_completed, hold a9ml.hold s (default 1200) so main can
#          test, then reboot to the bootloader.
# /data/local/tmp/a9-stay (RAM disk, gone at reboot) holds either phase while
# it exists and is fresh (a9ml.stayfresh, 600 s); a9ml.maxstay s (default 7200)
# caps the total uptime anyway.
# Reboot: setprop sys.powerctl reboot,bootloader (PON reason 2; the BCB write
# fails harmlessly, no /misc in the fstab). If init does not get us there in
# 90 s: sysrq 'b' (immediate reset; PON reason stays at the beacon's 2).
flag() { # flag <name> <default>, from /proc/cmdline
	for w in $(cat /proc/cmdline); do
		case "$w" in "$1="*) echo "${w#*=}"; return;; esac
	done
	echo "$2"
}
log() { echo "a9ml-deadman: $*" > /dev/kmsg; }
DEADMAN=$(flag a9ml.deadman 2400)
HOLD=$(flag a9ml.hold 1200)
MAXSTAY=$(flag a9ml.maxstay 7200)
STAY=/data/local/tmp/a9-stay
up() { s=$(cat /proc/uptime); echo "${s%%.*}"; }
[ "$DEADMAN" = 0 ] && { log "disabled (a9ml.deadman=0)"; exit 0; }
log "armed: boot_completed within ${DEADMAN}s, then hold ${HOLD}s; $STAY holds; cap ${MAXSTAY}s uptime"

# the stay file only holds while it is FRESH (touched within a9ml.stayfresh s,
# default 600): a host that lost the phone cannot keep it held for hours
# (boot 3, 2026-10-15: adb gone, stay file present, phone invisible)
FRESH=$(flag a9ml.stayfresh 600)
# cap(): the uptime cap; the host may raise it for a long hold through
# vendor.a9ml.maxstay (android-boot.sh --hold), never beyond 21600 s (6 h)
cap() {
	c=$(getprop vendor.a9ml.maxstay); case "$c" in ''|*[!0-9]*) c=0 ;; esac
	[ "$c" -gt 21600 ] && c=21600
	[ "$c" -gt "$MAXSTAY" ] && echo "$c" || echo "$MAXSTAY"
}
held() {
	[ -e "$STAY" ] && [ "$(up)" -lt "$(cap)" ] || return 1
	m=$(stat -c %Y "$STAY" 2>/dev/null) || return 1
	[ $(( $(date +%s) - m )) -lt "$FRESH" ]
}

# phase 1
t0=$(up)
while [ "$(getprop sys.boot_completed)" != 1 ]; do
	if [ $(( $(up) - t0 )) -ge "$DEADMAN" ] && ! held; then
		log "no boot_completed after ${DEADMAN}s"
		REASON=noboot
		break
	fi
	sleep 5
done
# phase 2
if [ -z "$REASON" ]; then
	log "boot_completed at $(up)s uptime; holding ${HOLD}s"
	t1=$(up)
	while [ $(( $(up) - t1 )) -lt "$HOLD" ] || held; do
		[ "$(up)" -ge "$(cap)" ] && break
		sleep 5
	done
	REASON=hold-expired
fi
log "firing ($REASON) at $(up)s: reboot to bootloader"
echo 2 > /sys/module/qcom_pon/parameters/beacon 2>/dev/null
sync
setprop sys.powerctl reboot,bootloader
sleep 90
log "init did not reboot within 90 s: sysrq b"
echo b > /proc/sysrq-trigger
