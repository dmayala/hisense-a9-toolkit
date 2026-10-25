#!/usr/bin/env bash
# Offline rehearsal of r0-ghost.sh (review 89b R1): fake adb, fake android-boot.sh, fake a9-health.sh. No phone.
D=$(cd "$(dirname "$0")" && pwd)
RUNNER=${RUNNER:-$D/../../scripts/eink-probe/sessions/r0-ghost.sh}
unset GHOST_NO_STOCK GHOST_MATCHED_SOURCE GHOST_LAUNCHER_COMPONENT GHOST_SOURCE_VIEWER_JSON
failures=0
R=$(mktemp -d /tmp/eink-ghost-rehearsal.XXXXXX)
echo "scratch: $R"
mk() { mkdir -p "$(dirname "$1")"; cat > "$1"; chmod +x "$1"; }
mk $R/bin/adb <<'A'
#!/usr/bin/env bash
echo "adb $*" >> "$FAKE/adb.log"
case "$*" in
  *SAFE3_PREP_CLOSED*)
    printf 'SAFE3_GATE \nSAFE3_BOOL a9_eink/parameters/enable N\nSAFE3_BOOL fp9929a/parameters/allow_power_on N\nSAFE3_BOOL panel_hisense_hlte556n_eink/parameters/enable N\nenable 0 waveform 1 transport 1 hv_on 0\ngpiochip0: 114 GPIOs, parent: platform/500000.pinctrl, 500000.pinctrl:\n gpio36 : out low func0 8mA pull up\n gpio86 : out low func0 8mA pull up\nSAFE3_ROOT_RC 0\n';;
  *SAFE3_PREP_VERIFY*)
    printf 'SAFE3_PROVISIONED 1\nSAFE3_USER_SETUP 1\nSAFE3_NIGHT 1\nSAFE3_STAY 7\nSAFE3_DISABLED org.lineageos.setupwizard\nSAFE3_DISABLED com.google.android.setupwizard\nSAFE3_HOME com.android.launcher3/.Launcher\nSAFE3_RESUMED com.android.launcher3/.Launcher\nSAFE3_PID 3153\nSAFE3_START 14000\nSAFE3_SETUP verified=yes\nSAFE3_ROOT_RC 0\n';;
  *'cat /sys/class/drm/card3-DSI-1/eink/status; cat'* )
    [ -e "$FAKE/final.sleep" ] && { touch "$FAKE/final.active"; sleep "$(cat "$FAKE/final.sleep")"; };;
  *"getprop ro.boot.slot_suffix"*) cat "$FAKE/slot" 2>/dev/null;;
  *"getprop sys.boot_completed"*) cat "$FAKE/bc" 2>/dev/null;;
  *"resolve-activity"*) cat "$FAKE/home";;
esac
exit 0
A
for t in fastboot; do printf '#!/bin/sh\necho "TRIPWIRE %s $*" >> "$FAKE/tripwire.log"; exit 99\n' $t > $R/bin/$t; chmod +x $R/bin/$t; done
REAL_PYTHON=$(command -v python3); export REAL_PYTHON
mk "$R/bin/python3" <<'A'
#!/usr/bin/env bash
case "$*" in
  *ghost_attempt.py*finish*)
    "$REAL_PYTHON" "$@"; rc=$?
    if [ -e "$FAKE/accounting.sleep" ] && [ ! -e "$FAKE/accounting.active" ]; then touch "$FAKE/accounting.active"; sleep "$(cat "$FAKE/accounting.sleep")"; fi
    exit "$rc";;
  *) exec "$REAL_PYTHON" "$@";;
