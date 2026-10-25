#!/usr/bin/env bash
# Ghost A/B controlled validation, MAINLINE part (review 89h follow-up "Minimal next controlled validation"): the C2
# module on the F6 kernel; ONE gate opening on slot B mainline, slot A restored after. Runs only after the stock
# reference (run-stock.sh, slot A) finished, unless --no-stock or GHOST_NO_STOCK=1 is explicit.
#   r0-ghost.sh 1 [--no-stock]
# Copied from r0-soak/r0-opening.sh (R1 unchanged: deadlines, restore on every exit path, bounded lock wait, series
# gate). Changes: one opening only; the session is ghost_ab.py from the frozen eink-ghost-run worktree (N repetitions
# of HOME + force_clear / warm am start Settings / force_clear, pg_settle_ms 100 and 0 interleaved ABBA); after the
# restore, the host-only ghost analysis (ghost-analyze analyze, with the stock reference).
# Modelled on the coordinator's dense-arm.sh. Series state: $SER/series.tsv (one line per finished opening).
# The series gate is kept (one line in series.tsv); with N fixed to 1 it only refuses a second run of the opening.
#
# R1 (review 89b section 3): every wait has a deadline and notices android-boot.sh ($BP) exiting; android-boot's
# "FAIL:" lines (and an ABL fallback to slot A, and a stopped E Ink session) count as failures; once the write has
# started, android-boot.sh --restore runs on EVERY exit path (EXIT/INT/TERM trap) and the series line is RC 9 unless
# the session itself finished. No killing of USB-lock holders: the runner waits for the lock (bounded, 15 min) and
# refuses (before the phone is touched) or, at the end, does not run --restore and says so.
# Env overrides exist only for the offline rehearsal (fake adb / android-boot / health); the defaults are the real ones.
set -u
early_refusal() {
  local status=$?
  [ "$status" = 0 ] || echo "GHOST REFUSED/FAILED: reason=${LAST_REASON:-preflight-check-failed} rc=$status; evidence=${OUT:-unallocated}/chain.log" >&2
}
trap early_refusal EXIT
N=; PLAN=0; PROBE=0; NO_STOCK=${GHOST_NO_STOCK:-0}
for arg in "$@"; do
  case "$arg" in
    --no-stock) NO_STOCK=1;;
    --plan) PLAN=1;;
    --probe) PROBE=1;;
    --*) echo "unknown option: $arg" >&2; exit 2;;
    *) [ -z "$N" ] || { echo "usage: r0-ghost.sh 1 [--no-stock]" >&2; exit 2; }; N=$arg;;
  esac
done
[ "$PROBE" = 0 ] || N=${N:-1}
[ -n "$N" ] || { echo "usage: r0-ghost.sh 1 [--no-stock] | --probe" >&2; exit 2; }
case "$N" in 1) ;; *) echo "one opening only: N must be 1" >&2; exit 2;; esac
DEPLOY=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
if [ -e "$DEPLOY/deployment.sha256" ] || [ -n "${GHOST_EXECUTION_TUPLE:-}" ]; then
  (cd "$DEPLOY" && sha256sum --quiet -c deployment.sha256) || { echo 'deployment seal mismatch' >&2; exit 2; }
  GHOST_EXECUTION_TUPLE=${GHOST_EXECUTION_TUPLE:-$DEPLOY/execution.json}
  [ "$(readlink -f "$GHOST_EXECUTION_TUPLE")" = "$DEPLOY/execution.json" ] || { echo 'unsealed tuple override' >&2; exit 2; }
  env_file=$(mktemp)
  "$DEPLOY/python3" -B "$DEPLOY/ghost_tuple.py" env --tuple "$GHOST_EXECUTION_TUPLE" > "$env_file" || { rm -f "$env_file"; exit 2; }
  . "$env_file"; rm -f "$env_file"
  export PATH="$GHOST_TOOL_PATH:$PATH" PYTHONDONTWRITEBYTECODE=1
  if [ "$PLAN" = 1 ]; then python3 -B "$DEPLOY/ghost_tuple.py" check --tuple "$GHOST_EXECUTION_TUPLE"; exit $?; fi
  : "${A9_SERIAL:?A9_SERIAL is required; no device discovery}"
  python3 -B "$DEPLOY/ghost_tuple.py" device-check --tuple "$GHOST_EXECUTION_TUPLE" || exit 2
  unset A9_USB_LOCKED A9_USB_LOCK_OWNER ADB FB EDL A9_HOME A9_RUN
  export ANDROID_SERIAL=$A9_SERIAL A9_HOME=$RUNWT ADB=$GHOST_ADB FB=$GHOST_FASTBOOT EDL=$RUNWT/scripts/edl
  export A9_GOLDEN A9_ANDROID A9_MAINLINE A9_DATA A9_BACKUPS A9_FW
  # The persistent stock journal survives fresh series/output directories.
  # Recovery is the first possible device action, even before refusal of a
  # previous output or an obsolete stock/done marker. No USB owner is killed.
  flock -w "$T_LOCK" "${XDG_RUNTIME_DIR:-/tmp}/a9-usb.lock" python3 -B "$DEPLOY/ghost_recovery.py" \
    --marker "$GHOST_RECOVERY_MARKER" --adb "$GHOST_ADB -s $A9_SERIAL" || { echo 'GHOST REFUSED: USB lock/recovery gate failed; no opening started' >&2; exit 3; }
  [ "$NO_STOCK" = 0 ] || { echo 'paired matched tuple forbids --no-stock' >&2; exit 2; }
