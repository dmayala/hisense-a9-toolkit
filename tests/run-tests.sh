#!/usr/bin/env bash
#
# run-tests.sh — offline tests of the flashing scripts against a fake phone
# (tests/fake-bin: adb, fastboot, lsusb simulated from a JSON state file).
# No phone, no network; never touches a real device (PATH is replaced).
#
# Fixtures: two small ext4 images built from the real v6 (crDroid key) and the
# testkey image in ~/a9-assets/roms/a16, holding only what the scripts read
# (build.prop, plat_mac_permissions.xml, framework-res.apk, netbpfload.rc).
#
# Usage: tests/run-tests.sh        (about 2 minutes; exit 0 = all passed)
#
set -uo pipefail
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; A9="$(dirname "$T")"
R=${A9_ROMS:-$HOME/a9-assets/roms/a16}
V6=$R/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v6.img
TK=$R/a9-los23.2-testkey-mochibuilds-20260823-hwcguard.img
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
PASS=0; FAILN=0
pass() { echo "  PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL  $1"; FAILN=$((FAILN + 1)); }

# ---------------------------------------------------------------- fixtures
fixture() { # fixture <src.img> <out.img>
  local d="$W/fx-$(basename "$2" .img)"; mkdir -p "$d/system/etc/selinux" "$d/system/framework" "$d/system/etc/init" "$d/system/etc/vintf" "$d/system/system_ext/apex"
  for f in build.prop etc/selinux/plat_mac_permissions.xml framework/framework-res.apk etc/init/netbpfload.rc; do
    debugfs -R "dump /system/$f $d/system/$f" "$1" >/dev/null 2>&1
  done
  for m in $(debugfs -R "ls /system/etc/vintf" "$1" 2>/dev/null | grep -o 'compatibility_matrix\.[0-9]*\.xml' | sort -u); do
    debugfs -R "dump /system/etc/vintf/$m $d/system/etc/vintf/$m" "$1" >/dev/null 2>&1
  done
  for a in $(debugfs -R "ls /system/system_ext/apex" "$1" 2>/dev/null | grep -o 'com\.android\.vndk\.v[0-9]*\.apex' | sort -u); do : > "$d/system/system_ext/apex/$a"; done
  mke2fs -q -t ext4 -d "$d" "$2" 96M >/dev/null 2>&1 && sha256sum "$2" | sed "s|  .*|  $(basename "$2")|" > "$2.sha256"
}
echo "== building fixtures"
fixture "$V6" "$W/v6.img" && fixture "$TK" "$W/testkey.img" || { echo "fixture build failed"; exit 1; }
mkdir -p "$W/files"; cp "$W/fx-v6/system/framework/framework-res.apk" "$W/fx-v6/system/etc/selinux/plat_mac_permissions.xml" "$W/files/"
printf 'ANDROID!' > "$W/boot.img"; head -c 32 /dev/zero >> "$W/boot.img"; printf '\002\000\000\000' >> "$W/boot.img"; head -c 4096 /dev/zero >> "$W/boot.img"

# isolated A9_HOME: runs/ and the lock go to the temp dir, data/ is the real one
mkdir -p "$W/home"; ln -s "$A9/scripts" "$W/home/scripts"; ln -s "$A9/data" "$W/home/data"
export A9_HOME="$W/home" XDG_RUNTIME_DIR="$W" PATH="$T/fake-bin:$PATH" A9_SETTLE=0 A9_BOOT_TIMEOUT=20 \
       FAKE_FILES="$W/files" FAKE_LOG="$W/calls.log" FAKE_STATE="$W/state.json" ANDROID_SERIAL=FAKE0001 A9_SERIAL=FAKE0001 FAKE_SERIAL=FAKE0001
S="$A9/scripts"
reset() { echo "{\"mode\":\"android\",\"slot\":\"${1:-a}\",\"boot_ok\":{\"a\":true,\"b\":${2:-true}}}" > "$FAKE_STATE"; : > "$FAKE_LOG"; }
state() { python3 -I -c "import json,sys;print(eval(sys.argv[1],{},json.load(open('$FAKE_STATE'))))" "$1"; }

