#!/system/bin/sh
# emul-step.sh -- ONE supervised thermal emulation step ON THE PHONE (mainline
# Android, root), self-contained so that a wedged adb cannot leave a zone
# emulated (runs/sensors.md section 11; reviews 39 and 41).
#   sh emul-step.sh <zone type> <millidegrees> <hold s, 1..7> <expected uname -v> <run id>
# Run it detached (setsid) from the loopback root shell (thermal/host-step.sh
# does that); the host follows it on the live kernel log (host-step.sh klog_start, dmesg -w over adb):
# every line also goes to /dev/kmsg as "a9-emul <run id>: ...", and to
# /data/local/tmp/a9-emul.log. The LAST line of every invocation, refusals
# included, is "a9-emul <run id>: END rc=<n>"; rc 0 (with a PASS line) is the
# only pass. The run id ([A-Za-z0-9._-], from the host) keeps an earlier
# step's lines from being read as this step's result (review 41 F3).
#
# Order of events:
#  1. refusals: bad run id, wrong kernel (uname -v != the build proven to carry
#     0045/0046), temperature outside 1..94999 m C, hold outside 1..7 s,
#     unknown zone, another step running (lock), not pinned to CPU 7 (checked
#     by reading the affinity back), any cooling device of the zone not at 0;
#  2. a safety-net clearer (hold + 3 s, so <= 10 s) starts BEFORE the write
#     (cancel token file, never a signal);
#  3. the write runs in a tracked background writer that records its exit
#     status; tsens interrupt count (all "thermal-sensor" lines of
#     /proc/interrupts, every CPU) before it, then 1 s and 2 s after: a rise
#     above A9_EMUL_STORM (default 20) clears at once and reports STORM;
#  4. by +2 s the writer must have finished with status 0 (else WRITER-STUCK /
#     WRITE-FAILED), and the zone must read exactly T at +1 s, +2 s or in a
#     hold sample (else NOT-APPLIED): proof the emulation was really applied;
#  5. once per second until the hold ends (at least 2 s): zone temp, cur_state;
#  6. clear (echo 0, status checked), then up to 5 s for temp != T and every
#     cur_state 0.
# Cleanup rules (review 41 F1): the safety net is cancelled only after a clear
# that succeeded AFTER the writer finished; a failed clear leaves it armed and
# waits for it. While a writer is still pending nothing can know whether its
# write lands later: the script clears now, starts a late clearer that clears
# again once the writer is gone, leaves the safety net armed and KEEPS the
# lock (the next step is refused until a human checks). The lock is released
# only when the writer is gone, a clear succeeded and the zone read real again.
# A write blocked on tz->lock cannot be undone by anything here: the vendor
# dead-man is the last fail-safe.
# No child is ever signalled (thermal step-2 attempt 2, END rc=143): Android's
# mksh (R59 "Android") turns a SIGTERM that hits a subshell while it runs
# builtins into kill(0, SIGTERM) on the whole process group when the parent was
# its group leader (main.c quitenv(), kshpgrp == kshpid; setsid makes that
# true), so `kill $NET` at the moment the safety net woke TERMed this script
# after a good clear. The safety net is cancelled through the file $NETC
# instead, and ignores SIGTERM like the late clearer, so a stray TERM can
# neither propagate nor disarm it. runs/thermal-rc143-fix.md.
# A9_SYS / A9_PROC / A9_TMP / A9_KMSG / A9_UNAME_V / A9_EMUL_NOPIN: offline
# test hooks only (tests/emul-step-test.sh); never set on the phone.
set -u
ZT=${1:?zone type}; T=${2:?millidegrees}; H=${3:?hold s}; KV=${4:?expected uname -v}; RID=${5:?run id}
SYS=${A9_SYS:-/sys}; PROC=${A9_PROC:-/proc}; TMP=${A9_TMP:-/data/local/tmp}
KMSG=${A9_KMSG:-/dev/kmsg}; STORM=${A9_EMUL_STORM:-20}
LOG=$TMP/a9-emul.log; LOCK=$TMP/a9-emul.lock; WST=$TMP/a9-emul.lock/writer-status
case "$RID" in ''|*[!A-Za-z0-9._-]*) RID=BADID;; esac
NETC=$TMP/a9-emul.net-cancel-$RID-$$
say() { echo "a9-emul $RID: $*" >> "$KMSG" 2>/dev/null; echo "$(cut -d' ' -f1 "$PROC/uptime" 2>/dev/null) $RID $*" >> "$LOG"; echo "$*"; }
OWN=0; KEEP=; NET=; W=
fin() {
	if [ $OWN = 1 ]; then
		# a cancel token the safety net will never read (it is gone) is removed
		[ -n "$NET" ] && ! kill -0 "$NET" 2>/dev/null && rm -f "$NETC"
		if [ -z "$KEEP" ]; then rm -f "$WST"; rmdir "$LOCK" 2>/dev/null
		else say "lock $LOCK KEPT ($KEEP): rmdir it only after checking emul_temp is 0 and no writer is left"; fi
	fi
	say "END rc=$1"
}
trap 'fin $?' EXIT
die() { say "REFUSED: $*"; exit 2; }
[ "$RID" != BADID ] || die "run id must be [A-Za-z0-9._-]+"