else
  unset GHOST_TUPLE_SHA
  if [ "$PLAN" = 1 ]; then echo 'no sealed execution tuple; no commands executed' >&2; exit 2; fi
fi
MATCHED_ARGS=(); REPS_DEFAULT=24
if [ "${GHOST_MATCHED_SOURCE:-0}" = 1 ]; then
  : "${GHOST_LAUNCHER_COMPONENT:?matched run needs GHOST_LAUNCHER_COMPONENT}"
  : "${GHOST_SOURCE_VIEWER_JSON:?matched run needs explicit GHOST_SOURCE_VIEWER_JSON}"
  MATCHED_ARGS=(--matched-source --launcher-component "$GHOST_LAUNCHER_COMPONENT"
    --source-viewer "$GHOST_SOURCE_VIEWER_JSON" --ghost-arms 100
    --source-roi "${GHOST_SOURCE_ROI:-32,100,792,1560}" --wallpaper-roi "${GHOST_WALLPAPER_ROI:-32,100,792,1560}"
    --source-wait-ms "${GHOST_SOURCE_WAIT_MS:-4000}" --recovery-marker "${GHOST_RECOVERY_MARKER:-$HOME/A9-build/mainline/eink-settle-f6/r0-ghost/recovery/stock-home.json}")
  REPS_DEFAULT=12
