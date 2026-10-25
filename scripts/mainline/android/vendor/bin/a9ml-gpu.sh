#!/system/bin/sh
# a9ml-gpu.sh -- A9 mainline GPU mode (Adreno 610 + Mesa freedreno), used by
# init.a9ml-gpu.rc. Root, phhsu_daemon. INERT unless the vendor was built with
# build-vendor.sh --gpu (ro.vendor.a9ml.gpu=1). runs/gpu.md ("Review 27 fixes").
#
# FAIL CLOSED: Mesa is selected only when EVERY prerequisite was verified by
# readback and all three probes passed; anything else selects ANGLE
# explicitly (persist.graphics.egl=angle, read back), never "leave as is".
#
#   setup   (post-fs-data) prerequisites, each verified:
#           firmware_class.path (set to /data/local/tmp/a9fw when the cmdline
#           left it empty) reads back; the firmware copy passes sha256sum -c
#           against the vendor's SHA256SUMS; vkms.map_wc reads back Y. vkms
#           image only (ro.boot.a9ml.eink=0): no E Ink module may be loaded,
#           MDSS gets driver_override=none (read back) so msm.ko cannot bind
#           the display subsystem, modules.gpu is loaded, and then the GPU
#           clock controller, the GPU SMMU and the GPU must be BOUND, MDSS
#           must NOT be, msm.separate_gpu_kms=Y; then the qualification runs
#           HERE, before the composer (early-boot) or the allocator opens vkms.
#   select  (early-boot) E Ink image: gpucc, SMMU, GPU bound AND MDSS bound
#           to msm-mdss (an MDSS probe failure is a HARD STOP: it would leave
#           a9_eink without transport), msm.separate_gpu_kms=Y, then the
#           qualification (the composer uses the a9_eink card, not vkms).
# (G1 attempt 1, MEASURED: gpucc as a module let the SMMU and then adreno and
#  msm-mdss fail at the kernel's deferred-probe timeout; gpucc is built in
#  now and these bind checks turn any such failure into a stop.)
#           Both images: the selection.
# Qualification = a9-gpu-probe info, render, dmabuf (shared vkms dma-buf
# CPU<->GPU readback with fences), ONE attempt each, each under a hard
# timeout. A deadline guard reboots (sysrq b; PON beacon 2 -> bootloader
# fastboot, where the host supervisor restores slot a) if the qualification
# has not finished after A9ML_GPU_DEADLINE s (75, below the rc timeout_period 120): a probe stuck in the kernel
# would otherwise block every later opener of the GPU node (msm load_gpu
# mutex), minigbm included, i.e. a hung boot.
SYS=${A9ML_SYS:-/sys}; DEV=${A9ML_DEV:-/dev}; VND=${A9ML_VENDOR:-/vendor}; DATA=${A9ML_DATA:-/data}   # host test roots
SYSRQ=${A9ML_SYSRQ:-/proc/sysrq-trigger}
log() { echo "a9ml-gpu: $*" >> ${A9ML_KMSG:-/dev/kmsg}; }
M=${A9ML_MODDIR:-$VND/lib/modules}
LOG=$DATA/local/tmp/a9ml-gpu.log
QUAL=$DATA/local/tmp/a9ml-gpu.qual        # PASS | FAIL:<why>
PD=$SYS/bus/platform/devices
GPUCC=5990000.clock-controller GSMMU=59a0000.iommu GPUDEV=5900000.gpu MDSS=5e00000.display-subsystem
bound() { [ -e "$PD/$1/driver" ]; }
drvname() { bound "$1" || { echo -; return; }; d=$(readlink -f "$PD/$1/driver" 2>/dev/null); echo "${d##*/}"; }   # "-" = unbound (attempt 2 logged "driver")
# gpu_bound_why: empty when gpucc, the GPU SMMU and the GPU are all bound
gpu_bound_why() {
	w=""
	for d in $GPUCC $GSMMU $GPUDEV; do bound $d || w="${w}$d-unbound "; done
	echo "$w"
}
opts() { [ -f $M/modules.options ] && sed -n "s/^$1 //p" $M/modules.options; }
pbool() { case "$(cat "$1" 2>/dev/null)" in Y|y|1) echo Y ;; N|n|0) echo N ;; *) echo "" ;; esac; }
[ "$(getprop ro.vendor.a9ml.gpu)" = 1 ] || exit 0
EINK=$(getprop ro.boot.a9ml.eink)

