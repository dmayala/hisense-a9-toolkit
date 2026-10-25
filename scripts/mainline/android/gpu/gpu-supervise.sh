#!/usr/bin/env bash
# gpu-supervise.sh -- host supervisor for the GPU phone stages (runs/gpu.md):
#   g1   vkms display + GPU, no E Ink                       (G1 PASSED, attempt 3)
#   g2a  E Ink + GPU, E Ink gates CLOSED                     (first G2 stage)
#   g2b  E Ink + GPU, gates OPEN for --gate-secs, no hold    (only after a g2a PASS)
# ATTENDED only. Written and fault-injected offline (tests/test-gpu-supervise.sh);
# never run against the phone by its author. g1-supervise.sh = `--stage g1`.
#
#   A9_GPU_ATTENDED=yes gpu-supervise.sh --stage g1 --code <c>
#   A9_GPU_ATTENDED=yes gpu-supervise.sh --stage g2a --code <c> --waveform <blob> --template <ct>
#        (gates stay closed; one dry-run update attaches the transport, review 38 R2)
#   A9_GPU_ATTENDED=yes A9_EINK_E5C_PASSED=yes gpu-supervise.sh --stage g2b --code <c> \
#        --after <g2a supervise dir> --waveform <blob> --template <ct> --bins <lo-hi> [--gate-secs 120]
#   (<c> = the approval code `android-boot.sh [--eink ...] --boot <img> --vendor <img>` prints for
#    exactly the same arguments)
#
# Review 31 fixes (2a-2c):
#  2a  it re-executes itself in its OWN SESSION (setsid -w): a terminal/SSH
#      hangup or a group signal to the caller's group cannot reach it; its log
#      goes straight to a file (no tee pipe), SIGPIPE is ignored. Stop it only
#      with `kill -TERM <pid>` (printed at start); INT/TERM/HUP to it = stop +
#      restore.
#  2b  android-boot.sh runs in its own process group; a stop kills the WHOLE
#      group (lp-write, fastboot, sleeps), then every process still holding
#      the USB lock (fd on a9-usb.lock) is listed and terminated before the
#      restore; the adb server is started BEFORE the child, so it never
#      inherits the lock.
#  2c  the verdict needs real kmsg (`Linux version 7.` and the qualification
#      line), SurfaceFlinger on Mesa (`GLES: freedreno, FD610` in dumpsys),
#      not just the absence of stop patterns.
# Exit: 0 stage PASS + slot a back; 1 FAIL/INCOMPLETE + slot a back; 3 slot a
# NOT confirmed (human: a9-recover skill); 2 refused, nothing written.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
AB=${A9_SUP_ANDROID_BOOT:-${A9_G1_ANDROID_BOOT:-$HERE/../android-boot.sh}}
HEALTH=${A9_SUP_HEALTH:-${A9_G1_HEALTH:-$REPO/scripts/a9-health.sh}}
RUNS=${A9_SUP_RUNS:-${A9_G1_RUNS:-$REPO/runs}}
OUTD=${A9_SUP_OUT:-${A9_G1_OUT:-$HOME/A9-build/mainline/gpu/android/out}}
ADB=${A9_SUP_ADB:-adb}
LOCKF=${A9_SUP_LOCK:-${XDG_RUNTIME_DIR:-/tmp}/a9-usb.lock}
STAGE=g1 CODE="" MAXRUN="" RECOVER=4800 POLL=${A9_SUP_POLL:-${A9_G1_POLL:-5}} AFTER="" WF="" CT="" BINS="" GSECS=120
ARGS=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    --stage) STAGE=${2:-}; shift ;;
    --code) CODE=${2:-}; shift ;;
    --max-run) MAXRUN=${2:-}; shift ;;
    --recover-window) RECOVER=${2:-}; shift ;;
    --after) AFTER=${2:-}; shift ;;
    --waveform) WF=${2:-}; shift ;;
    --template) CT=${2:-}; shift ;;
    --bins) BINS=${2:-}; shift ;;
    --gate-secs) GSECS=${2:-}; shift ;;
    *) sed -n '2,32p' "$0"; exit 2 ;;
  esac; shift
