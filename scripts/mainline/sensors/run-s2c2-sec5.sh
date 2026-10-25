set -euo pipefail
# S2c-2 section 5 (always after run-s2c2-attempt.sh): pull the evidence while adb answers, end the boot
# wrapper with TERM (its documented end; its lock keeper, main 966c853, dies with it by pdeathsig), wait
# a BOUNDED time for the USB lock to be free, and NEVER kill a lock holder: if the lock is still held, it
# reports the holders and stops (section 6). Then return to slot A, final golden restore from the MAIN
# checkout, health. No fd 9 from the caller reaches adb.
exec 9>&-
W=/home/user/A9-build/mainline/sensors-s1
SESSION=${SESSION:-$(cat "$W/current-session-s2c2")}
test -f "$SESSION/operator.env"
# shellcheck disable=SC1091
source "$SESSION/operator.env"
if [ ! -f "$SESSION/boot-holder.pid" ]; then
  echo 'no boot holder was started (sections 1-2 failed before the write): the phone was not touched; nothing to end or restore'
  exit 0
fi
BOOT_PID=$(cat "$SESSION/boot-holder.pid")
case "$BOOT_PID" in ''|*[!0-9]*) echo 'bad boot-holder.pid'; exit 1;; esac
S1_BOOT_ID=${S1_BOOT_ID:-}

RESULT=$(head -n1 "$SESSION/result" 2>/dev/null || echo MISSING)
case "$RESULT" in RUNNING) RESULT=CONTAINMENT-UNVERIFIED;; esac   # the attempt stopped mid-way
printf 'result=%s\n' "$RESULT" | tee "$SESSION/outcome.txt"
case "$RESULT" in PASS-S2C2|FAIL-S2C2*|CONTAINMENT-UNVERIFIED|MISSING) ;; *) echo "unexpected result $RESULT: treated as CONTAINMENT-UNVERIFIED"; RESULT=CONTAINMENT-UNVERIFIED;; esac