qfail() { echo "FAIL:$1" > $QUAL; log "qualification FAIL: $1"; }
# qualify: one attempt, bounded; result in $QUAL
qualify() {
	mkdir -p ${LOG%/*}; : > $LOG
	: > $QUAL.running
	( sleep ${A9ML_GPU_DEADLINE:-75}
	  if [ -e $QUAL.running ]; then
		log "qualification still running after ${A9ML_GPU_DEADLINE:-75} s (GPU/TZ hang?): sysrq reboot -> bootloader fastboot"
		echo b > $SYSRQ
	  fi ) </dev/null >/dev/null 2>&1 &
	i=0
	while [ $i -lt ${A9ML_GPUWAIT:-100} ]; do
		for r in $SYS/class/drm/renderD*; do
			[ -e "$r" ] || continue
			case "$(readlink -f $r/device)" in */5900000.gpu) break 2 ;; esac
		done
		sleep 0.1; i=$((i+1))
	done
	log "render nodes after $((i/10)) s: $(ls $DEV/dri 2>/dev/null | tr '\n' ' ')"
	res=PASS
	for t in info render dmabuf; do
		timeout -s KILL ${A9ML_GPU_STEP:-20} $VND/bin/a9-gpu-probe $t >> $LOG 2>&1
		rc=$?
		[ $rc = 0 ] && grep -q "^A9GPU result PASS $t" $LOG && continue
		res="FAIL:$t(rc=$rc)"; break
	done
	rm -f $QUAL.running
	while read -r l; do log "$l"; done < $LOG
	echo "$res" > $QUAL
	log "qualification: $res"
}

case "$1" in
setup)
	why=""
	P=$SYS/module/firmware_class/parameters/path
	FWD=$(cat $P 2>/dev/null)
	if [ -z "$FWD" ]; then
		FWD=$DATA/local/tmp/a9fw
		{ echo -n "$FWD" > $P; } 2>/dev/null
		[ "$(cat $P 2>/dev/null)" = "$FWD" ] || why="${why}firmware_class.path "
	fi
	log "firmware_class.path=$(cat $P 2>/dev/null)"
	if mkdir -p "$FWD" && cp -R $VND/firmware/a9gpu/. "$FWD/" && (cd "$FWD" && sha256sum -c $VND/firmware/a9gpu.SHA256SUMS >/dev/null 2>&1); then
		log "GPU firmware copied to $FWD and verified ($(wc -l < $VND/firmware/a9gpu.SHA256SUMS) files)"
	else
		why="${why}firmware-copy/verify "
	fi
	W=$SYS/module/vkms/parameters/map_wc
	{ echo 1 > $W; } 2>/dev/null
	[ "$(pbool $W)" = Y ] && log "vkms.map_wc=Y (read back)" || why="${why}vkms.map_wc "
	if [ "$EINK" != 1 ]; then
		# G1 admission: the vkms image must never load the E Ink stack
		[ -e $SYS/module/a9_eink ] && why="${why}a9_eink-loaded "
		# the vkms image never binds the display subsystem (no dispcc, no E Ink)
		{ echo none > $PD/$MDSS/driver_override; } 2>/dev/null
		[ "$(cat $PD/$MDSS/driver_override 2>/dev/null)" = none ] || why="${why}mdss-driver_override "
		[ -z "$why" ] && while read -r ko; do
			[ -n "$ko" ] || continue
			insmod "$M/$ko" $(opts "$ko") 2>/dev/null && log "insmod $ko $(opts "$ko")" || log "insmod $ko failed (or loaded)"
		done < $M/modules.gpu
		[ "$(pbool $SYS/module/msm/parameters/separate_gpu_kms)" = Y ] || why="${why}msm-separate_gpu_kms "
		why="${why}$(gpu_bound_why)"
		bound $MDSS && why="${why}mdss-bound($(drvname $MDSS)) "
		log "bound: gpucc=$(drvname $GPUCC) smmu=$(drvname $GSMMU) gpu=$(drvname $GPUDEV) mdss=$(drvname $MDSS)"
	fi
	if [ -n "$why" ]; then
		setprop vendor.a9ml.gpu.setup "fail:${why% }"
		qfail "setup(${why% })"
		exit 0
	fi
	setprop vendor.a9ml.gpu.setup ok
	[ "$EINK" != 1 ] && qualify
	;;