done
case "$STAGE" in
  g1) BOOT=${A9_SUP_BOOTIMG:-${A9_G1_BOOTIMG:-$OUTD/a9-android-boot-gpu.img}}; VENDOR=${A9_SUP_VENDOR:-${A9_G1_VENDOR:-$OUTD/vendor_b-gpu.img}}; EXTRA=() ;;
  g2a|g2b) BOOT=${A9_SUP_BOOTIMG:-$OUTD/a9-android-eink-boot-gpu.img}; VENDOR=${A9_SUP_VENDOR:-$OUTD/vendor_b-eink-gpu.img}; EXTRA=(--eink) ;;
  *) echo "refused: --stage g1|g2a|g2b"; exit 2 ;;
esac
if [ "$STAGE" = g2a ]; then
  [ -n "$WF" ] && [ -n "$CT" ] || { echo "refused: g2a needs --waveform and --template (the gates-closed dry-run update, review 38 R2)"; exit 2; }
  EXTRA=(--eink --waveform "$WF" --template "$CT")
fi
[ -n "$MAXRUN" ] || { MAXRUN=2700; [ "$STAGE" = g2b ] && MAXRUN=$((2700 + GSECS)); }
[ "${A9_GPU_ATTENDED:-${A9_G1_ATTENDED:-}}" = yes ] || { echo "refused: GPU stages must be attended (A9_GPU_ATTENDED=yes, a human at the phone)"; exit 2; }
[ -n "$CODE" ] || { echo "refused: --code missing (android-boot.sh check mode prints it)"; exit 2; }
if [ "$STAGE" = g2b ]; then
  [ "${A9_EINK_E5C_PASSED:-}" = yes ] || { echo "refused: g2b needs A9_EINK_E5C_PASSED=yes"; exit 2; }
  [ -n "$WF" ] && [ -n "$CT" ] && [ -n "$BINS" ] || { echo "refused: g2b needs --waveform, --template and --bins"; exit 2; }
  [ -f "$AFTER/verdict.txt" ] && grep -q '^verdict: G2A PASS restored=yes' "$AFTER/verdict.txt" \
    || { echo "refused: g2b needs --after <a g2a supervise dir with 'G2A PASS'>"; exit 2; }
  want="boot $(sha256sum "$BOOT" | cut -c1-16) vendor $(sha256sum "$VENDOR" | cut -c1-16)"
  grep -q "$want" "$AFTER/supervise.log" || { echo "refused: the g2a PASS was for other images (want '$want')"; exit 2; }
  EXTRA=(--eink --waveform "$WF" --template "$CT" --open-gates --bins "$BINS" --gate-secs "$GSECS")
fi

# ---- 2a: own session, file log, no SIGPIPE
if [ "${A9_SUP_SESSION:-}" != 1 ]; then
  mkdir -p "$RUNS"; SUP=$RUNS/$(date -u +%Y%m%dT%H%M%SZ)-$STAGE-supervise; mkdir -p "$SUP"
  echo "gpu-supervise: own session; log $SUP/supervise.log (tail -f it); stop only with kill -TERM <pid in that log>"
  A9_SUP_SESSION=1 A9_SUP_DIR=$SUP exec setsid -w "$0" "${ARGS[@]}" </dev/null >>"$SUP/supervise.log" 2>&1
fi
SUP=$A9_SUP_DIR
trap '' PIPE
T0=$(date +%s)
echo "gpu-supervise[$$] stage $STAGE: boot $(sha256sum "$BOOT" | cut -c1-16) vendor $(sha256sum "$VENDOR" | cut -c1-16) max-run $MAXRUN recover $RECOVER"

"$AB" --offline "${EXTRA[@]}" --boot "$BOOT" --vendor "$VENDOR" > "$SUP/offline.log" 2>&1 \
  || { echo "refused: android-boot.sh --offline failed (see $SUP/offline.log); nothing written"; echo "verdict: REFUSED" > "$SUP/verdict.txt"; exit 2; }