echo "== edl write guard"
mkdir -p "$W/edlbin"; printf '#!/bin/sh\necho FAKE-EDL "$@"\n' > "$W/edlbin/edl"; chmod +x "$W/edlbin/edl"
for c in "w persist" "w devinfo" "w diag" "w modemst1" "w abl_a" "w xbl_b" "ws 0" "wl d" "e misc" "qfil a b c"; do
  PATH="$W/edlbin:$PATH" LOADER=/dev/null "$S/edl" $c x >/dev/null 2>&1; [ $? = 3 ] && pass "edl refuses '$c'" || fail "edl did not refuse '$c'"
done
touch "$W/loader"
PATH="$W/edlbin:$PATH" LOADER="$W/loader" "$S/edl" w super x 2>&1 | grep -q FAKE-EDL && pass "edl allows 'w super'" || fail "edl blocked 'w super'"
PATH="$W/edlbin:$PATH" LOADER="$W/loader" A9_EDL_ALLOW_BOOTCHAIN=1 "$S/edl" w abl_a x 2>&1 | grep -q FAKE-EDL && pass "edl allows abl_a with A9_EDL_ALLOW_BOOTCHAIN=1" || fail "opt-in ignored"
PATH="$W/edlbin:$PATH" EDL_RAW=1 "$S/edl" w persist x >/dev/null 2>&1; [ $? = 3 ] && pass "EDL_RAW=1 does not bypass the guard" || fail "EDL_RAW bypassed the guard"

echo "== a9-compat"
reset a
"$S/a9-compat.sh" "$W/v6.img" >/dev/null && pass "v6 compatible with (fake) running v6" || fail "v6 refused"
"$S/a9-compat.sh" "$W/testkey.img" >/dev/null; [ $? = 1 ] && pass "testkey image refused (platform key)" || fail "testkey image not refused"
out=$("$S/a9-compat.sh" "$W/testkey.img" --against "$W/v6.img"); printf '%s' "$out" | grep -q "REFUSE: platform key differs" && pass "--against image works" || fail "--against"

echo "== flash-test-b"
reset a
"$S/flash-test-b.sh" "$W/v6.img" >/dev/null && ! grep -qE "fastboot (flash|set_active)|adb reboot" "$FAKE_LOG" && pass "check mode passes and writes nothing" || fail "check mode"
reset b
"$S/flash-test-b.sh" "$W/v6.img" >/dev/null 2>&1; [ $? = 1 ] && pass "refuses when the phone is on slot b" || fail "did not refuse on slot b"
reset a
"$S/flash-test-b.sh" "$W/testkey.img" >/dev/null 2>&1; [ $? = 1 ] && ! grep -q "fastboot flash" "$FAKE_LOG" && pass "refuses a key-changing image, nothing flashed" || fail "testkey image flashed or not refused"
reset a
"$S/flash-test-b.sh" "$W/v6.img" --write --no-readback >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ "$(state 'written')" = "['system_b']" ] && [ "$(state 'slot')" = b ] && pass "write: only system_b written, ends on slot b" || fail "write path (rc $rc, written $(state 'written'), slot $(state 'slot'))"
grep -E "fastboot flash .*_a|set_active a" "$FAKE_LOG" >/dev/null && fail "write path touched slot a" || pass "write path never touches slot a"
reset a
"$S/flash-test-b.sh" "$W/v6.img" --boot "$W/boot.img" --write --no-readback >/dev/null 2>&1
[ "$(state 'written')" = "['boot_b', 'system_b']" ] && pass "--boot writes boot_b (bootloader) then system_b (fastbootd)" || fail "--boot (written $(state 'written'))"
reset a
"$S/flash-test-b.sh" --boot "$W/boot.img" --write --no-readback >/dev/null 2>&1
[ "$(state 'written')" = "['boot_b']" ] && pass "try-boot: boot_b only, system untouched" || fail "try-boot (written $(state 'written'))"
reset a false
timeout 900 "$S/flash-test-b.sh" "$W/v6.img" --write --no-readback >/dev/null 2>&1; rc=$?
[ $rc = 1 ] && [ "$(state 'slot')" = a ] && [ "$(state 'mode')" = android ] && pass "B fails to boot -> returns to slot a by itself" || fail "fallback (rc $rc, slot $(state 'slot'), mode $(state 'mode'))"
reset a
"$S/flash-test-b.sh" "$W/v6.img" --write --back >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ "$(state 'slot')" = a ] && pass "--back (with readback) passes and returns to slot a" || fail "--back (rc $rc, slot $(state 'slot'))"
ls "$W/home/runs/" 2>/dev/null | grep -q flash-test-b && pass "run dirs written under runs/" || fail "no run dir"