fi
SER=${SER:-$(cd "$(dirname "$0")" && pwd)}
OUT=$SER/opening-$N
[ "$PROBE" = 0 ] || OUT=$SER/probes/mainline-$(date -u +%Y%m%dT%H%M%S)-$$
SP=/tmp/claude-1000/-home-user-A9/138276b1-32cf-47e9-a654-4be339175015/scratchpad
RUNWT=${RUNWT:-$HOME/A9-wt/eink-ghost-run}           # frozen worktree: ghost_ab.py on the R0 machinery + ghost-analyze (never edited during runs)
BOOTWT=${BOOTWT:-$HOME/A9-wt/eink-trial3b-run2}       # frozen worktree: the android-boot.sh the dense arms used
A9DIR=${A9DIR:-/home/user/A9}                        # a9-health.sh and the --restore android-boot.sh
BOOT=${BOOT:-$HOME/A9-build/mainline/eink-settle-f6/out/a9-android-eink-boot-f6.img}   # r75b with the F6 kernel (0057)
BOOT_SHA=${BOOT_SHA:-410e5f0f0247da8aeee5f1e19b976c4cc4d4424f4bc3e9496e0eb3227a3d40ef}
VEND=${VEND:-$HOME/A9-build/mainline/eink-settle-f6/c2/vendor_b-eink-settle-c2.img}   # d4474717 with the C2 a9_eink.ko only
VEND_SHA=${VEND_SHA:-a1e9090c92b505c754e64ae75dde3b9c18836496994c720b168bb78aecca031b}
MOD_SHA=${MOD_SHA:-d76169ba9ac905925d8a13659b79ae3fea179321ba70045be8fadca560158b0a}
RA=${RA:-$HOME/A9-build/mainline/eink-trial3b/perf-20261017/rootadb.sh -s ${A9_SERIAL:?}}
LOCK="${XDG_RUNTIME_DIR:-/tmp}/a9-usb.lock"
# deadlines (s): review 89b R1
T_HEALTH=${T_HEALTH:-1800} T_REBOOT=${T_REBOOT:-900} T_BOOTB=${T_BOOTB:-1500} T_HOLD=${T_HOLD:-600} T_LOCK=${T_LOCK:-900}
T_CMD=${T_CMD:-30} T_RESTORE=${T_RESTORE:-900}
POLL=${POLL:-3}
GOAL=${GOAL:-1000}; CAP=${CAP:-60}
CALIB=${CALIB:-$SP/probe/calib.json}
F=${F:-$HOME/A9-build/mainline/firmware/hisense}
FAILPAT='^FAIL:\|FAILED\|ERROR\|ABL fell back\|E Ink session stopped'
G() { command grep -a "$@"; }
log() { LAST_REASON=$*; echo "$(date -u +%T) $*" >> "$OUT/chain.log"; }
series() {
  local -a restored=()
  [ "${HOME_RESTORED:-0}" != 1 ] || restored=(--home-restored)
  OUT=$(python3 -B "$DEPLOY/ghost_attempt.py" finish --out "$OUT" --row "$1" "${2:-0}" "${3:-0}" "${4:-0}" "${5:-0}" "${6:-none}" "${restored[@]}") || exit 9
}
lock_wait() {  # lock_wait <seconds>: 0 when nobody holds the USB lock
  local end=$(( $(date +%s) + $1 ))
  until flock -n "$LOCK" true 2>/dev/null; do
    [ "$(date +%s)" -lt "$end" ] || return 1
    sleep 10
  done
}
bp_wait() {  # bp_wait <grep pattern> <seconds>: 0 = the pattern appeared; 1 = $BP exited or the deadline passed
  local end=$(( $(date +%s) + $2 ))
  until G -q -- "$1" "$OUT/boot.log" 2>/dev/null; do
    kill -0 "$BP" 2>/dev/null || return 1
    [ "$(date +%s)" -lt "$end" ] || return 1
    sleep "$POLL"
  done
}
boot_failed() { G -q -- "$FAILPAT" "$OUT/boot.log" 2>/dev/null; }
home_read() (
  set -o pipefail
  timeout -k 5 "$T_CMD" adb shell 'cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN' </dev/null |
    python3 -c 'import re,sys; s=sys.stdin.read().strip(); assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*/[A-Za-z0-9_.$]+",s); p,a=s.split("/",1); print(p+"/"+(p+a if a.startswith(".") else a))'
)
healthy_a() {
  local file=$1
  timeout -k 5 "$T_CMD" bash "$A9DIR/scripts/a9-health.sh" --json > "$file" 2>/dev/null || return 1
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("slot")=="_a" and d.get("boot_completed")=="1" else 1)' "$file" 2>/dev/null
}

# The source/legacy entry point also refuses to step over a persistent stock
# rollback. The sealed deployed path already recovered it under the USB lock.
if [ -z "${GHOST_TUPLE_SHA:-}" ] && [ -e "${GHOST_RECOVERY_MARKER:-$HOME/A9-build/mainline/eink-settle-f6/r0-ghost/recovery/stock-home.json}" ]; then
  : "${A9_SERIAL:?pending stock recovery requires A9_SERIAL}"
  flock -w "$T_LOCK" "$LOCK" python3 -B "$DEPLOY/ghost_recovery.py" \
    --marker "${GHOST_RECOVERY_MARKER:-$HOME/A9-build/mainline/eink-settle-f6/r0-ghost/recovery/stock-home.json}" --adb "adb -s $A9_SERIAL" || { echo 'GHOST REFUSED: USB lock/recovery gate failed; no opening started' >&2; exit 3; }
fi

