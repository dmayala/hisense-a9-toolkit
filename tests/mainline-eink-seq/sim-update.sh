#!/bin/sh
# one simulated a9_eink update (see sim-lib.sh)
. "$SIM_LIB"
P=$R/sys/module/a9_eink/parameters
if [ "$(ctr wf)" = 0 ]; then
	setc wf 1
	klog 6 "a9-eink eink-display: waveform tables loaded (composer 8c89557b...)"
	if ! scen old-kernel; then
		if scen bad-template; then
			klog 3 "a9-eink eink-display: no valid control template (-22): refusing to drive"
			addc aborts 1; status; exit 0
		fi
		klog 6 "a9-eink eink-display: control template loaded (sha256 a88ae8ef...)"
		setc tpl 1
	fi
	klog 6 "a9-eink eink-display: transport on msm: 448x829 XR24 pitch 1792, frame period 11764 us"
	setc tx 1
fi
# what msm/DPU print on the client's first open and modeset (boot 8, MEASURED):
# a uevent record with dictionary lines, and two harmless *_once warnings
if [ ! -e $S/modeset-seen ]; then
	touch $S/modeset-seen
	klog 6 "msm_dsi 5e94000.dsi: bound"; kdict "SUBSYSTEM=platform"; kdict "DEVICE=+platform:5e94000.dsi"
	klog 3 "msm_dpu 5e01000.display-controller: no GPU device was found"
	klog 4 "[drm:programmable_fetch_get_num_lines:199] low vbp+vfp may lead to perf issues in some cases"
fi
if [ "$(param a9_eink enable)" = N ]; then  # dry run
	sleep 0.5
	cp $R/fix/grey $R/sys/kernel/debug/a9_eink/grey; cp $R/fix/levels_before $R/sys/kernel/debug/a9_eink/levels_before
	cp $R/fix/last $R/sys/kernel/debug/a9_eink/last
	echo "mode 0x203 pre 0x203 clear 1 type 2 bin 10 t1 30 t2 30 frames 60 steps 1 dry 1 aborted 0" > $S/last1
	addc updates 1; status; exit 0
fi
setc hv_on 1; status
if [ "$(param panel_hisense_hlte556n_eink enable)" = N ]; then
	klog 4 "panel-hisense-hlte556n-eink panel: refusing to power the E Ink panel"
	klog 3 "a9-eink eink-display: FP9929A is not on after enable: no frames sent (gates?)"
	addc aborts 1; setc hv_on 0; status; exit 0
fi
if [ "$(param fp9929a allow_power_on)" = N ]; then
	klog 4 "fp9929a 0-0048: power-up refused: boot with fp9929a.allow_power_on=1 to allow it"
	klog 3 "panel-hisense-hlte556n-eink panel: FP9929A power-up failed: -1"
	klog 4 "panel-hisense-hlte556n-eink panel: Skipping unprepare of already unprepared panel"
	video 1
	# as boot 11 (MEASURED): ID from the regmap cache, then the whole table NACKs
	klog 6 "tc358767 0-000f: before init sequence: ID read 0, ID 0x6601"
	k=0; while [ $k -lt 25 ]; do klog 3 "tc358767 0-000f: init seq #$k reg 0x448: -6"; k=$((k + 1)); done
	klog 3 "tc358767 0-000f: main link stream start error: -6"
	scen bridge-retry && { klog 3 "tc358767 0-000f: init seq #0 reg 0x448: -6"; klog 3 "tc358767 0-000f: main link stream start error: -6"; }
	sleep 0.05
	klog 3 "a9-eink eink-display: FP9929A is not on after enable: no frames sent (panel_hisense_hlte556n_eink.enable / fp9929a.allow_power_on?)"
	klog 4 "a9-eink eink-display: update dropped: pipeline not available (-5)"
	video 0; addc aborts 1; setc hv_on 0; status; exit 0
fi
# HV up (fp9929a with 0021/0022): PWRUP, 25 ms, VCOM_EN, wait for PG = 1
scen hv-stuck && echo 1 > $S/stuck
pg_on=1; { scen pg-stuck0 || scen pg-inverted; } && pg_on=0
# fp9929a max_on_ms (0024): armed at energize, independent of every consumer
cap=$(cat $R/sys/module/fp9929a/parameters/max_on_ms)
read -r te _ < /proc/uptime; echo "$te $cap" > $S/energized; echo "$te" > $S/energized.at
( sleep $(awk -v c="$cap" 'BEGIN { printf "%.3f", c / 1000 }')
  if [ -e $S/energized ] && [ "$(getpin 36)$(getpin 86)" != 00 ]; then
	setpin 36 0; sleep 0.05; setpin 86 0
	read -r tf _ < /proc/uptime; echo "$tf" > $S/timer_fired
	klog 2 "fp9929a 0-0048: max_on_ms $cap expired: E Ink rails forced down"
	rm -f $S/energized
  fi ) &
setpin 86 1; sleep 0.025; setpin 36 1
if [ $pg_on = 1 ]; then
	sleep 0.01; setpin 82 1
	tc=30; scen cold && tc=-5
	klog 6 "fp9929a 0-0048: on: VCOM 2730 mV (code 0x7e), temp $tc C, PG 1 after 10000 us"
