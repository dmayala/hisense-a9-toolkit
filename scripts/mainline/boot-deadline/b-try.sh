#!/usr/bin/env bash
# agent owns execution. One B attempt, bounded observation, unconditional A return.
set -euo pipefail
[[ $# -ge 1 && $# -le 3 ]] || { echo 'usage: b-try.sh boot.img [vendor.img] [system.img] (or A9_BTRY_SYSTEM)' >&2; exit 2; }
BOOT=$(realpath "$1"); VENDOR=${2:-}
SYSTEM=${3-${A9_BTRY_SYSTEM:-}}
if [[ -n $SYSTEM ]]; then SYSTEM=$(realpath -e "$SYSTEM"); fi
[[ -f "$BOOT" && ( -z "$VENDOR" || -f "$VENDOR" ) ]] || exit 2
[[ -z $SYSTEM || -f $SYSTEM ]] || exit 2
hold=${A9_BTRY_HOLD:-0}
[[ $hold =~ ^[0-9]{1,5}$ ]] || { echo 'A9_BTRY_HOLD must be seconds (0..86400)' >&2; exit 2; }
hold=$((10#$hold))
((hold<=86400)) || { echo 'A9_BTRY_HOLD exceeds 86400 seconds' >&2; exit 2; }
onb=${A9_BTRY_ONB:-}
before_b=${A9_BTRY_BEFORE_B:-}
if [[ -n $before_b ]]; then
  before_b=$(realpath "$before_b")
  [[ -x $before_b && -n $VENDOR ]] || { echo 'A9_BTRY_BEFORE_B requires executable hook and vendor image' >&2; exit 2; }
fi
if [[ -n $onb ]]; then
  onb=$(realpath "$onb")
  [[ -x $onb && $hold -gt 0 ]] || { echo 'A9_BTRY_ONB requires an executable script and A9_BTRY_HOLD>0' >&2; exit 2; }
fi
hvenc_qualify=${A9_SWEEP_HVENC_QUALIFY:-0}
case "$hvenc_qualify" in 0|1) ;; *) echo "A9_SWEEP_HVENC_QUALIFY must be 0 or 1" >&2; exit 2;; esac
. "${A9_HOME:-/home/user/A9}/scripts/a9-env.sh"
edl_count() {
  local listing rc=0
  listing=$(timeout --kill-after=1 3 lsusb -d 05c6:9008 2>/dev/null) || rc=$?
  # lsusb exits 1 when no matching device exists. Other failures are unknown.
  ((rc<=1)) || return 1
  printf '%s\n' "$listing" | awk 'NF {n++} END {print n+0}'
}
if [[ ${A9_SERIAL:-} == unknown-* ]]; then
  # Registry resolution may fail in bootloader. Fall back only with one
  # attached transport across both tools; never wait on the sentinel serial.
  devices=$( { timeout 4 "$ADB" devices 2>/dev/null | awk 'NR>1 && NF>=2 && $1!="*" {print $1}'; timeout 4 "$FB" devices 2>/dev/null | awk 'NF>=2 {print $1}'; } | sort -u)
  n_edl=$(edl_count) || n_edl=unknown
  if [[ -n "$devices" && $(printf '%s\n' "$devices" | wc -l) -eq 1 && $n_edl == 0 ]] ||
     [[ -z "$devices" && $n_edl == 1 ]]; then
    A9_SERIAL=; unset ANDROID_SERIAL
  else
    echo 'NOUSB boot_completed=0 slot_A=unknown detail=registry_unresolved_ambiguous_transport' ; exit 2
  fi
fi
deadline=$(python3 - "$BOOT" <<'PY'
import sys,struct
b=open(sys.argv[1],'rb').read(4096)
assert b[:8]==b'ANDROID!'
cmd=(b[64:576].split(b'\0')[0]+b' '+b[608:1632].split(b'\0')[0]).decode()
args=[x.split('=',1)[1] for x in cmd.split() if x.startswith('a9_deadline=')]
assert args, 'b-try requires a boot packaged with a9_deadline=N; plain r22e lacks it'
n=int(args[-1])
assert n>0, 'b-try requires enabled kernel boot deadline'
print(n)
PY
)
a9_usb_lock_inherit
result=NOUSB detail=setup_failed attempted=0 restored=0 collecting=0 b_completed=0 hook_pid= lease_pid= edl_seen=0 edl_reset_attempted=0
formatted=0 a_repair_attempted=0 a_return_recovery=0
saved_system_server_loop=0
saved_crash_extract_failed=0
saved_hvenc_ok=0
out="${A9_BTRY_LOGS:-/home/user/A9-build/mainline/b-try-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
mkdir -m 700 -p "$out"
out=$(realpath "$out")
export A9_BTRY_LOGS="$out" A9_BTRY_BOOT="$BOOT" A9_BTRY_IN_HOOK=1 A9_SERIAL
export A9_BTRY_SYSTEM="$SYSTEM"
adb_cmd() { timeout 10 "$ADB" "$@"; }
fb_cmd() { timeout 45 "$FB" "$@"; }
probe_boot() {
  local phase=$1 limit=${2:-20} rc=0 start=$SECONDS line count=0 valid=0 bytes
  boot_slot= boot_completed=
  # adbd being online does not imply a shell can finish within ten seconds.
  # Use a tagged record, no PTY, and absolute getprop paths; tolerate unrelated
  # shell startup output, but refuse partial, contradictory or failed replies.
  timeout --kill-after=1 "$limit" "$ADB" shell -T \
    'printf "A9_BTRY_BOOT_STATUS slot=%s completed=%s\n" "$(/system/bin/getprop ro.boot.slot_suffix)" "$(/system/bin/getprop sys.boot_completed)"' \
    >"$out/boot-probe.out" 2>"$out/boot-probe.err" || rc=$?
  while IFS= read -r line || [[ -n $line ]]; do
    line=${line%$'\r'}
    [[ $line == A9_BTRY_BOOT_STATUS* ]] || continue
    count=$((count+1))
    if [[ $line =~ ^A9_BTRY_BOOT_STATUS\ slot=(_[ab])\ completed=([01]?)$ ]]; then
      boot_slot=${BASH_REMATCH[1]}; boot_completed=${BASH_REMATCH[2]}; valid=1
    else valid=0; fi
  done <"$out/boot-probe.out"
  if ((rc!=0 || count!=1 || valid!=1)); then boot_slot=; boot_completed=; valid=0; fi
  bytes=$(wc -c <"$out/boot-probe.out")
  # Keep raw output private, and retain numeric failure evidence rather than
  # silently converting timeouts and malformed output into an empty property.
  printf 'phase=%s rc=%s timeout=%s elapsed=%s bytes=%s records=%s valid=%s\n' \
    "$phase" "$rc" "$limit" "$((SECONDS-start))" "$bytes" "$count" "$valid" >>"$out/boot-probe.log"
}
mode() {
  if timeout 3 "$FB" getvar current-slot >/dev/null 2>&1; then echo fastboot; return; fi
  local n
  n=$(edl_count) || n=unknown
  if [[ $n == 1 ]]; then echo edl; return; fi
  if [[ $n != 0 ]]; then echo edl_ambiguous; return; fi
  timeout 3 "$ADB" get-state 2>/dev/null | tr -d '\r\n' || true
}
reset_edl() {
  local loader="$A9_FW/prog_firehose_ddr_001360E1.elf" n rc=0
  ((edl_reset_attempted==0)) || return 1
  n=$(edl_count) || return 1
  [[ $n == 1 && -f $loader ]] || { echo 'EDL reset refused: require one 9008 device and the 001360E1 loader' >&2; return 1; }
  edl_reset_attempted=1
  echo "EDL/9008: one guarded reset only; evidence: $out/edl-reset.log" >&2
  # Inherit this runner's USB lock. This literal wrapper/verb is the only EDL
  # operation: no storage discovery, partition reads, writes, erases or XML.
  timeout --signal=TERM --kill-after=5 60 env -u ANDROID_SERIAL A9_SERIAL= EDL_RAW=0 \
    LOADER="$loader" /home/user/A9/scripts/edl reset --loader="$loader" >"$out/edl-reset.log" 2>&1 || rc=$?
  printf '%s\n' "$rc" >"$out/edl-reset.rc"
  tail -n 20 "$out/edl-reset.log" >&2
  # A reset may disconnect before its reply; observe USB even after a nonzero
  # exit, but never resend the reset to the same run.
  echo "EDL reset rc=$rc; waiting for bootloader/Android" >&2
}
wait_mode() {
  local wanted=$1 end=$((SECONDS+$2))
  while ((SECONDS<end)); do [[ $(mode) == "$wanted" ]] && return 0; sleep 2; done
  return 1
}
bootloader() {
  local end=$((SECONDS+$1)) m
  while ((SECONDS<end)); do
    m=$(mode)
    [[ $m == fastboot ]] && return 0
    if [[ $m == edl ]]; then
      if ((attempted)); then edl_seen=1; result=HANG; detail=edl; fi
      if ((edl_reset_attempted==0)); then reset_edl || return 1; fi
    fi
    [[ $m != edl_ambiguous ]] || return 1
    if [[ $m == device || $m == recovery ]]; then adb_cmd reboot bootloader >/dev/null 2>&1 || true; fi
    sleep 2
  done
  return 1
}
android_a() {
  local end=$((SECONDS+480)) recovery_after=$((SECONDS+15)) s m
  a_return_recovery=0
  while ((SECONDS<end)); do
    probe_boot return_a
    [[ $boot_slot == _a && $boot_completed == 1 ]] && return 0
    m=$(adb_cmd get-state 2>/dev/null | tr -d '\r\n') || m=
    # Ignore the old recovery transport briefly while a reboot disconnects.
    if [[ $m == recovery ]] && ((SECONDS>=recovery_after)); then
      a_return_recovery=1
      echo 'Android A entered recovery before boot_completed' >&2
      return 1
    fi
    sleep 3
  done
  return 1
}
repair_a_data() {
  local m s log="$out/return-a-repair.log"
  local format_log="$out/return-a-format.log" format_rc=0
  ((a_repair_attempted==0 && (formatted || a_return_recovery))) || return 1
  a_repair_attempted=1
  echo 'Return A: one TWRP data-format repair, then one Android A retry' >&2
  printf 'formatted=%s A_entered_recovery=%s\n' "$formatted" "$a_return_recovery" >>"$log"
  m=$(mode)
  case "$m" in
    recovery) ;;
    device) adb_cmd reboot recovery >>"$log" 2>&1 || return 1 ;;
    # Best effort only: this ABL may not implement the recovery reboot verb.
    fastboot) fb_cmd reboot recovery >>"$log" 2>&1 || return 1 ;;
    *) echo "Return A repair unavailable: mode=$m" >&2; return 1 ;;
  esac
  wait_mode recovery 180 || return 1
  sleep 6
  s=$(adb_cmd shell getprop ro.boot.slot_suffix 2>/dev/null | tr -d '\r\n') || s=
  [[ $s == _a ]] || { echo 'Return A repair refused: recovery is not on slot A' >&2; return 1; }
  # Authorized for a run that formatted data, or A's fallback into recovery.
  # This does not touch kdebuginfo; retain its independent boot evidence.
  timeout 300 "$ADB" shell twrp format data >"$format_log" 2>&1 || format_rc=$?
  cat "$format_log" >>"$log"
  if grep -qiE 'Unable to wipe|In use' "$format_log"; then
    # FIX9 lets TWRP open userdata through dm-default-key. Clear only its
    # metadata header, then reboot recovery to release the in-use mapping.
    echo 'Return A: TWRP data in use; clear 4 MiB metadata, reboot recovery, retry format once' >&2
    adb_cmd shell 'dd if=/dev/zero of=/dev/block/by-name/metadata bs=1M count=4' >>"$log" 2>&1 || return 1
    adb_cmd reboot recovery >>"$log" 2>&1 || return 1
    sleep 6
    wait_mode recovery 180 || return 1
    sleep 6
    s=$(adb_cmd shell getprop ro.boot.slot_suffix 2>/dev/null | tr -d '\r\n') || s=
    [[ $s == _a ]] || { echo 'Return A repair refused: recovery is not on slot A' >&2; return 1; }
    format_log="$out/return-a-format-retry.log"; format_rc=0
    timeout 300 "$ADB" shell twrp format data >"$format_log" 2>&1 || format_rc=$?
    cat "$format_log" >>"$log"
  fi
  ((format_rc==0)) && ! grep -qiE 'Unable to wipe|In use' "$format_log" || return 1
  adb_cmd shell 'sh /system/bin/rebootsystem.sh' >>"$log" 2>&1 || true
  adb_cmd reboot >>"$log" 2>&1 || true
  android_a
}
return_a() {
  bootloader "$((deadline+120))" || return 1
  fb_cmd set_active a >&2 || return 1
  fb_cmd reboot >&2 || return 1
  if android_a; then return 0; fi
  if ((a_repair_attempted==0 && (formatted || a_return_recovery))); then
    repair_a_data
  else
    return 1
  fi
}
recovery() {
  local m
  m=$(mode)
  if [[ $m == edl ]]; then bootloader 120 || return 1; m=fastboot; fi
  [[ $m != edl_ambiguous ]] || return 1
  if [[ $m == fastboot ]]; then
    # Bootloader's recovery verb is not reliable on this ABL. Reach A first.
    fb_cmd set_active a >&2; fb_cmd reboot >&2; android_a
    m=device
  fi
  [[ $m == recovery ]] || adb_cmd reboot recovery >/dev/null
  wait_mode recovery 180
  sleep 6
}
wait_pull_ready() {
  local expected=${1:-recovery} until=$((SECONDS+120)) since=-1 state rc
  while ((SECONDS<until)); do
    rc=0
    state=$(timeout --kill-after=1 3 "$ADB" get-state 2>/dev/null) || rc=$?
    state=${state//$'\r'/}
    if ((rc==0)) && [[ $state == "$expected" ]]; then
      ((since>=0)) || since=$SECONDS
      if ((SECONDS-since>=10)); then return 0; fi
    else since=-1; fi
    sleep 1
  done
  return 1
}
pull_archive() {
  local remote=$1 destination=$2 attempt rc valid command
  if [[ $shell_kind == su ]]; then command="su -c 'cat $remote'";
  else command="cat $remote"; fi
  for attempt in 1 2 3; do
    rc=0 valid=0
    # Recovery MTP can re-enumerate after tar creation. Re-establish ten
    # seconds of consecutive recovery observations before each exec-out.
    # The existing stock-A su path instead requires stable Android 'device'.
    if wait_pull_ready "${pull_state:-recovery}"; then
      timeout --kill-after=1 60 "$ADB" exec-out "$command" \
        >"$destination.part" 2>"$destination.pull-$attempt.err" || rc=$?
      if ((rc==0)) && tar -tf "$destination.part" >/dev/null 2>&1; then valid=1; fi
    else rc=124; fi
    printf 'attempt=%s rc=%s valid_tar=%s\n' "$attempt" "$rc" "$valid" >>"$destination.pull.log"
    if ((rc==0 && valid)); then mv -f "$destination.part" "$destination"; return 0; fi
    ((attempt<3)) || break
    echo 'Log archive transport/read failed; retrying after 10s' >&2
    sleep 10
  done
  return 1
}
pull_logs() {
  local shell_kind=direct pull_state=recovery mnt=/tmp/a9-mainline-read latest previous saved boot_dirs archive=/tmp/b-try-mainline.tar
  # Mainline logs remain root0600; use A's existing root when available.
  if adb_cmd shell 'su -c "id -u"' 2>/dev/null | tr -d '\r' | grep -qx 0; then
    shell_kind=su; pull_state=device; mnt=/mnt/a9-kdebug; archive=/data/local/tmp/b-try-mainline.tar
  elif ! adb_cmd shell id -u 2>/dev/null | tr -d '\r' | grep -qx 0; then
    collecting=1
    recovery || return 1
  elif [[ $(adb_cmd get-state 2>/dev/null | tr -d '\r') == device ]]; then
    pull_state=device
  fi
  wait_pull_ready "$pull_state" || return 1
  rsh() {
    if [[ $shell_kind == su ]]; then
      local quoted
      printf -v quoted '%q' "$1"
      adb_cmd shell "su -c $quoted"
    else adb_cmd shell "$1"; fi
  }
  # Collect independently of kdebuginfo: a pre-PID1 panic has no mainline dir.
  # Read before mounting that filesystem or doing any further return reboot.
  pull_pstore || echo "pstore collection unavailable; inspect $out" >&2
  rsh "mkdir -p $mnt; grep -q ' $mnt ' /proc/mounts || mount -t ext4 -o rw,noatime /dev/block/bootdevice/by-name/kdebuginfo $mnt" >&2
  latest=$(rsh "ls -1 $mnt/mainline | grep '^boot-[0-9]*$' | sort | tail -1" | tr -d '\r')
  [[ $latest =~ ^boot-[0-9]{10}$ ]] || { echo 'No mainline log directory found' >&2; return 1; }
  # Acceptance can warm-restart B once. Preserve the preceding boot as well,
  # keeping its key/TA evidence separate instead of mixing boot state records.
  previous=$(rsh "ls -1 $mnt/mainline | grep '^boot-[0-9]*$' | sort | tail -2 | head -1" | tr -d '\r')
  saved=$latest
  if [[ $previous =~ ^boot-[0-9]{10}$ && $previous != "$latest" ]]; then saved="$previous $latest"; fi
  boot_dirs=$saved
  rsh "tar -C $mnt/mainline -cf $archive $saved" >&2 || return 1
  # Root path is private: stream via exec-out instead of adb pull permissions.
  pull_archive "$archive" "$out/mainline.tar" || return 1
  tar -xf "$out/mainline.tar" -C "$out" || return 1
  rsh "rm -f $archive" >/dev/null
  # The early hook can run before the logger has drained its queue, and live
  # logcat may rotate during a long sweep. Extract the retained records after
  # the original owner has returned and pulled the saved boot. A separate file
  # avoids mixing a reboot's KEY/CONFIG with the initial hook's mount STATE.
  local fbe_extract
  fbe_extract="$(dirname -- "${BASH_SOURCE[0]}")/../reboot/fbe-onb.py"
  if [[ -f $fbe_extract ]]; then
    python3 -B "$fbe_extract" --saved-boot "$out/$latest" --out "$out/fbe-evidence-saved.log" >"$out/fbe-extract.log" 2>&1 || true
  fi
  local rpmb_extract="$(dirname -- "${BASH_SOURCE[0]}")/../reboot/rpmb-evidence.py"
  if [[ -f $rpmb_extract ]]; then
    python3 -B "$rpmb_extract" --saved-boot "$out/$latest" --out "$out/rpmb-evidence.log" --qseecomd-out "$out/qseecomd-logcat.log" >"$out/rpmb-extract.log" 2>&1 || true
    for saved in $boot_dirs; do
      python3 -B "$rpmb_extract" --saved-boot "$out/$saved" --out "$out/$saved/rpmb-evidence.log" --qseecomd-out "$out/$saved/qseecomd-logcat.log" >"$out/$saved/rpmb-extract.log" 2>&1 || true
      if [[ -f $fbe_extract ]]; then
        python3 -B "$fbe_extract" --saved-boot "$out/$saved" --out "$out/$saved/fbe-evidence.log" >"$out/$saved/fbe-extract.log" 2>&1 || true
      fi
    done
  fi
  local crash_extract="$(dirname -- "${BASH_SOURCE[0]}")/../acceptance/crash-evidence.py"
  if [[ -f $crash_extract ]]; then
    for saved in $boot_dirs; do
      python3 -B "$crash_extract" --saved-boot "$out/$saved" \
        >"$out/$saved/crash-extract.log" 2>&1
      if [[ $? == 0 ]]; then
        cat "$out/$saved/crash-extract.log"
        if grep -q ' system_server_crash_loop=1 ' "$out/$saved/crash-extract.log"; then saved_system_server_loop=1; fi
      else saved_crash_extract_failed=1; echo 'STABILITY_SUMMARY capture_extract_failed=1'; fi
    done
  else saved_crash_extract_failed=1; echo 'STABILITY_SUMMARY capture_extractor_missing=1'; fi
  local hvenc_extract="$(dirname -- "${BASH_SOURCE[0]}")/../video/qualification/extract.py"
  local hvenc_args=()
  if [[ -f $hvenc_extract ]]; then
    for saved in $boot_dirs; do
      hvenc_args=()
      if [[ $saved == "$latest" ]]; then
        hvenc_args=(--host-stage "$out/hvenc-qualification.json" --media "$out/hvenc-output.mp4")
      fi
      if python3 -B "$hvenc_extract" --saved-boot "$out/$saved" "${hvenc_args[@]}" >"$out/$saved/hvenc-extract.log" 2>&1; then
        cat "$out/$saved/hvenc-extract.log"
        if [[ $saved == "$latest" ]] && grep -q 'probe_seen=1 native_pass=1 .*diagnostics_ok=1' "$out/$saved/hvenc-extract.log"; then saved_hvenc_ok=1; fi
      else echo "HVENC_DIAGNOSTICS extract_failed=1"; fi
    done
  fi
  echo "mainline logs: $out/$latest" >&2
  # Raw kernel/TA/daemon logs stay in the private log directory. They may
  # contain identifiers; console summaries use only fixed numeric protocols.
}
pull_pstore() {
  local archive_pstore=${archive%.tar}-pstore.tar
  rsh 'mkdir -p /sys/fs/pstore && { grep -q " /sys/fs/pstore " /proc/mounts || mount -t pstore pstore /sys/fs/pstore; } && ls -la /sys/fs/pstore' >"$out/pstore-list.txt" 2>&1 || return 1
  rsh "tar -C /sys/fs -cf $archive_pstore pstore" >&2 || return 1
  pull_archive "$archive_pstore" "$out/pstore.tar" || return 1
  tar -xf "$out/pstore.tar" -C "$out" || return 1
  rsh "rm -f $archive_pstore" >/dev/null
  cat "$out/pstore-list.txt" >&2
  local f
  for f in "$out"/pstore/*; do
    [[ -f $f ]] || continue
    echo "pstore tail: $f" >&2
    tail -c 16384 "$f" >&2
  done
  # Do not unlink any pstore records. Firmware may already have erased RAM
  # during reset; an empty archive is evidence of absence, not no panic.
}
hold_b() {
  local until=$((SECONDS+hold)) last= m s hook_rc=0
  echo "B boot_completed=1; holding ${hold}s to verify deadline disarm" >&2
  export A9_BTRY_OWNER_END
  A9_BTRY_OWNER_END=$(python3 -c 'import sys,time;print(time.monotonic()+int(sys.argv[1]))' "$hold")
  # Same owner end before/after the hook's intentional reboot. Its volatile
  # lease disappears at reboot and is recreated when ordinary B shell returns.
  python3 -B "$(dirname -- "${BASH_SOURCE[0]}")/hold-lease.py" --deadline "$A9_BTRY_OWNER_END" >"$out/hold-lease.log" 2>&1 &
  lease_pid=$!
  if [[ -n $onb ]]; then
    echo "B hook: $onb; output: $out/onb.log; limit: ${hold}s" >&2
    # Propagate the original owner's absolute monotonic deadline through any
    # prerequisite hook. A late qualification cannot reset the hold budget.
    # No runner mode polling during the hook. Only the bounded lease keeper
    # sends its existing once-per-minute hold heartbeat.
    # timeout owns the child group, including adb and Python descendants.
    timeout --signal=TERM --kill-after=10 "$hold" "$onb" >"$out/onb.log" 2>&1 &
    hook_pid=$!
    wait "$hook_pid" || hook_rc=$?
    hook_pid=
    printf '%s\n' "$hook_rc" >"$out/onb.rc"
    tail -n 35 "$out/onb.log" >&2
  fi
  # Always check B once after the hook, even if it consumed the full hold.
  while :; do
    m=$(mode)
    if [[ $m == edl ]]; then edl_seen=1; result=HANG; detail=edl; return; fi
    if [[ $m == fastboot ]]; then result=HANG; detail=fastboot_during_hold; return; fi
    if [[ $m == device ]]; then
      probe_boot hold_b
      if [[ $boot_slot == _a && $boot_completed == 1 ]]; then result=HANG; detail=A_fallback_during_hold; return; fi
      if [[ $boot_slot == _b && $boot_completed == 1 ]]; then last=$SECONDS; fi
    fi
    ((SECONDS<until && hook_rc==0)) || break
    sleep 2
  done
  if ((hook_rc!=0)); then
    result=HANG; detail=onb_failed_rc_${hook_rc}
  elif [[ -n $last ]] && ((SECONDS-last<=15)); then
    result=PASS; detail=B_boot_completed_hold_${hold}s
  else
    result=NOUSB; detail=B_hold_transport_lost
  fi
}
finish() {
  local rc=2
  trap - EXIT INT TERM
  set +e
  if [[ -n $lease_pid ]]; then
    kill -TERM "$lease_pid" 2>/dev/null
    wait "$lease_pid" 2>/dev/null
    lease_pid=
  fi
  if [[ -n $hook_pid ]]; then
    kill -TERM "$hook_pid" 2>/dev/null
    wait "$hook_pid" 2>/dev/null
    hook_pid=
  fi
  if return_a; then restored=1; fi
  # A may be unbootable yet TWRP can still read unencrypted logs and pstore.
  if ((attempted)) && { ((restored)) || [[ $(mode) == recovery ]]; }; then
    pull_logs || echo "Log collection unavailable; inspect $out" >&2
    if ((restored && collecting)); then return_a && restored=1 || restored=0; fi
  fi
  if ((edl_seen)); then result=HANG; detail=edl;
  elif ((!restored)); then result=NOUSB; detail=return_A_failed; fi
  if ((restored && !edl_seen && saved_system_server_loop)); then
    result=HANG; detail=system_server_crash_loop
  elif [[ $result == PASS ]] && ((saved_crash_extract_failed)); then
    result=HANG; detail=crash_evidence_unavailable
  fi
  if [[ $result == PASS ]] && ((hvenc_qualify && !saved_hvenc_ok)); then
    result=HANG; detail=hvenc_qualification_diagnostics
  fi
  ((restored)) || echo 'Return to Android A failed; slot_A=0 in result' >&2
  [[ $result == PASS ]] && rc=0
  [[ $result == HANG ]] && rc=1
  printf '%s boot_completed=%s slot_A=%s detail=%s logs=%s\n' "$result" "$b_completed" "$restored" "$detail" "$out"
  exit "$rc"
}
# Run the exact flash-test-b check-only path before installing the EXIT trap:
# refusal must not reboot a phone or start a partition write. FORMAT=1 skips
# checks requiring root on Android A; recovery writes and readback stay strict.
if [[ -n $SYSTEM ]]; then
  detail=system_prepare
  check="$(dirname -- "${BASH_SOURCE[0]}")/../system-delivery/flash-test-b.sh"
  [[ -x $check ]] || check="$A9_HOME/scripts/flash-test-b.sh"
  if ! timeout --kill-after=5 180 "$check" "$SYSTEM" >"$out/system-check.log" 2>&1; then
    echo "NOUSB boot_completed=0 slot_A=unknown detail=system_check_failed logs=$out"
    exit 2
  fi
  system_want=$(awk '{print $1}' "$SYSTEM.sha256")
  system_size=$(stat -c %s "$SYSTEM")
fi
trap finish EXIT
trap 'detail=interrupted; exit 2' INT TERM
if [[ -n $SYSTEM ]]; then
  # Stock fastbootd cannot open system_b for writing. Use the same TWRP RAM
  # delivery as vendor, before changing vendor or formatting shared data.
  recovery
  [[ $(adb_cmd shell getprop ro.boot.slot_suffix | tr -d '\r\n') == _a ]] || { detail=system_recovery_slot; exit 2; }
  system_pushed=0
  for t in 1 2 3; do
    if timeout --kill-after=5 900 "$ADB" push "$SYSTEM" /tmp/b-try-system.img >&2; then system_pushed=1; break; fi
    adb_cmd shell rm -f /tmp/b-try-system.img >/dev/null 2>&1 || true
    sleep 10
  done
  ((system_pushed)) || { detail=system_push_failed; exit 2; }
  timeout --kill-after=5 300 "$ADB" shell 'blockdev --setrw /dev/block/mapper/system_b && dd if=/tmp/b-try-system.img of=/dev/block/mapper/system_b bs=4M conv=fsync' >&2 || { detail=system_flash_failed; exit 2; }
  if ! got=$(timeout --kill-after=5 180 "$ADB" shell "head -c $system_size /dev/block/mapper/system_b | sha256sum" | tr -d '\r' | awk '{print $1}'); then
    detail=system_readback_failed; exit 2
  fi
  [[ $got == "$system_want" ]] || { detail=system_mismatch; exit 2; }
  adb_cmd shell rm -f /tmp/b-try-system.img >/dev/null || true
  printf 'SYSTEM_DELIVERY verified=1 bytes=%s sha256=%s\n' "$system_size" "$system_want" >"$out/system-delivery.log"
fi
if [[ -n "$VENDOR" ]]; then
  # Any plain/wrapped vendor can be supplied. Add the matching deadline
  # trigger and early logger policy to a private derivative before flashing.
  helper="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/prepare-vendor.py"
  python3 "$helper" "$BOOT" "$VENDOR" "$out/vendor.img" "$out/vendor-overlay" >&2
  VENDOR="$out/vendor.img"
  recovery
  want=$(sha256sum "$VENDOR" | cut -d' ' -f1)
  for t in 1 2 3 4 5; do timeout 180 "$ADB" push "$VENDOR" /tmp/b-try-vendor.img >&2 && break; sleep 8; timeout 10 "$ADB" shell rm -f /tmp/b-try-vendor.img >/dev/null 2>&1 || true; done
  got=$(timeout 180 "$ADB" shell 'blockdev --setrw /dev/block/mapper/vendor_b && dd if=/tmp/b-try-vendor.img of=/dev/block/mapper/vendor_b bs=4M conv=fsync >/dev/null 2>&1 && sync && rm /tmp/b-try-vendor.img && sha256sum /dev/block/mapper/vendor_b' | tr -d '\r' | cut -d' ' -f1)
  [[ $got == "$want" ]] || { detail=vendor_mismatch; exit 2; }
  rm -f "$VENDOR"
  # Optional same-key diagnostic snapshot while the existing owner is already
  # in recovery. The hook owns no reboot/cleanup and must finish before B boots.
  if [[ -n $before_b ]]; then
    A9_BTRY_IN_SETUP=1 timeout --signal=TERM --kill-after=5 90 "$before_b" >"$out/before-b.log" 2>&1 || { detail=before_b_failed; exit 2; }
  fi
  if [[ ${A9_BTRY_FORMAT:-0} == 1 ]]; then
    timeout 300 "$ADB" shell twrp format data >&2 || { detail=format_failed; exit 2; }
    formatted=1
  fi
fi
if [[ -n $VENDOR || -n $SYSTEM ]]; then
  adb_cmd shell 'sh /system/bin/rebootsystem.sh' >/dev/null 2>&1 || true
fi
bootloader 180
fb_cmd flash boot_b "$BOOT" >&2
fb_cmd set_active b >&2
attempted=1
fb_cmd reboot >&2
detail=B_observation_timeout
observation_start=$SECONDS
reboot_grace=$((SECONDS+15))
end=$((SECONDS+deadline+120))
b_departed=0 last_mode=unobserved
echo "B observation: format=$formatted deadline=${deadline}s budget=$((deadline+120))s" >&2
while ((SECONDS<end)); do
  m=$(mode)
  if [[ $m != "$last_mode" ]]; then
    echo "B observation: mode=${m:-offline} elapsed=$((SECONDS-observation_start))s" >&2
    last_mode=$m
  fi
  if [[ $m == edl ]]; then edl_seen=1; result=HANG; detail=edl; break; fi
  if [[ $m == fastboot ]]; then
    # A successful reboot reply can precede USB disconnect. Do not mistake
    # that old transport for a deadline return. Once it leaves, any fastboot
    # return is a failure; never leaving is bounded by the 15-second grace.
    if ((b_departed || SECONDS>=reboot_grace)); then
      result=HANG; detail=returned_fastboot; break
    fi
  else
    b_departed=1
  fi
  if [[ $m == device ]]; then
    probe_limit=$((end-SECONDS))
    ((probe_limit>20)) && probe_limit=20
    ((probe_limit>0)) || break
    probe_boot observe_b "$probe_limit"
    if [[ $boot_slot == _b && $boot_completed == 1 ]]; then
      b_completed=1
      result=PASS; detail=B_boot_completed
      echo "B observation: boot_completed=1 elapsed=$((SECONDS-observation_start))s" >&2
      ((hold==0)) || hold_b
      break
    fi
    if [[ $boot_slot == _a && $boot_completed == 1 ]]; then result=HANG; detail=automatic_A_fallback; break; fi
  fi
  sleep 2
done
if [[ $result == NOUSB && $detail == B_observation_timeout && ( ${m:-} == device || ${m:-} == recovery ) ]]; then
  result=HANG; detail=B_boot_incomplete
fi
# Keep the observation outcome visible even if finish reports an A-return error.
echo "B observation: result=$result boot_completed=$b_completed detail=$detail elapsed=$((SECONDS-observation_start))s" >&2
