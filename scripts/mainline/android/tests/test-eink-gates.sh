#!/usr/bin/env bash
# test-eink-gates.sh -- host test of `a9ml-eink.sh open/close` against a fake
# sysfs whose kernel bool parameters print Y/N, as the real ones do (eink run 3
# refused the open because "N" was compared with "0"). Offline; run by
# android-boot.sh --offline --eink.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT=$HERE/vendor/bin/a9ml-eink.sh
fails=0
mk() {   # mk <dir> <a9_eink.enable initial Y|N>
  local R=$1
  mkdir -p "$R/stubs" "$R/proc" "$R/fw/hisense" "$R/sys/devices/platform/eink-display/drm/card1/card1-DSI-1/eink" "$R/sys/class/drm"
  : > "$R/proc/cmdline"
  ln -s ../../devices/platform/eink-display/drm/card1 "$R/sys/class/drm/card1"
  ln -s ../../devices/platform/eink-display/drm/card1/card1-DSI-1 "$R/sys/class/drm/card1-DSI-1"
  E=$R/sys/devices/platform/eink-display/drm/card1/card1-DSI-1/eink
  printf 'enable 0 waveform 1 transport 0 hv_on 0 stall_fault 0 template 1\nupdates 2 aborts 0 frames 0 late 0 vbl_skips 0 commit_fail 0 max_gap_us 0 late_drive 0 vbl_skips_drive 0 max_gap_drive_us 0\nrail_fault 0 rail_faults 0 rail_at_ms 0 rail_getter 0 rail_pg_raw 0 rail_on 0 rail_energized 0 rail_forced 0 rail_emergency 0 rail_pg_valid 0 rail_tx_on 0 rail_gone 0 rail_front_null 0 rail_armed 0 rail_session_ms 0 rail_since_wf_ms 0 rail_wf_ret 0 force_clear 1\n' > "$E/status"
  : > "$E/force_clear"
  for m in a9_eink fp9929a panel_hisense_hlte556n_eink drm; do mkdir -p "$R/sys/module/$m/parameters"; done
  M=$R/sys/module
  echo 0x10 > "$M/drm/parameters/debug"
  echo "$2" > $M/a9_eink/parameters/enable; echo N > $M/fp9929a/parameters/allow_power_on
  echo N > $M/panel_hisense_hlte556n_eink/parameters/enable; echo Y > $M/a9_eink/parameters/init_flash
  echo 20000 > $M/fp9929a/parameters/max_on_ms; echo 1000 > $M/fp9929a/parameters/min_off_ms
  for f in prefill_frames:4 bin_min:0 bin_max:13 hv_max_ms:60000 hv_hold_ms:3000 pg_dip_policy:1; do echo "${f#*:}" > $M/a9_eink/parameters/${f%%:*}; done
  echo 1 > "$M/fp9929a/parameters/pg_dip_policy"
  head -c 64 /dev/zero > "$R/fw/hisense/a9-waveform.bin"; head -c 64 /dev/zero > "$R/fw/hisense/a9-ctrl-template.bin"
  printf 'vendor.a9ml.eink.card=card1\nvendor.a9ml.eink.bins=8-10\n' > "$R/props"
  cat > "$R/stubs/getprop" <<S
#!/bin/sh
sed -n "s/^\$1=//p" $R/props | tail -n1
S
  cat > "$R/stubs/setprop" <<S
#!/bin/sh
echo "\$1=\$2" >> $R/props
if [ "\$1" = vendor.a9ml.eink.kmsg_start ]; then
  printf '%s %s%s%s\n' "\$(cat $R/sys/module/drm/parameters/debug)" "\$(cat $R/sys/module/a9_eink/parameters/enable)" "\$(cat $R/sys/module/fp9929a/parameters/allow_power_on)" "\$(cat $R/sys/module/panel_hisense_hlte556n_eink/parameters/enable)" > $R/preopen
  cat $R/sys/module/a9_eink/parameters/prefill_frames > $R/preopen-prefill
  cat $R/sys/module/a9_eink/parameters/pg_dip_policy $R/sys/module/fp9929a/parameters/pg_dip_policy > $R/preopen-pg
fi
S
  printf '#!/bin/sh\necho "[ 1.0] a9-eink eink-display: control template loaded (sha256 a88ae8ef...)"\n' > "$R/stubs/dmesg"
  printf '#!/bin/sh\necho "$@" >> "%s/sleeps"\n' "$R" > "$R/stubs/sleep"
  chmod +x "$R"/stubs/*
}
run() { local R=$1; shift; PATH="$R/stubs:$PATH" A9ML_FAULT_DIR="$R/faults" A9ML_SYS="$R/sys" A9ML_PROC="$R/proc" A9ML_KMSG="$R/kmsg" A9ML_FWDIR="$R/fw" "${A9ML_TEST_SHELL:-sh}" "$SCRIPT" "$@"; }
check() { if eval "$2"; then echo "  ok   $1"; else echo "  FAIL $1"; sed 's/^/     kmsg: /' "$R/kmsg" 2>/dev/null; fails=$((fails+1)); fi; }

R=$(mktemp -d); mk "$R" N; M=$R/sys/module
run "$R" open >/dev/null 2>&1
check "open with every gate reading N: opens all three, bins 8-10, hv_max 15000, stock-like hv_hold 3000" \
  '[ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = 111 ] && [ "$(cat $M/a9_eink/parameters/bin_min)-$(cat $M/a9_eink/parameters/bin_max)" = 8-10 ] && [ "$(cat $M/a9_eink/parameters/hv_max_ms)" = 15000 ] && [ "$(cat $M/a9_eink/parameters/hv_hold_ms)" = 3000 ] && grep -q "vendor.a9ml.eink.watch=1" $R/props && grep -q "gates OPEN" $R/kmsg'
check "the open requested one dry-run update (force_clear)" '[ "$(cat $R/sys/devices/platform/eink-display/drm/card1/card1-DSI-1/eink/force_clear)" = 1 ]'
check "DRM debug reads 0 at the pre-open checkpoint with every gate still N" \
  '[ "$(cat $R/preopen)" = "0 NNN" ] && [ "$(cat $M/drm/parameters/debug)" = 0 ] && grep -q "DRM debug 0x10 -> 0 (read back)" $R/kmsg'
check "default overwrites stale K=4 and verifies K=12 before the first gate" \
  '[ "$(cat $R/preopen-prefill)" = 12 ] && [ "$(cat $M/a9_eink/parameters/prefill_frames)" = 12 ] && grep -q "prefill_frames 12 (read back, gates closed)" $R/kmsg'
check "stock PG policy opens without the removed PG timeout parameter" \
  '[ "$(tr "\n" : < $R/preopen-pg)" = 1:1: ] && [ "$(cat $R/preopen)" = "0 NNN" ] && [ ! -e $M/fp9929a/parameters/pg_timeout_ms ]'
echo Y > $M/a9_eink/parameters/enable; echo Y > $M/fp9929a/parameters/allow_power_on; echo Y > $M/panel_hisense_hlte556n_eink/parameters/enable
run "$R" close >/dev/null 2>&1
check "close from Y/Y/Y: all gates 0, bins 0-13, read back closed" \
  '[ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = 000 ] && [ "$(cat $M/a9_eink/parameters/bin_min)-$(cat $M/a9_eink/parameters/bin_max)" = 0-13 ] && grep -q "gates closed (read back)" $R/kmsg'
check "close never restores boot DRM debug while shutting down" '[ "$(cat $M/drm/parameters/debug)" = 0 ]'
rm -rf "$R"

# Both the explicit short-diagnostic opt-out and an old zero reset are retained.
for choice in 0 default; do
  R=$(mktemp -d); mk "$R" N; M=$R/sys/module
  echo "vendor.a9ml.eink.hv_hold=$choice" >> "$R/props"
  echo 0 > "$M/a9_eink/parameters/hv_hold_ms"
  run "$R" open >/dev/null 2>&1
  want=3000; [ "$choice" != 0 ] || want=0
  check "idle hold $choice sets $want without changing absolute HV caps" \
    '[ "$(cat $M/a9_eink/parameters/hv_hold_ms)" = "$want" ] && [ "$(cat $M/a9_eink/parameters/hv_max_ms)" = 15000 ] && [ "$(cat $M/fp9929a/parameters/max_on_ms)" = 20000 ]'
  run "$R" close >/dev/null 2>&1
  rm -rf "$R"
done

R=$(mktemp -d); mk "$R" N; M=$R/sys/module
rm "$M/drm/parameters/debug"
run "$R" open >/dev/null 2>&1
check "missing DRM debug parameter refuses before any drive gate opens" \
  'grep -q "cannot disable DRM debug before opening drive gates" $R/kmsg && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = NNN ]'
rm -rf "$R"

R=$(mktemp -d); mk "$R" N; M=$R/sys/module
mkdir "$R/debug-directory"; rm "$M/drm/parameters/debug"; ln -s "$R/debug-directory" "$M/drm/parameters/debug"
run "$R" open >/dev/null 2>&1
check "unreadable DRM debug parameter refuses before any drive gate opens" \
  'grep -q "cannot disable DRM debug before opening drive gates" $R/kmsg && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = NNN ]'
rm -rf "$R"

R=$(mktemp -d); mk "$R" N; M=$R/sys/module
CAT=$(command -v cat)
cat > "$R/stubs/cat" <<S
#!/bin/sh
if [ "\$1" = "$M/drm/parameters/debug" ]; then echo 0x10; else exec "$CAT" "\$@"; fi
S
chmod +x "$R/stubs/cat"
run "$R" open >/dev/null 2>&1
check "DRM debug readback still enabled refuses before any drive gate opens" \
  'grep -q "cannot disable DRM debug before opening drive gates" $R/kmsg && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = NNN ]'
rm -rf "$R"

for delay in default 123; do
  R=$(mktemp -d); mk "$R" N; M=$R/sys/module
  [ "$delay" = default ] || echo "a9ml.drmdebug_s=$delay" > "$R/proc/cmdline"
  run "$R" drmdebug-off >/dev/null 2>&1
  want=$delay; [ "$delay" = default ] && want=60
  check "boot debug timer uses $want seconds without init inline expansion" \
    '[ "$(cat $R/sleeps)" = "$want" ] && [ "$(cat $M/drm/parameters/debug)" = 0 ] && [ "$(cat $M/a9_eink/parameters/enable)" = N ]'
  rm -rf "$R"
done

R=$(mktemp -d); mk "$R" N; M=$R/sys/module
echo 'a9ml.drmdebug_s=invalid' > "$R/proc/cmdline"
run "$R" drmdebug-off >/dev/null 2>&1
check "invalid boot debug delay is rejected without sleep or gate writes" \
  '[ ! -e "$R/sleeps" ] && grep -q "invalid a9ml.drmdebug_s" $R/kmsg && [ "$(cat $M/a9_eink/parameters/enable)" = N ]'
check "init launches the timer as a script argument without dollar expressions" \
  'grep -qx "service a9ml_drmdebug_off /system/bin/sh /vendor/bin/a9ml-eink.sh drmdebug-off" "$HERE/vendor/etc/init/init.a9ml-eink.rc"'
rm -rf "$R"

R=$(mktemp -d); mk "$R" Y; M=$R/sys/module
run "$R" open >/dev/null 2>&1
check "open refused while a9_eink.enable reads Y; nothing else opened" \
  'grep -q "gate a9_eink/enable is .1. before opening" $R/kmsg && [ "$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = NN ] && grep -q "vendor.a9ml.eink.gate=refused" $R/props'
rm -rf "$R"

R=$(mktemp -d); mk "$R" N; M=$R/sys/module; sed -i 's/template 1/template 0/' $R/sys/devices/platform/eink-display/drm/card1/card1-DSI-1/eink/status
run "$R" open >/dev/null 2>&1
check "open refused without a loaded control template; gates stay N" \
  'grep -q "control template not loaded" $R/kmsg && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)" = NN ]'
rm -rf "$R"

# Review 19: property boundaries, write/read failures and ordering, all fake sysfs.
for k in $(seq 2 32); do
  R=$(mktemp -d); mk "$R" N; M=$R/sys/module
  echo "vendor.a9ml.eink.prefill_frames=$k" >> "$R/props"
  run "$R" open >/dev/null 2>&1; rc=$?
  check "qualification override K=$k selected before opening" \
    '[ "$rc" = 0 ] && [ "$(cat $R/preopen-prefill)" = "$k" ] && [ "$(cat $R/preopen)" = "0 NNN" ] && [ "$(cat $M/a9_eink/parameters/enable)" = 1 ]'
  rm -rf "$R"
done
for k in 0 1 33 -1 abc 4junk 0x0c 012 999999999999999999999999999999999999; do
  R=$(mktemp -d); mk "$R" N; M=$R/sys/module
  echo "vendor.a9ml.eink.prefill_frames=$k" >> "$R/props"
  run "$R" open >/dev/null 2>&1; rc=$?
  check "invalid qualification override '$k' refuses with all gates closed" \
    '[ "$rc" = 1 ] && grep -q "bad vendor.a9ml.eink.prefill_frames" $R/kmsg && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = NNN ] && [ ! -e "$R/preopen" ] && [ "$(cat $M/a9_eink/parameters/prefill_frames)" = 4 ]'
  rm -rf "$R"
done
for failure in missing write read mismatch empty read-error-with-value; do
  R=$(mktemp -d); mk "$R" N; M=$R/sys/module
  PF=$M/a9_eink/parameters/prefill_frames
  case "$failure" in
    missing) rm "$PF" ;;
    write) rm "$PF"; mkdir "$PF" ;;
    *)
      CAT=$(command -v cat)
      case "$failure" in
        read) body='exit 1' ;;
        mismatch) body='echo 4' ;;
        empty) body='exit 0' ;;
        read-error-with-value) body='echo 12; exit 1' ;;
      esac
      cat > "$R/stubs/cat" <<S
#!/bin/sh
if [ "\$1" = "$PF" ]; then $body; else exec "$CAT" "\$@"; fi
S
      chmod +x "$R/stubs/cat"
      ;;
  esac
  run "$R" open >/dev/null 2>&1; rc=$?
  check "prefill $failure refuses before any gate or dry-run write" \
    '[ "$rc" = 1 ] && grep -q "open refused:.*prefill_frames" $R/kmsg && grep -q "vendor.a9ml.eink.gate=refused" $R/props && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = NNN ] && [ ! -e "$R/preopen" ] && [ ! -s "$E/force_clear" ]'
  if [ "$failure" = missing ]; then
    check "missing sysfs parameter is never created as a regular file" '[ ! -e "$PF" ]'
  fi
  rm -rf "$R"
done

# patch 0039: the rail-fault latch is required, reported and fatal to the session
ST=devices/platform/eink-display/drm/card1/card1-DSI-1/eink/status
R=$(mktemp -d); mk "$R" N; M=$R/sys/module; sed -i '/^rail_fault /d' "$R/sys/$ST"
run "$R" open >/dev/null 2>&1; rc=$?
check "open refused when the kernel status lacks rail_fault (no 0039); gates stay N" \
  '[ "$rc" = 1 ] && grep -q "a9_eink status has no .rail_fault." $R/kmsg && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = NNN ]'
rm -rf "$R"

R=$(mktemp -d); mk "$R" N; M=$R/sys/module; sed -i 's/^rail_fault 0 rail_faults 0/rail_fault 1 rail_faults 1/' "$R/sys/$ST"
run "$R" open >/dev/null 2>&1; rc=$?
check "open refused while a rail fault is latched (never cleared by userspace)" \
  '[ "$rc" = 1 ] && grep -q "fault present before opening: a9_eink rail_fault 1" $R/kmsg && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = NNN ] && [ ! -e $M/a9_eink/parameters/clear_fault ]'
rm -rf "$R"

for src in status kmsg; do
  R=$(mktemp -d); mk "$R" N; M=$R/sys/module
  echo Y > $M/a9_eink/parameters/enable; echo Y > $M/fp9929a/parameters/allow_power_on; echo Y > $M/panel_hisense_hlte556n_eink/parameters/enable
  echo "vendor.a9ml.eink.gate=open" >> "$R/props"
  if [ $src = status ]; then
    sed -i 's/^rail_fault 0 rail_faults 0 rail_at_ms 0 rail_getter 0 rail_pg_raw 0/rail_fault 1 rail_faults 1 rail_at_ms 1054681 rail_getter 0 rail_pg_raw 0/' "$R/sys/$ST"
    want="a9_eink rail_fault 1 (pg_raw 0 getter 0)"
  else
    printf '#!/bin/sh\necho "[ 1054.681452] a9-eink eink-display: FP9929A dropped out while the pipeline was on: rail fault latched (getter 0 pg_raw 0 on 1 energized 1 forced 0 emergency 0 pg_valid 1 tx_on 1 gone 0 front_null 1 armed 0 session_ms 2666 since_wf_ms 26 wf_ret 0); write 1 to a9_eink.clear_fault, then a full Clear runs first"\n' > "$R/stubs/dmesg"
    want="kmsg: a9-eink eink-display: FP9929A dropped out while the pipeline was on: rail fault latched"
  fi
  run "$R" watch >/dev/null 2>&1; rc=$?
  # the gate close itself runs "$0 close" through the Android shebang; the
  # close path is covered above. Here: detected, latched, never cleared.
  check "watch ($src): rail fault is a session fault, reason latched, never cleared" \
    '[ "$rc" = 1 ] && grep -q "vendor.a9ml.eink.gate=faulted" $R/props && grep -qF "vendor.a9ml.eink.fault=$(printf '%s' "$want" | cut -c1-80)" $R/props && grep -qF "$want" $R/kmsg && grep -q "FAULT: .*-> gates closed" $R/kmsg && [ ! -e $M/a9_eink/parameters/clear_fault ]'
  rm -rf "$R"
done

# GPU rendering uses the same panel checks and gate sequence; no GPU admission
# helper or guard service is required.
R=$(mktemp -d); mk "$R" N; M=$R/sys/module
printf 'ro.vendor.a9ml.gpu=1\nvendor.a9ml.eink.gate=open\n' >> "$R/props"
run "$R" open >/dev/null 2>&1; rc=$?
check "GPU build opens through the normal panel checks without a GPU admission helper" \
  '[ "$rc" = 0 ] && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = 111 ] && grep -q "vendor.a9ml.eink.watch=1" "$R/props"'
rm -rf "$R"

R=$(mktemp -d); mk "$R" N; M=$R/sys/module
printf 'ro.vendor.a9ml.gpu=1\nvendor.a9ml.eink.fault=rail fault\n' >> "$R/props"
run "$R" open >/dev/null 2>&1; rc=$?
check "GPU build still refuses a latched E Ink fault before any gate opens" \
  '[ "$rc" = 1 ] && [ "$(cat $M/a9_eink/parameters/enable)$(cat $M/fp9929a/parameters/allow_power_on)$(cat $M/panel_hisense_hlte556n_eink/parameters/enable)" = NNN ] && grep -q "fault latched this boot" "$R/kmsg"'
rm -rf "$R"

[ $fails = 0 ] && echo "test-eink-gates: PASS" || { echo "test-eink-gates: $fails FAILED"; exit 1; }
