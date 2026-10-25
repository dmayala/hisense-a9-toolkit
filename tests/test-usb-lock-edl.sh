#!/usr/bin/env bash
# Offline test (2026-10-17): every script that enters/uses EDL or reboots the
# phone takes the shared USB lock (refuse with rc 3 when held), and nesting
# works: a script called by a locked script inherits the lock instead of
# refusing. Private lock path ($XDG_RUNTIME_DIR in a temp dir), fake phone on
# PATH, fake edl binary: never touches the real lock, USB or a phone.
set -u
R=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'kill $(cat "$T/holder.pid" 2>/dev/null) 2>/dev/null; rm -rf "$T"' EXIT
mkdir -p "$T/edlbin" "$T/fw"
printf '#!/bin/sh\necho FAKE-EDL "$@" >> "$T/edl.log"\n' > "$T/edlbin/edl"; chmod +x "$T/edlbin/edl"
: > "$T/loader"
# inputs the --write paths check before they reach the lock
head -c 24576 /dev/zero > "$T/fw/gpt_main4.bin"; head -c 20480 /dev/zero > "$T/fw/gpt_backup4.bin"
echo '<patches><patch physical_partition_number="4"/></patches>' > "$T/fw/patch4.xml"
for c in 1:99 2:1043289 3:100957 4:46563 5:281874; do truncate -s $(( ${c#*:} * 4096 )) "$T/fw/super_${c%%:*}.img"; done
export T
export PATH="$R/tests/fake-bin:$T/edlbin:$PATH" XDG_RUNTIME_DIR="$T" A9_HOME="$R" A9_SETTLE=0 \
  FAKE_LOG="$T/calls.log" FAKE_STATE="$T/state.json" FAKE_SERIAL=FAKE0001 A9_SERIAL=FAKE0001 ANDROID_SERIAL=FAKE0001 \
  LOADER="$T/loader" A9_FW="$T/fw" A9_DEVICE_JSON="$T/reg.json"
echo '{"mode":"edl","slot":"a","boot_ok":{"a":true,"b":true}}' > "$T/state.json"; : > "$T/calls.log"
fail=0
ok()  { echo "PASS $*"; }
bad() { echo "FAIL $*"; fail=1; }

# someone else holds the lock (a real a9_usb_lock holder, as promote-b-to-a would)
setsid bash -c ". '$R/scripts/a9-env.sh'; a9_usb_lock; echo \$\$ > '$T/holder.pid'; sleep 600" >/dev/null 2>&1 </dev/null &
for _ in $(seq 50); do [ -s "$T/holder.pid" ] && break; sleep 0.1; done
sleep 0.5

refused() { # refused <label> <cmd...>: rc 3 and no edl / reboot call
  local l="$1"; shift
  : > "$T/edl.log"; : > "$T/calls.log"
  "$@" >"$T/out.txt" 2>&1 </dev/null; local rc=$?
  if [ $rc = 3 ] && ! [ -s "$T/edl.log" ] && ! grep -qE "reboot|flash|set_active" "$T/calls.log"; then ok "$l refused while the lock is held (rc 3)"
  else bad "$l: rc $rc, edl calls: $(cat "$T/edl.log" | head -2), out: $(head -3 "$T/out.txt")"; fi
}
S=$R/scripts
refused "scripts/edl printgpt"          "$S/edl" printgpt --lun=4
refused "backup-edl.sh gpt"             "$S/backup-edl.sh" gpt
refused "edl-cli.sh gpt"                "$S/edl-cli.sh" gpt
refused "edl-restore-minimal.sh check"  "$S/edl-restore-minimal.sh" check
refused "verify-partitions-edl.sh"      env OUT="$T/vout" "$S/verify-partitions-edl.sh"
refused "restore-factory-gpt-lun4.sh --write" "$S/restore-factory-gpt-lun4.sh" --write
refused "flash-stock-super.sh --write"  "$S/flash-stock-super.sh" --write
refused "abort-to-slot-a.sh"            "$S/abort-to-slot-a.sh"
refused "twrp.sh enter"                 "$S/twrp.sh" enter
refused "twrp.sh exit"                  "$S/twrp.sh" exit
refused "restore-golden.sh --write"     "$S/restore-golden.sh" --write --enter
kill "$(cat "$T/holder.pid")" 2>/dev/null; sleep 0.5

# free lock: a script takes it; its child (the edl wrapper) inherits, not refuses
: > "$T/edl.log"
bash -c ". '$S/a9-env.sh'; a9_usb_lock; '$S/edl' printgpt --lun=4; echo child_rc=\$?" >"$T/out.txt" 2>&1
grep -q "child_rc=0" "$T/out.txt" && grep -q FAKE-EDL "$T/edl.log" && ok "child edl inherits the lock held by its parent script" || bad "nesting: $(cat "$T/out.txt")"
# standalone with the lock free: runs and releases
: > "$T/edl.log"; "$S/edl" printgpt >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && grep -q FAKE-EDL "$T/edl.log" && ok "edl runs when the lock is free" || bad "edl free rc $rc"
# release after exit
( exec 8>"$T/a9-usb.lock"; flock -n 8 ) && ok "lock released after the script exits" || bad "lock still held"

# env markers alone must not bypass: stale owner pid, or no owner
setsid bash -c ". '$R/scripts/a9-env.sh'; a9_usb_lock; echo \$\$ > '$T/holder.pid'; sleep 600" >/dev/null 2>&1 </dev/null &
for _ in $(seq 50); do [ -s "$T/holder.pid" ] && break; sleep 0.1; done; sleep 0.5
refused "edl with A9_USB_LOCKED=1 but no owner"   env A9_USB_LOCKED=1 "$S/edl" printgpt
refused "edl with A9_USB_LOCKED=1 and a dead owner" env A9_USB_LOCKED=1 A9_USB_LOCK_OWNER=999999 "$S/edl" printgpt
exit $fail