else
	scen pg-inverted && { sleep 0.01; setpin 82 0; }
	sleep 0.1
	klog 3 "fp9929a 0-0048: PG not asserted within 100 ms (last 0): powering down"
	setpin 36 0; sleep 0.05; setpin 86 0; rm -f $S/energized
	scen pg-inverted && setpin 82 1
	klog 3 "panel-hisense-hlte556n-eink panel: FP9929A power-up failed: -110"
	video 1
	klog 6 "tc358767 0-000f: before init sequence: ID read 0, ID 0x6601"
	sleep 0.03
	klog 3 "a9-eink eink-display: FP9929A is not on after enable: no frames sent (panel_hisense_hlte556n_eink.enable / fp9929a.allow_power_on?)"
	klog 4 "a9-eink eink-display: update dropped: pipeline not available (-5)"
	video 0; addc aborts 1; setc hv_on 0; status; exit 0
fi
video 1
klog 6 "tc358767 0-000f: before init sequence: ID read 0, ID 0x6601"
scen bridge-err && klog 3 "tc358767 0-000f: init seq #10 reg 0x13c: -107"
sleep 0.06
# a9_eink (0026): bin admission for a real waveform update, checked with the
# fresh TMST before any frame
if [ "$(nparam a9_eink zero_test)" = 0 ]; then
	# 0027: a real update needs a valid TMST from this power-up
	if scen tmst-fail; then
		klog 3 "a9-eink eink-display: no valid fresh temperature (FP9929A missing, thermistor ok): update refused"
		addc aborts 1; power_off; exit 0
	fi
	bn=10; scen e5c-bin3 && bn=3
	bmin=$(nparam a9_eink bin_min); bmax=$(nparam a9_eink bin_max)
	if [ $bn -lt $bmin ] || [ $bn -gt $bmax ]; then
		klog 3 "a9-eink eink-display: bin $bn outside [$bmin,$bmax]: update refused"
		addc aborts 1; power_off; exit 0
	fi
fi
if scen cold; then   # a9_eink (0025): below 0 C nothing is driven
	klog 3 "a9-eink eink-display: panel temperature below 0 C (FP9929A -5 C, epd_therm 30343 mC): update refused"
	addc aborts 1; power_off; exit 0
fi
if [ "$(getpin 82)" != 1 ]; then   # a9_eink (0022): PG must be 1
	klog 3 "a9-eink eink-display: FP9929A is not on after enable: no frames sent (panel_hisense_hlte556n_eink.enable / fp9929a.allow_power_on?)"
	klog 4 "a9-eink eink-display: update dropped: pipeline not available (-5)"
	addc aborts 1; power_off; exit 0
fi
zn=$(nparam a9_eink zero_test)
if [ "$zn" -gt 0 ]; then n=$zn; else n=123; fi
# frames follow the wall clock (85 Hz); few forks, so the sequencer's own
# processes are not starved under QEMU's emulated CPU
t0=$(cut -d' ' -f1 /proc/uptime); i=0 ret=0 ev=0
hang=0; scen hang && hang=1; scen hangb && [ "$zn" = 425 ] && hang=1
while :; do
	read -r en < $P/enable; case $en in Y|1) ;; *) ret=-125; break;; esac
	read -r up _ < /proc/uptime
	i=$(awk -v a="$t0" -v b="$up" 'BEGIN { printf "%d", (b - a) * 85 }')
	[ $i -ge $n ] && [ $hang = 0 ] && { i=$n; break; }
	[ $ev = 0 ] && [ $i -ge 85 ] && { ev=1; scen dsi-error && klog 3 "[drm:dpu_encoder_frame_done_timeout] *ERROR* enc31 frame done timeout"; scen late && addc late 1; }
	sleep 0.2
done
[ $i -gt $n ] && i=$n
addc frames $i; setc gap 11800
# zero tests so far (e3a = 1, e3b = 2, e3t = 3)
[ "$zn" -gt 0 ] && addc zt 1
# e3t-drive: a drive-counter gap in the e3t session (a kernel bug: no drive
# frame is sent there); tpllate: 3 late gaps with the template on the
# glass in E5c (info); drivelate: a late gap with a drive frame (FAIL)
[ "$zn" -gt 0 ] && [ "$(ctr zt)" = 3 ] && scen e3t-drive && addc lated 1
[ "$zn" = 0 ] && scen tpllate && { addc late 3; addc vbl 3; setc gap 30000; }
[ "$zn" = 0 ] && scen drivelate && { addc late 1; addc lated 1; }
[ "$zn" = 0 ] && setc gapd 11800
if [ "$zn" -gt 0 ]; then
	if scen old-kernel || scen old-zero-text; then
		klog 6 "a9-eink eink-display: zero test: $i/$n all-zero frames with HV on ($ret)"
	else
		klog 6 "a9-eink eink-display: zero test: $i/$n template frames with HV on ($ret)"
	fi
	# as the kernel prints it: %#x of 0 is "0x0"
	echo "mode 0x0 pre 0x0 clear 0 type 0 bin 10 t1 30 t2 30 frames $i steps 0 dry 0 aborted $([ $ret = 0 ] && echo 0 || echo 1)" > $S/last1
else
	printf 'mode 0x203 pre 0x203 clear 1 type 2 bin 10 t1 30 t2 30 t1_raw 30 t2_raw 30343\nrect 0,0-1648,824 steps 1 dry 0 aborted 0\nframes 60 sha256 -\n' > $R/sys/kernel/debug/a9_eink/last
	echo "mode 0x203 pre 0x203 clear 1 type 2 bin 10 t1 30 t2 30 frames 60 steps 1 dry 0 aborted 0" > $S/last1
fi
[ $ret = 0 ] && addc updates 1 || addc aborts 1
status
power_off
