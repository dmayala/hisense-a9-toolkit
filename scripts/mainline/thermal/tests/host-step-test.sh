#!/usr/bin/env bash
# host-step-test.sh -- offline test of thermal/host-step.sh (host side of the
# supervised thermal steps, runs/sensors.md 11.2) against a fake phone: fake
# adb (-s checked) runs its command in mksh with /dev/kmsg mapped to a file,
# the phone's "nc 127.0.0.1 2324" is mksh -s (a root shell reading its command
# from the connection, as nc -L sh does), the phone's dmesg -w follows that
# file. The live log reaches the host only through the adb root-channel stream
# (klog_start). No phone.
# Covers review 41 F2 (root-shell transport) and F3 (stale results), and the
# step-2 attempt-1 live-log fix (stream over adb, heartbeat, marker round trip,
# stalled stream detected before and during a step), each with a negative
# control that reproduces the fault.
#   scripts/mainline/thermal/tests/host-step-test.sh
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TH=$(cd "$HERE/.." && pwd)
SH=$(command -v mksh || true)
[ -n "$SH" ] || exec nix-shell -p mksh --run "$0"
R=$(mktemp -d)
cleanup() { pkill -CONT -f "tail -n [+]1 -f $R/kmsg" 2>/dev/null; kstop; klog_stop >/dev/null 2>&1; pkill -f "$R/" 2>/dev/null; rm -rf "$R"; }
trap cleanup EXIT
fails=0
chk() { if [ "$1" = "$2" ]; then echo "PASS $3"; else echo "FAIL $3 (got '$1', want '$2')"; fails=$((fails + 1)); fi; }

mkdir -p "$R"/phone/bin "$R"/pt "$R"/E
: > "$R"/kmsg
ln -s "$SH" "$R"/phone/bin/sh
printf '#!/bin/sh\nexec %s -s\n' "$SH" > "$R"/phone/bin/nc                     # loopback root shell
cat > "$R"/phone/bin/dmesg <<DM
#!$SH
[ "\$1" = -w ] || exit 1
tail -n +1 -f $R/kmsg & t=\$!
trap 'kill \$t; exit 0' TERM INT HUP
wait
DM
cat > "$R"/adb <<AD
#!/bin/sh
[ "\$1" = -s ] && { echo "\$2" >> $R/adb-serials; shift 2; }
[ "\$1" = shell ] || exit 1
c=\$(printf '%s' "\$2" | sed "s#>* */dev/kmsg#>> $R/kmsg#g")   # /dev/kmsg never truncates
printf '%s\\n' "\$2" >> $R/adb-cmds
PATH=$R/phone/bin:\$PATH exec $SH -c "\$c"
AD
# adb that drops the step launch (nothing reaches the phone)
printf '#!/bin/sh\ncase "$*" in *emul-step.sh*) exit 0;; esac\nexec %s/adb "$@"\n' "$R" > "$R"/adb-droplaunch
# adb that stalls the live stream right after launching cd-step.sh (the attempt-1 failure)
printf '#!/bin/sh\n%s/adb "$@"; rc=$?\ncase "$*" in *cd-step.sh*) pkill -STOP -f "tail -n [+]1 -f %s/kmsg";; esac\nexit $rc\n' "$R" "$R" > "$R"/adb-stall
chmod +x "$R"/phone/bin/nc "$R"/phone/bin/dmesg "$R"/adb "$R"/adb-droplaunch "$R"/adb-stall
cp "$TH"/emul-step.sh "$TH"/cd-step.sh "$R"/pt/                                # "adb push"
UTS='#1 SMP PREEMPT Thu Oct 16 12:00:00 UTC 2026'
export A9_SYS=$R/sys A9_PROC=$R/proc A9_TMP=$R/pt A9_KMSG=$R/kmsg A9_UNAME_V=$UTS A9_EMUL_NOPIN=1
export A9_ADB=$R/adb A9_PHONE_TMP=$R/pt A9_PHONE_SYS=$R/sys A9_HOLD=2 A9_STEP_WAIT=15 A9_KLOG_BEAT=1 A9_KLOG_RTT=3 A9_SERIAL=FAKE123
E=$R/E; L=$E/kmsg-live.txt
stall() { pkill -$1 -f "tail -n [+]1 -f $R/kmsg"; }

