#!/usr/bin/env bash
# emul-step-test.sh -- offline test of thermal/emul-step.sh (the on-phone
# emulation step) in mksh, Android's shell, against a fake sysfs/proc. No phone.
#   scripts/mainline/thermal/tests/emul-step-test.sh
# The fake "kernel" makes the zone read the emulated value while emul_temp is
# nonzero, as __thermal_zone_get_temp() does below the critical trip.
# Review 41 F1 faults are injected (failed write, failed clear, a writer that
# returns late, a zone that never reads the value) and, when the reviewed
# commit 0a2f418 is in this repository, also run against that old script as
# negative controls (they must show the fault there).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
S=$HERE/../emul-step.sh
R=$(mktemp -d); trap 'kstop; kill $(jobs -p) 2>/dev/null; chmod -R u+w "$R" 2>/dev/null; rm -rf "$R"' EXIT
fails=0
SH=$(command -v mksh || true)
[ -n "$SH" ] || exec nix-shell -p mksh --run "$0"
OLD=$R/old-emul-step.sh
git -C "$HERE" show 0a2f418:scripts/mainline/thermal/emul-step.sh > "$OLD" 2>/dev/null || OLD=

kern() {   # fake kernel: temp = emulated value while emul_temp is nonzero, else real
	( while :; do e=$(cat $Z/emul_temp 2>/dev/null); case "$e" in ''|0) v=${REAL:-39000};; *) v=$e;; esac
	  [ "$(cat $Z/temp)" = "$v" ] || { echo "$v" > $Z/temp.n && mv -f $Z/temp.n $Z/temp; }; sleep 0.1; done ) </dev/null >/dev/null 2>&1 &
	echo $! >> "$R"/kpids
}
kstop() { [ -s "$R"/kpids ] && kill $(cat "$R"/kpids) 2>/dev/null; : > "$R"/kpids; }
mk() {   # fresh fake phone: zone cpu4-thermal real 39 C, one cdev at 0, tsens irq lines
	kstop
	chmod -R u+w "$R"/sys 2>/dev/null; rm -rf "$R"/{sys,proc,tmp}
	mkdir -p "$R"/sys/class/thermal/thermal_zone3/cdev0 "$R"/proc "$R"/tmp
	Z=$R/sys/class/thermal/thermal_zone3
	echo cpu4-thermal > $Z/type; echo 39000 > $Z/temp; : > $Z/emul_temp
	echo cpufreq-cpu4 > $Z/cdev0/type; echo 0 > $Z/cdev0/cur_state
	echo "15 0.1" > "$R"/proc/uptime
	irq 100
	[ "${1:-}" = nokern ] || kern
}
irq() { printf '           CPU0 CPU1\n 45:  %s  0  GICv3 222 Level  4411000.thermal-sensor\n 46:  3  0  GICv3 223 Level  4411000.thermal-sensor\n' "$1" > "$R"/proc/interrupts; }
ENVS() { env A9_SYS=$R/sys A9_PROC=$R/proc A9_TMP=$R/tmp A9_KMSG=$R/tmp/kmsg A9_UNAME_V=KV A9_EMUL_NOPIN=${NOPIN:-1} "$@"; }
run() { ENVS "$SH" "$S" "$@"; }
# runw <script> <args>: run with a wrapper that delays the writer's echo of $DELAYT by $DELAY s
runw() { local s=$1; shift; ENVS "$SH" -c 'echo() { if [ "$*" = "$DELAYT" ]; then sleep "$DELAY"; fi; builtin echo "$@"; }; s=$1; shift; . "$s" "$@"' w "$s" "$@"; }
chk() { if [ "$1" = "$2" ]; then echo "PASS $3"; else echo "FAIL $3 (got '$1', want '$2')"; fails=$((fails + 1)); fi; }
has() { grep -qF -- "$1" "$R"/tmp/kmsg; echo $?; }

mk; run cpu4-thermal 91000 3 KV r1 >/dev/null; rc=$?
chk $rc 0 "nominal: PASS after clear"
chk "$(cat $Z/emul_temp)" 0 "nominal: emul_temp cleared"
chk "$(has 'a9-emul r1: cleared (hold 3s over)')" 0 "nominal: kmsg carries the clear, tagged with the run id"
chk "$(has 'a9-emul r1: PASS')" 0 "nominal: PASS line tagged"
chk "$(tail -n1 "$R"/tmp/kmsg)" "a9-emul r1: END rc=0" "nominal: last line END rc=0"
[ ! -e "$R"/tmp/a9-emul.lock ]; chk $? 0 "nominal: lock released"

