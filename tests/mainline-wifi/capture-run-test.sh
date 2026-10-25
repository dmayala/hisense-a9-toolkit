#!/usr/bin/env bash
# Offline test of scripts/mainline/wifi/host/capture-run.sh against a fake adb on PATH.
# No phone: the fake adb refuses any call without `-s FAKESERIAL`, serves a canned baseline, and runs
# every other root-channel payload with the host's sh (dmesg shimmed), so the real poller and the real
# PID-based stop logic execute here. Exercises start/barrier/ctl/stop, quoting, PID-only stop, hold
# window and the failure cases from review 42. Usage: tests/mainline-wifi/capture-run-test.sh (~40 s)
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); A9=$(cd "$HERE/../.." && pwd)
CAP=$A9/scripts/mainline/wifi/host/capture-run.sh
T=$(mktemp -d); export FAKE=$T/fake; mkdir -p "$FAKE/bin" "$FAKE/shim"
DECOY=
cleanup() { [ -n "$DECOY" ] && kill "$DECOY" 2>/dev/null; pkill -f "$T" 2>/dev/null; sleep 0.3; pkill -9 -f "$T" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT
PASS=0; FAILN=0
ok() { echo "  PASS  $1"; PASS=$((PASS + 1)); }
no() { echo "  FAIL  $1"; FAILN=$((FAILN + 1)); }
check() { local d=$1; shift; if "$@"; then ok "$d"; else no "$d"; fi; }

cat > "$FAKE/bin/adb" <<'EOF'
#!/usr/bin/env bash
[ "${1:-}" = -s ] || { echo "error: fake adb requires -s" >&2; exit 1; }
[ "$2" = FAKESERIAL ] || { echo "error: device '$2' not found" >&2; exit 1; }
shift 2; echo "$*" >> "$FAKE/adb.log"
state=$(cat "$FAKE/state" 2>/dev/null || echo device)
case "${1:-}" in
  get-state) [ "$state" = device ] && { echo device; exit 0; }; echo "error: no devices/emulators found" >&2; exit 1;;
  shell) ;;
  *) echo "fake adb: unsupported $1" >&2; exit 1;;
esac
[ "$state" = device ] || { echo "error: no devices/emulators found" >&2; exit 1; }
case "${2:-}" in *"nc 127.0.0.1 2324"*) ;; *) echo "fake adb: not the root channel: $2" >&2; exit 1;; esac
mode=$(cat "$FAKE/mode" 2>/dev/null || echo good)
[ "$mode" = adberror ] && { echo "error: no devices/emulators found"; exit 1; }
pay=$(mktemp "$FAKE/pay.XXXXXX"); cat > "$pay"
if grep -q '^echo BASELINE-END$' "$pay"; then
  boot=$(cat "$FAKE/bootid" 2>/dev/null || cat /proc/sys/kernel/random/boot_id)
  wall=$(date +%s); st=$((wall - 10)); [ "$mode" = stalestay ] && st=$((wall - 400))
  bc=1; [ "$mode" = notcompleted ] && bc=0
  ms=offline; [ "$mode" = mpssrunning ] && ms=running
  echo "BOOTID $boot"; echo "UPTIME 600.12 500.00"; echo "WALL $wall"
  echo "CMDLINE console=x a9ml.maxstay=7200 a9ml.hold=1200"; echo "BOOTCOMPLETED $bc"; echo "MAXSTAYPROP "
  echo "STAY -rw------- 1 root root 0 now /data/local/tmp/a9-stay"; echo "STAYMTIME $st"
  echo "RP remoteproc0 name=adsp state=offline fw=adsp.mdt recovery=enabled coredump=disabled compat=qcom,sm6115-adsp-pas, dev=/sys/x/a400000.remoteproc"
  echo "RP remoteproc1 name=6080000.remoteproc state=$ms fw=modem.mdt recovery=disabled coredump=disabled compat=qcom,sm6115-mpss-pas, dev=/sys/x/6080000.remoteproc"
  echo "IRQ 100: 0 GIC q6v5 wdog"
  [ "$mode" = nopstore ] || echo "PSTORELOG [    0.5] ramoops: using 0x100000@0xb0580000"
  echo "THERMAL thermal_zone0 cpu 35000"; echo "BATT cap=80 temp=300 uv=4000000 ua=-100 status=Charging"
  echo "BASELINE-END"