select)
	if [ "$EINK" = 1 ] && [ "$(getprop vendor.a9ml.gpu.setup)" = ok ]; then
		log "bound: gpucc=$(drvname $GPUCC) smmu=$(drvname $GSMMU) gpu=$(drvname $GPUDEV) mdss=$(drvname $MDSS)"
		w=$(gpu_bound_why)
		if [ "$(drvname $MDSS)" != msm-mdss ]; then
			log "STOP: $MDSS not bound to msm-mdss: the a9_eink transport is missing"
			qfail "mdss-not-bound"
		elif [ "$(pbool $SYS/module/msm/parameters/separate_gpu_kms)" != Y ] || [ -n "$w" ]; then
			qfail "eink-modules(separate_gpu_kms ${w% })"
		else
			qualify
		fi
	fi
	q=$(cat $QUAL 2>/dev/null); [ -n "$q" ] || q="FAIL:no-qualification"
	want=angle state="angle:$q"
	if [ "$q" = PASS ]; then
		if [ "$(getprop ro.vendor.a9ml.gpu.egl)" = mesa ]; then want=mesa state=mesa; else state=angle:forced; fi
	fi
	setprop persist.graphics.egl $want
	got=$(getprop persist.graphics.egl)
	if [ "$got" != "$want" ]; then
		# never leave a Mesa selection behind a failed write
		setprop persist.graphics.egl angle
		state="angle:readback($got)"
	fi
	# Apps: GraphicsEnvironment (A16, flag use_query_angle_choice compiled OFF in the v7
	# GSI) turns ANGLE on for EVERY app whenever ro.hardware.egl=angle, whatever
	# persist.graphics.egl says; SurfaceFlinger's libEGL only reads persist.graphics.egl.
	# So ro.hardware.egl must be "mesa" for apps to leave ANGLE/SwiftShader. The vendor
	# build.prop does NOT define it in a GPU build (ro. is write-once): it is set here,
	# once, to match the selection. Mesa only when SF got Mesa AND the build allows apps
	# to use it (ro.vendor.a9ml.gpu.app_egl, default mesa; "angle" = the old behaviour).
	# If the write does not read back, init's follow-up (init.a9ml-gpu.rc) sets angle.
	hw=angle
	# eink-trial3b (review 74): apps get Mesa only if the qualification PASSED and the selection
	# is mesa (state), not on a readback of persist.graphics.egl alone: a failed qualification
	# plus a stale persist=mesa whose ANGLE write was refused must still give apps ANGLE.
	if [ "$q" = PASS ] && [ "$state" = mesa ] && [ "$(getprop persist.graphics.egl)" = mesa ] && [ "$(getprop ro.vendor.a9ml.gpu.app_egl)" = mesa ]; then hw=mesa; fi
	setprop ro.hardware.egl $hw
	[ "$(getprop ro.hardware.egl)" = "$hw" ] || log "ro.hardware.egl=$hw did not read back ('$(getprop ro.hardware.egl)'): init falls back to angle for apps"
	setprop vendor.a9ml.gpu.apps_egl "$(getprop ro.hardware.egl)"   # what apps will really use (informational; empty = write refused)
	log "EGL: $state (persist.graphics.egl=$(getprop persist.graphics.egl), ro.hardware.egl=$(getprop ro.hardware.egl) for apps)"
	setprop vendor.a9ml.gpu.state "$state"
	;;
*) echo "usage: $0 setup|select" >&2; exit 2 ;;
esac
exit 0