# ---- series gate (offline; before anything touches the phone)
mkdir -p "$SER"
exec {LEDGER_FD}> "$SER/attempt.lock"
flock -n "$LEDGER_FD" || { echo 'another ghost runner owns this series' >&2; exit 2; }
[ -e "$OUT" ] && { echo "opening $N already ran ($OUT exists)" >&2; exit 2; }
if [ "$PROBE" = 0 ]; then
python3 -B "$DEPLOY/ghost_attempt.py" check --series "$SER" || exit 2
touch "$SER/series.tsv"
ok_sum=0; prev=0
while IFS=$'\t' read -r n rc ok to faults goal stop; do
  [ -n "$n" ] || continue
  prev=$((prev + 1)); ok_sum=$((ok_sum + ok))
  if [ "$rc" != 0 ] || [ "$to" != 0 ] || [ "$faults" != 0 ] || [ "$goal" != 0 ] || [ "$stop" != none ]; then
    echo "series ended at opening $n (rc $rc, timeouts $to, faults $faults, goal $goal, stop '$stop'): no further opening without a review" >&2; exit 2
  fi
done < "$SER/series.tsv"
[ "$prev" = $((N - 1)) ] || { echo "opening $N needs exactly $((N - 1)) finished openings in series.tsv (have $prev)" >&2; exit 2; }
left=$((GOAL - ok_sum)); [ $left -gt 0 ] || { echo "goal already reached ($ok_sum dips)" >&2; exit 2; }
[ $((ok_sum + left)) -le $CAP ] || left=$((CAP - ok_sum))
fi
for f in "$BOOT:$BOOT_SHA" "$VEND:$VEND_SHA"; do
  [ "$(sha256sum "${f%%:*}" | cut -d' ' -f1)" = "${f##*:}" ] || { echo "sha mismatch: ${f%%:*}" >&2; exit 2; }
done
[ -f "$RUNWT/scripts/eink-probe/sessions/trial3b-rail-r0.sh" ] || { echo "no frozen run worktree $RUNWT" >&2; exit 2; }
G -q 'def settle_stops' "$RUNWT/scripts/eink-probe/sessions/trial3b_rail_r0.py" || { echo "$RUNWT lacks the S1-S5 script" >&2; exit 2; }
G -q '"S7: VCOM readback' "$RUNWT/scripts/eink-probe/sessions/trial3b_rail_r0.py" && G -q -- '--log-updates' "$RUNWT/scripts/eink-probe/sessions/trial3b_rail_r0.py" \
  || { echo "$RUNWT lacks S7 / --log-updates" >&2; exit 2; }
[ -f "$RUNWT/scripts/eink-probe/sessions/ghost_ab.py" ] && [ -x "$RUNWT/scripts/eink-probe/ghost-analyze" ] || { echo "$RUNWT lacks ghost_ab.py / ghost-analyze" >&2; exit 2; }
[ "$PROBE" = 1 ] || [ "$NO_STOCK" = 1 ] || [ -e "$SER/stock/done" ] || { echo "the stock reference must run first (run-stock.sh; $SER/stock/done missing)" >&2; exit 2; }
if [ "${GHOST_MATCHED_SOURCE:-0}" = 1 ]; then
  [ -f "$RUNWT/scripts/eink-probe/sessions/ghost_matched.py" ] && [ -f "$RUNWT/scripts/eink-probe/matched_source.py" ] \
    || { echo "frozen run worktree lacks matched-source harness (review/re-freeze required)" >&2; exit 2; }
  [ -n "${GHOST_TUPLE_SHA:-}" ] || { echo 'matched opening requires a sealed execution tuple' >&2; exit 2; }
  if [ "$PROBE" = 0 ]; then python3 -B "$DEPLOY/ghost_tuple.py" stock-check --tuple "$GHOST_EXECUTION_TUPLE" || exit 2; fi
  python3 "$RUNWT/scripts/eink-probe/sessions/ghost_ab.py" --plan --ghost-reps "${REPS:-$REPS_DEFAULT}" "${MATCHED_ARGS[@]}" >/dev/null \
    || { echo "invalid matched-source plan: not started" >&2; exit 2; }
fi
[ -f "$CALIB" ] || { echo "no camera calibration $CALIB" >&2; exit 2; }
STOCK_ARGS=()
if [ "$NO_STOCK" = 1 ] || [ "$PROBE" = 1 ]; then
  STOCK_REF="pending (--no-stock / GHOST_NO_STOCK=1)"
else
  STOCK_REF=$(cat "$SER/stock/done")
  STOCK_ARGS=(--stock "$SER/stock/run")
fi
mkdir -p "$(dirname "$OUT")"; mkdir "$OUT" || exit 2
INIT_ARGS=(); [ "$PROBE" = 0 ] || INIT_ARGS=(--probe)
python3 -B "$DEPLOY/ghost_attempt.py" init --out "$OUT" --series "$SER" "${INIT_ARGS[@]}" || exit 2
log "ghost A/B opening (stock reference: $STOCK_REF); probe=$PROBE"