esac
A
mk $R/a9/scripts/a9-health.sh <<'A'
#!/usr/bin/env bash
if [ -s "$FAKE/restore.called" ] && [ -e "$FAKE/bad-final-health" ]; then echo '{}'; else cat "$FAKE/health"; fi
A
mk $R/a9/scripts/mainline/android/android-boot.sh <<'A'
#!/usr/bin/env bash
[ "$1" = --restore ] && { echo restore >> "$FAKE/restore.called"; [ -e "$FAKE/restore.sleep" ] && sleep "$(cat "$FAKE/restore.sleep")"; [ -e "$FAKE/bad-final-home" ] && echo wrong.home/.Wrong > "$FAKE/home"; echo "restored slot A"; exit "$(cat "$FAKE/restore.rc" 2>/dev/null || echo 0)"; }
echo "TRIPWIRE unexpected a9 android-boot $*" >> "$FAKE/tripwire.log"; exit 99
A
mk $R/bootwt/scripts/mainline/android/android-boot.sh <<'A'
#!/usr/bin/env bash
case " $* " in *" --write "*) ;; *) echo "check ok; to write these images: abc123"; exit 0;; esac
echo "$$" >> "$FAKE/boot-pids"
echo "write start mode $(cat $FAKE/mode)"
barrier() {
  local -a a=("$@"); local i fd=
  for ((i=0;i<${#a[@]};i++)); do [ "${a[i]}" != --latency-prepared-fd ] || fd=${a[i+1]}; done
  [ -n "$fd" ] || exit 8
  bash "$FAKE/boot-barrier.sh" "$fd" || exit 8
}
record_open() {
  [ -n "${GHOST_ATTEMPT_OUT:-}" ] || return 0 # pinned old-wrapper negative control only
  python3 -B "$GHOST_ATTEMPT_HELPER" request --out "$GHOST_ATTEMPT_OUT" || exit 8
  printf 'GHOST_GATE open\nY\nY\nY\n' > "$GHOST_ATTEMPT_OUT/open-proof.txt"
  python3 -B "$GHOST_ATTEMPT_HELPER" opened --out "$GHOST_ATTEMPT_OUT" --evidence "$GHOST_ATTEMPT_OUT/open-proof.txt" || exit 8
}
case "$(cat $FAKE/mode)" in
  fail-pre) echo "FAIL: vendor image does not match"; exit 1;;
  silent-hang) exec sleep 100007;;
  abl-fallback) echo "fastboot reboot"; echo _a > $FAKE/slot; echo 1 > $FAKE/bc; sleep 4; echo "Android on kernel 5.4 (ABL fell back)"; exec sleep 100007;;
  no-hold) echo "fastboot reboot"; echo _b > $FAKE/slot; echo 1 > $FAKE/bc; barrier "$@"; sleep 3; echo "!! E Ink session stopped: gpu admission"; echo "rebooting to the bootloader"; exit 1;;
  hold-silent) echo "fastboot reboot"; echo _b > $FAKE/slot; echo 1 > $FAKE/bc; barrier "$@"; exec sleep 100007;;
  b-never) echo "fastboot reboot"; exec sleep 100007;;
  ok) echo "fastboot reboot"; echo _b > $FAKE/slot; echo 1 > $FAKE/bc; barrier "$@";
      case " $* " in *" --ghost-closed-probe "*) echo '-- GHOST CLOSED PROBE ADMITTED'; exec sleep 100007;; esac
      record_open; sleep 2; echo "-- HOLD 1500 s"; exec sleep 100007;;
