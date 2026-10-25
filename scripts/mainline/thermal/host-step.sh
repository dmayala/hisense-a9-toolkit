# host-step.sh -- HOST side of the supervised thermal steps (runs/sensors.md
# 11.2). Source it in bash after setting E (evidence dir) and UTS (expected
# uname -v):
#   . scripts/mainline/thermal/host-step.sh
#   klog_start                   live kernel log $E/kmsg-live.txt over the adb root channel + heartbeat
#   klog_check                   one marker through /dev/kmsg must come back on the live log (<= KLOG_RTT s)
#   klog_stop                    stop it (target dmesg by its own PID, same boot, exact cmdline only)
#   R <command>                  one command on the loopback root shell
#   cd_step <cdev n> <policy n>  step (a): cd-step.sh, detached, then its output
#   step <zone type> <m C>       one emul-step.sh run; returns 0 only on its PASS
# Fixes of review 41:
#  F2  R sends its command inside one single-quoted word, so it refuses a
#      command containing a single quote (or a newline) instead of letting it
#      escape to the adb-side shell; nested shells are not built inline any more
#      (cd-step.sh / emul-step.sh are pushed and invoked by path).
#  F3  step gives every invocation a fresh run id, reads only the part of the
#      live log written after its own launch (byte offset), waits for that run
#      id's terminal "END rc=" line (timeout = FAIL), takes the "after" IRQ
#      snapshot only then, and returns the step's own outcome.
# Live log (thermal step-2 attempt 1, 2026-10-16: the NCM stream went silent,
# usb0 lost its IPv4): the log no longer uses NCM. As in the Wi-Fi capture
# (wifi/host/capture-run.sh), `adb shell 'nc 127.0.0.1 2324'` gets the target
# script on stdin; the target prints its own PID (KLOGPID) and execs dmesg -w;
# the host prefixes every line with its receive time. A heartbeat writes
# "a9-hb <n>" to /dev/kmsg every KLOG_BEAT s, so a quiet kernel still grows the
# log and "silent" is detectable. Every step first proves a marker round trip
# (klog_check), refuses to launch without it, and classifies a TIMEOUT as
# "live log DEAD" or "live log alive". The live log is evidence, not the
# control path: the step clears itself on the phone, the dead-man is the
# fail-safe. IRQ snapshots go over the root channel too.
# Offline test hooks: A9_ADB, A9_PHONE_TMP, A9_PHONE_SYS, A9_STEP_WAIT, A9_HOLD,
# A9_KLOG_BEAT, A9_KLOG_RTT. A9_SERIAL: passed to adb as -s.
ADB=${A9_ADB:-adb}; PT=${A9_PHONE_TMP:-/data/local/tmp}
STEP_WAIT=${A9_STEP_WAIT:-25}; HOLD=${A9_HOLD:-5}; PSYS=${A9_PHONE_SYS:-/sys}
KLOG_BEAT=${A9_KLOG_BEAT:-5}; KLOG_RTT=${A9_KLOG_RTT:-10}

R() {   # loopback root shell; the command travels as ONE single-quoted word
	case "$1" in *"'"*|*$'\n'*)
		echo "R: refused, the command contains a single quote or newline (would escape the transport): $1" >&2; return 2;; esac
	timeout 20 "$ADB" ${A9_SERIAL:+-s "$A9_SERIAL"} shell "echo '$1' | nc -w 15 127.0.0.1 2324" </dev/null
}
# irq_ <file>: host time, then the tsens lines of /proc/interrupts (root channel, not NCM)
irq_() { { echo "HOSTTIME $EPOCHREALTIME"; R 'grep -E "CPU0|thermal-sensor" /proc/interrupts'; } > "$1"; }

