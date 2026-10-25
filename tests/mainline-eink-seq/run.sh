#!/usr/bin/env bash
# run.sh -- offline tests of scripts/mainline/kernel/initramfs/a9-eink-seq
# against the fake hardware in sim-lib.sh, under busybox sh (the initramfs
# shell). No phone. ~3 min.  Usage: tests/mainline-eink-seq/run.sh [scenario...]
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PY=$(command -v python3)
REPO=$(cd "$HERE/../.." && pwd)
SEQ=$REPO/scripts/mainline/kernel/initramfs/a9-eink-seq
BB=${BUSYBOX:-$(nix-build '<nixpkgs>' -A pkgsStatic.busybox --no-out-link 2>/dev/null)/bin/busybox}
[ -x "$BB" ] || { echo "no busybox"; exit 2; }
T=${KEEP:-$(mktemp -d)}; mkdir -p "$T"
# at exit: stop every process of this run (loggers, followers, simulated
# updates) by its unique tree path, then remove the tree
cleanup() {
	for i in 1 2 3; do
		for e in /proc/[0-9]*/environ; do   # every process started for this run
			grep -qz "^A9_SEQ_DIR=$T/" "$e" 2>/dev/null && { p=${e#/proc/}; kill "${p%/environ}" 2>/dev/null; }
		done
		pkill -f "$T/" 2>/dev/null; sleep 0.3
	done
	[ -n "${KEEP:-}" ] || rm -rf "$T"
}
trap cleanup EXIT
mkdir -p "$T/bb" "$T/fbin"; for a in $("$BB" --list); do ln -sf "$BB" "$T/bb/$a"; done
# the fakes start with #!/bin/sh (busybox in QEMU); on the host that is bash
# (slow to start): run them with the same busybox sh
for f in "$HERE"/bin/*; do sed "1s|^#!/bin/sh|#!$T/bb/sh|" "$f" > "$T/fbin/${f##*/}"; chmod 755 "$T/fbin/${f##*/}"; done
export SIM_LIB=$HERE/sim-lib.sh SIM_DIR=$HERE
# payload sha256 of the simulator's FAKE control template (sim-lib.sh setup)
CT_FAKE_SHA=$(head -c 1485568 /dev/zero | tr '\0' '\377' | sha256sum | cut -c1-64)
pass=0 fail=0
ok() { echo "  ok   $*"; pass=$((pass + 1)); }
no() { echo "  FAIL $*"; fail=$((fail + 1)); }

newphone() { # newphone <name> <scenario words>
	export SIM_ROOT=$T/$1/root
	export A9_SEQ_ROOT=$SIM_ROOT A9_SEQ_DIR=$T/$1/seq A9_SEQ_PREPATH=$T/fbin:$T/bb
	export A9_SEQ_KMSG_CMD="$PY -I $HERE/bin/kmsgcat $SIM_ROOT/kmsg.rec" A9_SEQ_KMSG_LOG=$T/fbin/fakelog
	export A9_SEQ_FW=$SIM_ROOT/lib/firmware/hisense/a9-waveform.bin A9_SEQ_REBOOT="touch $T/$1/REBOOTED"
	export A9_SEQ_SELF=$SEQ A9_SEQ_NICE=0 A9_SEQ_BOOTLOG=/nonexistent
	export A9_SEQ_CT=$SIM_ROOT/lib/firmware/hisense/a9-ctrl-template.bin A9_SEQ_CT_SHA=$CT_FAKE_SHA
	shift
	PATH=$T/fbin:$T/bb "$T/bb/sh" -c ". $SIM_LIB; setup $*"
	printf 'G' > "$SIM_ROOT/fix/grey"; printf 'L' > "$SIM_ROOT/fix/levels_before"
	printf 'mode 0x203 pre 0x203 clear 1 type 2 bin 10 t1 30 t2 30 t1_raw 30 t2_raw 30343\nrect 0,0-1648,824 steps 1 dry 1 aborted 0\nframes 60 sha256 %s\n' \
		6661424b107d3839bf06d690e8d9a27413144a8f93f3f3f8f5868d24c29b39f7 > "$SIM_ROOT/fix/last"
	head -c 1357952 /dev/zero | tr '\0' 'G' > "$SIM_ROOT/fix/grey"; head -c 1357952 /dev/zero | tr '\0' '\017' > "$SIM_ROOT/fix/levels_before"
}
seq() { PATH=$T/bb "$T/bb/sh" "$SEQ" "$@" 2>&1 | tee -a "$A9_SEQ_DIR.out"; }
has() { grep -qE -- "$1" "$A9_SEQ_DIR.out"; }
hvoff() { [ "$(cat $SIM_ROOT/tlmm/0x00d24004)$(cat $SIM_ROOT/tlmm/0x00556004)" = 00 ]; }
gclosed() { [ "$(cat 2>/dev/null $SIM_ROOT/sys/module/a9_eink/parameters/enable $SIM_ROOT/sys/module/panel_hisense_hlte556n_eink/parameters/enable $SIM_ROOT/sys/module/fp9929a/parameters/allow_power_on 2>/dev/null | tr -d "\n" | tr 0 N)" = NNN ] || [ "$(cat $SIM_ROOT/sys/module/panel_hisense_hlte556n_eink/parameters/enable $SIM_ROOT/sys/module/fp9929a/parameters/allow_power_on | tr -d "\n" | tr 0 N)" = NN -a ! -e $SIM_ROOT/sys/module/a9_eink ]; }
common_end() { hvoff && ok "$1: PWRUP/VCOM_EN low at the end" || no "$1: HV pins high"; gclosed && ok "$1: all gates closed" || no "$1: a gate open"; }

t_pginv() { echo "== PG inverted (0 on, 1 off): fp9929a times out waiting for PG 1, a9_eink refuses, stop"
	newphone pginv pg-inverted
	seq pre "$(sha256sum "$A9_SEQ_FW" | cut -d' ' -f1)" >/dev/null; has 'RESULT pre PASS' && ok "pre PASS" || no "pre"
	seq e3 full >/dev/null
	# fp9929a drops VCOM_EN after its 100 ms PG wait, before the 120 ms steady window
	has 'PG_VERDICT (raw-low-when-on|undetermined)' && ok "PG verdict: a stop ($(grep -o 'PG_VERDICT [a-z-]*' $A9_SEQ_DIR.out))" || no "PG verdict: $(grep PG_VERDICT $A9_SEQ_DIR.out)"
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3 not FAIL"
	has 'FAIL e3.pg_polarity' && ok "pg_polarity names the mismatch" || no "pg_polarity"
	grep -q 'PG not asserted' "$A9_SEQ_DIR/ABORTED" && ok "tripped on the fp9929a PG timeout" || no "latch: $(cat $A9_SEQ_DIR/ABORTED)"
	seq e5a >/dev/null; has 'refused: ABORTED' && ok "e5a refused" || no "e5a ran"
	[ "$(grep -c 'a9-eink-seq: MARK e3b open' $SIM_ROOT/kmsg.rec)" = 0 ] && ok "no session B" || no "session B ran"
	common_end pginv
}
t_stuck1() { echo "== PG reads 1 on AND off: no evidence, stop (e3 full)"
	newphone stuck1 pg-stuck1
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'PG_VERDICT high-no-toggle' && ok "verdict high-no-toggle" || no "verdict: $(grep PG_VERDICT $A9_SEQ_DIR.out)"
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3"
	[ "$(grep -c 'a9-eink-seq: MARK e3b open' $SIM_ROOT/kmsg.rec)" = 0 ] && ok "no session B" || no "session B ran"
	common_end stuck1
}
t_good() { echo "== good (PG 1 on, 0 off, as measured on boot 9): full E3 -> E5c"
	newphone good pg-good
	seq pre >/dev/null; has 'RESULT pre PASS' && ok "pre PASS" || no "pre"
	seq e5a >/dev/null; has 'refused: stage e3' && ok "e5a refused before e3" || no "order e5a"
	seq e3 full >/dev/null
	has 'PG_VERDICT active-high' && ok "PG verdict active-high" || no "verdict"
	has 'RESULT e3 PASS' && ok "e3 PASS" || { no "e3: $(grep -E 'FAIL|ABORT' $A9_SEQ_DIR.out | head -5)"; }
	seq e5c >/dev/null; has 'refused: stage e5b' && ok "e5c refused before e5b" || no "order e5c"
	seq e5a >/dev/null; has 'RESULT e5a DRY-OK' && ok "e5a dry OK" || no "e5a: $(grep -E 'FAIL|ABORT' $A9_SEQ_DIR.out | tail -3)"
	has 'E5A_FRAMES frames 60 sha256 6661424b' && ok "e5a reports frames/sha" || no "e5a frames line"
	seq e5b >/dev/null; has 'refused: stage e5a' && ok "e5b refused without the host verdict" || no "order e5b"
	seq verdict e5a 60 6661424b107d3839bf06d690e8d9a27413144a8f93f3f3f8f5868d24c29b39f7 >/dev/null
	has 'RESULT verdict PASS' && ok "verdict PASS" || no "verdict"
	seq e5b >/dev/null; has 'RESULT e5b PASS' && ok "e5b PASS" || no "e5b: $(grep -E 'FAIL|ABORT' $A9_SEQ_DIR.out | tail -3)"
	seq e5c >/dev/null; has 'RESULT e5c PASS' && ok "e5c PASS" || no "e5c: $(grep -E 'FAIL|ABORT' $A9_SEQ_DIR.out | tail -3)"
	has 'ok   e5c.update_frames: Clear request frames 60 \(bin 10: 60\)' && has 'ok   e5c.init_frames: init flash frames 63 \(bin 10: 63\)' && ok "exact per-bin frame counts" || no "frame counts"
	[ ! -e "$A9_SEQ_DIR/ABORTED" ] && ok "no abort" || no "aborted: $(cat $A9_SEQ_DIR/ABORTED)"
	seq safe >/dev/null; has 'verified PWRUP=0 VCOM_EN=0' && ok "final safe verified" || no "final safe"
	t=$(cat $A9_SEQ_DIR/hv_total_ms); [ "$t" -gt 5000 ] && [ "$t" -lt 15000 ] && ok "HV total $t ms" || no "HV total $t"
	common_end good
}
t_dsierr() { echo "== DSI error during E3 session B: the watcher trips"
	newphone dsierr pg-good dsi-error
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3 not FAIL"
	grep -q 'frame done timeout' "$A9_SEQ_DIR/ABORTED" && ok "abort reason is the kmsg line" || no "reason: $(cat $A9_SEQ_DIR/ABORTED)"
	grep -q 'zero test: .*(-125)' $SIM_ROOT/kmsg.rec && ok "update stopped by enable=0" || no "update not stopped"
	common_end dsierr
}
t_stuck() { echo "== HV does not go down: TLMM force-off"
	newphone stuck pg-good hv-stuck
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3 not FAIL"
	grep -q 0x00d24004 $SIM_ROOT/sim/devmem.writes 2>/dev/null && ok "VCOM_EN forced through TLMM" || no "no TLMM write"
	grep -q 'driven low through TLMM' $A9_SEQ_DIR/seq.log && ok "logged" || no "not logged"
	[ ! -e $T/stuck/REBOOTED ] && ok "no reboot needed" || no "rebooted"
	common_end stuck
}
t_hang() { echo "== update never ends: the kernel cap (max_on_ms 4000) drops the pins"
	newphone hang pg-good hang
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3 not FAIL"
	capcheck hang 4000
	common_end hang
}
# review 4: the kernel cap (fp9929a.max_on_ms, set per session) must drop the
# pins at the cap while the runner is blind; the simulator's timer starts at
# energize like 0024's, so check energize -> drop <= cap + 150 ms
capcheck() { # capcheck <tag> <cap>
	f=$SIM_ROOT/sim/timer_fired
	[ -s "$f" ] && ok "$1: kernel timer fired" || { no "$1: kernel timer did not fire"; return; }
	d=$(awk -v a="$(cut -d' ' -f1 $SIM_ROOT/sim/energized.at 2>/dev/null || echo 0)" -v b="$(cat $f)" 'BEGIN { printf "%d", (b - a) * 1000 }')
	[ "$d" -ge "$2" ] && [ "$d" -le $(( $2 + 150 )) ] && ok "$1: pins dropped $d ms after energize (cap $2)" || no "$1: dropped $d ms after energize (cap $2)"
	grep -q "max_on_ms $2 expired" "$A9_SEQ_DIR/ABORTED" 2>/dev/null && ok "$1: aborted on the kernel's max_on line" || ok "$1: aborted ($(head -c 80 $A9_SEQ_DIR/ABORTED))"
	[ "$(cat $SIM_ROOT/sys/module/fp9929a/parameters/max_on_ms)" = 20000 ] && ok "$1: max_on_ms restored to 20000" || no "$1: max_on_ms $(cat $SIM_ROOT/sys/module/fp9929a/parameters/max_on_ms)"
}
t_capblock() { echo "== review 4: pattern client blocked beyond the cap, update hung: kernel cap drops the pins at 4000 ms"
	newphone capblock pg-good hang pattern-block
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3"
	capcheck capblock 4000
	common_end capblock
}
t_capi2c() { echo "== review 4: session B I2C reads stall, update hung in B: kernel cap drops the pins at 6000 ms"
	newphone capi2c pg-good hangb i2c-stall
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'MARK e3b open' $SIM_ROOT/kmsg.rec 2>/dev/null; grep -q 'MARK e3b open' $SIM_ROOT/kmsg.rec && ok "session B ran" || no "session B did not run"
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3"
	capcheck capi2c 6000
	common_end capi2c
}
t_capswoff() { echo "== review 4: software reports off (hv_on 0) while the pins stay high: kernel cap drops them at 4000 ms"
	newphone capswoff pg-good sw-off-pins-high
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3"
	capcheck capswoff 4000
	common_end capswoff
}
t_cold() { echo "== FP9929A reads -5 C (signed TMST, 0025): a9_eink refuses, no frames, stop"
	newphone cold pg-good cold
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3"
	grep -q 'below 0 C' "$A9_SEQ_DIR/ABORTED" && ok "tripped on the refusal" || no "latch: $(cat $A9_SEQ_DIR/ABORTED)"
	has 'FAIL e3a.temp' && ok "temp criterion failed (-5 C)" || no "temp criterion"
	common_end cold
}
t_e5bretry() { echo "== E5b: the bridge path retried (a second stream error): E5b FAIL"
	newphone e5bretry pg-good bridge-retry
	seq pre >/dev/null; seq e3 full >/dev/null; seq e5a >/dev/null
	seq verdict e5a 60 6661424b107d3839bf06d690e8d9a27413144a8f93f3f3f8f5868d24c29b39f7 >/dev/null
	seq e5b >/dev/null
	has 'RESULT e5b FAIL' && ok "e5b FAIL" || no "e5b"
	has 'FAIL e5b.stream_err_once' && ok "stream error twice caught" || no "criterion"
	has 'FAIL e5b.bridge_one_pass' && ok "26 init failures caught" || no "criterion one_pass"
	common_end e5bretry
}
t_e5cbin() { echo "== E5c: the fresh bin (3) is outside E5a's bin +-1: the driver refuses, no frames"
	newphone e5cbin pg-good e5c-bin3
	seq pre >/dev/null; seq e3 full >/dev/null; seq e5a >/dev/null
	seq verdict e5a 60 6661424b107d3839bf06d690e8d9a27413144a8f93f3f3f8f5868d24c29b39f7 >/dev/null
	seq e5b >/dev/null; seq e5c >/dev/null
	has 'E5C_BINS 9-11' && ok "admitted bins 9-11 (E5a bin 10)" || no "bins: $(grep E5C_BINS $A9_SEQ_DIR.out)"
	has 'RESULT e5c FAIL' && ok "e5c FAIL" || no "e5c"
	grep -q 'outside \[9,11\]' "$A9_SEQ_DIR/ABORTED" && ok "tripped on the driver's refusal" || no "latch: $(cat $A9_SEQ_DIR/ABORTED)"
	[ "$(cat $SIM_ROOT/sys/module/a9_eink/parameters/bin_min)/$(cat $SIM_ROOT/sys/module/a9_eink/parameters/bin_max)" = 0/13 ] && ok "bins restored 0/13" || no "bins not restored"
	common_end e5cbin
}
t_tmstfail() { echo "== E5c: the TMST read fails at this power-up: no fallback, the driver refuses, no frames"
	newphone tmstfail pg-good tmst-fail
	seq pre >/dev/null; seq e3 full >/dev/null; seq e5a >/dev/null
	seq verdict e5a 60 6661424b107d3839bf06d690e8d9a27413144a8f93f3f3f8f5868d24c29b39f7 >/dev/null
	seq e5b >/dev/null; seq e5c >/dev/null
	has 'RESULT e5c FAIL' && ok "e5c FAIL" || no "e5c"
	grep -q 'no valid fresh temperature' "$A9_SEQ_DIR/ABORTED" && ok "tripped on the refusal" || no "latch: $(cat $A9_SEQ_DIR/ABORTED)"
	common_end tmstfail
}
t_e3fullstop() { echo "== e3 full stop (--e3full): A + B, then the phone latches the stop itself"
	newphone e3fs pg-good
	seq pre >/dev/null; seq e3 full stop >/dev/null
	has 'RESULT e3 STOP-B \(zero frames only; sessions A\+B PASS, 0 criteria failed; PG active-high' && ok "STOP-B with A+B clean" || no "$(grep 'RESULT e3' $A9_SEQ_DIR.out)"
	grep -q 'E3 full (A+B) measurement-only stop' "$A9_SEQ_DIR/ABORTED" && ok "latched on the phone" || no "not latched"
	has '^SAFE .*off-verified' && ok "off verified in the result" || no "no SAFE line"
	seq e5a >/dev/null; has 'refused' && ok "e5a refused" || no "e5a ran"
	common_end e3fs
}
t_flaky() { echo "== failing devmem reads during the session: never PG evidence"
	newphone flaky pg-good devmem-flaky
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'PG_VERDICT invalid-samples' && ok "verdict invalid-samples" || no "verdict: $(grep PG_VERDICT $A9_SEQ_DIR.out)"
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3"
	[ "$(grep -c 'a9-eink-seq: MARK e3b open' $SIM_ROOT/kmsg.rec)" = 0 ] && ok "no session B" || no "session B ran"
	common_end flaky
}
t_stuck0() { echo "== PG reads 0 on AND off: no evidence, stop after E3 session A"
	newphone stuck0 pg-stuck0
	seq pre >/dev/null; seq e3 >/dev/null
	has 'PG_VERDICT (low-no-toggle|undetermined)' && ok "verdict: a stop ($(grep -o 'PG_VERDICT [a-z-]*' $A9_SEQ_DIR.out))" || no "verdict: $(grep PG_VERDICT $A9_SEQ_DIR.out)"
	has 'RESULT e3 STOP-A' && ok "e3 STOP-A" || no "e3"
	[ "$(grep -c 'a9-eink-seq: MARK e3b open' $SIM_ROOT/kmsg.rec)" = 0 ] && ok "no session B" || no "session B ran"
	common_end stuck0
}
t_nodrm() { echo "== E2b did not bind msm: pre fails, nothing powered"
	newphone nodrm pg-good no-drm
	seq pre >/dev/null; has 'RESULT pre FAIL' && ok "pre FAIL" || no "pre"
	seq e3 >/dev/null; has 'refused' && ok "e3 refused" || no "e3 ran"
	grep -q 'fp9929a .*: on:' $SIM_ROOT/kmsg.rec && no "HV rose" || ok "HV never rose"
	common_end nodrm
}
t_badverdict() { echo "== host CLI disagrees with the phone"
	newphone badv pg-good
	seq pre >/dev/null; seq e3 full >/dev/null; seq e5a >/dev/null
	seq verdict e5a 60 0000000000000000000000000000000000000000000000000000000000000000 >/dev/null
	has 'RESULT verdict FAIL' && ok "verdict FAIL" || no "verdict"
	seq e5b >/dev/null; has 'refused' && ok "e5b refused" || no "e5b ran"
	common_end badv
}
t_bridge() { echo "== bridge init write error with HV up"
	newphone bridge pg-good bridge-err
	seq pre >/dev/null; seq e3 full >/dev/null
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3"
	grep -q 'init seq' "$A9_SEQ_DIR/ABORTED" && ok "tripped on the bridge error" || no "reason: $(cat $A9_SEQ_DIR/ABORTED)"
	common_end bridge
}
t_aonly() { echo "== plain e3 (--e3): unconditional latched stop after session A, even when A passes"
	newphone aonly pg-good
	seq pre >/dev/null; seq e3 >/dev/null
	has 'PG_VERDICT active-high' && ok "A measured (active-high)" || no "verdict"
	has 'RESULT e3 STOP-A \(measurement only; session A: 0 criteria failed' && ok "STOP-A with A clean" || no "$(grep RESULT $A9_SEQ_DIR.out)"
	grep -q 'E3A measurement-only stop' "$A9_SEQ_DIR/ABORTED" && ok "latched" || no "not latched"
	[ "$(grep -c 'a9-eink-seq: MARK e3b open' $SIM_ROOT/kmsg.rec)" = 0 ] && ok "no session B" || no "session B ran"
	seq e5a >/dev/null; has 'refused' && ok "e5a refused" || no "e5a ran"
	common_end aonly
}
# a trip (watcher/guardian/host) at each opening step, synchronous and concurrent:
# no later opening step, no pattern launch, no power-up
t_inject() {
	for how in sync conc; do
	for at in baseline zero_test max_on gate_fp gate_panel gate_enable; do
		echo "== abort injected at $at ($how)"
		newphone "inj-$how-$at" pg-good
		HL=$T/inj-$how-$at/hooks.log
		cat > "$T/inj-$how-$at/hook" <<-H
		#!$T/bb/sh
		echo "\$1" >> $HL
		[ "\$1" = $at ] || exit 0
		if [ $how = sync ]; then $T/bb/sh $SEQ _safe 1 "injected at $at"
		else ( $T/bb/sh $SEQ _safe 1 "injected at $at" & ); sleep 0.3; fi
		H
		chmod 755 "$T/inj-$how-$at/hook"
		seq pre >/dev/null
		A9_SEQ_HOOK="$T/inj-$how-$at/hook" seq e3 >/dev/null
		[ "$(tail -n1 $HL)" = $at ] && ok "no opening step after $at ($(tr '\n' ' ' < $HL))" || no "steps: $(tr '\n' ' ' < $HL)"
		[ ! -s $SIM_ROOT/sim/launch.log ] && ok "no pattern launch" || no "pattern launched"
		grep -q 'fp9929a .*: on:' $SIM_ROOT/kmsg.rec && no "HV rose" || ok "HV never rose"
		grep -q "injected at $at" "$A9_SEQ_DIR/ABORTED" && ok "latched" || no "latch: $(cat $A9_SEQ_DIR/ABORTED)"
		common_end "inj-$how-$at"
	done; done
}
# ---- control template (patch 0028), drive counters (0029), e3t -------------
fullpass() { # pre + e3 full, both PASS
	seq pre "$(sha256sum "$A9_SEQ_FW" | cut -d' ' -f1)" "$(sha256sum "$A9_SEQ_CT" | cut -d' ' -f1)" >/dev/null
	has 'RESULT pre PASS' && ok "pre PASS (waveform + template hashes)" || no "pre: $(grep -E 'FAIL' $A9_SEQ_DIR.out | head -3)"
	seq e3 full >/dev/null; has 'RESULT e3 PASS' && ok "e3 PASS" || no "e3: $(grep -E 'FAIL|ABORT' $A9_SEQ_DIR.out | head -3)"
}
t_e3tsame() { echo "== e3t with the pattern the shadow already holds (boot 13): no update possible -> gates stay closed, FAIL"
	newphone e3tsame pg-good
	fullpass
	# as on boot 13 with the old sequencer: the shadow holds what e3t would commit
	echo black > "$A9_SEQ_DIR/last_pattern"; echo black > "$SIM_ROOT/sim/shadow"
	u0=$(grep -c . "$SIM_ROOT/sim/update.log" 2>/dev/null || echo 0)
	seq e3t stop >/dev/null
	has "FAIL e3t.pattern: pattern 'black' = the shadow's content" && ok "guard names the no-change pattern" || no "guard: $(grep -E 'FAIL|RESULT e3t' $A9_SEQ_DIR.out | head -3)"
	grep -q "e3t: gates \[" "$A9_SEQ_DIR/seq.log" && no "gates opened" || ok "gates never opened"
	grep -q 'fp9929a .*: on: ' "$SIM_ROOT/kmsg.rec" && [ "$(grep -c 'fp9929a .*: on: ' "$SIM_ROOT/kmsg.rec")" -gt 2 ] && no "HV rose in e3t" || ok "HV never rose in e3t (2 on: lines = A + B)"
	[ -e "$A9_SEQ_DIR/ABORTED" ] && ok "latched" || no "not latched"
	common_end e3tsame
}
t_samefake() { echo "== simulator: the fake a9eink-pattern commits nothing for an unchanged image (driver damage rule)"
	newphone samefake pg-good
	fullpass   # e3a black, e3b white: two updates
	[ "$(grep -c 'fp9929a .*: on: ' "$SIM_ROOT/kmsg.rec")" = 2 ] && ok "two HV sessions" || no "on lines"
	A9_SEQ_DIR=$A9_SEQ_DIR SIM_ROOT=$SIM_ROOT PATH=$T/fbin:$T/bb "$T/bb/sh" -c ". $SIM_LIB; a9eink-pattern -d x --hold 1 white" >/dev/null 2>&1
	grep -qx white "$SIM_ROOT/sim/nochange.log" && ok "white after white: no update (boot 13)" || no "fake updated anyway"
	common_end samefake
}
t_e3t() { echo "== e3t (--e3t): template-only session with bridge/clock/DPU reads, then a latched stop"
	newphone e3t pg-good
	seq e3t stop >/dev/null; has 'refused: stage e3' && ok "e3t refused before e3" || no "order e3t"
	fullpass
	has 'ok   e3a.template: status template 1' && ok "template 1 after session A" || no "e3a.template"
	has 'ok   e3a.zero_line: zero test 255/255 template frames' && ok "template zero-test text accepted" || no "zero line"
	seq e3t stop >/dev/null
	has 'RESULT e3t STOP-T \(template frames only; e3t PASS, 0 criteria failed\)' && ok "STOP-T, e3t PASS" || no "$(grep -E 'RESULT e3t|FAIL' $A9_SEQ_DIR.out | head -5)"
	grep -q 'e3t template session: measurement-only stop' "$A9_SEQ_DIR/ABORTED" && ok "latched on the phone" || no "not latched"
	grep -q 'e3t: HV ON (VCOM_EN high) at uptime .*WATCH THE GLASS' "$A9_SEQ_DIR/seq.log" && grep -q 'e3t: HV OFF at uptime' "$A9_SEQ_DIR/seq.log" && ok "HV ON / HV OFF lines logged" || no "HV ON/OFF lines"
	grep -q 'a9-eink-seq: e3t: HV ON' $SIM_ROOT/kmsg.rec && ok "HV ON line in kmsg (host live view)" || no "HV ON not in kmsg"
	[ "$(grep -c ' 0x0500 TC_IDREG 0x00006601 ' $A9_SEQ_DIR/e3t/regs.txt)" = 2 ] && ok "regs.txt: TC_IDREG read at 1 s and 3 s" || no "regs.txt: $(head -3 $A9_SEQ_DIR/e3t/regs.txt)"
	[ "$(tail -n1 $A9_SEQ_DIR/e3t/regs.txt | cut -d' ' -f3)" != 0x0508 ] && [ "$(grep -v ' clk ' $A9_SEQ_DIR/e3t/regs.txt | grep '^2 ' | tail -n1 | cut -d' ' -f3)" = 0x0508 ] && ok "SYSSTAT read last" || no "SYSSTAT order"
	grep -q 'e3t.tc_POCTRL info reg 0x0448: 1 s 0x00000086, 3 s 0x00000086 (init-sequence 0x00000086: match)' $A9_SEQ_DIR/criteria.e3t && ok "table readback compared (match)" || no "POCTRL info"
	grep -q 'e3t.tc_VFUEN0 info .*MISMATCH' $A9_SEQ_DIR/criteria.e3t && ok "self-clearing VFUEN0 reported as mismatch (info)" || no "VFUEN0 info"
	grep -q 'e3t.dsierrcnt_delta info DSIERRCNT 0x00000000 -> 0x00000002 1 s later: delta 2' $A9_SEQ_DIR/criteria.e3t && ok "DSIERRCNT delta" || no "dsierrcnt: $(grep dsierr $A9_SEQ_DIR/criteria.e3t)"
	grep -q 'e3t.disp_cc_mdss_byte0_clk.1 info clk_rate 60104166 Hz at mid read 1 (in ' $A9_SEQ_DIR/criteria.e3t && ok "byte clock in range" || no "clk"
	grep -q 'e3t.drm_state info rc 0, .*format=XR24' $A9_SEQ_DIR/criteria.e3t && ok "DPU state read (background)" || no "state: $(grep drm_state $A9_SEQ_DIR/criteria.e3t)"
	[ -s "$A9_SEQ_DIR/st.e3t.ps" ] && has 'ok   e3t.late: late \+0 \(up to the DPU state read' && ok "timing gated up to the state read" || no "late window"
	has 'ok   e3t.late_drive: late_drive \+0' && has 'ok   e3t.max_gap_drive' && ok "drive counters +0" || no "drive counters"
	grep -q 'e3t.glass info NOT machine-checkable' $A9_SEQ_DIR/criteria.e3t && ok "glass recorded as not checkable" || no "glass"
	seq e5a >/dev/null; has 'refused: ABORTED' && ok "e5a refused after the stop" || no "e5a ran"
	common_end e3t
}
t_e3tread() { echo "== e3t: one TC358767 read fails: info only, no abort"
	newphone e3tread pg-good tc-read-fail
	fullpass
	seq e3t stop >/dev/null
	has 'RESULT e3t STOP-T \(template frames only; e3t PASS' && ok "e3t PASS despite the failed read" || no "$(grep -E 'RESULT e3t|FAIL' $A9_SEQ_DIR.out | head -5)"
	grep -q 'e3t.tc_DSI_LANESTATUS0 info reg 0x0214: 1 s x, 3 s x' $A9_SEQ_DIR/criteria.e3t && ok "failed read recorded as x" || no "x line"
	grep -q 'e3t.tc_reads info 2 failed' $A9_SEQ_DIR/criteria.e3t && ok "failed reads counted (2)" || no "count: $(grep tc_reads $A9_SEQ_DIR/criteria.e3t)"
	grep -q 'e3t template session: measurement-only stop' "$A9_SEQ_DIR/ABORTED" && ok "only the planned stop latched" || no "latch: $(cat $A9_SEQ_DIR/ABORTED)"
	common_end e3tread
}
t_e3tdrive() { echo "== e3t: a drive-counter gap during template-only frames: FAIL"
	newphone e3tdrive pg-good e3t-drive
	fullpass
	seq e3t stop >/dev/null
	has 'FAIL e3t.late_drive: late_drive \+1' && ok "late_drive +1 caught" || no "late_drive"
	has 'RESULT e3t STOP-T \(template frames only; e3t FAIL, 1 criteria failed' && ok "e3t FAIL (1 criterion)" || no "$(grep 'RESULT e3t' $A9_SEQ_DIR.out)"
	common_end e3tdrive
}
e5to() { # e5to <name> <scenario...>: pre, e3 full, e5a, verdict, e5b, e5c
	n=$1; newphone "$@"
	fullpass
	seq e5a >/dev/null
	seq verdict e5a 60 6661424b107d3839bf06d690e8d9a27413144a8f93f3f3f8f5868d24c29b39f7 >/dev/null
	seq e5b >/dev/null; has 'RESULT e5b PASS' && ok "e5b PASS" || no "e5b"
	seq e5c >/dev/null
}
t_e5ctpl() { echo "== E5c: total late +3 with the template on the glass, drive counters 0: FAIL (review 10: totals gate too)"
	e5to e5ctpl pg-good tpllate
	has 'RESULT e5c FAIL' && ok "e5c FAIL on the totals" || no "e5c not FAIL: $(grep -E 'RESULT e5c' $A9_SEQ_DIR.out | tail -1)"
	has 'FAIL e5c.late: late \+3' && has 'FAIL e5c.max_gap: max_gap_us 30000' && ok "totals gate" || no "totals: $(grep -E 'e5c.(late|max_gap)' $A9_SEQ_DIR.out | head -3)"
	has 'ok   e5c.late_drive: late_drive \+0' && has 'ok   e5c.max_gap_drive: max_gap_drive_us 11800' && ok "drive counters gate" || no "drive counters"
	has 'info e5c.prep_us: a9e_prepare 2100 us' && ok "prep_us reported" || no "prep_us"
	common_end e5ctpl
}
t_e5cdrive() { echo "== E5c: one late gap with a drive frame on the glass: FAIL"
	e5to e5cdrive pg-good drivelate
	has 'FAIL e5c.late_drive: late_drive \+1' && ok "late_drive +1 caught" || no "late_drive"
	has 'RESULT e5c FAIL' && ok "e5c FAIL" || no "e5c"
	common_end e5cdrive
}
t_notpl() { echo "== pre: no control template file, or the host's hash differs: FAIL"
	newphone notpl pg-good no-template
	seq pre >/dev/null; has 'RESULT pre FAIL' && ok "pre FAIL without the file" || no "pre"
	has 'FAIL pre.template_file: .* sha256 missing' && has 'FAIL pre.template_payload' && ok "template criteria name it" || no "criteria"
	[ -e "$A9_SEQ_DIR/modprobe.out" ] && no "modprobe ran" || ok "modprobe not run"
	common_end notpl
	newphone tplhash pg-good
	seq pre - 0000000000000000000000000000000000000000000000000000000000000000 >/dev/null
	has 'RESULT pre FAIL' && has 'FAIL pre.template_file: .*\(host: 0000' && ok "pre FAIL on the host hash mismatch" || no "hash mismatch"
	has 'ok   pre.template_payload' && ok "payload itself valid (only the host hash differs)" || no "payload"
	common_end tplhash
	newphone tplpay pg-good
	A9_SEQ_CT_SHA=1111111111111111111111111111111111111111111111111111111111111111 seq pre >/dev/null
	has 'FAIL pre.template_payload' && has 'RESULT pre FAIL' && ok "pre FAIL on a payload hash that is not the kernel's" || no "payload hash"
	common_end tplpay
}
t_oldkernel() { echo "== kernel without patch 0028: old status (pre FAIL) / old zero-test text (e3 FAIL)"
	newphone oldk pg-good old-kernel
	seq pre >/dev/null; has 'FAIL pre.kernel_0028' && has 'RESULT pre FAIL' && ok "pre FAIL: no template field" || no "pre: $(grep -E 'kernel_0028|RESULT' $A9_SEQ_DIR.out)"
	common_end oldk
	newphone oldtext pg-good old-zero-text
	seq pre >/dev/null; seq e3 full >/dev/null
	has "FAIL e3a.zero_line: kernel without patch 0028: 'a9-eink eink-display: zero test: 255/255 all-zero frames" && ok "'all-zero frames' text fails as a kernel without 0028" || no "zero line: $(grep zero_line $A9_SEQ_DIR.out)"
	has 'RESULT e3 FAIL' && ok "e3 FAIL" || no "e3"
	[ "$(grep -c 'a9-eink-seq: MARK e3b open' $SIM_ROOT/kmsg.rec)" = 0 ] && ok "no session B" || no "session B ran"
	common_end oldtext
}
ALL="e3tsame samefake e3t e3tread e3tdrive e5ctpl e5cdrive notpl oldkernel pginv stuck1 aonly cold e5bretry e5cbin tmstfail capblock capi2c capswoff e3fullstop flaky inject stuck0 good dsierr stuck hang nodrm badverdict bridge"
for s in ${@:-$ALL}; do "t_$s"; done
echo "a9-eink-seq tests: $pass passed, $fail failed"
[ $fail = 0 ]