# ---- preconditions with deadlines; refusing here touches nothing on the phone
if ! lock_wait "$T_LOCK"; then log "USB lock still held after $T_LOCK s: NOT started"; series 9 0 0 0 0 usb-lock-held; exit 1; fi
adb start-server >/dev/null 2>&1
end=$(( $(date +%s) + T_HEALTH ))
until healthy_a "$OUT/health-before.json"; do
  if [ "$(date +%s)" -ge "$end" ]; then log "slot A not ready after $T_HEALTH s: NOT started"; series 9 0 0 0 0 slot-A-not-ready; exit 1; fi
  sleep 10
done
ORIGINAL_HOME=$(home_read) || { log "preferred HOME could not be recorded: NOT started"; series 9 0 0 0 0 home-not-recorded; exit 1; }
printf '%s\n' "$ORIGINAL_HOME" > "$OUT/original-home.txt"
if [ -n "${GHOST_TUPLE_SHA:-}" ]; then
  python3 -B "$DEPLOY/ghost_tuple.py" check --tuple "$GHOST_EXECUTION_TUPLE" > "$OUT/tuple-before-write.log" || exit 2
fi

# ---- confirmation code (android-boot.sh dry run: writes nothing)
cd "$BOOTWT" || { log "no $BOOTWT"; series 9 0 0 0 0 no-bootwt; exit 1; }
ARGS="--eink --waveform $F/a9-waveform.bin --template $F/a9-ctrl-template.bin --open-gates --bins 0-13 --gate-secs 1500 --boot $BOOT --vendor $VEND --hold 1500 --no-restore --require-preparation"
[ "$PROBE" = 0 ] || ARGS="${ARGS/--open-gates/} --ghost-closed-probe"
C=$(A9_EINK_E5C_PASSED=yes timeout 600 bash scripts/mainline/android/android-boot.sh $ARGS 2>&1 | G -o "these images: [0-9a-f]*" | awk '{print $3}')
log "code $C"
if [ -z "$C" ]; then log "no confirmation code: nothing written"; series 9 0 0 0 0 no-code; exit 1; fi