mk; ( while :; do [ "$(cat $Z/emul_temp)" != 0 ] && [ -s $Z/emul_temp ] && irq $((RANDOM + 1000)); sleep 0.2; done ) & ST=$!
run cpu4-thermal 91000 5 KV r2 >/dev/null; rc=$?; kill $ST
chk $rc 3 "storm: detected"
chk "$(cat $Z/emul_temp)" 0 "storm: cleared"
chk "$(has 'a9-emul r2: cleared (STORM')" 0 "storm: in kmsg"
chk "$(tail -n1 "$R"/tmp/kmsg)" "a9-emul r2: END rc=3" "storm: END rc=3"

mk; run cpu4-thermal 95000 3 KV r3 >/dev/null; chk $? 2 "refuses 95000"
chk "$(cat $Z/emul_temp)" "" "nothing written on refusal"
chk "$(tail -n1 "$R"/tmp/kmsg)" "a9-emul r3: END rc=2" "refusal ends with END rc=2"
mk; run cpu4-thermal 91000 8 KV r4 >/dev/null; chk $? 2 "refuses hold 8 s"
mk; run cpu4-thermal 91000 3 OTHER r5 >/dev/null; chk $? 2 "refuses another kernel"
mk; run cpu9-thermal 91000 3 KV r6 >/dev/null; chk $? 2 "refuses unknown zone"
mk; run cpu4-thermal 91000 3 KV 'bad;id' >/dev/null; chk $? 2 "refuses a run id with shell characters"
mk; echo 1 > $Z/cdev0/cur_state; run cpu4-thermal 91000 3 KV r7 >/dev/null; chk $? 2 "refuses cdev not at 0"
mk; mkdir "$R"/tmp/a9-emul.lock; run cpu4-thermal 91000 3 KV r8 >/dev/null; chk $? 2 "refuses while locked"
[ -d "$R"/tmp/a9-emul.lock ]; chk $? 0 "foreign lock kept"
chk "$(cat $Z/emul_temp)" "" "nothing written while locked"

# CPU 7 pinning is verified (review 41 F1 note): a fake taskset that does not take effect is refused
mkdir -p "$R"/bin
fakeset() { printf '#!/bin/sh\n[ $# = 3 ] && exit 0\necho "pid $2%ss current affinity mask: %s"\n' "'" "$1" > "$R"/bin/taskset; chmod +x "$R"/bin/taskset; }
fakeset 3ff
mk; NOPIN=0 PATH=$R/bin:$PATH run cpu4-thermal 91000 3 KV r9 >/dev/null; chk $? 2 "refuses when the CPU 7 affinity does not read back"
chk "$(cat $Z/emul_temp)" "" "nothing written when not pinned"
[ ! -e "$R"/tmp/a9-emul.lock ]; chk $? 0 "pin refusal releases its own lock"
fakeset 00000080
mk; NOPIN=0 PATH=$R/bin:$PATH run cpu4-thermal 91000 2 KV r10 >/dev/null; chk $? 0 "pinned (affinity 00000080 read back): PASS"
chk "$(has 'a9-emul r10: pinned to CPU 7')" 0 "pin logged"

mk; ( sleep 0.5; echo 1 > $Z/cdev0/cur_state ) & run cpu4-thermal 91000 2 KV r11 >/dev/null; chk $? 1 "FAIL when cdev stays up after clear"
[ -d "$R"/tmp/a9-emul.lock ]; chk $? 0 "lock kept when the zone is not verified back"

mk; env A9_SYS=$R/sys A9_PROC=$R/proc A9_TMP=$R/tmp A9_KMSG=$R/tmp/kmsg A9_UNAME_V=KV A9_EMUL_NOPIN=1 "$SH" "$S" cpu4-thermal 91000 4 KV r12 >/dev/null & P=$!; sleep 2.5; kill -STOP $P; sleep 5.5
chk "$(has 'cleared (hold')" 1 "wedged step did not clear by itself"
chk "$(cat $Z/emul_temp)" 0 "safety net clears a wedged step (hold 4 + 3 s)"
chk "$(has 'a9-emul r12: safety-net clear')" 0 "safety net in kmsg"
kill -KILL $P 2>/dev/null; wait $P 2>/dev/null