elif grep -q '/dev/kmsg' "$pay"; then
  tok=$(sed -n 's/^echo \(a9-capture-barrier-[0-9a-f]*\) > \/dev\/kmsg$/\1/p' "$pay")
  if [ "$mode" = slowmark ]; then ( sleep 3; echo "$tok" >> "$FAKE/kmsg-inject" ) >/dev/null 2>&1 &
  else echo "$tok" >> "$FAKE/kmsg-inject"; fi
else
  cp "$pay" "$FAKE/last-payload"
  PATH=$FAKE/shim:$PATH exec sh < "$pay"
fi
EOF
cat > "$FAKE/shim/dmesg" <<'EOF'
#!/usr/bin/env bash
[ "${1:-}" = -w ] || exit 0
for i in $(seq 1 150); do echo "[    0.$i] ring line $i"; done
while :; do sleep 0.3; [ -e "$FAKE/quiet" ] || echo "[ tick ] $i"; i=$((i + 1))
  if [ -s "$FAKE/kmsg-inject" ]; then cat "$FAKE/kmsg-inject"; : > "$FAKE/kmsg-inject"; fi; done
EOF
chmod +x "$FAKE/bin/adb" "$FAKE/shim/dmesg"
export PATH=$FAKE/bin:$PATH A9_SERIAL=FAKESERIAL
reset() { rm -f "$FAKE/mode" "$FAKE/state" "$FAKE/bootid" "$FAKE/adb.log" "$FAKE/quiet"; }
tp() { sed -n "1s/^[0-9.]* $3 \([0-9][0-9]*\)\$/\1/p" "$1/$2.txt"; }  # tp <dir> <stream> <tag>
alive() { kill -0 "$1" 2>/dev/null; }

echo "== happy path, awkward directory name"
reset; C="$T/cap 'q' \$x dir"
out=$("$CAP" start "$C" 2>&1); check "start succeeds" grep -q '^started' <<<"$out"
check "dir 0700" [ "$(stat -c %a "$C")" = 700 ]
check "files 0600" [ -z "$(find "$C" -type f ! -perm 0600)" ]
check "nothing pushed to the target" bash -c '! grep -qE "^(push|install)" "$FAKE/adb.log"'
DP=$(tp "$C" dmesg CAPPID); PP=$(tp "$C" poll POLLPID)
check "target PIDs recorded" [ -n "$DP" -a -n "$PP" ]
mkdir -p "$FAKE/decoy"; cp "$FAKE/shim/dmesg" "$FAKE/decoy/dmesg"; FAKE=$T/nowhere "$FAKE/decoy/dmesg" -w >/dev/null & DECOY=$!  # another 'dmesg -w'
sleep 3
out=$("$CAP" barrier "$C" 2>&1); check "barrier ready" grep -qx 'CAPTURE READY' <<<"$out"
check "barrier reports dead-man margin" grep -q '^DEADMAN stay_age=' <<<"$out"
out=$("$CAP" barrier "$C" --mark 2>&1); check "barrier --mark ready (marker seen)" grep -qx 'CAPTURE READY' <<<"$out"
check "poller ran real loop" grep -q '^[0-9.]* TRIAL stage=' "$C/poll.txt"
"$CAP" ctl "$C" -- bash -c 'python3 "$1/coordinator-rehearsal.py" "$FAKE/bin/adb" || exit 2; echo out1; echo "err '\''1'\''" >&2; exit 3' sh "$HERE" >/dev/null 2>&1; rc=$?
check "ctl passes the exit status" [ $rc = 3 ]
check "ctl stdout timestamped" grep -q '^[0-9]*\.[0-9]* out1$' "$C/ctl-stdout.txt"
check "ctl stderr timestamped" grep -q "^[0-9]*\.[0-9]* err '1'\$" "$C/ctl-stderr.txt"
check "ctl exit recorded" grep -q '^3 ' "$C/ctl-exit.txt"
check "actual controller prepare/run/healthy/failure/stop through fake adb" grep -q 'FAKE-ADB actual prepare/run/preflight-failure/stop PASS' "$C/ctl-stdout.txt"
if ! grep -q 'preflight-failure/stop PASS' "$C/ctl-stdout.txt"; then cat "$C/ctl-stdout.txt" "$C/ctl-stderr.txt"; fi
"$CAP" stop "$C" >/dev/null 2>&1; rc=$?
check "stop refused inside the hold window" [ $rc != 0 ]
check "readers survive the refused stop" bash -c "kill -0 $DP && kill -0 $PP"
A9_CAPTURE_HOLD=2 "$CAP" stop "$C" --wait >/dev/null 2>&1; sleep 0.5
check "stop --wait killed the target dmesg by PID" bash -c "! kill -0 $DP 2>/dev/null"
check "stop --wait killed the target poller by PID" bash -c "! kill -0 $PP 2>/dev/null"
check "unrelated dmesg-named process untouched" alive "$DECOY"
check "stop log shows two PID kills" [ "$(grep -c '^CAPSTOP killed' "$C/stop-target.txt")" = 2 ]
check "host pipelines gone" bash -c "! kill -0 $(cat "$C/dmesg.hostpid") 2>/dev/null && ! kill -0 $(cat "$C/poll.hostpid") 2>/dev/null"
check "eof.txt written" grep -q '^dmesg.txt lines=' "$C/eof.txt"
check "all files 0600 after barrier/ctl/stop" [ -z "$(find "$C" -type f ! -perm 0600)" ]
check "every adb call had -s" bash -c '! grep -q "fake adb requires" "$1"/*.txt' _ "$C"