# ---- from here on the phone may be written: --restore runs on every exit path
RC=9; BP=; RESTORED=0; HOME_RESTORED=0; SIGNAL_EXIT=0; ACCOUNTING_READY=0
commit_finish_series() {
  local recorded_stop=$stop
  if [ "$RC" != 0 ] && [ -n "${WHY:-}" ]; then recorded_stop=$WHY; fi
  series "$RC" "${ok:-0}" "${to:-0}" "${fa:-0}" "$goal" "$recorded_stop"
}
cleanup_signal() {
  RC=9; WHY=signal; SIGNAL_EXIT=$((128 + $1))
  echo "GHOST REFUSED/FAILED: signal=$1; restoration/accounting continues; evidence=$OUT/chain.log" >&2
  if [ "$ACCOUNTING_READY" = 1 ]; then
    commit_finish_series
    exit "$SIGNAL_EXIT"
  fi
  # Record, but finish the already-running restore/evidence operations. Unlike
  # ignoring signals, this cannot turn a late termination into capture success.
}
on_signal() { RC=9; WHY=signal; exit $((128 + $1)); }
finish() {
  local incoming=$? restore_rc=9 end restored_home=
  trap - EXIT
  trap 'cleanup_signal 2' INT
  trap 'cleanup_signal 15' TERM
  trap 'cleanup_signal 1' HUP
  [ "$RESTORED" = 1 ] && return
  RESTORED=1
  if [ "$incoming" != 0 ] && [ "$RC" = 0 ]; then RC=9; WHY=${WHY:-exit-$incoming}; fi
  log "finish: RC $RC"
  if [ -n "$BP" ]; then
    kill -TERM -- "-$BP" 2>/dev/null || true
    for i in $(seq 20); do kill -0 "$BP" 2>/dev/null || break; sleep 1; done
    kill -KILL -- "-$BP" 2>/dev/null || true
  fi
  if lock_wait "$T_LOCK"; then
    (cd "$A9DIR" && timeout -k 5 "$T_RESTORE" bash scripts/mainline/android/android-boot.sh --restore) > "$OUT/restore.log" 2>&1
    restore_rc=$?; log "restore rc=$restore_rc"
    if [ "$restore_rc" != 0 ] || G -q -- "$FAILPAT" "$OUT/restore.log"; then
      RC=9; WHY=restore-failed; log "RESTORE FAILED: nonzero command or failure banner"
    fi
    end=$(( $(date +%s) + T_HEALTH ))
    until healthy_a "$OUT/health-after.json"; do
      if [ "$(date +%s)" -ge "$end" ]; then RC=9; WHY=restore-health-failed; break; fi
      sleep "$POLL"
    done
    if restored_home=$(home_read) && [ "$restored_home" = "$ORIGINAL_HOME" ] && [ "$restore_rc" = 0 ] && [ "${WHY:-}" != restore-health-failed ] && [ "${WHY:-}" != restore-failed ]; then
      log "home restored: yes"
      HOME_RESTORED=1
    else
      RC=9; WHY=${WHY:-restore-home-failed}; case "$WHY" in restore-*) ;; *) WHY=restore-home-failed;; esac
      log "home restored: no; RESTORE/HEALTH/HOME verification failed"
    fi
  else
    log "RESTORE NOT RUN: USB lock still held after $T_LOCK s (phone may be on slot B or in fastboot; run android-boot.sh --restore by hand)"
    RC=9; WHY=restore-lock-held; log "home restored: no (restore omitted)"
  fi
  # ---- series line: N rc ok timeouts faults goal stop
  local S="$OUT/run/summary.md" ok to fa goal stop
  ok=$(sed -n 's/^pg-settle: \([0-9]*\) recovered dips, \([0-9]*\) timeouts.*/\1/p' "$S" 2>/dev/null | head -1)
  to=$(sed -n 's/^pg-settle: \([0-9]*\) recovered dips, \([0-9]*\) timeouts.*/\2/p' "$S" 2>/dev/null | head -1)
  fa=$(sed -n 's/.*rail faults \([0-9]*\)$/\1/p' "$S" 2>/dev/null | tail -1)
  goal=0; G -q "GOAL REACHED" "$S" 2>/dev/null && goal=1
  stop=$(sed -n 's/^stop reason: //p' "$S" 2>/dev/null | head -1)
  if [ "$RC" != 0 ] && [ -n "${WHY:-}" ]; then stop=$WHY; fi
  case "$stop" in "none"*) stop=none;; "") stop=$([ "$RC" = 0 ] && echo none || echo "${WHY:-no-session}");; *) stop=$(echo "$stop" | tr '\t' ' ' | cut -c1-80);; esac
  # ghost analysis: host-only, frames already on disk, after the restore; never talks to the phone
  if [ -d "$OUT/run/runs" ]; then
    (cd "$RUNWT" && timeout 1800 scripts/eink-probe/ghost-analyze analyze "$OUT/run" --calib "$CALIB" "${STOCK_ARGS[@]}" \
      --out "$SER/analysis") > "$OUT/analysis.log" 2>&1
    log "ghost analysis rc=$? ($(tail -1 "$OUT/analysis.log" 2>/dev/null))"
  fi
  log done
  ACCOUNTING_READY=1
  commit_finish_series
  if [ "$RC" != 0 ] || [ "$incoming" != 0 ] || [ "$SIGNAL_EXIT" != 0 ]; then
    echo "GHOST REFUSED/FAILED: reason=${WHY:-$stop} rc=$RC incoming=$incoming signal=$SIGNAL_EXIT; evidence=$OUT/chain.log" >&2
  fi
  if [ "$SIGNAL_EXIT" != 0 ]; then exit "$SIGNAL_EXIT"; fi
  if [ "$incoming" != 0 ]; then exit "$incoming"; fi
  exit "$RC"
}
trap finish EXIT
trap 'on_signal 2' INT
trap 'on_signal 15' TERM
trap 'on_signal 1' HUP
WHY=