echo "-- review 41 F1 faults"
# F1a: both writes fail (emul_temp is a directory): the old script logged PASS, rc 0
f1a() { mk; rmdir $Z/emul_temp 2>/dev/null; rm -f $Z/emul_temp; mkdir $Z/emul_temp; "$@" >/dev/null 2>&1; echo $?; }
chk "$(f1a run cpu4-thermal 91000 2 KV f1a)" 4 "F1a failed write: rc 4 (WRITE-FAILED), not PASS"
chk "$(has 'a9-emul f1a: PASS')" 1 "F1a: no PASS line"
chk "$(has 'a9-emul f1a: WRITE-FAILED')" 0 "F1a: WRITE-FAILED logged"
[ -n "$OLD" ] && { chk "$(f1a ENVS "$SH" "$OLD" cpu4-thermal 91000 2 KV)" 0 "F1a negative control: the reviewed script returns 0 (fault reproduced)"
	chk "$(grep -c '^a9-emul: PASS' "$R"/tmp/kmsg)" 1 "F1a negative control: reviewed script logs PASS"; }

# F1b: the writer returns 4 s late (hold 2, safety net at 5 s): old = PASS at ~3 s, lock gone, 91000 lands later
mk; t0=$SECONDS; DELAYT=91000 DELAY=4 runw "$S" cpu4-thermal 91000 2 KV f1b >/dev/null 2>&1; rc=$?; el=$((SECONDS - t0))
chk $rc 5 "F1b delayed writer: rc 5 (WRITER-STUCK), not PASS"
chk "$(has 'a9-emul f1b: PASS')" 1 "F1b: no PASS line"
[ -d "$R"/tmp/a9-emul.lock ]; chk $? 0 "F1b: lock KEPT while the writer is pending"
chk "$(has 'a9-emul f1b: lock')" 0 "F1b: kept lock logged"
sleep 4
chk "$(cat $Z/emul_temp)" 0 "F1b: after the late write landed, emul_temp is 0 again"
chk "$(has 'a9-emul f1b: late clear after writer')" 0 "F1b: late clearer ran after the writer"
chk "$(has 'a9-emul f1b: safety-net clear')" 0 "F1b: safety net was not cancelled"
if [ -n "$OLD" ]; then
	mk; DELAYT=91000 DELAY=4 runw "$OLD" cpu4-thermal 91000 2 KV >/dev/null 2>&1; rc=$?
	chk "$rc $([ -e "$R"/tmp/a9-emul.lock ] && echo lock || echo nolock)" "0 nolock" "F1b negative control: reviewed script PASSes and drops the lock"
	sleep 4; kstop
	chk "$(cat $Z/emul_temp)" 91000 "F1b negative control: the late write leaves 91000 armed (fault reproduced)"
fi

# F1c: the clear fails (emul_temp read-only 1.5..3.5 s), the safety net (5 s) must stay armed
f1c() { mk; ( sleep 1.5; chmod 444 $Z/emul_temp; sleep 2; chmod 644 $Z/emul_temp ) </dev/null >/dev/null 2>&1 & "$@" >/dev/null 2>&1; echo $?; }
chk "$(f1c run cpu4-thermal 91000 2 KV f1c)" 6 "F1c failed clear: rc 6, not PASS"
chk "$(has 'a9-emul f1c: CLEAR FAILED')" 0 "F1c: CLEAR FAILED logged"
chk "$(has 'a9-emul f1c: safety-net clear')" 0 "F1c: safety net left armed and fired"
chk "$(cat $Z/emul_temp)" 0 "F1c: zone cleared by the safety net"
chk "$(has 'a9-emul f1c: PASS')" 1 "F1c: no PASS line"
[ -n "$OLD" ] && { chk "$(f1c ENVS "$SH" "$OLD" cpu4-thermal 91000 2 KV)" 1 "F1c negative control: reviewed script FAILs ..."
	sleep 3; chk "$(cat $Z/emul_temp)" 91000 "F1c negative control: ... with its safety net cancelled, 91000 stays (fault reproduced)"; }