esac
A
python3 - "$D/../../scripts/mainline/android/android-boot.sh" "$R/boot-barrier.sh" <<'PY'
from pathlib import Path
import sys
s=Path(sys.argv[1]).read_text();a=s.index('latency_prepared() {');z=s.index('\n}\n',a)+3
Path(sys.argv[2]).write_text('set -u\nLATENCY_PREP_FD=$1\nfail() { echo "FAIL: $*"; exit 1; }\n'+s[a:z]+'\nlatency_prepared\n')
PY
mk $R/runwt/scripts/eink-probe/sessions/ghost-ab.sh <<'A'
#!/usr/bin/env bash
echo "$*" > "$FAKE/r0-args"
o=; while [ $# -gt 0 ]; do [ "$1" = --out ] && o=$2; shift; done
mkdir -p "$o/runs"; printf 'stop reason: none (ran to the end of the plan)\n\npg-settle: 7 recovered dips, 0 timeouts, 0 budget skips\nsince the preflight reading: rail faults 0\n' > "$o/summary.md"
[ -e "$FAKE/r0-holds-lock" ] && { ( flock "$FAKE/a9-usb.lock" sleep 40 ) >/dev/null 2>&1 & sleep 1; }
echo "fake r0 ran" ; exit 0
A
printf 'def settle_stops(): pass\n"S7: VCOM readback"\n--log-updates\n' > $R/runwt/scripts/eink-probe/sessions/trial3b_rail_r0.py
echo boot > $R/boot.img; echo vend > $R/vend.img
touch $R/runwt/scripts/eink-probe/sessions/ghost_ab.py $R/runwt/scripts/eink-probe/sessions/trial3b-rail-r0.sh
mk $R/runwt/scripts/eink-probe/ghost-analyze <<'A'
#!/usr/bin/env bash
[ -s "$FAKE/restore.called" ] || grep -q "RESTORE NOT RUN" "${2%/run}/chain.log" || { echo "TRIPWIRE analysis before restore attempt" >> "$FAKE/tripwire.log"; exit 99; }
echo "ghost-analyze $*" >> "$FAKE/analyze.log"; echo "ghost analysis: fake"
[ -e "$FAKE/analysis.sleep" ] && { touch "$FAKE/analysis.active"; sleep "$(cat "$FAKE/analysis.sleep")"; }
exit 0
A
echo '{"H": [[1,0,0],[0,1,0],[0,0,1]]}' > $R/calib.json
export PATH=$R/bin:$PATH FAKE=$R XDG_RUNTIME_DIR=$R RUNWT=$R/runwt BOOTWT=$R/bootwt A9DIR=$R/a9 BOOT=$R/boot.img VEND=$R/vend.img \
  BOOT_SHA=$(sha256sum $R/boot.img | cut -d' ' -f1) VEND_SHA=$(sha256sum $R/vend.img | cut -d' ' -f1) \
  GHOST_ATTEMPT_HELPER=$D/../../scripts/eink-probe/sessions/ghost_attempt.py CALIB=$R/calib.json T_HEALTH=12 T_REBOOT=12 T_BOOTB=12 T_HOLD=12 T_LOCK=12 POLL=1 RA=fake GHOST_SETUP_WAIT=0.1
case_() {  # case_ <name> <mode> <expect restore 0|1> <expect series rc> [setup]
  local name=$1 mode=$2 want_r=$3 want_rc=$4 setup=${5:-}
  local -a RUN_ARGS=()
  export SER=$R/ser-$name; mkdir -p $SER/stock; echo "rc0 rehearsal" > $SER/stock/done; rm -f $R/restore.called $R/slot $R/bc $R/restore.rc $R/restore.sleep $R/bad-final-home $R/bad-final-health $R/final.sleep $R/final.active $R/analysis.sleep $R/analysis.active $R/accounting.sleep $R/accounting.active
  echo 'bitpit.launcher/.ui.HomeActivity' > "$R/home"
  echo "$mode" > $R/mode; echo '{"slot":"_a","boot_completed":"1"}' > $R/health
  eval "$setup"
  local t0=$(date +%s)
  if [ -n "${LATE_SIGNAL:-}" ]; then
    python3 -c 'import os,signal,sys; signal.signal(signal.SIGINT,signal.SIG_DFL); os.execvp("bash",["bash",*sys.argv[1:]])' "$RUNNER" 1 "${RUN_ARGS[@]}" > "$SER/out.txt" 2>&1 & local rp=$!
    local end=$(( $(date +%s) + 20 ))
    local signal_marker=$R/${SIGNAL_BOUNDARY:-final.active}
    until [ -e "$signal_marker" ]; do [ "$(date +%s)" -lt "$end" ] && kill -0 "$rp" 2>/dev/null || break; sleep .05; done
    [ -e "$signal_marker" ] && sleep .2 && kill -"$LATE_SIGNAL" "$rp"
    wait "$rp"; local rc=$?
  elif [ -n "${SIG_AFTER:-}" ]; then
    bash "$RUNNER" 1 "${RUN_ARGS[@]}" > $SER/out.txt 2>&1 & local rp=$!; sleep $SIG_AFTER; kill -${KILL_SIGNAL:-TERM} $rp; if [ -n "${REPEAT_SIGNAL:-}" ]; then sleep 1; kill -TERM $rp 2>/dev/null || true; fi; wait $rp; local rc=$?
  else
    timeout 120 bash "$RUNNER" 1 "${RUN_ARGS[@]}" > $SER/out.txt 2>&1; local rc=$?
  fi
  local dt=$(( $(date +%s) - t0 )) r=0; [ -s $R/restore.called ] && r=1
  local receipt=$SER/series.tsv
  [ -s "$receipt" ] || receipt=$SER/last-attempt/result.tsv
  local line=$(cat "$receipt" 2>/dev/null | tr '\t' ' ')
  local src=$(cut -f2 "$receipt" 2>/dev/null)
  local verdict=PASS; [ "$r" = "$want_r" ] && [ "$src" = "$want_rc" ] && [ $rc -ne 124 ] || verdict=FAIL
  if [ "$want_rc" = 0 ]; then [ "$rc" = 0 ] || verdict=FAIL; else [ "$rc" != 0 ] || verdict='FAIL(false process success)'; fi
  if [ -n "${LATE_SIGNAL:-}" ]; then
    [ -e "$signal_marker" ] && grep -q 'r0 rc=0' "$SER/opening-1/chain.log" && [ "$(cut -f7 "$receipt")" = signal ] || verdict='FAIL(late signal not exercised/recorded)'
  fi
  if [ "$want_rc" = 0 ] && [[ " ${RUN_ARGS[*]} " != *" --probe "* ]]; then
    [ -s "$SER/series.tsv" ] || verdict='FAIL(success without verified opening consumption)'
  fi
  while read -r pid; do
    state=$(ps -o stat= -p "$pid" 2>/dev/null)
    if [ -n "$state" ] && [[ "$state" != Z* ]]; then
      verdict="FAIL(leftover fake boot $pid)"; kill -TERM "$pid"
    fi
  done < "$R/boot-pids"
  case "$mode" in silent-hang|b-never) [ "$dt" -ge 12 ] || verdict="FAIL(deadline not exercised)";; esac
  if [ "$name" = hold-never-printed ]; then [ "$dt" -ge 12 ] || verdict="FAIL(hold deadline not exercised)"; fi
  [ "$verdict" = PASS ] || failures=$((failures + 1))
  echo "$verdict $name: runner rc $rc in ${dt}s, restore=$r, series: $line"
  tail -3 $SER/opening-1/chain.log 2>/dev/null | sed 's/^/      /'
}
case_ fail-pre-reboot fail-pre 1 9
case_ silent-before-reboot silent-hang 1 9
case_ abl-fallback-to-A abl-fallback 1 9
case_ gate-fault-no-hold no-hold 1 9
case_ hold-never-printed hold-silent 1 9
case_ slot-B-never-boots b-never 1 9
case_ normal-session ok 1 0
case_ restore-failure ok 1 9 'echo 7 > "$R/restore.rc"'
case_ final-health-failure ok 1 9 'touch "$R/bad-final-health"'
case_ final-home-failure ok 1 9 'touch "$R/bad-final-home"'
case_ slot-A-not-ready ok 0 9 'echo "{\"slot\":\"_b\",\"boot_completed\":\"1\"}" > $R/health'
case_ usb-lock-held ok 0 9 '( flock $R/a9-usb.lock sleep 40 ) & sleep 1'
case_ lock-held-at-restore ok 0 9 'touch $R/r0-holds-lock'
rm -f $R/r0-holds-lock; sleep 30
SIG_AFTER=6 T_HOLD=60 case_ sigterm-while-waiting-for-hold hold-silent 1 9
SIG_AFTER=6 KILL_SIGNAL=HUP T_HOLD=60 case_ sighup-while-waiting-for-hold hold-silent 1 9
SIG_AFTER=6 REPEAT_SIGNAL=1 T_HOLD=60 case_ repeated-signal-during-restore hold-silent 1 9 'echo 3 > "$R/restore.sleep"'
for sig in INT TERM HUP; do
  LATE_SIGNAL=$sig case_ "late-$sig-after-success" ok 1 9 'echo 2 > "$R/final.sleep"'