kstop() { [ -s "$R"/kpids ] && kill $(cat "$R"/kpids) 2>/dev/null; : > "$R"/kpids; }
mk() {   # fake phone: zone cpu4-thermal (real 39 C, cdev0 at 0), cooling_device5 = cpufreq-cpu4 on policy4
	kstop; rm -rf "$R"/sys "$R"/proc; rm -f "$R"/pt/a9-emul.log "$R"/pt/cd-*.txt; rmdir "$R"/pt/a9-emul.lock 2>/dev/null
	Z=$R/sys/class/thermal/thermal_zone3; C=$R/sys/class/thermal/cooling_device5; F=$R/sys/devices/system/cpu/cpufreq/policy4
	mkdir -p $Z/cdev0 $C $F "$R"/proc
	echo cpu4-thermal > $Z/type; echo 39000 > $Z/temp; : > $Z/emul_temp
	echo cpufreq-cpu4 > $Z/cdev0/type; echo 0 > $Z/cdev0/cur_state
	echo cpufreq-cpu4 > $C/type; echo 0 > $C/cur_state
	echo 2208000 > $F/scaling_max_freq; echo "1958400 2208000" > $F/scaling_available_frequencies
	echo "15 0.1" > "$R"/proc/uptime
	printf '           CPU0 CPU1\n 45:  100  0  GICv3 222 Level  4411000.thermal-sensor\n' > "$R"/proc/interrupts
	[ "${1:-}" = nokern ] && return
	( while :; do   # fake kernel: emulated temp, cooling state -> max_freq
		e=$(cat $Z/emul_temp 2>/dev/null); case "$e" in ''|0) v=39000;; *) v=$e;; esac
		[ "$(cat $Z/temp)" = "$v" ] || { echo $v > $Z/temp.n && mv -f $Z/temp.n $Z/temp; }
		case "$(cat $C/cur_state)" in 1) m=1958400;; *) m=2208000;; esac
		[ "$(cat $F/scaling_max_freq)" = $m ] || echo $m > $F/scaling_max_freq
		sleep 0.1; done ) </dev/null >/dev/null 2>&1 &
	echo $! >> "$R"/kpids
}
. "$TH"/host-step.sh

echo "-- live log over the adb root channel (step-2 attempt 1: NCM stream went silent)"
printf '[    1.000000] ring line before the stream\n' >> "$R"/kmsg
klog_start > "$R"/k.out 2>&1; chk $? 0 "klog_start: stream up, marker round trip"
tp=$(klog_tpid); c=$(tr '\000' ' ' < /proc/${tp:-0}/cmdline 2>/dev/null)
case "$c" in *"/dmesg -w ") r=0;; *) r=1;; esac; chk $r 0 "the target printed its own PID and it IS dmesg -w ($tp)"
grep -q '^[0-9]*\.[0-9]* \[    1.000000\] ring line before the stream$' $L; chk $? 0 "ring lines arrive with a host receive time"
grep -qvx FAKE123 "$R"/adb-serials; chk $? 1 "every adb call carried -s \$A9_SERIAL"
s0=$(grep -c ' a9-hb ' $L); sleep 3; s1=$(grep -c ' a9-hb ' $L)
chk $(( s1 > s0 )) 1 "heartbeat: a quiet kernel still grows the log ($s0 -> $s1 a9-hb lines)"
chk $(( $(klog_silent) <= 2 )) 1 "klog_silent small while alive"