echo "== promote-b-to-a"
reset a; "$S/flash-test-b.sh" "$W/v6.img" --write >/dev/null 2>&1
"$S/promote-b-to-a.sh" "$W/v6.img" >/dev/null 2>&1 && pass "promote check passes after a tested flash" || fail "promote check"
"$S/promote-b-to-a.sh" "$W/testkey.img" >/dev/null 2>&1; [ $? = 1 ] && pass "promote refuses an image that is not on system_b" || fail "promote accepted an untested image"
"$S/promote-b-to-a.sh" "$W/v6.img" --write >/dev/null 2>&1; rc=$?
[ "$(state 'slot')" = a ] && [ "$(state 'written')" = "['system_b', 'system_a']" ] && pass "promote writes system_a and ends on slot a" || fail "promote (rc $rc, written $(state 'written'), slot $(state 'slot'))"

echo "== usb lock"
( exec 9>"$W/a9-usb.lock"; flock 9; sleep 5 ) & sleep 1
reset a; "$S/flash-test-b.sh" "$W/v6.img" --write --no-readback >/dev/null 2>&1; rc=$?
[ $rc = 3 ] && pass "second writer refused while the USB lock is held" || fail "lock not enforced (rc $rc)"
wait
"$(dirname "$0")/test-usb-lock-adb-server.sh" && pass "lock released although an adb server was spawned under it" || fail "adb server keeps the USB lock"
"$(dirname "$0")/test-usb-lock-children.sh" >/dev/null && pass "lock released after kill -9 although children (tee, sleep, subshell) survive" || fail "surviving children keep the USB lock"
"$(dirname "$0")/test-usb-lock-edl.sh" >"$W/lock-edl.out" 2>&1 && pass "every EDL/reboot script takes the USB lock (rc 3 when held); nested scripts inherit it" || { fail "EDL/reboot scripts and the USB lock"; sed "s/^/    /" "$W/lock-edl.out"; }