done
LATE_SIGNAL=TERM SIGNAL_BOUNDARY=restore.called case_ late-TERM-during-restore ok 1 9 'echo 2 > "$R/restore.sleep"'
LATE_SIGNAL=TERM SIGNAL_BOUNDARY=analysis.active case_ late-TERM-during-analysis ok 1 9 'echo 2 > "$R/analysis.sleep"'
LATE_SIGNAL=TERM SIGNAL_BOUNDARY=accounting.active case_ late-TERM-during-accounting ok 1 9 'echo 2 > "$R/accounting.sleep"'
echo "r0 args (last session): $(cat $R/r0-args 2>/dev/null | sed 's/--adb [^-]*//; s#--calib [^ ]*##; s#--out [^ ]*##; s#--stop-file [^ ]*##')"
echo "analysis (normal session): $(command grep -a 'ghost analysis' $R/ser-normal-session/opening-1/chain.log) / $(cat $R/analyze.log 2>/dev/null | head -1)"
check() {
  local name=$1; shift
  if "$@"; then echo "PASS $name"; else echo "FAIL $name"; failures=$((failures + 1)); fi
}
refuse() {
  local name=$1 expected=$2; shift 2
  local rc=0
  SER=$R/ser-$name bash "$RUNNER" "$@" > "$R/refuse-$name.log" 2>&1 || rc=$?
  [ "$rc" = 2 ] && grep -q "$expected" "$R/refuse-$name.log"
}
mkdir -p "$R/ser-nostock"
check missing-stock refuse nostock 'stock reference must run first' 1
check opening-2 refuse normal-session 'one opening only' 2
check opening-again refuse normal-session 'already ran' 1
check opening-2-no-stock refuse normal-session 'one opening only' 2 --no-stock
check opening-again-no-stock refuse normal-session 'already ran' 1 --no-stock
check extra-argument refuse extra 'usage:' 1 1
check unknown-option refuse unknown 'unknown option' 1 --bogus
case_ no-stock-flag ok 1 0 'rm -rf "$SER/stock"; RUN_ARGS=(--no-stock)'
case_ no-stock-env ok 1 0 'rm -rf "$SER/stock"; RUN_ARGS=(); export GHOST_NO_STOCK=1'
unset GHOST_NO_STOCK
check default-analysis-stock grep -q -- '--stock ' "$R/analyze.log"
# The fake analyzer logs arguments globally, so inspect its call for each output path.
for name in no-stock-flag no-stock-env; do
  line=$(grep "ser-$name/opening-1/run" "$R/analyze.log")
  check "$name-analysis-no-stock" test -n "$line"
  if [[ "$line" == *--stock* ]]; then echo "FAIL $name passed --stock"; failures=$((failures + 1)); fi
  check "$name-pending-log" grep -q 'stock reference: pending' "$R/ser-$name/opening-1/chain.log"
