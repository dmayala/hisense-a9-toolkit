#!/system/bin/sh
# a9ml-eink.sh -- phase-2 E Ink helper (init.a9ml-eink.rc). Root, phhsu_daemon.
#   setup   load /vendor/lib/modules (modules.load order), find the a9_eink card
#           BY PLATFORM PATH (/sys/devices/platform/eink-display/drm/card*), make
#           every other KMS card except vkms root-only (nobody may become DRM
#           master on the msm card: the a9_eink driver commits there), and set
#           vendor.hwc.drm.device for drm_hwcomposer. Gates stay closed.
#   open    E5c-style session. Refused unless: the waveform exists under
#           /data/local/tmp/a9fw (firmware_class.path); the kernel has the
#           safety params (patches 0021-0026); fp9929a.max_on_ms is its 20000
#           default (not changed here); vendor.a9ml.eink.bins names the bins
#           E5c admitted. Sets hv_max_ms 15000, bin_min/bin_max, hv_hold_ms 3000
#           (stock-like idle hold; explicit vendor.a9ml.eink.hv_hold=0 opts out),
#           prefill_frames 12 (or vendor.a9ml.eink.prefill_frames, 2..32),
#           verified by readback, init_flash 1, then panel gate, FP9929A gate,
#           a9_eink.enable.
#   close   a9_eink.enable 0 first (emergency stop), then the two gates.
SYS=${A9ML_SYS:-/sys}; DEV=${A9ML_DEV:-/dev}; PROC=${A9ML_PROC:-/proc} # host test roots
# pbool <file>: a kernel bool parameter reads "Y"/"N" (also accept 1/0); prints
# 1, 0, or nothing when unreadable. Writes always use 1/0. (Eink run 3: the old
# `= 0` compare read "N" as "already 1" and refused the open.)
pbool() {
	case "$(cat "$1" 2>/dev/null)" in
		Y|y|1) echo 1 ;; N|n|0) echo 0 ;; *) echo "" ;;
	esac
}
log() { echo "a9ml-eink: $*" >> ${A9ML_KMSG:-/dev/kmsg}; }
# The start time protects this one-shot read-only diagnostic from PID reuse.
# /proc/stat's comm may contain spaces or ')'; fields after its LAST ') ' are
# stable. No process name, PID, address or register value is logged.
msm_task_start() {
	msm_stat=$(cat "$PROC/$1/stat" 2>/dev/null) || return 1
	msm_fields=${msm_stat##*) }
	[ "$msm_fields" != "$msm_stat" ] || return 1
	set -- $msm_fields
	[ "$#" -ge 20 ] || return 1
	shift 19
	case "$1" in ''|*[!0-9]*) return 1 ;; esac
	echo "$1"
}
msm_slow_diag() {
	[ -n "$2" ] && [ "$(msm_task_start "$1")" = "$2" ] || return 0
	msm_state=$(sed -n 's/^State:[[:space:]]*\([A-Z]\).*/\1/p' "$PROC/$1/status" 2>/dev/null)
	[ "$msm_state" != Z ] || return 0
	case "$msm_state" in [A-Z]) ;; *) msm_state=unavailable ;; esac
	msm_wchan=$(cat "$PROC/$1/wchan" 2>/dev/null)
	case "$msm_wchan" in ''|*[!a-zA-Z0-9_.]*) msm_wchan=unavailable ;; esac
	# Strip stack addresses and offsets; at most 12 kernel symbol names.
	msm_stack=$(sed -n '1,12{s/^\[<[0-9a-fA-F]*>\] \([a-zA-Z0-9_][a-zA-Z0-9_.]*\)+0x[0-9a-fA-F]*\/0x[0-9a-fA-F]*.*$/\1/p;};12q' "$PROC/$1/stack" 2>/dev/null)
	[ "$(msm_task_start "$1")" = "$2" ] || return 0
	log "MSM_INSMOD slow after_ms=5000 state=$msm_state wchan=$msm_wchan"
	if [ -n "$msm_stack" ]; then
		for msm_symbol in $msm_stack; do log "MSM_INSMOD stack function=$msm_symbol"; done
	else
		log "MSM_INSMOD stack unavailable"
	fi
}
msm_insert() {
	log "MSM_INSMOD begin"
	insmod "$@" 2>/dev/null &
	msm_pid=$!
	msm_start=$(msm_task_start "$msm_pid")
	# Never delay, kill, retry or unload the insertion. The sidecar sleeps once,
	# reads only this task, then exits; init also owns its service process group.
	# The independent boot deadline remains the reset cap for an unkillable task.
	( sleep 5; msm_slow_diag "$msm_pid" "$msm_start" ) &
	if wait "$msm_pid"; then msm_rc=0; else msm_rc=$?; fi
	log "MSM_INSMOD end rc=$msm_rc"
	return "$msm_rc"
}
# Boot-only DRM diagnosis must end before any panel drive is admitted.
# Keep this quiet for the entire session; close must not restore the old mask.
quiet_drm_debug() {
	dbg=$SYS/module/drm/parameters/debug
	old=$(cat "$dbg" 2>/dev/null) || return 1
	echo 0 > "$dbg" || return 1
	[ "$(cat "$dbg" 2>/dev/null)" = 0 ] || return 1
	log "DRM debug $old -> 0 (read back)"
}
# card_kind <sysfs card dir>: a9_eink | vkms | msm | other:<driver>
card_kind() {
	r=$(readlink -f "$1" 2>/dev/null)
	case "$r" in
		*/devices/platform/eink-display/*) echo a9_eink; return ;;
		*/devices/faux/vkms/*) echo vkms; return ;;
	esac
	drv=$(readlink -f "$1/device/driver" 2>/dev/null); drv=${drv##*/}
	case "$drv" in
		vkms) echo vkms ;;
		a9-eink|a9_eink) echo a9_eink ;;
		msm*|msm_dpu|msm_mdss) echo msm ;;
		*) case "$r" in *display-controller*|*display-subsystem*) echo msm ;; *) echo "other:${drv:-none}" ;; esac ;;
	esac
}
# eink_status / sfield <key>: the a9_eink status attribute and one value from it
eink_status() { cat $SYS/class/drm/$(getprop vendor.a9ml.eink.card)-*/eink/status 2>/dev/null; }
sfield() { eink_status | tr ' \n' '\n\n' | awk -v k="$1" 'p==k {print; exit} {p=$0}'; }
# Kernel monotonic timestamps survive ring-buffer wrap; line offsets do not.
# Only a successful kernel starvation grant advances this floor.
kmsg_window() {
	floor=$(getprop vendor.a9ml.eink.recovered_at); [ -n "$floor" ] || floor=0
	# Generic severity checks apply only after this session's admission.
	# Literal fault strings retain the whole-boot floor until a kernel grant.
	if [ "${1:-}" = -r ]; then
		session_floor=$(getprop vendor.a9ml.eink.session_at)
		[ -n "$session_floor" ] && floor=$session_floor
	fi
	dmesg "$@" 2>/dev/null | awk -v floor="$floor" '
		match($0, /\[[ ]*[0-9]+\.[0-9]+\]/) {
			t=substr($0,RSTART,RLENGTH); gsub(/[][ ]/, "", t)
			if (t+0 > floor+0) print
		}'
}
recovery_field() {
	cat $SYS/class/drm/$(getprop vendor.a9ml.eink.card)-*/eink/stall_recovery 2>/dev/null |
	awk -v k="$1" '{for(i=1;i<NF;i++) if($i==k) {print $(i+1); exit}}'
}
# fault_now [kmsg_start_line]: print the first fault found and return 0, else 1.
#  - a9_eink stall_fault != 0 (patch 0026), rail_fault != 0 (patch 0039:
#    FP9929A device/API/hard-off failure with the pipeline on; the kernel already latched it),
#    commit_fail / aborts != 0
#  - the literal fault strings at any level, this whole boot
#  - with a start line (session running): any err-or-worse message from
#    fp9929a / a9-eink after it (before the push of the template the driver
#    logs one benign "no valid control template (-2)", so that generic rule
#    only applies inside a session)
fault_now() {
	tf=$(recovery_field transport_hard); [ "$tf" = 1 ] && { echo "transport completion hard fault"; return 0; }
	pf=$(recovery_field pmic_hard); [ "$pf" = 1 ] && { echo "FP9929A hard fault"; return 0; }
	sf=$(sfield stall_fault); [ -n "$sf" ] && [ "$sf" != 0 ] && { echo "a9_eink stall_fault $sf"; return 0; }
	rf=$(sfield rail_fault); [ -n "$rf" ] && [ "$rf" != 0 ] && { echo "a9_eink rail_fault $rf (pg_raw $(sfield rail_pg_raw) getter $(sfield rail_getter))"; return 0; }
	cf=$(sfield commit_fail); cb=$(getprop vendor.a9ml.eink.commit_base); [ -n "$cf" ] && [ "$cf" != "${cb:-0}" ] && { echo "a9_eink commit_fail $cf"; return 0; }
	ab=$(sfield aborts); ab0=$(getprop vendor.a9ml.eink.aborts_base); [ -n "$ab" ] && [ "$ab" != "${ab0:-0}" ] && { echo "a9_eink aborts $ab"; return 0; }
	l=$(kmsg_window | grep -E 'max_on_ms [0-9]+ expired|emergency off|fault latched|frame-progress fault|dropped out while the pipeline|update refused|power-up aborted|control template .*rejected|waveform.bin rejected' | tail -n1)
	[ -n "$l" ] && { echo "kmsg: ${l#*] }" | cut -c1-200; return 0; }
	if [ -n "${1:-}" ]; then
		l=$(kmsg_window -r | grep -E '^<[0-3]>' | grep -iE 'fp9929a|a9[-_]eink|eink-display|FP9929A' | tail -n1)
		[ -n "$l" ] && { echo "kmsg: ${l#*] }" | cut -c1-200; return 0; }
	fi
	return 1
}
P=${A9ML_SYS:-/sys}/module
FW=${A9ML_FWDIR:-/data/local/tmp/a9fw}/hisense/a9-waveform.bin
CT=${A9ML_FWDIR:-/data/local/tmp/a9fw}/hisense/a9-ctrl-template.bin   # patch 0028: no template, no drive
# Numeric context only. Never copy arbitrary sysfs/property strings to kmsg.
fault_number() {
	context_number=$(cat "$1" 2>/dev/null)
	case "$context_number" in ''|*[!0-9]*) echo unavailable; return ;; esac
	[ "${#context_number}" -le 10 ] && echo "$context_number" || echo unavailable
}
fault_context() {
	# At a waveform-end rail latch this is the completed waveform's context;
	# at an earlier power/rail failure it is only the previous update. Keep the
	# label explicit. A later refused update does not overwrite the last row.
	context_update=$(awk '$1=="last" {
		for(i=2;i<NF;i+=2)
			if ($i ~ /^(mode|pre|clear|type|bin|t1|t2|frames|steps|dry|aborted|rect|begin_us|total_us|prep_us)$/ &&
			    $(i+1) ~ /^(-?[0-9]+|0[xX][0-9a-fA-F]+|[0-9]+,[0-9]+-[0-9]+,[0-9]+)$/)
				printf "%s %s ", $i, $(i+1)
		exit
	}' "$bundle/status.txt")
	log "fault snapshot $seq/3 last_update: ${context_update:-unavailable}"
	context_limits=""
	for key in pg_dip_policy hv_hold_ms hv_max_ms; do
		context_limits="$context_limits $key $(fault_number "$P/a9_eink/parameters/$key")"
	done
	log "fault snapshot $seq/3 limits:$context_limits"
	# This driver's brightness_get returns a cached level, without I2C access.
	# Fixed board labels only; no enumeration of arbitrary LED/device names.
	for channel in a9:warm:center a9:white:center a9:warm:edge a9:white:edge; do
		log "fault snapshot $seq/3 frontlight: $channel $(fault_number "$SYS/class/leds/$channel/brightness")"
	done
	context_wifi=""
	for service in a9ml_wifi_session vendor.a9ml_wifi_hal wpa_supplicant; do
		context_state=$(getprop "init.svc.$service")
		case "$context_state" in running|stopped|stopping|restarting) ;; *) context_state=unavailable ;; esac
		context_wifi="$context_wifi $service $context_state"
	done
	# Service liveness is NOT association or traffic/RF power evidence.
	log "fault snapshot $seq/3 wifi_services:$context_wifi"
}
# Capture a fault before any retry/backoff. Full dmesg and FULL status, private
# files, at most three bundles per boot. Kernel snapshot preserves the original
# phase even after a neutral commit overwrites the live profile.
snapshot_fault() {
	base=${A9ML_FAULT_DIR:-/data/local/tmp/a9ml-eink-faults}
	seq=$(getprop vendor.a9ml.eink.snapshot_count)
	case "$seq" in ''|0) seq=1 ;; 1) seq=2 ;; 2) seq=3 ;; *) return ;; esac
	setprop vendor.a9ml.eink.snapshot_count "$seq"
	umask 077
	mkdir -p "$base" || return
	bundle=$base/$(date -u +%Y%m%dT%H%M%SZ)-$$-$seq
	mkdir "$bundle" || return
	# Capture the ring FIRST. Do not filter/truncate away unrelated DPU/IRQ
	# errors; no raw dmesg contents are copied into public properties or kmsg.
	dmesg > "$bundle/dmesg.txt" 2> "$bundle/dmesg.err"
	{
		echo "reason=$1"
		echo "utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		echo "uptime=$(cat "$PROC/uptime" 2>/dev/null)"
		echo "gate=$(getprop vendor.a9ml.eink.gate)"
		echo "fault=$(getprop vendor.a9ml.eink.fault)"
		echo "audioserver=$(getprop init.svc.audioserver)"
		echo "bins_requested=$(getprop vendor.a9ml.eink.bins)"
		for q in a9_eink/enable fp9929a/allow_power_on panel_hisense_hlte556n_eink/enable \
		         a9_eink/hv_max_ms fp9929a/max_on_ms a9_eink/bin_min a9_eink/bin_max; do
			echo "$q=$(cat "$P/${q%/*}/parameters/${q#*/}" 2>/dev/null)"
		done
	} > "$bundle/state.txt"
	eink_status > "$bundle/status.txt"
	for a in stall_snapshot stall_recovery rail_diag; do
		cat $SYS/class/drm/$(getprop vendor.a9ml.eink.card)-*/eink/$a > "$bundle/$a.txt" 2>/dev/null
	done
	fault_context
	# Preserve the controller-owned numeric summary in the kernel ring too:
	# /data is RAM on these boots and the private bundle cannot survive reboot.
	log "fault snapshot $seq/3 status: $(awk '{for(i=1;i<NF;i++) if($i ~ /^(enable|waveform|template|updates|frames|dry|aborted|stall_fault|rail_fault|commit_fail|aborts)$/) printf "%s %s ", $i, $(i+1)} END {print ""}' "$bundle/status.txt")"
	for part in stall_snapshot stall_recovery rail_diag; do
		while IFS= read -r summary; do log "fault snapshot $seq/3 $part: $summary"; done < "$bundle/$part.txt"
	done
	for a in cpu io memory; do cat "$PROC/pressure/$a" > "$bundle/pressure-$a.txt" 2>/dev/null; done
	# Bounded selected kernel tasks only, no process argv/environment.
	n=0
	for task in "$PROC"/[0-9]*; do
		comm=$(cat "$task/comm" 2>/dev/null) || continue
		case "$comm" in a9_eink|a9_eink_r|atomic-worker-*|crtc_commit*|msm_*commit*) ;; *) continue ;; esac
		{
			echo "task=${task##*/} comm=$comm"
			for a in wchan schedstat stack; do echo "$a:"; cat "$task/$a" 2>/dev/null; done
		} >> "$bundle/tasks.txt"
		n=$((n+1)); [ "$n" -lt 16 ] || break
	done
	log "fault snapshot saved ($seq/3)"
}
# Recover only a typed starvation shutdown. No writes to either clear_fault.
# All gates stay closed until the kernel acknowledges quiescence and budget.
recovery_error() {
	# Property values have a small Android limit; keep the full reason in kmsg.
	setprop vendor.a9ml.eink.open_error "$(printf '%s' "$*" | cut -c1-80)"
	log "recovery failed stage=$(getprop vendor.a9ml.eink.recovery_stage): $*"
	return 1
}
recover_starvation() {
	setprop vendor.a9ml.eink.open_error ""
	setprop vendor.a9ml.eink.recovery_stage checking
	case "$(recovery_field kind)" in 1|3) ;; *) recovery_error "no pending typed starvation"; return 1 ;; esac
	retry_before=$(recovery_field retries)
	grant_before=$(recovery_field grants)
	case "$grant_before" in ''|*[!0-9]*) recovery_error "missing kernel grant receipt"; return 1 ;; esac
	case "$retry_before" in 0|1) ;; *) recovery_error "retry budget unavailable"; return 1 ;; esac
	wait_ms=$(recovery_field backoff_ms)
	case "$wait_ms" in 5000) delay=5 ;; 30000) delay=30 ;; *) recovery_error "invalid backoff"; return 1 ;; esac
	setprop vendor.a9ml.eink.gate recovering
	sh "$0" close || return 1
	setprop vendor.a9ml.eink.recovery_stage backoff
	sleep "$delay"
	[ "$(getprop vendor.a9ml.eink.gate)" = recovering ] || return 1
	RS=$(ls $SYS/class/drm/$(getprop vendor.a9ml.eink.card)-*/eink/recover_stall 2>/dev/null | head -n1)
	# Advance the old-log floor only after the grant, but save its timestamp
	# BEFORE the write so a new error after the grant cannot be hidden by it.
	stamp=$(dmesg | sed -n 's/.*\[ *\([0-9]*\.[0-9]*\)\].*/\1/p' | tail -n1)
	case "$stamp" in ''|*[!0-9.]*) recovery_error "no monotonic log floor"; return 1 ;; esac
	setprop vendor.a9ml.eink.recovery_stage kernel-request
	[ -n "$RS" ] && echo 1 > "$RS" || { recovery_error "kernel grant write failed"; return 1; }
	# The successful synchronous request RESETS kind to0. Check its receipt,
	# not the pre-grant kind; never spend a second grant to authorize admission.
	[ "$(recovery_field kind)" = 0 ] &&
	[ "$(recovery_field result)" = 0 ] &&
	[ "$(recovery_field grants)" = "$((grant_before+1))" ] || { recovery_error "kernel grant readback mismatch"; return 1; }
	[ "$(getprop vendor.a9ml.eink.gate)" = recovering ] || return 1
	setprop vendor.a9ml.eink.recovered_at "$stamp"
	setprop vendor.a9ml.eink.commit_base "$(sfield commit_fail)"
	setprop vendor.a9ml.eink.aborts_base "$(sfield aborts)"
	setprop vendor.a9ml.eink.fault ""
	setprop vendor.a9ml.eink.recovery_stage admission
	log "starvation recovery grant $((retry_before+1))/2; reopening with normal temperature/bin admission"
	[ "$(getprop vendor.a9ml.eink.gate)" = recovering ] || return 1
	sh "$0" open recovery || { recovery_error "open: $(getprop vendor.a9ml.eink.open_error)"; return 1; }
	# updates includes dry runs. Require real transport frames AND a completed
	# non-dry update; a dry computation alone never establishes glass progress.
	progress_updates=$(sfield updates); progress_frames=$(sfield frames)
	case "$progress_updates:$progress_frames" in *[!0-9:]*|:*|*:) recovery_error "missing progress counters"; return 1 ;; esac
	setprop vendor.a9ml.eink.recovery_stage driven-update
	FC=$(ls $SYS/class/drm/$(getprop vendor.a9ml.eink.card)-*/eink/force_clear 2>/dev/null | head -n1)
	[ -n "$FC" ] && echo 1 > "$FC" || { recovery_error "post-open clear request failed"; return 1; }
	j=0
	while [ "$j" -lt 40 ]; do
		[ "$(getprop vendor.a9ml.eink.gate)" = open ] || return 1
		[ "$(pbool $P/a9_eink/parameters/enable)$(pbool $P/fp9929a/parameters/allow_power_on)$(pbool $P/panel_hisense_hlte556n_eink/parameters/enable)" = 111 ] || { recovery_error "gates closed before driven progress"; return 1; }
		progress_fault=$(fault_now "$(getprop vendor.a9ml.eink.kmsg_start)") && { recovery_error "$progress_fault"; return 1; }
		if [ "$(sfield dry)" = 0 ] && [ "$(sfield aborted)" = 0 ] &&
		   [ "$(sfield updates)" -gt "$progress_updates" ] && [ "$(sfield frames)" -gt "$progress_frames" ]; then
			setprop vendor.a9ml.eink.recovery_stage complete
			log "recovery driven progress: updates $progress_updates -> $(sfield updates), frames $progress_frames -> $(sfield frames), dry 0 aborted 0"
			return 0
		fi
		sleep 0.5; j=$((j+1))
	done
	recovery_error "no completed driven update after 40 polls"
}

