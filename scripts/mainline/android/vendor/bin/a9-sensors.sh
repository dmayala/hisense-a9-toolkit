#!/system/bin/sh
# a9-sensors.sh -- production ADSP sensors bring-up for mainline Android (S2b/S2c), used by
# init.a9-sensors.rc. Root, phhsu_daemon. INERT unless BOTH the vendor was built with
# sensors (ro.vendor.a9ml.sensors=1) AND the kill switch is on (persist.a9.sensors=1,
# default unset = OFF; S2c ships it =1 only in the test vendor's build.prop) AND /data is
# RAM ext4 or the checked ROW29 f2fs mapper. Real userdata is refused.
#
# FAIL CLOSED, NEVER REBOOT: any failed prerequisite ends with
# vendor.a9.sensors.state=fail:<why> and vendor.a9.sensors.ready=0, the ADSP stopped if
# this script started it. ADSP recovery stays DISABLED: a crash leaves sensors off for
# this boot (state down:<why>); nothing restarts or loops.
#
# Same bring-up as the reviewed S1/S2a harness (s1-target.sh), minus the test-only parts:
#  1. registry: a private tmpfs on /mnt/vendor/a9-sensors (refused if anything is mounted
#     there); the hash-pinned registry
#     seed from the vendor; the listener's own RAM copy (registry, stock sensors config,
#     soc0 identity) under /mnt/vendor/a9-sensors/s1-listener-root. Real persist: never.
#     The TEE independently owns /mnt/vendor/persist; never scan or overlay it.
#  2. firmware: verify/reuse the existing firmware_class.path tree (the integrated
#     read-only vendor bridge), or populate a private copy when absent/stale.
#  3. modules (modules.sensors, options from modules.options: fastrpc a9_sensor_fastrpc=1,
#     qcom_q6v5_pas a9_adsp_manual_start=1), read back.
#  4. the one ADSP remoteproc (OF remoteproc@a400000, qcom,sm6115-adsp-pas), offline:
#     recovery + coredump disabled (read back), start, running within the deadline.
#  5. /dev/fastrpc-adsp, then s1-listener --service (the reviewed RAM-only, confined
#     registry broker), READY within the deadline.
#  6. SNS 400 advertised (s2-sns-probe --wait), then vendor.a9.sensors.ready=1.
#  7. supervise: ADSP running and listener alive, every A9S_POLL s; otherwise ready=0,
#     state=down:<why>; a dead listener stops the ADSP (a PD waiting on registry I/O
#     would otherwise stall). Never a restart.
SYS=${A9S_SYS:-/sys}; DEV=${A9S_DEV:-/dev}; VND=${A9S_VENDOR:-/vendor}; PROC=${A9S_PROC:-/proc}
MNT=${A9S_MNT:-/mnt/vendor/a9-sensors}; DATA=${A9S_DATA:-/data}
log() { echo "a9-sensors: $*" >> ${A9S_KMSG:-/dev/kmsg}; }
M=$VND/lib/modules
CFG=$VND/etc/a9-sensors
LOGD=$DATA/vendor/a9-sensors
ROOTD=$MNT/s1-listener-root
STARTED=0
LPID=
setstate() { setprop vendor.a9.sensors.state "$1"; log "state $1"; }
fail() {
	setprop vendor.a9.sensors.ready 0
	if [ -n "$LPID" ]; then kill "$LPID" 2>/dev/null; fi
	[ "$STARTED" = 1 ] && [ -n "$RP" ] && { echo stop > "$RP/state"; } 2>/dev/null
	setstate "fail:$1"
	exit 0
}
uptime_s() { read -r u rest < "$PROC/uptime"; echo "${u%%.*}"; }

[ "$1" = start ] || exit 2
[ "$(getprop ro.vendor.a9ml.sensors)" = 1 ] || exit 0
if [ "$(getprop persist.a9.sensors)" != 1 ]; then setstate off; setprop vendor.a9.sensors.ready 0; exit 0; fi
setstate starting
umask 077

# Shared test-/data gate. Registry/listener payloads still use private tmpfs.
. "$VND/bin/a9ml-test-data.sh" || fail data-guard
a9ml_test_data "$PROC" "$SYS" "$DATA" || fail unsupported-data
mkdir -p "$LOGD" || fail log-dir

