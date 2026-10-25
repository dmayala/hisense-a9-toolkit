#!/usr/bin/env bash
# Host-side, reset-surviving capture for one Wi-Fi active trial (runs/wifi-run1-analysis.md,
# "Smallest capture change"; fixes per runs/review-42-wifi.md B1-B9). Coordinator-only.
#
# Streams over the existing root channel: `adb -s S shell 'nc 127.0.0.1 2324'` with the target
# script on adb's stdin. The listener (init.hlte556n.rc a9ml_rootsh: `nc -L /system/bin/sh`) runs a
# root sh on the socket, which executes every line it receives. Nothing is pushed to the target and no
# target file is created; the target readers print their own PID first and are stopped by that PID
# only (never pkill by name), after checking boot_id and the exact cmdline.
#
#   capture-run.sh start   <dir>              baseline (validated) + dmesg -w stream + poller
#   capture-run.sh barrier <dir> [--mark]     fresh baseline + liveness/freshness; prints CAPTURE READY
#                                             (--mark: also write one marker line to /dev/kmsg and wait for it)
#   capture-run.sh ctl     <dir> [--mark] -- cmd...
#                                             runs the barrier itself and, only if it passes, the controller
#                                             (run-mark; stdout/stderr spooled to files, timestamped; exit status)
#   capture-run.sh mark    <dir>              record the run start time only (if the controller is run otherwise)
#   capture-run.sh stop    <dir> [--wait|--force]
#                                             refuses within the hold window after the run mark while the phone is on
#                                             USB (default 420 s: guardian 270 s + dead-man 90 s + reset margin)
#
# Env: A9_SERIAL (else scripts/a9-env.sh resolves it; required, every adb call gets -s),
#      A9_CAPTURE_HOLD (s, default 420), A9_CAPTURE_STAY_MAX_AGE (s, default 300),
#      A9_CAPTURE_MIN_MARGIN (s, default 900). Logs may contain device identifiers: keep them private.
set -u
umask 077
cmd=${1:?start|barrier|ctl|mark|stop}; D=${2:?capture dir}; shift 2
HERE=$(cd "$(dirname "$0")" && pwd)
HOLD=${A9_CAPTURE_HOLD:-420}
STAY_MAX_AGE=${A9_CAPTURE_STAY_MAX_AGE:-300}
MIN_MARGIN=${A9_CAPTURE_MIN_MARGIN:-900}
RP=/mnt/a9-wifi/trial

if [ -z "${A9_SERIAL:-}" ] && [ -r "$HERE/../../../a9-env.sh" ]; then . "$HERE/../../../a9-env.sh" >/dev/null 2>&1 || :; fi
S=${A9_SERIAL:-}
case "$S" in ''|unknown-*) echo "no device serial (set A9_SERIAL or a9-devices.sh use)" >&2; exit 2;; esac

die() { echo "capture-run: $*" >&2; exit 1; }
now() { echo "$EPOCHREALTIME"; }
# R <payload>: one bounded root command; payload on stdin, so no quoting through adb/sh at all
R() { printf '%s\n' "$1" | timeout 40 adb -s "$S" shell 'timeout 30 nc 127.0.0.1 2324' 2>&1; }
on_usb() { [ "$(timeout 10 adb -s "$S" get-state </dev/null 2>/dev/null | tr -d '\r')" = device ]; }