case "$1" in
recover)
	if recover_starvation; then exit 0; fi
	sh "$0" close
	[ "$(getprop vendor.a9ml.eink.gate)" = closed ] && exit 1
	setprop vendor.a9ml.eink.fault "$(getprop vendor.a9ml.eink.open_error | cut -c1-80)"
	setprop vendor.a9ml.eink.gate faulted
	exit 1
	;;
setup)
	[ "$(getprop ro.boot.a9ml.eink)" = 1 ] || exit 0
	M=${A9ML_MODDIR:-/vendor/lib/modules}
	[ -f $M/modules.load ] || { log "no $M/modules.load: vendor without E Ink modules"; exit 0; }
	# modules.load order (snapshot-kernel.sh): dispcc-sm6115 first (msm-mdss
	# needs its clocks: eink run 4 had no dispcc, msm-mdss hit the deferred-probe
	# timeout and failed -110), msm, panel, tc358767; before a9_eink the msm
	# DRM card must exist (the a9_eink transport commits there), at most 30 s.
	while read -r ko; do
		[ -n "$ko" ] || continue
		if [ "$ko" = a9_eink.ko ]; then
			j=0
			while [ $j -lt ${A9ML_MSMWAIT:-300} ]; do
				for c in $SYS/class/drm/card[0-9]*; do [ "$(card_kind "$c")" = msm ] && break 2; done
				sleep 0.1; j=$((j+1))
			done
			msmc=""; for c in $SYS/class/drm/card[0-9]*; do [ "$(card_kind "$c")" = msm ] && msmc=${c##*/}; done
			[ -n "$msmc" ] && log "msm DRM card $msmc present after $((j/10)) s" \
				|| log "WARNING no msm DRM card after $((j/10)) s: a9_eink will have no transport (updates dropped, gates refuse)"
		fi
		# per-module insmod arguments (GPU vendor only: msm.ko separate_gpu_kms=1,
		# so a GPU bind failure can never hold up the msm KMS card; runs/gpu.md)
		o=""; [ -f $M/modules.options ] && o=$(sed -n "s/^$ko //p" $M/modules.options)
		if [ "$ko" = msm.ko ]; then
			msm_insert "$M/$ko" $o; rc=$?
		else
			insmod "$M/$ko" $o 2>/dev/null; rc=$?
		fi
		if [ "$rc" = 0 ]; then
			log "insmod $ko${o:+ $o}"
		else
			log "insmod $ko FAILED (or built in / loaded)"
		fi
	done < $M/modules.load
	i=0; C=""
	while [ $i -lt ${A9ML_WAIT:-100} ]; do
		for c in $SYS/class/drm/card[0-9]*; do
			[ "$(card_kind "$c")" = a9_eink ] && { C=$c; break; }
		done
		[ -n "$C" ] && [ -e $DEV/dri/${C##*/} ] && break
		C=""; sleep 0.1; i=$((i+1))
	done
	[ -n "$C" ] || { log "no a9_eink card after 10 s: hwcomposer stays on vkms"; exit 0; }
	card=${C##*/}
	# classify every KMS card by its sysfs device path / bound driver, never
	# by number: vkms (a faux device, no DRIVER= in uevent: boot eink-2 locked
	# it by mistake and minigbm died) and unknown cards stay as ueventd made
	# them; only the msm card (the a9_eink driver's transport: nobody else may
	# become DRM master there) is made root-only.
	for d in $SYS/class/drm/card[0-9]*; do
		n=${d##*/}; case "$n" in *-*) continue ;; esac
		k=$(card_kind "$d")
		case "$k" in
			msm) chown root root $DEV/dri/$n; chmod 0600 $DEV/dri/$n; log "/dev/dri/$n (msm) made root-only" ;;
			*) log "/dev/dri/$n ($k) left as is" ;;
		esac
	done
	for a in $SYS/class/drm/$card-*/eink/*; do
		# Diagnostic input and manual-clear events retain root ownership.
		case "${a##*/}" in test_pattern|manual_clear|recover_stall) continue ;; esac
		[ -e "$a" ] && chown system system "$a"
	done
	# timing (run 5: late 59, late_drive 11, max_gap_drive 25.5 ms with SF and
	# SwiftShader on the same cores): the TCON worker [a9_eink] and msm's
	# commit workers run SCHED_FIFO so frame pacing beats userspace load.
	# The render producer must remain SCHED_OTHER/nice -10: an equal-priority
	# FIFO producer on the same CPU would delay a waking commit consumer.
	for pid in $(ls "$PROC" | grep -E '^[0-9]+$'); do
		c=$(cat "$PROC/$pid/comm" 2>/dev/null) || continue
		case "$c" in
			a9_eink_r)
				if chrt -o -p 0 "$pid" >/dev/null 2>&1 && renice -n -10 -p "$pid" >/dev/null 2>&1; then
					log "SCHED_OTHER nice -10: $c ($pid)"
				else
					log "WARNING cannot set SCHED_OTHER nice -10: $c ($pid)"
				fi ;;
			a9_eink) chrt -f -p 50 "$pid" >/dev/null 2>&1 && log "SCHED_FIFO 50: $c ($pid)" ;;
			crtc_commit*|crtc_event*|msm_*commit*) chrt -f -p 49 $pid >/dev/null 2>&1 && log "SCHED_FIFO 49: $c ($pid)" ;;
		esac
	done
	setprop vendor.hwc.drm.device /dev/dri/$card
	setprop vendor.a9ml.eink.card "$card"
	setprop vendor.a9ml.eink.ready 1
	log "a9_eink = /dev/dri/$card ($C); drm_hwcomposer pointed at it; gates closed"
	;;