echo "-- review 41 F2: cooling-device command through the root-shell transport"
mk; : > "$R"/adb-cmds
OLDCMD="setsid sh -c 'echo 1 > $R/quote-target; echo 0 > $R/quote-target' </dev/null >/dev/null 2>&1 &"
R_old() { timeout 20 "$ADB" shell "echo '$1' | nc -w 15 127.0.0.1 2324" </dev/null; }   # section 11.2 as reviewed
( cd "$R" && R_old "$OLDCMD" ) >/dev/null 2>&1; sleep 1
chk "$(head -c 12 "$R"/quote-target 2>/dev/null)" "setsid sh -c" "F2 negative control: reviewed R lets the adb-side shell write the target itself (fault reproduced)"
rm -f "$R"/quote-target "$R"/'1 > '* 2>/dev/null
R "$OLDCMD" >/dev/null 2>&1; chk $? 2 "F2: R refuses a command with a single quote"
[ ! -e "$R"/quote-target ]; chk $? 0 "F2: nothing ran for the refused command"
: > "$R"/adb-cmds
cd_step 5 4 > "$R"/cd.out 2>&1; chk $? 0 "F2: cd_step 5 4 PASS through the transport"
grep -qE "^echo 'setsid sh $R/pt/cd-step.sh 5 4 [A-Za-z0-9._-]+ </dev/null >/dev/null 2>&1 &' \| nc -w 15 127.0.0.1 2324$" "$R"/adb-cmds
chk $? 0 "F2: the adb-side shell received exactly one intact single-quoted command"
grep -q ': state 1 max_freq 1958400 ' $L; chk $? 0 "F2: on the phone the cooling device went to 1 and limited policy4 one step"
grep -q ': after reset state 0 max_freq 2208000$' $L; chk $? 0 "F2: and was reset to 0 by the phone itself"
chk "$(cat $C/cur_state)" 0 "F2: cooling device at 0 afterwards"
grep -q 'state 1 max_freq 1958400' "$R"/cd.out; chk $? 0 "F2: cd_step shows the phone-side record"

echo "-- review 41 F3: results of an earlier step"
STALE='[  100.000000] a9-emul: PASS: real 39000 after 1s, cdev [cpufreq-cpu4=0], irq total +0
[  101.000000] a9-emul 20261016T000000Z-old-1-1: PASS: real 39000 after 1s
[  101.100000] a9-emul 20261016T000000Z-old-1-1: END rc=0'
mk; printf '%s\n' "$STALE" >> "$R"/kmsg; klog_check >/dev/null
grep -qE "a9-emul: (PASS|FAIL|REFUSED|cleared \(STORM)" $L; chk $? 0 "F3 negative control: the reviewed wait grep matches a stale PASS at once (fault reproduced)"
rm -f $E/irq-after-*
t0=$SECONDS; ADB=$R/adb-droplaunch STEP_WAIT=3 step cpu4-thermal 91000 > "$R"/s.out 2>&1; rc=$?
chk $rc 1 "F3: nothing launched + stale PASS/END in the log -> FAIL"
grep -q 'TIMEOUT' "$R"/s.out; chk $? 0 "F3: reported as TIMEOUT"
chk "$(ls $E/irq-after-* 2>/dev/null | wc -l)" 0 "F3: no after snapshot without a terminal result"
chk $(( SECONDS - t0 >= 3 )) 1 "F3: waited its full timeout instead of breaking on the old line"

mk; printf '%s\n' "$STALE" >> "$R"/kmsg
step cpu4-thermal 91000 > "$R"/s.out 2>&1; chk $? 0 "F3: real step (UTS with spaces and # carried intact) PASS"
rid=$(sed -n 's/^step \(.*\): PASS$/\1/p' "$R"/s.out)
tend=$(grep -F "a9-emul $rid: END rc=0" $L | tail -n1 | cut -d' ' -f1)
tirq=$(sed -n 's/^HOSTTIME //p' $E/irq-after-$rid.txt)
awk -v a="${tirq:-0}" -v b="${tend:-9e18}" 'BEGIN{exit !(a >= b)}'; chk $? 0 "F3: after snapshot taken after this step's END line arrived ($tend <= $tirq)"
grep -q 'CPU0' $E/irq-after-$rid.txt; chk $? 0 "F3: IRQ snapshot read over the root channel"
grep -q 'old-1-1' $E/step-$rid.txt; chk $? 1 "F3: the step record holds only this run id"