echo "== flash-recovery"
mkrec() { python3 -I -c "
import struct,sys
h=bytearray(4096); h[:8]=b'ANDROID!'; struct.pack_into('<I',h,36,4096); struct.pack_into('<I',h,40,2); struct.pack_into('<I',h,1632,int(sys.argv[2]))
open(sys.argv[1],'wb').write(bytes(h)+bytes(8192))" "$1" "$2"; sha256sum "$1" | sed "s|  .*|  $(basename "$1")|" > "$1.sha256"; }
mkrec "$W/twrp-good.img" 88722; mkrec "$W/twrp-new.img" 88722; mkrec "$W/twrp-bad.img" 88722; mkrec "$W/twrp-nodtbo.img" 0
# the real abl-check (Unicorn, real ABL) has its own suite, tests/abl-check-tests.sh; stub it here
printf '#!/bin/sh\necho "abl-check $*" >> "$FAKE_LOG"\nexit ${FAKE_ABL_RC:-0}\n' > "$W/abl-check-stub"; chmod +x "$W/abl-check-stub"
export A9_ABL_CHECK="$W/abl-check-stub"
reset a; FAKE_ABL_RC=1 "$S/flash-recovery.sh" "$W/twrp-new.img" --rollback "$W/twrp-good.img" --write >/dev/null 2>&1; rc=$?
[ $rc = 1 ] && grep -q "abl-check $W/twrp-new.img recovery" "$FAKE_LOG" && ! grep -q "fastboot flash" "$FAKE_LOG" && pass "abl-check failure stops the flash before any write" || fail "abl-check failure ignored (rc $rc)"
reset a; "$S/flash-recovery.sh" "$W/twrp-nodtbo.img" --rollback "$W/twrp-good.img" >/dev/null 2>&1; [ $? = 1 ] && pass "refuses an image without recovery_dtbo" || fail "accepted an image without recovery_dtbo"
reset a; "$S/flash-recovery.sh" "$W/twrp-new.img" --rollback "$W/twrp-good.img" >/dev/null 2>&1 && ! grep -q "fastboot flash" "$FAKE_LOG" && pass "check mode writes nothing" || fail "check mode"
reset a; "$S/flash-recovery.sh" "$W/twrp-new.img" --rollback "$W/twrp-good.img" --write >/dev/null 2>&1; rc=$?
[ $rc = 0 ] && [ "$(state 'written')" = "['recovery_a', 'recovery_b']" ] && [ "$(state 'mode')" = android ] && pass "writes both recovery slots, verifies in TWRP, returns to Android" || fail "recovery write (rc $rc, written $(state 'written'), mode $(state 'mode'))"
reset a; ( # simulate the human: once the bad recovery sits unauthorized, enter the bootloader by hand
  for i in $(seq 1 60); do grep -q recovery_unauth "$FAKE_STATE" && { sleep 2; python3 -I -c "import json;p='$FAKE_STATE';d=json.load(open(p));d['mode']='bootloader';json.dump(d,open(p,'w'))"; break; }; sleep 1; done ) &
A9_HUMAN_WAIT=60 "$S/flash-recovery.sh" "$W/twrp-bad.img" --rollback "$W/twrp-good.img" --write >/dev/null 2>&1; rc=$?; wait
[ $rc = 1 ] && [ "$(state 'written')" = "['recovery_a', 'recovery_b', 'recovery_a', 'recovery_b']" ] && [ "$(state 'hash')" != "" ] && [ "$(state 'mode')" = android ] && pass "unauthorized recovery -> rolls back to the good image" || fail "rollback (rc $rc, written $(state 'written'), mode $(state 'mode'))"
grep -q "flash recovery" "$FAKE_LOG" && ! awk '/reboot fastboot/{f=1} /reboot bootloader/{f=0} f && /flash recovery/' "$FAKE_LOG" | grep -q . && pass "never flashes recovery from fastbootd" || fail "flashed recovery from fastbootd"

echo "== device registry (local/device.json)"
REG="$W/reg.json"; reset a
r=$(env -u A9_SERIAL A9_DEVICE_JSON="$REG" bash -c ". $S/a9-env.sh; echo \$A9_SERIAL")
[ "$r" = FAKE0001 ] && [ "$(python3 -I -c "import json;print(json.load(open('$REG'))['default'])")" = FAKE0001 ] && pass "first run records the only A9 as default" || fail "first-run registry ($r)"
FAKE_EXTRA_A9=FAKE0002 A9_DEVICE_JSON="$REG" "$S/a9-devices.sh" scan >/dev/null 2>&1
r=$(env -u A9_SERIAL A9_DEVICE_JSON="$REG" bash -c ". $S/a9-env.sh; echo \$A9_SERIAL")
[ "$r" = FAKE0001 ] && pass "a second A9 is recorded but the default stays" || fail "default changed ($r)"
r=$(env -u A9_SERIAL A9_DEVICE=a9-2 A9_DEVICE_JSON="$REG" bash -c ". $S/a9-env.sh; echo \$A9_SERIAL")
[ "$r" = FAKE0002 ] && pass "A9_DEVICE=a9-2 selects the second phone" || fail "A9_DEVICE by name ($r)"
r=$(env -u A9_SERIAL A9_DEVICE=nosuch A9_DEVICE_JSON="$REG" bash -c ". $S/a9-env.sh; echo \$A9_SERIAL" 2>/dev/null)
case "$r" in unknown-A9_DEVICE-*) pass "unknown A9_DEVICE targets nothing (no fallback to any phone)" ;; *) fail "unknown A9_DEVICE resolved to '$r'" ;; esac
A9_DEVICE_JSON="$REG" "$S/a9-devices.sh" use a9-2 >/dev/null && r=$(env -u A9_SERIAL A9_DEVICE_JSON="$REG" bash -c ". $S/a9-env.sh; echo \$A9_SERIAL")
[ "$r" = FAKE0002 ] && pass "a9-devices.sh use switches the default" || fail "use ($r)"

echo "== gsi-check"
"$S/gsi-check.sh" "$W/v6.img" --no-phone >/dev/null && pass "gsi-check: v6 fixture has no blocking failure" || fail "gsi-check v6"

echo "RESULT: $PASS passed, $FAILN failed"
[ "$FAILN" = 0 ]