open)
	refuse() { setprop vendor.a9ml.eink.open_error "$(printf '%s' "$*" | cut -c1-80)"; log "open refused: $*"; [ "$(getprop vendor.a9ml.eink.gate)" = faulted ] || setprop vendor.a9ml.eink.gate refused; exit 1; }
	# A property trigger may arrive after recover opened the gates directly.
	if [ "$(getprop vendor.a9ml.eink.gate)" = open ] &&
	   [ "$(pbool $P/a9_eink/parameters/enable)$(pbool $P/fp9929a/parameters/allow_power_on)$(pbool $P/panel_hisense_hlte556n_eink/parameters/enable)" = 111 ]; then
		f=$(fault_now) && refuse "$f"
		setprop vendor.a9ml.eink.watch 1
		exit 0
	fi
	# Hard faults require explicit intervention, never a clear_fault here.
	[ -z "$(getprop vendor.a9ml.eink.fault)" ] || refuse "fault latched this boot: $(getprop vendor.a9ml.eink.fault)"
	f=$(fault_now) && refuse "fault present before opening: $f"
	[ -s "$FW" ] || refuse "no waveform at $FW"
	[ -s "$CT" ] || refuse "no control template at $CT"
	# the status must carry the 0028-0030 fields, then a dry-run update (enable
	# is 0: computed and hashed, nothing committed, no HV) makes the driver load
	# both blobs; both must be accepted before any gate opens
	for k in template stall_fault late_drive max_gap_drive_us commit_fail rail_fault; do
		[ -n "$(sfield $k)" ] || refuse "a9_eink status has no '$k' (kernel lacks patches 0026-0030 / 0039)"
	done
	for g in a9_eink/enable panel_hisense_hlte556n_eink/enable fp9929a/allow_power_on; do
		v=$(pbool $P/${g%%/*}/parameters/${g#*/})
		[ "$v" = 0 ] || refuse "gate $g is '${v:-unreadable}' before opening (want closed)"
	done
	# Review 19: select and verify admission credit while every gate is closed.
	# The optional property is for future qualification; accept canonical decimal.
	K=$(getprop vendor.a9ml.eink.prefill_frames) || refuse "cannot read prefill_frames property"
	[ -n "$K" ] || K=12
	case "$K" in
		[2-9]|[12][0-9]|3[0-2]) ;;
		*) refuse "bad vendor.a9ml.eink.prefill_frames '$K' (want 2..32)" ;;
	esac
	PF=$P/a9_eink/parameters/prefill_frames
	[ -e "$PF" ] || refuse "kernel lacks a9_eink/prefill_frames (patch 0038 missing)"
	echo "$K" > "$PF" || refuse "prefill_frames write failed"
	actual=$(cat "$PF" 2>/dev/null) || refuse "prefill_frames readback failed"
	[ "$actual" = "$K" ] || refuse "prefill_frames readback '$actual', expected $K"
	log "prefill_frames $K (read back, gates closed)"
	quiet_drm_debug || refuse "cannot disable DRM debug before opening drive gates"
	# Camera comparison boot52: six ordinary waveform-end PG dips recovered
	# in 27-28ms, then the seventh hit the six-token HV-time bucket. PG was
	# high again before ordered shutdown; this was not a 100ms settle timeout.
	# Both pieces must implement stock PG telemetry before gates open.
	[ "$(cat "$P/a9_eink/parameters/pg_dip_policy" 2>/dev/null)" = 1 ] &&
	[ "$(cat "$P/fp9929a/parameters/pg_dip_policy" 2>/dev/null)" = 1 ] || refuse "stock PG policy missing: refresh kernel and E Ink module"
	log "PG dips: stock telemetry only; no qualification, quota or storm latch"

	# dry run: one forced update makes the worker (re)load the waveform and the
	# template (a9_load_waveform runs at every kicked update until both load)
	FC=$(ls $SYS/class/drm/$(getprop vendor.a9ml.eink.card)-*/eink/force_clear 2>/dev/null | head -n1)
	[ -n "$FC" ] || refuse "no force_clear attribute"
	echo 1 > "$FC" || refuse "force_clear write failed (no worker?)"
	i=0; while [ $i -lt 30 ] && { [ "$(sfield template)" != 1 ] || [ "$(sfield waveform)" != 1 ]; }; do sleep 0.5; i=$((i+1)); done
	[ "$(sfield waveform)" = 1 ] || refuse "waveform not loaded after a dry-run update"
	[ "$(sfield template)" = 1 ] || refuse "control template not loaded after a dry-run update"
	# Loaded status is authoritative. Its historical printk can have rotated
	# out of dmesg; absence of that old line cannot veto a granted recovery.
	log "pre-open: waveform 1 template 1, counters: $(eink_status | head -n1)"
	# the session's kmsg window starts here, before the first gate opens
	setprop vendor.a9ml.eink.kmsg_start $(( $(dmesg -r 2>/dev/null | wc -l) + 1 ))
	setprop vendor.a9ml.eink.session_at "$(dmesg | sed -n 's/.*\[ *\([0-9]*\.[0-9]*\)\].*/\1/p' | tail -n1)"
	# fail closed unless the kernel carries the E Ink safety patches the
	# diskless sequencer relies on (0021-0026: gate interlock,
	# per-session max_on_ms, min_off_ms, frame-progress interlock + bins)
	for q in fp9929a/max_on_ms fp9929a/min_off_ms \
	         a9_eink/bin_min a9_eink/bin_max a9_eink/hv_max_ms a9_eink/hv_hold_ms a9_eink/init_flash a9_eink/enable; do
		[ -e $P/${q%%/*}/parameters/${q#*/} ] || refuse "kernel lacks $q (safety patch missing)"
	done
	# HV caps: the kernel's fp9929a.max_on_ms (default 20000) is the binding
	# per-session limit and is NOT changed here; a9_eink.hv_max_ms must stay
	# below it (sequencer: 15000)
	FPMAX=$(cat $P/fp9929a/parameters/max_on_ms)
	[ "$FPMAX" = 20000 ] || refuse "fp9929a.max_on_ms is $FPMAX, expected the 20000 default"
	echo 15000 > $P/a9_eink/parameters/hv_max_ms
	[ "$(cat $P/a9_eink/parameters/hv_max_ms)" = 15000 ] || refuse "a9_eink.hv_max_ms not set"
	# bin admission: only the temperature bins the E5c run admitted
	# (vendor.a9ml.eink.bins=lo-hi, set by android-boot.sh --bins)
	B=$(getprop vendor.a9ml.eink.bins); lo=${B%-*}; hi=${B#*-}
	case "$lo$hi" in ''|*[!0-9]*) refuse "no vendor.a9ml.eink.bins (lo-hi)" ;; esac
	[ "$lo" -le "$hi" ] && [ "$hi" -le 13 ] || refuse "bad bins $B"
	echo "$lo" > $P/a9_eink/parameters/bin_min; echo "$hi" > $P/a9_eink/parameters/bin_max
	[ "$(cat $P/a9_eink/parameters/bin_min)-$(cat $P/a9_eink/parameters/bin_max)" = "$lo-$hi" ] || refuse "bins not set"
	# Stock keeps the pipeline open for roughly 2-3s after its queue empties.
	# mainline3 with hold=0 repeatedly cold-started the bridge; shared-bus NACKs
	# and arbitration loss then made FP9929A VCOM programming fail (-EAGAIN).
	# Reuse the existing kernel idle hold, measured from real transport output.
	# The 15s/20s absolute HV limits and independent fault checks still apply.
	# Keep an explicit opt-out for short diagnostics; "default" also resets a
	# stale zero rather than assuming the kernel parameter is still at boot's value.
	case "$(getprop vendor.a9ml.eink.hv_hold)" in
		0) idle_hold=0 ;;
		*) idle_hold=3000 ;;
	esac
	echo "$idle_hold" > "$P/a9_eink/parameters/hv_hold_ms" || refuse "cannot set idle hold"
	[ "$(cat "$P/a9_eink/parameters/hv_hold_ms")" = "$idle_hold" ] || refuse "idle hold readback mismatch"
	echo 1 > $P/a9_eink/parameters/init_flash
	[ "$(pbool $P/a9_eink/parameters/init_flash)" = 1 ] || refuse "init_flash not set"
	# Admission can wait for a dry-run job. A close during that wait cancels
	# automatic recovery too; do not turn a user's close into a later reopen.
	if [ "${2:-}" = recovery ] && [ "$(getprop vendor.a9ml.eink.gate)" != recovering ]; then
		log "starvation recovery cancelled during admission"
		exit 1
	fi
	echo 1 > $P/panel_hisense_hlte556n_eink/parameters/enable &&
	[ "$(pbool $P/panel_hisense_hlte556n_eink/parameters/enable)" = 1 ] &&
	{ echo 1 > $P/fp9929a/parameters/allow_power_on; } &&
	[ "$(pbool $P/fp9929a/parameters/allow_power_on)" = 1 ] &&
	{ echo 1 > $P/a9_eink/parameters/enable; } &&
	[ "$(pbool $P/a9_eink/parameters/enable)" = 1 ] &&
	{ log "gates OPEN (panel, fp9929a, a9_eink.enable), hv_hold_ms $(cat $P/a9_eink/parameters/hv_hold_ms)"; setprop vendor.a9ml.eink.gate open; setprop vendor.a9ml.eink.watch 1; exit 0; }
	log "open failed part-way: closing"; "$0" close; exit 1
	;;