echo "== review 45 regressions"
# 1: readiness cannot be stale or survive a failed barrier; ctl runs its own barrier
reset; C=$T/r45-stale; "$CAP" start "$C" >/dev/null 2>&1; sleep 3
P=$(tp "$C" poll POLLPID); kill -STOP "$P"; sleep 2.5
echo 1.000000 > "$C/barrier.epoch"
"$CAP" ctl "$C" -- touch "$T/ran-stale" >/dev/null 2>&1; rc=$?; kill -CONT "$P"
check "ctl ignores an old barrier.epoch and refuses (stale poller)" [ $rc != 0 -a ! -e "$T/ran-stale" ]
check "failed ctl barrier leaves no readiness token" [ ! -e "$C/barrier.epoch" ]
sleep 1; out=$("$CAP" barrier "$C" 2>&1); check "barrier passes again after resume" grep -qx 'CAPTURE READY' <<<"$out"
echo adberror > "$FAKE/mode"; "$CAP" barrier "$C" >/dev/null 2>&1
check "a failed barrier removes the earlier READY token" [ ! -e "$C/barrier.epoch" ]
"$CAP" ctl "$C" -- touch "$T/ran-after-fail" >/dev/null 2>&1; rc=$?
check "ctl refuses after a failed barrier" [ $rc != 0 -a ! -e "$T/ran-after-fail" ]
rm -f "$FAKE/mode"; "$CAP" stop "$C" --force >/dev/null 2>&1
# 2: freshness judged after the waits
reset; C=$T/r45-fresh; "$CAP" start "$C" >/dev/null 2>&1; sleep 3
echo slowmark > "$FAKE/mode"; P=$(tp "$C" poll POLLPID); kill -STOP "$P"
out=$("$CAP" barrier "$C" --mark 2>&1); kill -CONT "$P"
check "poller that stalls during the marker wait is caught at approval" grep -q 'poller stale at approval' <<<"$out"
check "...and the barrier is not ready" [ ! -e "$C/barrier.epoch" ]
rm -f "$FAKE/mode"; touch "$FAKE/quiet"; sleep 2.5
out=$("$CAP" barrier "$C" 2>&1); check "quiet dmesg without --mark is not ready" grep -q 'dmesg stream stale at approval' <<<"$out"
out=$("$CAP" barrier "$C" --mark 2>&1); check "quiet dmesg with --mark is ready (marker is the proof)" grep -qx 'CAPTURE READY' <<<"$out"
rm -f "$FAKE/quiet"; "$CAP" stop "$C" --force >/dev/null 2>&1
# 3: an unread caller cannot back-pressure the controller
reset; C=$T/r45-bp; "$CAP" start "$C" >/dev/null 2>&1; sleep 3
mkfifo "$T/fifo"; sleep 60 < "$T/fifo" & HOLDER=$!
"$CAP" ctl "$C" -- bash -c 'seq 1 20000; seq 1 3000 >&2; touch "$1"; exit 5' _ "$T/bp-marker" > "$T/fifo" 2>"$T/fifo" & CTL=$!
for i in $(seq 1 20); do [ -e "$T/bp-marker" ] && break; sleep 0.25; done
check "controller reached its marker with the caller's output unread" [ -e "$T/bp-marker" ]
for i in $(seq 1 60); do kill -0 $CTL 2>/dev/null || break; sleep 0.25; done
wait $CTL; rc=$?; kill $HOLDER 2>/dev/null
check "ctl returns the controller status despite the stuck caller" [ $rc = 5 ]
check "exit status recorded" grep -q '^5 ' "$C/ctl-exit.txt"
check "all 20000 stdout lines timestamped" [ "$(grep -c '^[0-9]*\.[0-9]* [0-9]*$' "$C/ctl-stdout.txt")" = 20000 ]
check "all 3000 stderr lines timestamped" [ "$(grep -c '^[0-9]*\.[0-9]* [0-9]*$' "$C/ctl-stderr.txt")" = 3000 ]
"$CAP" stop "$C" --force >/dev/null 2>&1