case "$T" in ''|*[!0-9]*) die "temperature $T is not a positive integer";; esac
[ "$T" -ge 1 ] && [ "$T" -lt 95000 ] || die "temperature $T outside 1..94999"
case "$H" in ''|*[!0-9]*) die "hold $H is not an integer";; esac
[ "$H" -ge 1 ] && [ "$H" -le 7 ] || die "hold $H outside 1..7 s"
[ "${A9_UNAME_V:-$(uname -v)}" = "$KV" ] || die "kernel '$(uname -v)' is not the expected '$KV' (0045/0046 not proven)"

Z=
for z in "$SYS"/class/thermal/thermal_zone*; do
	[ "$(cat "$z/type" 2>/dev/null)" = "$ZT" ] && { Z=$z; break; }
done
[ -n "$Z" ] || die "no zone of type $ZT"
mkdir "$LOCK" 2>/dev/null || die "another step holds $LOCK (rmdir it only after checking it is stale)"
OWN=1

# CPU 7 isolation is relied on (the tsens IRQ and its thread are on CPU 0), so
# it is verified, not assumed: the affinity read back must be exactly 0x80.
if [ "${A9_EMUL_NOPIN:-0}" = 1 ]; then
	say "pin: skipped (A9_EMUL_NOPIN=1, offline test only)"