mk nokern; printf '%s\n' "$STALE" >> "$R"/kmsg
step cpu4-thermal 91000 > "$R"/s.out 2>&1; chk $? 1 "F3: a non-PASS step (NOT-APPLIED) FAILs although the log holds old PASS lines"
grep -q 'END rc=4' "$R"/s.out; chk $? 0 "F3: its own END rc=4 is the result"
mk; printf '%s\n' "$STALE" >> "$R"/kmsg
step cpu9-thermal 91000 > "$R"/s.out 2>&1; chk $? 1 "F3: a refused step FAILs"
mk; printf '%s\n' "$STALE" >> "$R"/kmsg
UTS='#1 SMP PREEMPT other' step cpu4-thermal 91000 > "$R"/s.out 2>&1; chk $? 1 "F3: wrong kernel -> REFUSED -> FAIL"
grep -q 'END rc=2' "$R"/s.out; chk $? 0 "F3: refusal END rc=2"
UTS="bad'quote" step cpu4-thermal 91000 > "$R"/s.out 2>&1; chk $? 2 "F2: a UTS the transport cannot carry is refused"

echo "-- live log stalled (attempt 1: connection open, log not growing)"
mk; stall STOP; sleep 1.5
n0=$(wc -c < $L); echo 'a9-cd probe: END state 0' >> "$R"/kmsg; sleep 1.5
chk "$(wc -c < $L)" "$n0" "negative control: a stalled stream with the reader alive does not grow (fault reproduced)"
klog_alive; chk $? 0 "negative control: the host reader process is still alive (process liveness alone misses it)"
rm -f "$R"/pt/cd-*.txt
cd_step 5 4 > "$R"/cd.out 2>&1; chk $? 1 "stalled before a step: cd_step refuses"
grep -q 'live log DEAD' "$R"/cd.out && grep -q 'not launched' "$R"/cd.out; chk $? 0 "reported as live log DEAD, not launched"
chk "$(ls "$R"/pt/cd-*.txt 2>/dev/null | wc -l)" 0 "nothing ran on the phone"
step cpu4-thermal 91000 > "$R"/s.out 2>&1; chk $? 1 "stalled: step refuses too"
chk "$(cat $Z/emul_temp)" "" "no emul_temp write"
stall CONT; klog_check; chk $? 0 "stream resumed: marker round trip again"

echo "-- live log stalls DURING a step"
mk; rm -f "$R"/pt/cd-*.txt
ADB=$R/adb-stall STEP_WAIT=4 cd_step 5 4 > "$R"/cd.out 2>&1; chk $? 1 "cd_step: TIMEOUT FAIL"
grep -q 'TIMEOUT.*live log DEAD' "$R"/cd.out; chk $? 0 "timeout classified as live log DEAD"
grep -q '^END state 0' "$R"/cd.out; chk $? 0 "the phone record (root channel) shows the step reset itself"
chk "$(cat $C/cur_state)" 0 "cooling device at 0"
stall CONT

echo "-- klog_stop"
tp=$(klog_tpid); hp=$(klog_hostpid)
klog_stop > "$R"/ks.out 2>&1
grep -q "killed target $tp" "$R"/ks.out; chk $? 0 "target dmesg killed by its own PID after the cmdline check"
sleep 0.5; kill -0 $tp 2>/dev/null; chk $? 1 "target gone"; kill -0 $hp 2>/dev/null; chk $? 1 "host reader gone"
klog_start >/dev/null 2>&1; tp=$(klog_tpid)
sleep 300 & bogus=$!
sed -i "1s/KLOGPID [0-9]*/KLOGPID $bogus/" $L
klog_stop > "$R"/ks.out 2>&1
grep -q "target pid $bogus is now .*left alone" "$R"/ks.out; chk $? 0 "a PID that is not dmesg -w is left alone"
kill -0 $bogus 2>/dev/null; chk $? 0 "  (still running)"
kill $bogus $tp 2>/dev/null

[ $fails = 0 ] && echo "HOST-STEP TESTS: PASS" || { echo "HOST-STEP TESTS: $fails FAIL"; exit 1; }