# End the boot-wrapper holder: TERM to our own android-boot.sh --write (checked by /proc cmdline), then wait up
# to TERM_WAIT s (default 120) for it to exit, then up to LOCK_WAIT s (default 120) for the USB lock to be
# free. Nothing is ever sent SIGKILL and no lock holder is signalled: a holder that remains is reported
# and this script stops (rc 1, section 6).
USB_LOCK=${XDG_RUNTIME_DIR:-/tmp}/a9-usb.lock
lock_holders() {
  local id key path p
  id=$(stat -c '%d:%i' "$USB_LOCK" 2>/dev/null) || return 0
  # one stat for every fd of every process (a stat per fd made this take minutes on a busy host)
  stat -L -c '%d:%i %n' /proc/[0-9]*/fd/* 2>/dev/null | while read -r key path; do
    [ "$key" = "$id" ] && { p=${path#/proc/}; echo "${p%%/*}"; }
  done | sort -un
}
end_holder() {
  local pid=$1 end cmd h
  if kill -0 "$pid" 2>/dev/null; then
    cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    case "$cmd" in
      *android-boot.sh*--write*) ;;
      *) echo "pid $pid is alive but is not our android-boot.sh --write ($cmd): refusing to signal it"; return 1;;
    esac
    kill -TERM "$pid" 2>/dev/null || true
    end=$((SECONDS + ${TERM_WAIT:-120}))
    while kill -0 "$pid" 2>/dev/null && [ "$SECONDS" -lt "$end" ]; do sleep 1; done
    kill -0 "$pid" 2>/dev/null && { echo "boot wrapper $pid still running after TERM + ${TERM_WAIT:-120} s: not killing it (section 6)"; return 1; }
  fi
  end=$((SECONDS + ${LOCK_WAIT:-120}))
  while [ -n "$(lock_holders)" ] && [ "$SECONDS" -lt "$end" ]; do sleep 2; done
  if [ -n "$(lock_holders)" ]; then
    for h in $(lock_holders); do echo "USB lock still held by pid $h ($(tr '\0' ' ' < "/proc/$h/cmdline" 2>/dev/null)): not killing it"; done
    return 1
  fi
  return 0
}

# Evidence that only lives in RAM (/data/vendor/a9-sensors, kmsg) while adb answers; never delays the return.
R() {
  local log
  log=$(mktemp "$SESSION/root-step-XXXXXXXX.log") || return $?
  python3 -I - "$BUNDLE" "$ADB" "$A9_TRANSPORT_ID" "$A9_SERIAL" "$S1_BOOT_ID" "$log" "$1" <<'PYROOT' || return $?
import importlib.util,sys
sys.dont_write_bytecode=True
from pathlib import Path
spec=importlib.util.spec_from_file_location('s1_host',Path(sys.argv[1])/'s1-host.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
t=mod.Transport(Path(sys.argv[2]),sys.argv[3],sys.argv[4],'loopback',sys.argv[5])
sys.exit(t.execute(['/system/bin/sh','-c',sys.argv[7]],120,Path(sys.argv[6])))
PYROOT
  cat "$log"
}
if [ -n "${A9_TRANSPORT_ID:-}" ] && [ -n "${S1_BOOT_ID:-}" ] && [ "$(timeout 10 "$ADB" -t "$A9_TRANSPORT_ID" get-state 2>/dev/null </dev/null | tr -d '\r')" = device ]; then
  R 'for f in /data/vendor/a9-sensors/*; do echo "== $f"; cat "$f"; done; echo "== kmsg"; dmesg | tail -n 400; true' > "$SESSION/final-evidence.txt" 2>&1 \
    || echo 'WARN final evidence not read (root step failed)'
  timeout 60 "$ADB" -t "$A9_TRANSPORT_ID" shell -T 'logcat -d -b all -v monotonic' </dev/null > "$SESSION/final-logcat.txt" 2>&1 || true
fi

# Release the wrapper's USB lock (the attempt has finished or stopped).
end_holder "$BOOT_PID" || { echo "USB lock not released: see section 6"; exit 1; }
printf 'boot holder ended\n' | tee "$SESSION/boot-holder-ended.txt"

# Pre-authorized return (ADSP-CRASHED / CONTAINMENT-UNVERIFIED / MISSING): only if adb answers. A plain
# --to-slot-a waits up to 30 minutes for a device, so gate it on a live adb state instead.
case "$RESULT" in CONTAINMENT-UNVERIFIED|MISSING)
  ST=$(timeout 10 "$ADB" -s "$A9_SERIAL" get-state 2>/dev/null </dev/null | tr -d '\r' || true)
  printf 'adb state after an unverified S2c: %s\n' "${ST:-none}" | tee "$SESSION/adb-state-after.txt"
  test "$ST" = device || { echo 'adb does not answer: section 6 step 2 (physical Power/PON2) applies'; exit 1; };;
esac

# Return to A first (B kept), then the single final golden restore.
bash "$BOOT_SH" --to-slot-a --eink --boot "$BOOT" --vendor "$VENDOR" | tee "$SESSION/return-to-a.log"
# Final restore from the MAIN checkout, default paths, WITHOUT the wrapper env (the D0-env form failed).
( unset A9_ANDROID A9_MAINLINE A9_BASE_ANDROID_DTB A9_STOCK_BASE_DTS A9_GOLDEN GOLDEN BOOT_SH
  cd /home/user/A9 && bash scripts/mainline/android/android-boot.sh --restore ) \
  | tee "$SESSION/final-restore.log"
bash /home/user/A9/scripts/a9-health.sh --json | tee "$SESSION/health-final.json"
# Evidence of what reset the phone, if it did (slot B keeps no log; slot A's a9-bootlog module records the
# bootloader log, power-on reason and kernel log of each boot, last 8). Read only, never required.
bash /home/user/A9/scripts/a9-bootlog.sh pull all "$SESSION/bootlog" > "$SESSION/bootlog-pull.log" 2>&1 || true
test "$(timeout 10 "$ADB" -s "$A9_SERIAL" shell getprop ro.boot.slot_suffix | tr -d '\r')" = _a
test "$(timeout 10 "$ADB" -s "$A9_SERIAL" shell getprop sys.boot_completed | tr -d '\r')" = 1
printf 'RESULT=%s; return and restore verified\n' "$RESULT"