else
	command -v taskset >/dev/null 2>&1 || die "no taskset: cannot pin to CPU 7"
	taskset -p 80 $$ >/dev/null 2>&1 || die "taskset -p 80 $$ failed"
	m=$(taskset -p $$ 2>/dev/null); m=${m##*:}; m=${m# }; m=${m#0x}
	while [ "${m#0}" != "$m" ]; do m=${m#0}; done
	[ "$m" = 80 ] || die "not pinned to CPU 7 (affinity read back '$m', want 80)"
	say "pinned to CPU 7"
fi

CD=
for c in "$Z"/cdev[0-9]*; do
	[ -e "$c/cur_state" ] && CD="$CD $c"
done
states() { s=; for c in $CD; do s="$s $(cat "$c/type" 2>/dev/null)=$(cat "$c/cur_state" 2>/dev/null)"; done; echo "${s# }"; }
allzero() { for c in $CD; do [ "$(cat "$c/cur_state" 2>/dev/null)" = 0 ] || return 1; done; return 0; }
irqs() {   # sum over every CPU column of every tsens line
	n=0
	while read -r l; do
		set -- $l; shift
		for f; do case "$f" in *[!0-9]*) break;; esac; n=$((n + f)); done
	done <<EOF
$(grep 'thermal-sensor' "$PROC/interrupts" 2>/dev/null)
EOF
	echo $n
}
temp() { cat "$Z/temp" 2>/dev/null; }
clear_() { if echo 0 > "$Z/emul_temp"; then say "cleared ($1)"; return 0; fi; say "CLEAR FAILED ($1)"; return 1; }
writer_pending() { [ ! -s "$WST" ] && [ -n "$W" ] && kill -0 "$W" 2>/dev/null; }

# finish <reason> <rc>: the only way out once the write was issued.
finish() {
	why=$1; rc=$2
	[ "$rc" = 0 ] || say "$why"
	if writer_pending; then
		clear_ "$why; writer $W still pending" || :
		( trap '' TERM; while kill -0 "$W" 2>/dev/null; do sleep 1; done
		  if echo 0 > "$Z/emul_temp"; then echo "a9-emul $RID: late clear after writer $W ended" >> "$KMSG"
		  else echo "a9-emul $RID: late clear after writer $W FAILED" >> "$KMSG"; fi ) </dev/null >/dev/null 2>&1 &
		KEEP="writer $W pending; late clearer $! and safety net left armed"
		say "FAIL: writer $W pending; safety net not cancelled, late clearer started"
		exit "$( [ "$rc" = 0 ] && echo 5 || echo "$rc" )"
	fi
	if clear_ "$why"; then
		: > "$NETC" 2>/dev/null || say "note: cancel token $NETC not written; the safety net will clear again (harmless)"
	else
		[ "$rc" = 0 ] && rc=6
		say "safety net left armed; waiting for it (fires at hold+3 s)"
		wait "$NET" 2>/dev/null
	fi
	i=0
	while [ $i -lt 5 ]; do
		sleep 1; i=$((i + 1))
		t=$(temp)
		if [ -n "$t" ] && [ "$t" != "$T" ] && allzero; then
			[ "$rc" = 0 ] && say "PASS: real $t after ${i}s, cdev [$(states)], irq total +$(( $(irqs) - I0 ))" \
				|| say "FAIL: zone back to real $t after ${i}s, cdev [$(states)] (rc $rc)"
			exit "$rc"
		fi
	done
	KEEP="zone not verified back to real"
	say "FAIL: after clear temp $(temp) cdev [$(states)] irq +$(( $(irqs) - I0 ))"
	exit "$( [ "$rc" = 0 ] && echo 1 || echo "$rc" )"
}

allzero || die "a cooling device is not at 0 before the write: $(states)"
R0=$(temp)
say "zone $ZT ($Z) real $R0 cdev [$(states)] write $T hold ${H}s"

# 2. safety net, independent of this loop
# cancelled only by the token file $NETC (never by a signal, see above); a
# token written after it fired is removed by fin(); if both clears race, the
# second "echo 0" is harmless and the result is still verified below.
rm -f "$NETC"
( trap '' TERM; sleep $((H + 3))
  if [ -e "$NETC" ]; then rm -f "$NETC"
  else echo 0 > "$Z/emul_temp"; echo "a9-emul $RID: safety-net clear" >> "$KMSG"; fi ) </dev/null >/dev/null 2>&1 &
NET=$!

# 3. tracked writer: its status file appears only once the write returned
I0=$(irqs)
( echo "$T" > "$Z/emul_temp"; echo $? > "$WST" ) </dev/null >/dev/null 2>&1 &
W=$!
say "irq before $I0; write issued (writer $W)"
APPLIED=
sleep 1; I1=$(irqs); t=$(temp); [ "$t" = "$T" ] && APPLIED=1
sleep 1; I2=$(irqs); t=$(temp); [ "$t" = "$T" ] && APPLIED=1
say "irq +1s $((I1 - I0)) +2s $((I2 - I0)) (threshold $STORM); temp $t cdev [$(states)]"
[ $((I2 - I0)) -gt "$STORM" ] && finish "STORM: tsens interrupts +$((I2 - I0)) in 2 s" 3
writer_pending && finish "WRITER-STUCK: the write of $T had not returned after 2 s" 5
ws=$(cat "$WST" 2>/dev/null)
[ "$ws" = 0 ] || finish "WRITE-FAILED: writer status '${ws:-none (killed?)}'" 4
i=2
while [ $i -lt "$H" ]; do
	sleep 1; i=$((i + 1)); t=$(temp); [ "$t" = "$T" ] && APPLIED=1
	say "t+${i}s temp $t cdev [$(states)] irq +$(( $(irqs) - I0 ))"
done
[ -n "$APPLIED" ] || finish "NOT-APPLIED: zone never read $T during the hold" 4
finish "hold ${H}s over" 0