# ---- live kernel log over the adb root channel ----
KLOG_READER='printf -v cr "\r"; "$1" ${4:+-s "$4"} shell "nc 127.0.0.1 2324" < "$2" 2>&1 | while IFS= read -r l; do printf "%s %s\n" "$EPOCHREALTIME" "${l%$cr}"; done >> "$3"'
klog_hostpid() { cat "$E/klog.hostpid" 2>/dev/null; }
klog_tpid() { sed -n '1s/^[0-9.]* KLOGPID \([0-9][0-9]*\)$/\1/p' "$E/kmsg-live.txt" 2>/dev/null; }
klog_alive() { local p; p=$(klog_hostpid) && [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
klog_start() {
	local L=$E/kmsg-live.txt i b
	klog_alive && { echo "klog_start: already running (host pid $(klog_hostpid))" >&2; return 2; }
	b=$(R 'cat /proc/sys/kernel/random/boot_id' | tr -d '\r' | tail -n1)
	case "$b" in ????????-????-????-????-????????????) ;; *) echo "klog_start: no boot_id over the root channel: STOP" >&2; return 1;; esac
	echo "$b" > "$E/klog.boot"
	printf 'echo KLOGPID $$; exec dmesg -w\n' > "$E/klog.cmd"
	: > "$L"
	setsid nohup bash -c "$KLOG_READER" _ "$ADB" "$E/klog.cmd" "$L" "${A9_SERIAL:-}" </dev/null >/dev/null 2>&1 &
	echo $! > "$E/klog.hostpid"
	for i in $(seq 1 40); do [ -n "$(klog_tpid)" ] && break; sleep 0.25; done
	[ -n "$(klog_tpid)" ] || { echo "klog_start: the target reader did not report its PID: STOP" >&2; klog_stop >/dev/null 2>&1; return 1; }
	# heartbeat: a quiet kernel still grows the log; stops by itself with the reader
	setsid nohup bash -c 'n=0; while kill -0 "$1" 2>/dev/null; do n=$((n+1)); timeout 20 "$2" ${4:+-s "$4"} shell "echo '"'"'echo a9-hb $n > /dev/kmsg'"'"' | nc -w 5 127.0.0.1 2324" </dev/null >/dev/null 2>&1; sleep "$3"; done' \
		_ "$(klog_hostpid)" "$ADB" "$KLOG_BEAT" "${A9_SERIAL:-}" </dev/null >/dev/null 2>&1 &
	echo $! > "$E/klog.hbpid"
	klog_check || return 1
	echo "klog: live log $L (target dmesg pid $(klog_tpid), host pid $(klog_hostpid), heartbeat every ${KLOG_BEAT}s)"
}
# klog_check: a fresh marker written to /dev/kmsg comes back on the live log within KLOG_RTT s
klog_check() {
	local L=$E/kmsg-live.txt tok off i
	klog_alive || { echo "klog: live log DEAD (host reader not running)"; return 1; }
	tok=a9-mark-$(date -u +%H%M%S)-$$-$RANDOM; off=$(stat -c %s "$L") || return 1
	R "echo $tok > /dev/kmsg" >/dev/null 2>&1
	for i in $(seq 1 $((KLOG_RTT * 4))); do
		tail -c +$((off + 1)) "$L" | grep -qF -- "$tok" && return 0
		sleep 0.25
	done
	echo "klog: live log DEAD (marker $tok not back within ${KLOG_RTT}s)"; return 1
}
# klog_silent: seconds since the live log last grew
klog_silent() { echo $(( $(date +%s) - $(stat -c %Y "$E/kmsg-live.txt" 2>/dev/null || echo 0) )); }
klog_stop() {
	local tp hp hb b c
	tp=$(klog_tpid); hp=$(klog_hostpid); hb=$(cat "$E/klog.hbpid" 2>/dev/null)
	if [ -n "$tp" ]; then
		b=$(R 'cat /proc/sys/kernel/random/boot_id' | tr -d '\r' | tail -n1)
		c=$(R "cat /proc/$tp/cmdline" | tr '\000\r' ' ' | sed 's/ *$//')
		if [ "$b" != "$(cat "$E/klog.boot" 2>/dev/null)" ]; then echo "klog_stop: boot changed ($b); target left alone"
		else case "$c" in 'dmesg -w'|*/'dmesg -w') R "kill $tp" >/dev/null && echo "klog_stop: killed target $tp ($c)";;
			*) echo "klog_stop: target pid $tp is now [$c]; left alone";; esac; fi
	fi
	for p in $hb $hp; do kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null; done
	rm -f "$E/klog.hostpid" "$E/klog.hbpid"
}
rid_() { echo "$(date -u +%Y%m%dT%H%M%SZ)-$1-$$-$RANDOM" | tr -c 'A-Za-z0-9._\n-' _; }
# wait_end <file> <byte offset> <terminal-line prefix>: that line, written after the offset
wait_end() {
	local i=0
	while [ $i -lt "$STEP_WAIT" ]; do
		tail -c +$(($2 + 1)) "$1" 2>/dev/null | grep -qF -- "$3" && return 0
		sleep 1; i=$((i + 1))
	done
	return 1
}
lines_() { tail -c +$(($2 + 1)) "$1" 2>/dev/null | grep -F -- "$3: "; }