BASELINE='echo BOOTID $(cat /proc/sys/kernel/random/boot_id)
echo UPTIME $(cat /proc/uptime)
echo WALL $(date +%s)
echo CMDLINE $(cat /proc/cmdline)
echo BOOTCOMPLETED $(getprop sys.boot_completed)
echo MAXSTAYPROP $(getprop vendor.a9ml.maxstay)
echo STAY $(ls -l --full-time /data/local/tmp/a9-stay 2>&1)
echo STAYMTIME $(stat -c %Y /data/local/tmp/a9-stay 2>/dev/null)
for r in /sys/class/remoteproc/remoteproc*; do echo RP ${r##*/} name=$(cat $r/name) state=$(cat $r/state) fw=$(cat $r/firmware) recovery=$(cat $r/recovery 2>/dev/null) coredump=$(cat $r/coredump 2>/dev/null) compat=$(tr "\000" "," < $r/device/of_node/compatible 2>/dev/null) dev=$(readlink -f $r/device); done
grep -iE "q6v5|mpss|modem|wdog|fatal|handover|ready" /proc/interrupts | sed "s/^/IRQ /"
echo PSTOREMOUNT $(mount | grep pstore)
ls -la /sys/fs/pstore 2>&1 | sed "s/^/PSTORELS /"
dmesg | grep -iE "ramoops|pstore" | sed "s/^/PSTORELOG /"
for z in /sys/class/thermal/thermal_zone*; do echo THERMAL ${z##*/} $(cat $z/type 2>/dev/null) $(cat $z/temp 2>/dev/null); done
b=/sys/class/power_supply/battery; echo BATT cap=$(cat $b/capacity 2>/dev/null) temp=$(cat $b/temp 2>/dev/null) uv=$(cat $b/voltage_now 2>/dev/null) ua=$(cat $b/current_now 2>/dev/null) status=$(cat $b/status 2>/dev/null)
ls -la --full-time /mnt/a9-wifi '"$RP"' 2>&1 | sed "s/^/WIFIDIR /"
[ -r '"$RP"'/stage ] && echo STAGE $(cat '"$RP"'/stage)
[ -r '"$RP"'/fault ] && head -c 2048 '"$RP"'/fault | sed "s/^/FAULT /"
getprop | grep -iE "deadman|a9ml" | sed "s/^/PROP /"
echo BASELINE-END'

# validate <file> start|barrier: real content, not adb/nc error text (review 42 B4)
validate() {
  local f=$1 mode=$2 ok=1 l
  # error text anywhere except inside tagged target data lines (dmesg/ls/fault text may say "not found")
  if grep -vE '^(PSTORELOG|PSTORELS|IRQ|WIFIDIR|PROP|FAULT|THERMAL|STAY|CMDLINE) ' "$f" |
     grep -qiE '^(error|adb|nc|sh|timeout|/system/bin/sh):|no devices|device offline|unauthorized|connection refused|not found'; then
    echo "baseline has adb/nc/shell error text"; ok=0; fi
  [ "$(grep -cE '^BOOTID [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "$f")" = 1 ] || { echo "no valid boot_id"; ok=0; }
  grep -qx 'BOOTCOMPLETED 1' "$f" || { echo "sys.boot_completed is not 1"; ok=0; }
  grep -qx 'BASELINE-END' "$f" || { echo "baseline truncated"; ok=0; }
  l=$(grep -E '^RP .* compat=([^ ]*,)?qcom,sm6115-mpss-pas,' "$f")
  if [ "$(printf '%s' "$l" | grep -c .)" != 1 ]; then echo "expected exactly one SM6115 MPSS remoteproc"; ok=0
  else
    case "$l" in *' state=offline '*) ;; *) echo "MPSS not offline: $l"; ok=0;; esac
    [ "$mode" = barrier ] && case "$l" in *' recovery=disabled '*) ;; *) echo "MPSS recovery not disabled: $l"; ok=0;; esac
  fi
  grep -qiE '^PSTORELOG .*(ramoops|pstore)|^PSTOREMOUNT .*pstore' "$f" || { echo "no pstore/ramoops line"; ok=0; }
  [ $ok = 1 ]
}

# deadman <file>: stay file fresh enough and the uptime cap far enough away (review 42 precondition 2)
deadman() {
  awk -v maxage="$STAY_MAX_AGE" -v minmargin="$MIN_MARGIN" '
    $1=="WALL"{wall=$2} $1=="UPTIME"{up=int($2)} $1=="STAYMTIME"{st=$2} $1=="MAXSTAYPROP"{prop=$2}
    $1=="CMDLINE"{for(i=2;i<=NF;i++){split($i,kv,"="); if(kv[1]=="a9ml.maxstay")cm=kv[2]; if(kv[1]=="a9ml.deadman")dm=kv[2]}}
    END{ if(dm=="0"){print "DEADMAN disabled (a9ml.deadman=0)"; exit 0}
      if(cm=="")cm=7200; if(prop!~/^[0-9]+$/)prop=0; if(prop>21600)prop=21600; cap=(prop>cm)?prop:cm
      if(st==""){print "DEADMAN stay file missing: touch /data/local/tmp/a9-stay before the barrier"; exit 1}
      age=wall-st; margin=cap-up
      printf "DEADMAN stay_age=%ds cap=%ds uptime=%ds margin=%ds\n", age, cap, up, margin
      if(age>=maxage){print "DEADMAN stay file older than " maxage "s"; exit 1}
      if(margin<=minmargin){print "DEADMAN uptime cap margin <= " minmargin "s"; exit 1} }' "$1"
}
envwarn() { # warnings only: thermal and battery (review 42 precondition 3)
  awk '$1=="THERMAL" && $4~/^[0-9]+$/ && $4>=45000 {print "WARN thermal " $2 " " $3 " " $4}
       $1=="BATT"{for(i=2;i<=NF;i++){split($i,kv,"="); if(kv[1]=="cap" && kv[2]~/^[0-9]+$/ && kv[2]<50) print "WARN battery " kv[2] "%"}}' "$1"
}