drmdebug-off)
	# Run shell expansion here, not inside an init.rc service command: Android
	# init interprets dollar expressions in service arguments before exec.
	s=60
	for w in $(cat "$PROC/cmdline"); do
		case "$w" in a9ml.drmdebug_s=*) s=${w#*=} ;; esac
	done
	case "$s" in ''|*[!0-9]*) log "invalid a9ml.drmdebug_s: $s"; exit 1 ;; esac
	sleep "$s"
	quiet_drm_debug || { log "WARNING cannot disable boot DRM debug"; exit 1; }
	;;
watch)
	# runs while the gates are open: any fault -> close at once, latch the
	# reason in vendor.a9ml.eink.fault, gate=faulted. Faults: a9_eink
	# stall_fault (patch 0026 frame-progress interlock), rail_fault (0039), fp9929a max_on_ms /
	# emergency-off latch or a refused power-up (kmsg), a9_eink worker stop.
	start=$(getprop vendor.a9ml.eink.kmsg_start); [ -n "$start" ] || start=1
	log "fault watch armed (kmsg from line $start)"
	while [ "$(getprop vendor.a9ml.eink.gate)" = open ]; do
		if watch_fault=$(fault_now "$start"); then
			sh "$0" close
			snapshot_fault "$watch_fault"
			[ "$(getprop vendor.a9ml.eink.gate)" = closed ] && exit 0
			case "$(recovery_field kind)" in
			1|3) if recover_starvation; then
				start=$(getprop vendor.a9ml.eink.kmsg_start)
				continue
			fi ;;
			esac
			sh "$0" close
			[ "$(getprop vendor.a9ml.eink.gate)" = closed ] && exit 0
			admission_fault=$(getprop vendor.a9ml.eink.open_error)
			[ -n "$admission_fault" ] && watch_fault="$watch_fault; $admission_fault"
			setprop vendor.a9ml.eink.fault "$(printf '%s' "$watch_fault" | cut -c1-80)"
			setprop vendor.a9ml.eink.gate faulted
			log "FAULT: $watch_fault -> gates closed, latched for this boot (no clear_fault)"
			exit 1
		fi
		sleep 1
	done
	log "fault watch ended (gate $(getprop vendor.a9ml.eink.gate))"
	;;
close)
	echo 0 > $P/a9_eink/parameters/enable 2>/dev/null
	echo 0 > $P/fp9929a/parameters/allow_power_on 2>/dev/null
	echo 0 > $P/panel_hisense_hlte556n_eink/parameters/enable 2>/dev/null
	echo 0 > $P/a9_eink/parameters/bin_min 2>/dev/null; echo 13 > $P/a9_eink/parameters/bin_max 2>/dev/null
	st="$(pbool $P/a9_eink/parameters/enable)$(pbool $P/fp9929a/parameters/allow_power_on)$(pbool $P/panel_hisense_hlte556n_eink/parameters/enable)"
	[ "$st" = 000 ] && log "gates closed (read back), bins back to 0-13" || log "WARNING gates read back '$st' after close (want 000)"
	;;
esac