# 0. vendor payload integrity (one manifest over seed, registry manifest, soc0, firmware list)
[ -f "$CFG/SHA256SUMS" ] || fail no-manifest
(cd "$CFG" && sha256sum -c SHA256SUMS >/dev/null 2>&1) || fail manifest
TOKEN=$(sha256sum "$CFG/SHA256SUMS" | cut -d ' ' -f 1)

# 1. Registry stays in private tmpfs. The TEE's independent read-only physical
# persist mount (and its /persist alias) must not veto this setup: the broker
# translates DSP persist/sensors paths into ROOTD, never the physical mount.
# Do not inspect, bind over, relabel or change that other owner's filesystem.
while read -r dev point fs rest; do
	case "$point" in "$MNT"|"$MNT"/*) fail persist-already-mounted;; esac
done < "$PROC/mounts"
[ -d "$MNT" ] || mkdir -p "$MNT" || fail persist-dir
[ -z "$(ls -A "$MNT" 2>/dev/null)" ] || fail persist-dir-populated
mount -t tmpfs -o size=8m,mode=0700,nosuid,nodev,noexec tmpfs "$MNT" || fail persist-mount
n=0
while read -r dev point fs rest; do
	[ "$point" = "$MNT" ] || continue
	[ "$dev $fs" = "tmpfs tmpfs" ] || fail persist-not-tmpfs
	n=$((n+1))
done < "$PROC/mounts"
[ "$n" = 1 ] || fail persist-mount-count
timeout 10 tar -xf "$CFG/registry-seed.tar" -C "$MNT" || fail seed-extract
# registry.sha256 names absolute /mnt/vendor/persist/... paths; check them relative to / at $MNT
(cd / && sed "s#  /mnt/vendor/persist/#  ${MNT#/}/#" "$CFG/registry.sha256" | sha256sum -c >/dev/null 2>&1) || fail seed-hash
find "$MNT" \( -type l -o -type b -o -type c -o -type p -o -type s \) > "$LOGD/specials" || fail seed-scan
[ ! -s "$LOGD/specials" ] || fail seed-special
mkdir "$ROOTD" && chmod 0700 "$ROOTD" || fail root-create
mkdir -p "$ROOTD/persist" "$ROOTD/vendor/etc" "$ROOTD/sys/devices" || fail root-dirs
timeout 10 cp -R "$MNT/sensors" "$ROOTD/persist/sensors" || fail copy-registry
timeout 10 cp -R "$VND/etc/sensors" "$ROOTD/vendor/etc/sensors" || fail copy-config
timeout 10 cp -R "$CFG/soc0" "$ROOTD/sys/devices/soc0" || fail copy-soc0
find "$ROOTD" -type l > "$LOGD/specials" || fail root-scan
[ ! -s "$LOGD/specials" ] || fail root-link
log "persist: tmpfs seed + listener root staged"

# 2. firmware into firmware_class.path, verified
P=$SYS/module/firmware_class/parameters/path
FWD=$(cat "$P" 2>/dev/null)
if [ -z "$FWD" ]; then
	FWD=$DATA/local/tmp/a9fw
	{ echo -n "$FWD" > "$P"; } 2>/dev/null
	[ "$(cat "$P" 2>/dev/null)" = "$FWD" ] || fail firmware-path
fi
# The integrated bridge has qcom -> /vendor/firmware/qcom. Copying a directory
# over that symlink fails; it must never try to rewrite read-only vendor files.
# A verified persistent private copy is reusable too, without another write.
if (cd "$FWD" 2>/dev/null && sha256sum -c "$VND/firmware/a9adsp.SHA256SUMS" >/dev/null 2>&1); then
	log "firmware: existing tree matches ADSP manifest"
else
	[ ! -L "$FWD/qcom" ] || fail firmware-bridge-hash
	mkdir -p "$FWD" || fail firmware-dir
	cp -R "$VND/firmware/a9adsp/." "$FWD/" || fail firmware-copy
	(cd "$FWD" && sha256sum -c "$VND/firmware/a9adsp.SHA256SUMS" >/dev/null 2>&1) || fail firmware-hash
fi
log "firmware verified in $FWD"

# 3. modules (no kernel autoload helper), read back
[ -z "$(cat "$PROC/sys/kernel/modprobe" 2>/dev/null)" ] || fail modprobe-helper
[ -f "$M/modules.sensors" ] || fail no-module-list
. "$VND/bin/a9ml-module-load.sh" || fail module-loader
while read -r ko; do
	[ -n "$ko" ] || continue
	m=$(echo "${ko%.ko}" | tr - _)
	a9ml_module_load "$ko" "$SYS" "$M" || fail "insmod-$m"
done < "$M/modules.sensors"
[ "$(cat "$SYS/module/qcom_q6v5_pas/parameters/a9_adsp_manual_start" 2>/dev/null)" = Y ] || fail pas-gate
[ "$(cat "$SYS/module/fastrpc/parameters/a9_sensor_fastrpc" 2>/dev/null)" = Y ] || fail fastrpc-gate

# 4. the ADSP
RP= count=0
for p in "$SYS"/class/remoteproc/remoteproc*; do
	[ -e "$p/state" ] || continue
	# The integrated PAS module registers MPSS as well as ADSP. Count only
	# the ADSP-compatible node; leave the modem's state and recovery alone.
	of=$(readlink -f "$p/device/of_node") || continue
	tr '\000' '\n' < "$of/compatible" | grep -qx qcom,sm6115-adsp-pas || continue
	count=$((count+1)); RP=$p
done
[ "$count" = 1 ] || fail remoteproc-count
of=$(readlink -f "$RP/device/of_node") || fail of-node
[ "${of##*/}" = remoteproc@a400000 ] || fail wrong-node
tr '\000' '\n' < "$of/compatible" | grep -qx qcom,sm6115-adsp-pas || fail compatible
[ "$(cat "$RP/state")" = offline ] || fail "state-$(cat "$RP/state")"
{ echo disabled > "$RP/recovery"; echo disabled > "$RP/coredump"; } 2>/dev/null
[ "$(cat "$RP/recovery")" = disabled ] && [ "$(cat "$RP/coredump")" = disabled ] || fail recovery-coredump
STARTED=1
( echo start > "$RP/state" ) 2>/dev/null &
end=$(( $(uptime_s) + ${A9S_START_S:-30} ))
until [ "$(cat "$RP/state")" = running ]; do
	[ "$(uptime_s)" -lt "$end" ] || fail start-timeout
	case "$(cat "$RP/state")" in crashed) fail adsp-crashed;; esac
	sleep 0.2
