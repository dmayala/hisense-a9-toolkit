# sim-lib.sh -- fake A9 hardware for the a9-eink-seq tests (sourced by the fake
# binaries and by run.sh). Everything lives under $SIM_ROOT. It imitates the
# kernel interfaces the sequencer reads (module parameters, a9_eink status and
# debugfs, TLMM registers, debugfs gpio, pm_genpd, DPU encoder status, kmsg)
# and the behaviour of the a9_eink/fp9929a/panel drivers with patches 0021 and
# 0022 (fp9929a waits up to 100 ms for PG = 1 after VCOM_EN, else powers down;
# a9_eink requires PG = 1). PG scenarios: pg-good (0 off, 1 on: MEASURED on
# boot 9), pg-stuck0, pg-stuck1, pg-inverted (1 off, 0 on).
# Scenario: $SIM_ROOT/sim/scenario.
R=${SIM_ROOT:?}
S=$R/sim
K=$R/kmsg.rec

klog() { # klog <level> <msg>: one kmsg record "pri,seq,ts,-;msg"
	# QEMU (SIM_KMSG_DEV=1): the real /dev/kmsg, with the level as prefix
	if [ "${SIM_KMSG_DEV:-0}" = 1 ]; then echo "<$(( $1 % 8 ))>$2" > /dev/kmsg; return; fi
	(
		flock -x 8
		n=$(( $(cat $S/seq 2>/dev/null || echo 100) + 1 )); echo $n > $S/seq
		ts=$(awk '{printf "%.0f", $1 * 1000000}' /proc/uptime)
		printf '%s,%s,%s,-;%s\n' "$1" "$n" "$ts" "$2" >> $K
	) 8>>$S/klock
}
io_of() { case $1 in 2) echo 0x00502004;; 31) echo 0x00d1f004;; 36) echo 0x00d24004;; 82) echo 0x00552004;; 86) echo 0x00556004;; esac; }
regen_gpio() {
	{
		echo "gpiochip0: 114 GPIOs, parent: platform/500000.pinctrl, 500000.pinctrl:"
		for p in 2 31 36 82 86; do
			v=$(cat $R/tlmm/$(io_of $p)); c=$(cat $R/tlmm/$(printf '0x%08x' $(( $(io_of $p) - 4 ))))
			if [ $(( (c >> 9) & 1 )) = 1 ]; then d=out; b=$(( (v >> 1) & 1 )); else d=in; b=$(( v & 1 )); fi
			[ $b = 1 ] && l=high || l=low
			printf ' gpio%-4s: %s %s  func0 8mA pull up\n' $p $d $l
		done
		echo
		echo "gpiochip1: 9 GPIOs, parent: platform/1c40000.spmi:pmic@0:gpio@c000, x:"
		echo " gpio36 : in   high normal vin-0 pull-down 10uA"
	} > $R/sys/kernel/debug/gpio.tmp && mv $R/sys/kernel/debug/gpio.tmp $R/sys/kernel/debug/gpio
}
kdict() { # kdict <line>: a /dev/kmsg dictionary continuation line (host file only)
	[ "${SIM_KMSG_DEV:-0}" = 1 ] && return
	( flock -x 8; printf ' %s\n' "$1" >> $K ) 8>>$S/klock
}
setpin() { # setpin <n> <0|1>: output -> bits 1 and 0 follow; input 82 -> bit 0
	a=$(io_of $1)
	if [ "$(cat $S/stuck 2>/dev/null)" = 1 ] && [ $2 = 0 ] && [ $1 != 82 ]; then return; fi
	if [ $1 = 82 ]; then echo $2 > $R/tlmm/$a; else echo $(( $2 * 3 )) > $R/tlmm/$a; fi
	regen_gpio
}
getpin() { echo $(( $(cat $R/tlmm/$(io_of $1)) & 1 )); }
nparam() { cat $R/sys/module/$1/parameters/$2; }   # numeric parameters as written
param() { v=$(cat $R/sys/module/$1/parameters/$2); case $v in 1) echo Y;; 0) echo N;; *) echo "$v";; esac; }
ctr() { cat $S/c.$1 2>/dev/null || echo 0; }
setc() { echo $2 > $S/c.$1; }
addc() { setc $1 $(( $(ctr $1) + $2 )); }
status() {
	f=$R/sys/class/drm/card1-DSI-1/eink/status
	[ -d ${f%/status} ] || return 0
	en=0; [ "$(param a9_eink enable)" = Y ] && en=1
	{
		# old-kernel: the status before patches 0028/0029 (no template or
		# drive counters)
		if scen old-kernel; then
			echo "enable $en waveform $(ctr wf) transport $(ctr tx) hv_on $(ctr hv_on) stall_fault $(ctr stall)"
			echo "updates $(ctr updates) aborts $(ctr aborts) frames $(ctr frames) late $(ctr late) vbl_skips $(ctr vbl) commit_fail $(ctr cf) max_gap_us $(ctr gap)"
		else
			echo "enable $en waveform $(ctr wf) transport $(ctr tx) hv_on $(ctr hv_on) stall_fault $(ctr stall) template $(ctr tpl)"
			echo "updates $(ctr updates) aborts $(ctr aborts) frames $(ctr frames) late $(ctr late) vbl_skips $(ctr vbl) commit_fail $(ctr cf) max_gap_us $(ctr gap) late_drive $(ctr lated) vbl_skips_drive $(ctr vbld) max_gap_drive_us $(ctr gapd)"
		fi
		echo "last $(cat $S/last1 2>/dev/null || echo 'mode 0x0 pre 0x0 clear 0 type 0 bin 0 t1 0 t2 0 frames 0 steps 0 dry 0 aborted 0') rect 0,0-0,0 begin_us 0 total_us 0 prep_us 2100"
	} > $f.tmp && mv $f.tmp $f
}
scen() { grep -qw "$1" $S/scenario 2>/dev/null; }
video() { # video 1|0: vsync ticker (85 Hz) + MX vote
	if [ $1 = 1 ]; then
		echo "mx                              on                              0           256" > $R/sys/kernel/debug/pm_genpd/pm_genpd_summary
		touch $S/video
		( read -r v < $S/vsync; read -r t0 _ < /proc/uptime
		  while [ -e $S/video ]; do
			read -r up _ < /proc/uptime
			w=$(awk -v v="$v" -v a="$t0" -v b="$up" 'BEGIN { printf "%d", v + (b - a) * 85 }')
			echo $w > $S/vsync
			echo "intf:1  wb:-1  vsync:$w     underrun:       0    frame_done_cnt:0mode: video" > $R/sys/kernel/debug/dri/0/encoder-0/status
			sleep 0.05; done ) &
	else
		rm -f $S/video
		echo "mx                              off-0                           0           0" > $R/sys/kernel/debug/pm_genpd/pm_genpd_summary
	fi
}
power_off() { # the pipeline power-down: zero frame, CRTC off, fp9929a power-down
	sleep 0.03; video 0
	if scen sw-off-pins-high; then   # a driver bug: software says off, pins stay up
		setc hv_on 0; status; return
	fi
	setpin 36 0; sleep 0.05; setpin 86 0; rm -f $S/energized
	if scen pg-inverted || scen pg-stuck1; then setpin 82 1; else setpin 82 0; fi
	setc hv_on 0; status
}