# stream <name> <payload-file>: detached host reader, one line per received target line, host receive time first
READER='printf -v cr "\r"; adb -s "$1" shell "nc 127.0.0.1 2324" < "$2" 2>&1 | while IFS= read -r l; do printf "%s %s\n" "$EPOCHREALTIME" "${l%$cr}"; done >> "$3"'
stream() {
  : > "$D/$1.txt"
  setsid nohup bash -c "$READER" _ "$S" "$2" "$D/$1.txt" </dev/null >/dev/null 2>&1 &
  echo $! > "$D/$1.hostpid"
}
# tpid <name> <tag>: the target PID the reader printed as its first line
tpid() { sed -n "1s/^[0-9.]* $2 \([0-9][0-9]*\)\$/\1/p" "$D/$1.txt" 2>/dev/null; }
wait_tpid() { local i; for i in $(seq 1 40); do [ -n "$(tpid "$1" "$2")" ] && return 0; sleep 0.25; done; return 1; }
host_alive() { local p; p=$(cat "$D/$1.hostpid" 2>/dev/null) && kill -0 "$p" 2>/dev/null; }
host_kill() {
  local p s i; for s in "$@"; do p=$(cat "$D/$s.hostpid" 2>/dev/null) || continue
    kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null
    for i in $(seq 1 20); do kill -0 "$p" 2>/dev/null || break; sleep 0.25; done
    kill -KILL -- "-$p" 2>/dev/null || :; done
}
# target_stop: kill the recorded target readers by PID, same boot only, exact cmdline only
target_stop() {
  local boot dp pp
  boot=$(sed -n 's/^BOOTID //p' "$D/baseline-start.txt" 2>/dev/null)
  dp=$(tpid dmesg CAPPID); pp=$(tpid poll POLLPID)
  [ -n "$boot" ] || { echo "CAPSTOP no recorded boot_id; target readers left alone"; return 1; }
  [ -n "$dp$pp" ] || { echo "CAPSTOP no recorded target PIDs"; return 0; }
  R "echo CAPSTOP begin
b=\$(cat /proc/sys/kernel/random/boot_id)
if [ \"\$b\" != $boot ]; then echo CAPSTOP boot-changed \$b; else
for e in ${dp:+$dp:dmesg} ${pp:+$pp:sh}; do p=\${e%%:*}; w=\${e#*:}; c=\$(tr '\\000' ' ' < /proc/\$p/cmdline 2>/dev/null)
case \"\$w:\$c\" in 'dmesg:dmesg -w '|'dmesg:'*/dmesg' -w '|'sh:sh '|'sh:'*/sh' ') kill \$p && echo CAPSTOP killed \$p \"\$c\" || echo CAPSTOP kill-failed \$p;; *) echo CAPSTOP skip \$p \"[\$c]\";; esac
done; fi
echo CAPSTOP end"
}

# fresh <file> [pattern]: last matching line's host timestamp is 0..2 s old (future stamps rejected)
fresh() {
  local t; t=$(grep -E "^[0-9]+\.[0-9]+ ${2:-}" "$1" 2>/dev/null | tail -n 1 | cut -d' ' -f1)
  awk -v n="$(now)" -v t="${t:-0}" 'BEGIN{a=n-t; exit !(t>0 && a>=0 && a<2)}'
}
# do_barrier <mark 0|1>: readiness, judged at the END (after every wait) and recorded only on success.
# Any earlier readiness token is removed first, so a failed barrier always leaves the dir NOT ready.
do_barrier() {
  local mark=$1 ok=1 i tok seen
  rm -f "$D/barrier.epoch"
  [ -n "$(tpid dmesg CAPPID)" ] && [ -n "$(tpid poll POLLPID)" ] || { echo "target reader PID missing"; ok=0; }
  [ "$(wc -l < "$D/dmesg.txt" 2>/dev/null || echo 0)" -gt 100 ] || { echo "dmesg stream has no ring data"; ok=0; }
  [ "$(grep -c '^[0-9.]* POLL ' "$D/poll.txt" 2>/dev/null)" -gt 5 ] || { echo "poller has no records"; ok=0; }
  R "$BASELINE" > "$D/baseline-barrier.txt"
  validate "$D/baseline-barrier.txt" barrier || ok=0
  [ "$(sed -n 's/^BOOTID //p' "$D/baseline-barrier.txt")" = "$(sed -n 's/^BOOTID //p' "$D/baseline-start.txt" 2>/dev/null)" ] || { echo "boot_id changed since start"; ok=0; }
  deadman "$D/baseline-barrier.txt" || ok=0
  envwarn "$D/baseline-barrier.txt"
  if [ $mark = 1 ]; then
    # the marker is the dmesg proof: a quiet kernel is fine once our line comes back through the stream
    tok="a9-capture-barrier-$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
    R "echo $tok > /dev/kmsg" > /dev/null
    seen=0; for i in $(seq 1 40); do grep -q "$tok" "$D/dmesg.txt" && { seen=1; break; }; sleep 0.25; done
    [ $seen = 1 ] || { echo "kmsg marker not seen in dmesg stream"; ok=0; }
  else
    for i in $(seq 1 40); do fresh "$D/dmesg.txt" && break; sleep 0.25; done
  fi
  # final, immediately before approval: both host pipelines alive, last POLL and last dmesg line < 2 s old
  for s in dmesg poll; do host_alive "$s" || { echo "dead host stream $s"; ok=0; }; done
  fresh "$D/poll.txt" 'POLL ' || { echo "poller stale at approval (last POLL $(grep '^[0-9.]* POLL ' "$D/poll.txt" | tail -n 1 | cut -d' ' -f1))"; ok=0; }
  fresh "$D/dmesg.txt" || { echo "dmesg stream stale at approval (quiet kernel? use --mark)"; ok=0; }
  if [ $ok = 1 ]; then now > "$D/barrier.epoch"; echo "CAPTURE READY"; return 0; fi
  echo "CAPTURE NOT READY"; return 1
}

case $cmd in
start)
  umask 077; [ -e "$D" ] && die "exists: $D"; mkdir -m 0700 "$D" || die "cannot create $D"
  echo "$S" > "$D/serial"; now > "$D/start.epoch"
  R "$BASELINE" > "$D/baseline-start.txt"
  validate "$D/baseline-start.txt" start > "$D/baseline-start.check" || { cat "$D/baseline-start.check" >&2; die "baseline invalid; nothing started"; }
  envwarn "$D/baseline-start.txt"
  # stream 1: unfiltered kernel log, never cleared. exec keeps the printed PID = the dmesg PID.
  printf 'echo CAPPID $$; exec dmesg -w\n' > "$D/dmesg.cmd"
  stream dmesg "$D/dmesg.cmd"
  wait_tpid dmesg CAPPID || { host_kill dmesg; die "dmesg stream did not report its PID (see $D/dmesg.txt)"; }
  # stream 2: the poller, sent as text over the socket (nothing written on the target)
  stream poll "$HERE/poll-target.sh"
  if ! wait_tpid poll POLLPID; then
    target_stop > "$D/stop-target.txt"; host_kill dmesg poll; die "poller did not report its PID; dmesg stream stopped"
  fi
  # periodic host fsync (syncfs covers all files in $D)
  setsid nohup bash -c 'while kill -0 "$1" 2>/dev/null || kill -0 "$2" 2>/dev/null; do sync -f "$3" 2>/dev/null; sleep 1; done' \
    _ "$(cat "$D/dmesg.hostpid")" "$(cat "$D/poll.hostpid")" "$D/dmesg.txt" </dev/null >/dev/null 2>&1 &
  echo $! > "$D/sync.hostpid"
  echo "started $D (target dmesg pid $(tpid dmesg CAPPID), poller pid $(tpid poll POLLPID))" ;;
barrier)
  mark=0; [ "${1:-}" = --mark ] && mark=1
  do_barrier "$mark" ;;