done
log "ADSP running"

# 5. FastRPC device, then the confined registry listener in service mode
end=$(( $(uptime_s) + ${A9S_DEV_S:-10} ))
# (the listener itself pins the char device, its rdev and root ownership)
until [ -e "$DEV/fastrpc-adsp" ]; do [ "$(uptime_s)" -lt "$end" ] || fail no-fastrpc-device; sleep 0.2; done
BOOT=$(cat "$PROC/sys/kernel/random/boot_id")
: > "$LOGD/listener.log"
setsid "$VND/bin/s1-listener" --service "$BOOT" "$TOKEN" < /dev/null > "$LOGD/listener.log" 2>&1 &
LPID=$!
end=$(( $(uptime_s) + ${A9S_LISTENER_S:-10} ))
until grep -qx S1-LISTENER-READY "$LOGD/listener.log"; do
	kill -0 "$LPID" 2>/dev/null || fail listener-exited
	[ "$(uptime_s)" -lt "$end" ] || fail listener-not-ready
	sleep 0.2
done
log "listener ready (pid $LPID)"

# 6. SNS 400
"$VND/bin/s2-sns-probe" --wait 400 "${A9S_SNS_MS:-30000}" > "$LOGD/sns.log" 2>&1 || fail no-sns
setprop vendor.a9.sensors.ready 1
[ "$(getprop vendor.a9.sensors.ready)" = 1 ] || fail ready-prop
setstate up

# 7. supervise; never restart
while :; do
	sleep "${A9S_POLL:-5}"
	st=$(cat "$RP/state" 2>/dev/null)
	if [ "$st" != running ]; then
		setprop vendor.a9.sensors.ready 0
		setstate "down:adsp-$st"
		exit 0
	fi
	if ! kill -0 "$LPID" 2>/dev/null; then
		setprop vendor.a9.sensors.ready 0
		{ echo stop > "$RP/state"; } 2>/dev/null
		setstate down:listener-exited
		exit 0
	fi
done