setup() { # setup <scenario words>
	rm -rf $R; mkdir -p $S $R/tlmm $R/lib/firmware/hisense $R/dev/dri $R/sys/kernel/debug/a9_eink \
		$R/sys/kernel/debug/pm_genpd $R/sys/kernel/debug/dri/0/encoder-0 $R/sys/module/fp9929a/parameters \
		$R/sys/module/panel_hisense_hlte556n_eink/parameters $R/sys/bus/i2c/drivers/tc358767/0-000f \
		$R/sys/class/power_supply/battery "$R/sys/bus/iio/devices/iio:device1" $R/proc \
		"$R/sys/firmware/devicetree/base/soc@0/i2c@4a80000/pmic@48" $R/fix \
		$R/sys/kernel/debug/clk/disp_cc_mdss_byte0_clk $R/sys/kernel/debug/clk/disp_cc_mdss_pclk0_clk
	ln -s ../sys/firmware/devicetree/base $R/proc/device-tree   # a symlink, as on the phone
	echo "$*" > $S/scenario
	echo 0 > $S/vsync; echo 0 > $S/stuck
	scen devmem-flaky && touch $S/flaky
	for p in 2 31; do echo 3 > $R/tlmm/$(io_of $p); echo $(( 0x200 )) > $R/tlmm/$(printf '0x%08x' $(( $(io_of $p) - 4 ))); done
	for p in 36 86; do echo 0 > $R/tlmm/$(io_of $p); echo $(( 0x200 )) > $R/tlmm/$(printf '0x%08x' $(( $(io_of $p) - 4 ))); done
	echo 0 > $R/tlmm/0x00552000
	pg_off=0; { scen pg-inverted || scen pg-stuck1; } && pg_off=1
	echo $pg_off > $R/tlmm/0x00552004
	regen_gpio; video 0
	echo "msm dev=5e01000.display-controller unique=5e01000.display-controller" > $R/sys/kernel/debug/dri/0/name
	echo "intf:1  wb:-1  vsync:0     underrun:       0    frame_done_cnt:0mode: video" > $R/sys/kernel/debug/dri/0/encoder-0/status
	echo " 103:          0  msm_mdss     0 Edge      msm" > $R/proc/interrupts
	echo N > $R/sys/module/fp9929a/parameters/allow_power_on
	echo 20000 > $R/sys/module/fp9929a/parameters/max_on_ms
	echo 500 > $R/sys/module/fp9929a/parameters/min_off_ms
	echo N > $R/sys/module/panel_hisense_hlte556n_eink/parameters/enable
	echo 4455435 > $R/sys/class/power_supply/battery/voltage_now
	echo 30343 > "$R/sys/bus/iio/devices/iio:device1/in_temp_epd_top_therm_input"
	printf '\000\000\000\001\000\000\000\122\000\000\000\000' > "$R/sys/firmware/devicetree/base/soc@0/i2c@4a80000/pmic@48/pg-gpios"
	printf 'A9WF-fake-waveform' > $R/lib/firmware/hisense/a9-waveform.bin
	# a FAKE control template with the real header layout (A9CT, v1, 80,
	# 1485568) and a synthetic payload; its payload hash is passed to the
	# sequencer as A9_SEQ_CT_SHA (honoured only with a fake tree)
	scen no-template || {
		printf 'A9CT\001\000\000\000\120\000\000\000\000\253\026\000' > $R/lib/firmware/hisense/a9-ctrl-template.bin
		head -c 64 /dev/zero >> $R/lib/firmware/hisense/a9-ctrl-template.bin
		head -c 1485568 /dev/zero | tr '\0' '\377' >> $R/lib/firmware/hisense/a9-ctrl-template.bin
	}
	echo 60104166 > $R/sys/kernel/debug/clk/disp_cc_mdss_byte0_clk/clk_rate
	echo 40079000 > $R/sys/kernel/debug/clk/disp_cc_mdss_pclk0_clk/clk_rate
	printf 'plane[37]: plane-0\n\tcrtc=crtc-0\n\tfb=52\n\t\tformat=XR24 little-endian (0x34325258)\n\tcrtc-pos=448x829+0+0\n\tsrc-pos=448.000000x829.000000+0.000000+0.000000\ncrtc[44]: crtc-0\n\tenable=1\n\tactive=1\n' > $R/sys/kernel/debug/dri/0/state
	: > $K
	klog 6 "fp9929a 0-0048: at probe: PWRUP=1 VCOM_EN=1 PG=1"
	klog 6 "fp9929a 0-0048: VCOM 2730 mV (from cmdline) -> code 0x7e; power-up refused (safe default); max_on_ms 20000"
	klog 4 "msm_dsi 5e94000.dsi: supply refgen not found, using dummy regulator"
	scen no-drm || klog 6 "[drm] Initialized msm 1.13.0 for 5e01000.display-controller on minor 0"
	klog 4 "tc358767 0-000f: device ID not readable at probe (-6); checked again after DSI is up"
}