mark)
  now > "$D/run.epoch"; echo "run mark $(cat "$D/run.epoch")" ;;
ctl)
  # The barrier runs HERE, in the same invocation, immediately before the controller (review 45 #1).
  mark=0; [ "${1:-}" = --mark ] && { mark=1; shift; }
  [ "${1:-}" = -- ] && shift; [ $# -gt 0 ] || die "ctl needs a command"
  [ -e "$D/run.epoch" ] && die "a run was already started from $D (run.epoch exists)"
  do_barrier "$mark" || die "barrier failed; controller NOT run"
  now > "$D/run.epoch"; printf '%q ' "$@" > "$D/ctl-cmd.txt"
  # Controller stdout/stderr go straight to private files (plain file writes, never a pipe), so no
  # reader and no caller can back-pressure it (review 45 #3). Independent followers timestamp the
  # lines into ctl-std*.txt and show them to the caller; a stuck caller only stalls its display tail.
  : > "$D/ctl-stdout.raw"; : > "$D/ctl-stderr.raw"
  exec 3<&0
  "$@" <&3 >> "$D/ctl-stdout.raw" 2>> "$D/ctl-stderr.raw" & cpid=$!
  exec 3<&-
  trap 'kill -TERM "$cpid" 2>/dev/null' INT TERM HUP
  echo "$cpid" > "$D/ctl.pid"
  TSF='printf -v cr "\r"; tail -n +1 -F --pid="$1" "$2" 2>/dev/null | while IFS= read -r l; do printf "%s %s\n" "$EPOCHREALTIME" "${l%$cr}"; done >> "$3"'
  setsid nohup bash -c "$TSF" _ "$cpid" "$D/ctl-stdout.raw" "$D/ctl-stdout.txt" </dev/null >/dev/null 2>&1 & f1=$!
  setsid nohup bash -c "$TSF" _ "$cpid" "$D/ctl-stderr.raw" "$D/ctl-stderr.txt" </dev/null >/dev/null 2>&1 & f2=$!
  tail -n +1 -F --pid="$cpid" "$D/ctl-stdout.raw" 2>/dev/null & v1=$!
  tail -n +1 -F --pid="$cpid" "$D/ctl-stderr.raw" >&2 2>/dev/null & v2=$!
  wait "$cpid"; rc=$?
  echo "$rc $(now)" > "$D/ctl-exit.txt"
  # followers: the timestamp files only depend on the disk, wait for them (bounded); display tails get 2 s
  for i in $(seq 1 40); do kill -0 "$f1" 2>/dev/null || kill -0 "$f2" 2>/dev/null || break; sleep 0.25; done
  for p in "$f1" "$f2"; do kill -TERM -- "-$p" 2>/dev/null || :; done
  for i in $(seq 1 8); do kill -0 "$v1" 2>/dev/null || kill -0 "$v2" 2>/dev/null || break; sleep 0.25; done
  kill "$v1" "$v2" 2>/dev/null || :
  echo "lines stdout=$(wc -l < "$D/ctl-stdout.raw")/$(wc -l < "$D/ctl-stdout.txt") stderr=$(wc -l < "$D/ctl-stderr.raw")/$(wc -l < "$D/ctl-stderr.txt") (raw/timestamped)" >> "$D/ctl-exit.txt"
  timeout 2 echo "controller exit $rc" >&2 2>/dev/null; exit "$rc" ;;  # never block on a stuck caller
stop)
  mode=${1:-}
  present=0; on_usb && present=1
  if [ -e "$D/run.epoch" ] && [ $present = 1 ] && [ "$mode" != --force ]; then
    el=$(awk -v n="$(now)" -v t="$(cat "$D/run.epoch")" 'BEGIN{printf "%d", n-t}')
    if [ "$el" -lt "$HOLD" ]; then
      [ "$mode" = --wait ] || die "hold window: $el s of $HOLD s since run and the phone is still on USB (use --wait or --force)"
      while [ "$el" -lt "$HOLD" ] && on_usb; do sleep 5; el=$(awk -v n="$(now)" -v t="$(cat "$D/run.epoch")" 'BEGIN{printf "%d", n-t}'); done
      on_usb || present=0
    fi
  fi
  [ -e "$D/run.epoch" ] || echo "note: no run mark (ctl/mark); the hold window was not enforced"
  # target side first (readers only, by PID), then the host pipelines
  if [ $present = 1 ]; then target_stop > "$D/stop-target.txt"; else echo "phone not on USB; target readers not contacted" > "$D/stop-target.txt"; fi
  cat "$D/stop-target.txt"
  host_kill dmesg poll sync
  {
    echo "stop $(now) mode=${mode:-default} phone_on_usb=$present hold=${HOLD}s run_mark=$(cat "$D/run.epoch" 2>/dev/null || echo none)"
    for f in dmesg poll; do
      echo "$f.txt lines=$(wc -l < "$D/$f.txt") first=$(head -n 1 "$D/$f.txt" | cut -d' ' -f1) last=$(tail -n 1 "$D/$f.txt" | cut -d' ' -f1) target_pid=$(tpid "$f" "$([ $f = dmesg ] && echo CAPPID || echo POLLPID)")"
    done
    [ -e "$D/ctl-exit.txt" ] && echo "controller $(tr '\n' ' ' < "$D/ctl-exit.txt")"
  } | tee "$D/eof.txt"
  sync ;;
*) die "unknown command $cmd" ;;
esac