done
# An explicit opt-out also omits a reference that happens to exist.
case_ no-stock-existing ok 1 0 'export GHOST_NO_STOCK=1'
unset GHOST_NO_STOCK
line=$(grep 'ser-no-stock-existing/opening-1/run' "$R/analyze.log")
check no-stock-existing-analysis test -n "$line"
if [[ "$line" == *--stock* ]]; then echo 'FAIL existing stock passed --stock'; failures=$((failures + 1)); fi
# Matched opt-in must be rejected before phone mocks when the frozen source is old.
export GHOST_MATCHED_SOURCE=1 GHOST_LAUNCHER_COMPONENT=com.android.launcher3/.Launcher
export GHOST_SOURCE_VIEWER_JSON='["am","start","-n","com.example.viewer/.View","-d","{uri}"]'
mkdir -p "$R/ser-matched-missing/stock"; echo fake > "$R/ser-matched-missing/stock/done"
check matched-needs-frozen-harness refuse matched-missing 'lacks matched-source harness' 1
touch "$R/runwt/scripts/eink-probe/sessions/ghost_matched.py" "$R/runwt/scripts/eink-probe/matched_source.py"
mkdir -p "$R/ser-unsealed/stock"; echo fake > "$R/ser-unsealed/stock/done"
check unsealed-matched-refused refuse unsealed 'requires a sealed execution tuple' 1
unset GHOST_MATCHED_SOURCE GHOST_LAUNCHER_COMPONENT GHOST_SOURCE_VIEWER_JSON
check tripwire-empty test ! -s "$R/tripwire.log"
echo "failures: $failures; scratch: $R"
[ "$failures" = 0 ]