echo "== failure cases"
for m in adberror notcompleted mpssrunning nopstore; do
  reset; echo $m > "$FAKE/mode"; C=$T/fail-$m
  "$CAP" start "$C" >/dev/null 2>&1; rc=$?
  check "start refuses baseline: $m" [ $rc != 0 ]
  check "no stream started: $m" [ ! -e "$C/dmesg.hostpid" ]
done

reset; C=$T/stale; "$CAP" start "$C" >/dev/null 2>&1; sleep 3
P=$(tp "$C" poll POLLPID); kill -STOP "$P"; sleep 2.5
out=$("$CAP" barrier "$C" 2>&1); kill -CONT "$P"
check "barrier rejects a stalled poller" grep -q 'poller stale' <<<"$out"
check "barrier not ready" grep -qx 'CAPTURE NOT READY' <<<"$out"
echo stalestay > "$FAKE/mode"; out=$("$CAP" barrier "$C" 2>&1)
check "barrier rejects an old stay file" grep -q 'DEADMAN stay file older' <<<"$out"
echo adberror > "$FAKE/mode"; out=$("$CAP" barrier "$C" 2>&1)
check "barrier rejects adb error text" grep -qx 'CAPTURE NOT READY' <<<"$out"
rm -f "$FAKE/mode"; "$CAP" stop "$C" --force >/dev/null 2>&1

reset; C=$T/reboot; "$CAP" start "$C" >/dev/null 2>&1; sleep 1
DP=$(tp "$C" dmesg CAPPID)
echo 00000000-0000-0000-0000-000000000000 > "$FAKE/bootid"
out=$("$CAP" barrier "$C" 2>&1); check "barrier rejects a changed boot_id" grep -q 'boot_id changed' <<<"$out"
sed -i 's/^BOOTID .*/BOOTID 11111111-1111-1111-1111-111111111111/' "$C/baseline-start.txt"
"$CAP" stop "$C" --force >/dev/null 2>&1
check "stop kills nothing after a reboot (boot_id differs)" grep -q '^CAPSTOP boot-changed' "$C/stop-target.txt"

reset; C=$T/gone; "$CAP" start "$C" >/dev/null 2>&1; "$CAP" mark "$C" >/dev/null
echo offline > "$FAKE/state"; "$CAP" stop "$C" >/dev/null 2>&1; rc=$?
check "stop proceeds when the phone has left USB" [ $rc = 0 ]
check "target not contacted when gone" grep -q 'phone not on USB' "$C/stop-target.txt"
check "eof records phone_on_usb=0" grep -q 'phone_on_usb=0' "$C/eof.txt"

reset; A9_SERIAL= A9_DEVICE=no-such-phone "$CAP" start "$T/noserial" >/dev/null 2>&1; rc=$?
check "refuses without a resolved serial" [ $rc = 2 -a ! -e "$T/noserial" ]

echo "== $PASS passed, $FAILN failed"
[ $FAILN = 0 ]