# F1d: the write "succeeds" but the zone never reads the value (no emulation behind it)
mk nokern; run cpu4-thermal 91000 3 KV f1d >/dev/null 2>&1; chk $? 4 "F1d never applied: rc 4 (NOT-APPLIED), not PASS"
chk "$(has 'a9-emul f1d: NOT-APPLIED')" 0 "F1d: NOT-APPLIED logged"
chk "$(cat $Z/emul_temp)" 0 "F1d: cleared"
[ -n "$OLD" ] && { mk nokern; ENVS "$SH" "$OLD" cpu4-thermal 91000 3 KV >/dev/null 2>&1; chk $? 0 "F1d negative control: reviewed script PASSes without evidence (fault reproduced)"; }
[ -z "$OLD" ] && echo "note: commit 0a2f418 not in this repository, negative controls skipped"

echo "-- thermal step-2 attempt 2: END rc=143 after a good clear"
# Model of Android's mksh (R59 "Android", measured with the phone's own
# /system/bin/sh under qemu-aarch64, runs/thermal-rc143-fix.md): a SIGTERM that
# reaches a child subshell of a setsid'd (group-leader) script is re-raised by
# that child as kill(0, SIGTERM), so the script itself is TERMed. Host mksh
# does not do this, so the wrapper makes every signalling kill also TERM the
# script ($$). The old script cancelled its safety net with `kill $NET` -> 143.
PRE=$R/pre-rc143-emul-step.sh
git -C "$HERE" show 1d80d76:scripts/mainline/thermal/emul-step.sh > "$PRE" 2>/dev/null || PRE=
runk() { local s=$1; shift; ENVS "$SH" -c 'kill() { builtin kill "$@"; r=$?; case "$1" in -0) ;; *) builtin kill -TERM $$;; esac; return $r; }; s=$1; shift; . "$s" "$@"' k "$s" "$@"; }
mk; runk "$S" cpu4-thermal 46000 2 KV k1 >/dev/null 2>&1; rc=$?
chk $rc 0 "rc143: good clear under the Android-mksh model ends rc 0"
chk "$(tail -n1 "$R"/tmp/kmsg)" "a9-emul k1: END rc=0" "rc143: last line END rc=0"
chk "$(has 'a9-emul k1: PASS')" 0 "rc143: PASS logged"
[ ! -e "$R"/tmp/a9-emul.lock ]; chk $? 0 "rc143: lock released"
ls "$R"/tmp/a9-emul.net-cancel-k1-* >/dev/null 2>&1; chk $? 0 "rc143: safety net cancelled by token file (no signal)"
sleep 4
chk "$(has 'a9-emul k1: safety-net clear')" 1 "rc143: cancelled safety net did not fire"
ls "$R"/tmp/a9-emul.net-cancel-k1-* >/dev/null 2>&1; chk $? 2 "rc143: the safety net consumed its cancel token"
if [ -n "$PRE" ]; then
	mk; runk "$PRE" cpu4-thermal 46000 2 KV k0 >/dev/null 2>&1
	chk "$(tail -n1 "$R"/tmp/kmsg)" "a9-emul k0: END rc=143" "rc143 negative control: the 1d80d76 script ends END rc=143 after its good clear (fault reproduced)"
	chk "$(has 'a9-emul k0: cleared (hold 2s over)')" 0 "rc143 negative control: ... the clear itself was good"
	sleep 4
else echo "note: commit 1d80d76 not in this repository, rc143 negative control skipped"; fi
# genuine failures still fail under the same model (no signal is sent on any path)
mk; DELAYT=46000 DELAY=4 ENVS "$SH" -c 'kill() { builtin kill "$@"; r=$?; case "$1" in -0) ;; *) builtin kill -TERM $$;; esac; return $r; }; echo() { if [ "$*" = "$DELAYT" ]; then sleep "$DELAY"; fi; builtin echo "$@"; }; s=$1; shift; . "$s" "$@"' k "$S" cpu4-thermal 46000 2 KV k2 >/dev/null 2>&1
chk $? 5 "rc143: a writer still pending still fails rc 5 under the model"
[ -d "$R"/tmp/a9-emul.lock ]; chk $? 0 "rc143: ... and keeps the lock"
sleep 4
chk "$(has 'a9-emul k2: safety-net clear')" 0 "rc143: ... and its safety net still fired"
chk "$(cat $Z/emul_temp)" 0 "rc143: ... emul_temp 0 at the end"

[ $fails = 0 ] && echo "EMUL-STEP TESTS: PASS" || { echo "EMUL-STEP TESTS: $fails FAIL"; exit 1; }