# ---- boot slot B mainline with the M4 vendor (own process group, so finish can stop all of it)
set -m      # job control only for this launch: the background job gets its own process group (pgid = $BP)
PREP_ARGS=(); [ "$PROBE" = 0 ] || PREP_ARGS=(--probe --calib "$CALIB" --url "${GHOST_CAMERA_URL:-http://127.0.0.1:8093/stream}")
A9_EINK_E5C_PASSED=yes python3 -B "$DEPLOY/ghost_prepare.py" --out "$OUT" \
  --boot-script "$BOOTWT/scripts/mainline/android/android-boot.sh" \
  --adb-json '["adb"]' --launcher-component "${GHOST_LAUNCHER_COMPONENT:-}" \
  --hold-s "$T_HOLD" --command-s "$T_CMD" --arrival-s "$((T_REBOOT + T_BOOTB + T_HOLD))" \
  --poll "$POLL" --setup-wait "${GHOST_SETUP_WAIT:-5}" \
  "${PREP_ARGS[@]}" -- $ARGS --write --code "$C" > "$OUT/boot.log" 2>&1 &
BP=$!
set +m
if ! bp_wait "reboot into mainline\|fastboot reboot" "$T_REBOOT" || boot_failed; then
  WHY=no-reboot; log "android-boot did not reach the reboot (exited, $T_REBOOT s, or FAIL:)"; exit 1
fi
end=$(( $(date +%s) + T_BOOTB ))
while :; do
  s=$(timeout -k 5 "$T_CMD" adb shell getprop ro.boot.slot_suffix 2>/dev/null </dev/null | tr -d '\r')
  [ "$s" = _b ] && [ "$(timeout -k 5 "$T_CMD" adb shell getprop sys.boot_completed 2>/dev/null </dev/null | tr -d '\r')" = 1 ] && break
  if [ "$s" = _a ]; then WHY=abl-fallback-to-a; log "phone booted slot A (ABL fallback)"; exit 1; fi
  if ! kill -0 "$BP" 2>/dev/null; then WHY=boot-exited; log "android-boot exited before slot B booted"; exit 1; fi
  if boot_failed; then WHY=boot-fail; log "android-boot reported a failure before slot B booted"; exit 1; fi
  if [ "$(date +%s)" -ge "$end" ]; then WHY=slot-B-timeout; log "slot B not booted after $T_BOOTB s"; exit 1; fi
  sleep "$POLL"
done
READY_MARK='-- GHOST PREPARATION AND HOLD VERIFIED'
[ "$PROBE" = 0 ] || READY_MARK='-- GHOST CLOSED PROBE VERIFIED'
if ! bp_wait "$READY_MARK" "$T_HOLD" || boot_failed; then
  WHY=no-hold; log "no '-- HOLD' (gate opening / GPU admission fault, exit or $T_HOLD s)"; exit 1
fi
if [ "$PROBE" = 1 ]; then RC=0; log 'probe rc=0: gates closed, no reps; restoring'; exit 0; fi
if [ -n "${GHOST_TUPLE_SHA:-}" ]; then
  python3 -B "$DEPLOY/ghost_tuple.py" check --tuple "$GHOST_EXECUTION_TUPLE" > "$OUT/tuple-before-session.log" || { WHY=tuple-changed; exit 1; }
fi
cd "$RUNWT"
# ghost A/B session: R0 stops all on (rail fault/timeout, S1 75, S3, S5, S7, gate/GPU/temp/bins/drift, adb, STOP, cap)
bash scripts/eink-probe/sessions/ghost-ab.sh --go --adb "$RA" --calib "$CALIB" --out "$OUT/run" \
  --expect-module-sha $MOD_SHA --stop-file "$OUT/STOP" \
  --pg-settle-ms 100 --log-updates --ghost-reps ${REPS:-$REPS_DEFAULT} --ghost-arms 100,0 --post-s 3.5 --clean-retries 2 \
  --clean-threshold 0.02 --budget-s 1320 --gate-wait-s 30 --max-frames-stale-s 12 --stale-reconnect-s 3 \
  --s1-dip-ms 75 --s2-long-dips 1000 --s4-boot-dips $CAP "${MATCHED_ARGS[@]}" > "$OUT/r0.log" 2>&1
RC=$?
log "r0 rc=$RC"
timeout -k 5 "$T_CMD" adb shell "echo 'cat /sys/class/drm/card3-DSI-1/eink/status; cat /sys/class/drm/card3-DSI-1/eink/rail_diag; dmesg' | nc 127.0.0.1 2324" </dev/null > "$OUT/final-state.txt" 2>&1 \
  || { RC=9; WHY=final-evidence-failed; exit 1; }
exit 0      # finish (EXIT trap): stop android-boot, --restore, series line