cd_step() {   # <cooling device number> <policy number>
	local rid off L=$E/kmsg-live.txt
	case "$1$2" in ''|*[!0-9]*) echo "cd_step: numbers only" >&2; return 2;; esac
	klog_check || { echo "cd_step: not launched (no live log): STOP"; return 1; }
	rid=$(rid_ cd$1); off=$(stat -c %s "$L") || return 2
	R "setsid sh $PT/cd-step.sh $1 $2 $rid </dev/null >/dev/null 2>&1 &" || return 1
	if ! wait_end "$L" "$off" "a9-cd $rid: END "; then
		echo "cd_step $rid: TIMEOUT, no END line within ${STEP_WAIT}s: FAIL ($(klog_check >/dev/null && echo 'live log alive' || echo 'live log DEAD'); phone record:)"
		R "cat $PT/cd-$rid.txt $PSYS/class/thermal/cooling_device$1/cur_state"; return 1
	fi
	lines_ "$L" "$off" "a9-cd $rid" | tee "$E/cd-$rid.txt"
	R "cat $PT/cd-$rid.txt $PSYS/class/thermal/cooling_device$1/cur_state"
	lines_ "$L" "$off" "a9-cd $rid" | grep -qE 'REFUSED|FAIL' && return 1
	lines_ "$L" "$off" "a9-cd $rid" | grep -q ': END state 0$'
}

step() {   # <zone type> <millidegrees>; 0 only when THIS run printed PASS and END rc=0
	local rid off rc L=$E/kmsg-live.txt
	case "$UTS" in ''|*[!A-Za-z0-9\ \#:._+-]*) echo "step: UTS '$UTS' has characters the transport does not carry" >&2; return 2;; esac
	klog_check || { echo "step: not launched (no live log): STOP"; return 1; }
	rid=$(rid_ "$1-$2")
	irq_ "$E/irq-before-$rid.txt"
	off=$(stat -c %s "$L") || return 2
	R "setsid sh $PT/emul-step.sh $1 $2 $HOLD \"$UTS\" $rid </dev/null >/dev/null 2>&1 &" || return 1
	if ! wait_end "$L" "$off" "a9-emul $rid: END rc="; then
		echo "step $rid: TIMEOUT, no END line within ${STEP_WAIT}s: FAIL (no after snapshot; $(klog_check >/dev/null && echo 'live log alive' || echo 'live log DEAD'); phone log tail:)"
		lines_ "$L" "$off" "a9-emul $rid"; R "tail -n 20 $PT/a9-emul.log"; return 1
	fi
	irq_ "$E/irq-after-$rid.txt"
	lines_ "$L" "$off" "a9-emul $rid" | tee "$E/step-$rid.txt"
	rc=$(grep -F -- "a9-emul $rid: END rc=" "$E/step-$rid.txt" | tail -n1 | sed 's/.*END rc=//')
	if [ "$rc" = 0 ] && grep -qF -- "a9-emul $rid: PASS" "$E/step-$rid.txt"; then echo "step $rid: PASS"; return 0; fi
	echo "step $rid: FAIL (rc ${rc:-?})"; return 1
}