grep -q 'GPU: boot DTB + vendor are a GPU pair' "$SUP/offline.log" \
  || { echo "refused: the offline check did not confirm a GPU pair; nothing written"; echo "verdict: REFUSED" > "$SUP/verdict.txt"; exit 2; }

# 2b: the adb server must exist BEFORE the child takes the USB lock
"$ADB" start-server >/dev/null 2>&1 || echo "gpu-supervise: WARNING adb start-server failed"

CHILD="" REASON=""
lock_holders() {   # pids (not us) with an fd on the USB lock file
  local p f
  for p in /proc/[0-9]*; do
    [ "${p#/proc/}" = $$ ] && continue
    for f in "$p"/fd/*; do
      [ "$(readlink "$f" 2>/dev/null)" = "$LOCKF" ] && { echo "${p#/proc/}"; break; }
    done
  done 2>/dev/null
}
# review 38 R4: a g2b stop may land inside the gates-open window, where
# android-boot.sh has no trap; close the gates (bounded) before and after the
# group kill, so they do not stay open until --restore reboots the phone
close_gates() {
  [ "$STAGE" = g2b ] || return 0
  echo "gpu-supervise: closing the E Ink gates ($1): $(timeout 10 "$ADB" shell setprop vendor.a9ml.eink.gate closed </dev/null >/dev/null 2>&1 && echo sent || echo 'no answer')"
}
kill_child() {
  [ -n "$CHILD" ] || return 0
  if kill -0 -- "-$CHILD" 2>/dev/null; then
    close_gates "before the group kill"
    echo "gpu-supervise: stopping android-boot.sh process group $CHILD ($1)"
    kill -TERM -- "-$CHILD" 2>/dev/null; local i=0
    while kill -0 -- "-$CHILD" 2>/dev/null && [ $i -lt 20 ]; do sleep 1; i=$((i+1)); done
    kill -KILL -- "-$CHILD" 2>/dev/null
    close_gates "after the group kill"
  fi
  wait "$CHILD" 2>/dev/null
  local h; h=$(lock_holders)
  if [ -n "$h" ]; then
    echo "gpu-supervise: USB lock still held by: $(for p in $h; do printf '%s(%s) ' "$p" "$(cat /proc/$p/comm 2>/dev/null)"; done)-> TERM/KILL"
    kill -TERM $h 2>/dev/null; sleep 3; kill -KILL $h 2>/dev/null
  fi
}
on_signal() { REASON="interrupted ($1)"; kill_child "$REASON"; }
trap 'on_signal INT' INT; trap 'on_signal TERM' TERM; trap 'on_signal HUP' HUP

slot_a_ok() { local j; j=$("$HEALTH" --json 2>/dev/null) || return 1; printf '%s' "$j" | grep -qE '"slot":"_?a"'; }

# ---- the run (no --hold, ever), own process group
A9M_BC_WAIT=${A9M_BC_WAIT:-600} setsid "$AB" --write --code "$CODE" "${EXTRA[@]}" --boot "$BOOT" --vendor "$VENDOR" --watch 900 \
  > "$SUP/android-boot.log" 2>&1 < /dev/null &
CHILD=$!
while kill -0 "$CHILD" 2>/dev/null; do
  if [ $(( $(date +%s) - T0 )) -ge "$MAXRUN" ]; then REASON="overrun ${MAXRUN}s (telemetry lost / host step hung)"; kill_child "$REASON"; break; fi
  sleep "$POLL"
done
wait "$CHILD" 2>/dev/null; CRC=$?
[ -n "$REASON" ] || REASON="android-boot.sh exit $CRC"
echo "gpu-supervise: run ended after $(( $(date +%s) - T0 )) s: $REASON"
# a normally ended child may still have left group members behind
[ "$REASON" = "android-boot.sh exit $CRC" ] && kill -0 -- "-$CHILD" 2>/dev/null && kill_child "leftover group members"
trap '' INT TERM HUP   # the return to slot a must not be interrupted half-way

# ---- the return to slot a (always verified, retried)
RESTORED=0
if [ "$CRC" = 0 ] && [ "$REASON" = "android-boot.sh exit 0" ] && slot_a_ok; then
  RESTORED=1; echo "gpu-supervise: android-boot.sh restored; slot a verified"
fi
R0=$(date +%s) n=0
while [ $RESTORED = 0 ] && [ $(( $(date +%s) - R0 )) -lt "$RECOVER" ]; do
  n=$((n+1)); h=$(lock_holders); [ -n "$h" ] && { echo "gpu-supervise: lock holders before restore: $h -> TERM/KILL"; kill -TERM $h 2>/dev/null; sleep 3; kill -KILL $h 2>/dev/null; }
  echo "gpu-supervise: restore attempt $n"
  "$AB" --restore >> "$SUP/restore.log" 2>&1
  if slot_a_ok; then RESTORED=1; echo "gpu-supervise: slot a verified after restore attempt $n"; break; fi
  sleep "${A9_SUP_RETRY_SLEEP:-${A9_G1_RETRY_SLEEP:-30}}"
done
if [ $RESTORED = 0 ]; then
  echo "gpu-supervise: SLOT A NOT CONFIRMED after $RECOVER s. HUMAN: a9-recover skill (Power 10-15 s -> fastboot), then $AB --restore"
  echo "verdict: UNKNOWN restored=no" > "$SUP/verdict.txt"; exit 3
fi

# ---- verdict from the collected files (only now: slot a is back)
RD=$(grep -o 'collected into [^ ]*' "$SUP/android-boot.log" | tail -n1 | sed 's/collected into //')
v=PASS why=""
need() { v=FAIL; why="$why $1;"; }
sfield() { tr ' \n' '\n\n' < "$1" 2>/dev/null | awk -v k="$2" 'p==k {print; exit} {p=$0}'; }
if [ -z "$RD" ] || [ ! -d "$RD" ]; then need "no collection (boot never reached adb/collect)"
else
  gp() { sed -n "s/^\[$1\]: \[\(.*\)\]$/\1/p" "$RD/getprop.txt" 2>/dev/null | tail -n1; }
  # 2c: the kmsg must be real, SurfaceFlinger must run on Mesa
  grep -aq 'Linux version 7\.' "$RD/dmesg.txt" 2>/dev/null || need "dmesg.txt is not real kmsg"
  grep -aq 'a9ml-gpu: qualification: PASS' "$RD/dmesg.txt" 2>/dev/null || need "no qualification PASS in kmsg"
  grep -q 'GLES: freedreno, FD610' "$RD/dumpsys_SurfaceFlinger_.txt" 2>/dev/null || need "SurfaceFlinger not on Mesa FD610"
  [ "$(gp vendor.a9ml.gpu.state)" = mesa ] || need "gpu.state=$(gp vendor.a9ml.gpu.state)"
  [ "$(gp vendor.a9ml.gpu.setup)" = ok ] || need "gpu.setup=$(gp vendor.a9ml.gpu.setup)"
  [ "$(gp sys.boot_completed)" = 1 ] || need "boot_completed=$(gp sys.boot_completed)"
  # review 38 R6: vkms.map_wc and msm.separate_gpu_kms still Y at collect time
  [ "$(tr -d '\r' < "$RD/gpu-params.txt" 2>/dev/null | tr '\n' ' ')" = "Y Y " ] || need "gpu-params (map_wc separate_gpu_kms) = '$(tr -d '\r' < "$RD/gpu-params.txt" 2>/dev/null | tr '\n' ' ')' (want Y Y)"
  grep -qx 'A9GPU result PASS dmabuf' "$RD/a9ml-gpu.log" 2>/dev/null || need "shared dma-buf test not PASS"
  [ "$(grep -c '^A9GPU shared .*cpu->gpu 0 wrong, gpu->cpu 0 wrong, .* 0 px OUTSIDE it, fences 0 bad, glError 0x0$' "$RD/a9ml-gpu.log" 2>/dev/null)" = 3 ] \
    || need "not 3 clean shared geometries"
  pat='zap|qcom_scm.*(fail|error|timeout)|arm-smmu.*(fault|unhandled)|unhandled context fault|hangcheck|gpu fault|gpu hang|a6xx.*fault|recover(y|ing) gpu|kernel panic|hung_task|blocked for more than|soft lockup|hard lockup|sysrq reboot|qualification fail|failed to load adreno|stop: |(5900000|59a0000|5990000|5e00000)[^ ]*: (deferred probe timeout|probe with driver .* failed)'
  epat='max_on_ms [0-9]+ expired|emergency off|fault latched|frame-progress fault|dropped out while the pipeline|update refused|power-up aborted|control template .*rejected|waveform.bin rejected'
  # review 38 R3: G2 also greps the whole kmsg taken AFTER the E Ink session
  # (gates-open window included), not only the one collected before it
  K=("$RD/dmesg.txt"); [ "$STAGE" != g1 ] && K+=("$RD/eink/dmesg-after.txt")
  if [ "$STAGE" != g1 ]; then grep -aq 'Linux version 7\.' "$RD/eink/dmesg-after.txt" 2>/dev/null || need "eink/dmesg-after.txt missing or not real kmsg"; fi
  hits=$( { grep -haiE "$pat" "${K[@]}"; grep -haE '\bPAS\b|pas_(init|auth|mem)' "${K[@]}"; [ "$STAGE" != g1 ] && grep -haE "$epat" "${K[@]}"; } 2>/dev/null \
          | grep -vE 'a9ml-gpu: (firmware_class|GPU firmware copied)' | head -n 20)
  [ -z "$hits" ] || { need "stop pattern in kmsg"; printf '%s\n' "$hits" > "$SUP/stop-patterns.txt"; }
  if [ "$STAGE" = g1 ]; then
    [ "$(gp ro.boot.a9ml.eink)" = 0 ] || need "ro.boot.a9ml.eink=$(gp ro.boot.a9ml.eink) (G1 is vkms only)"
    grep -q '^a9_eink ' "$RD/proc_modules.txt" 2>/dev/null && need "a9_eink module loaded"
    grep -aq 'a9ml-gpu: bound: .* mdss=-' "$RD/dmesg.txt" 2>/dev/null || need "MDSS not shown unbound"
  else
    E=$RD/eink
    # G2a 2026-10-16 (pushfix): the blobs must be installed AS ROOT and
    # verified on the phone first; listed first so the verdict names it
    grep -q 'E Ink blobs installed as root' "$SUP/android-boot.log" \
      || need "E Ink blobs NOT installed ($(grep -m1 -o 'E Ink blob install FAILED: .*' "$SUP/android-boot.log" || echo 'no install line'))"
    [ "$(gp ro.boot.a9ml.eink)" = 1 ] || need "ro.boot.a9ml.eink=$(gp ro.boot.a9ml.eink) (G2 is the E Ink image)"
    [ "$(gp vendor.a9ml.eink.ready)" = 1 ] || need "eink.ready=$(gp vendor.a9ml.eink.ready)"
    card=$(gp vendor.a9ml.eink.card)
    [ -n "$card" ] && [ "$(gp vendor.hwc.drm.device)" = "/dev/dri/$card" ] || need "composer not on the a9_eink card ($(gp vendor.hwc.drm.device) vs $card)"
    grep -aq 'a9ml-gpu: bound: gpucc=[^ ]* smmu=arm-smmu gpu=adreno mdss=msm-mdss' "$RD/dmesg.txt" 2>/dev/null || need "MDSS/GPU not bound as required"
    # review 38 R2: the transport attaches at the first (dry-run) update, so
    # its kmsg line is only in the kmsg taken after the session
    grep -aq 'transport on msm' "$RD/eink/dmesg-after.txt" 2>/dev/null || need "a9_eink transport did not open on the msm KMS card"
    grep -q '5900000.gpu/drm/card' "$RD/gpu-drm.txt" 2>/dev/null && grep -q 'display-subsystem.*/drm/card\|5e01000.display-controller/drm/card' "$RD/gpu-drm.txt" 2>/dev/null \
      || need "separate GPU and KMS DRM devices not both present"
    # review 38 R1: blocks are "== /sys/kernel/debug/dri/<minor|unique> <name file>";
    # the a9_eink device = minor of its card, or the driver name a9_eink
    awk -v c="$card" '/^== /{s=($0 ~ "/dri/" substr(c,5) "( |$)") || ($3 == "a9_eink")} s && /modifier=/{print}' "$RD/dri-states.txt" 2>/dev/null > "$SUP/eink-modifiers.txt"
    [ -s "$SUP/eink-modifiers.txt" ] && ! grep -qv 'modifier=0x0$' "$SUP/eink-modifiers.txt" || need "a9_eink framebuffers not all LINEAR (or none found)"
    for f in eink-status-before.txt eink-status-after.txt $( [ "$STAGE" = g2b ] && echo eink-status-open.txt ); do
      [ -s "$E/$f" ] || { need "$f missing"; continue; }
      # review 38 R2: before the first update the transport is not attached
      # (transport 0 is normal there); after it, it must be
      [ "$f" = eink-status-before.txt ] || [ "$(sfield "$E/$f" transport)" = 1 ] || need "$f transport=$(sfield "$E/$f" transport)"
      for k in stall_fault rail_fault commit_fail aborts; do [ "$(sfield "$E/$f" $k)" = 0 ] || need "$f $k=$(sfield "$E/$f" $k)"; done
    done
    grep -q 'E Ink session stopped\|REFUSED to open the gates' "$SUP/android-boot.log" && need "E Ink session stopped/refused"
    if [ "$STAGE" = g2a ]; then
      # the dry run: gates verified closed before AND after, both blobs loaded
      grep -qxE 'gates before: (N N N |0 0 0 )' "$E/dryrun.txt" 2>/dev/null && grep -qxE 'gates after: (N N N |0 0 0 )' "$E/dryrun.txt" 2>/dev/null \
        && grep -q '^kicked ' "$E/dryrun.txt" || need "dry-run update not done with the gates verified closed (eink/dryrun.txt)"
      for k in waveform template; do [ "$(sfield "$E/eink-status-after.txt" $k)" = 1 ] || need "after the dry run $k=$(sfield "$E/eink-status-after.txt" $k)"; done
      for k in enable hv_on; do [ "$(sfield "$E/eink-status-after.txt" $k)" = 0 ] || need "gates-closed stage but $k=$(sfield "$E/eink-status-after.txt" $k)"; done
      # dry runs count as updates too; with the gates closed the last one must be dry
      [ "$(sfield "$E/eink-status-after.txt" dry)" = 1 ] || need "last update not a dry run (dry=$(sfield "$E/eink-status-after.txt" dry))"
    fi
    if [ "$STAGE" = g2b ]; then
      O=$E/eink-status-open.txt
      [ "$(sfield "$O" updates)" -gt 0 ] 2>/dev/null || need "no E Ink update while open"
      [ "$(sfield "$O" late_drive)" = 0 ] || need "late_drive=$(sfield "$O" late_drive) (runs 13/14: 0)"
      g=$(sfield "$O" max_gap_drive_us); [ "${g:-99999999}" -le "${A9_G2_MAX_GAP_US:-20000}" ] 2>/dev/null || need "max_gap_drive_us=$g > ${A9_G2_MAX_GAP_US:-20000} (runs 13/14: 16.6/15.3 ms)"
      [ "$(cat "$E/compare.rc" 2>/dev/null)" = 0 ] || need "eink-compare rc=$(cat "$E/compare.rc" 2>/dev/null) (want 0)"
      [ -s "$E/pace.log" ] || need "no pace samples"
    fi
  fi
fi
U=$(echo "$STAGE" | tr a-z A-Z)
echo "verdict: $U $v restored=yes${why:+ (}${why}${why:+)}" | tee "$SUP/verdict.txt"
[ "$v" = PASS ] && exit 0 || exit 1
