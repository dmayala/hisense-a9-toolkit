#!/usr/bin/env bash
# Rehearse bt-pair.sh's inline phone-side command strings under the PHONE's own
# /system/bin/sh (mksh R59 Android) + toybox in qemu-aarch64 (review 55 harness rule b).
# Offline: a sandbox copy of an extracted phone sysroot with a fake sysfs/a9fw tree;
# qemu -L maps absolute paths into it. Binder/Java commands (dumpsys, cmd, am, input,
# settings, uiautomator) and getevent cannot run here: they are the documented waiver.
#   A9_PHONE_SYSROOT=<dir with system/bin/{sh,toybox,linker64}, system/lib64> rehearse-phone-sh.sh
# Needs qemu-aarch64 on PATH (e.g. nix-shell -p qemu-user --run ...). Exit 0 all PASS, 3 = skipped.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
S=$HERE/../../scripts/mainline/bluetooth/bt-pair.sh
SR=${A9_PHONE_SYSROOT:-}
[ -x "$SR/system/bin/sh" ] && [ -x "$SR/system/bin/toybox" ] || { echo "SKIP: A9_PHONE_SYSROOT not set or incomplete"; exit 3; }
command -v qemu-aarch64 >/dev/null || { echo "SKIP: no qemu-aarch64"; exit 3; }
T=$(mktemp -d); BG=""; trap 'kill $BG 2>/dev/null; rm -rf "$T"' EXIT
cp -a "$SR/." "$T/"
# qemu-user cannot exec a child aarch64 ELF (no binfmt here): every toybox applet the phone shell
# runs is a host wrapper that starts the phone's toybox under its own qemu with the same sysroot
Q=$(command -v qemu-aarch64); mkdir -p "$T/xbin"
for a in cat ls wc sha256sum pidof ps grep head readlink tr id sleep; do
  printf '#!%s\nexec %s -L %s -0 %s %s/system/bin/toybox "$@" 2>/dev/null\n' "$(command -v bash)" "$Q" "$T" "$a" "$T" > "$T/xbin/$a"
  chmod +x "$T/xbin/$a"
done
mkdir -p "$T/sys/class/thermal/thermal_zone0" "$T/sys/class/thermal/thermal_zone1" "$T/sys/class/thermal/cooling_device0" \
  "$T/sys/class/drm/card3-DSI-1/eink" "$T/sys/module/a9_eink/parameters" "$T/sys/class/remoteproc" \
  "$T/sys/devices/platform/soc@0/4ac0000.geniqup/4a8c000.serial/serial0/serial0-0/bluetooth/hci0" "$T/sys/class/bluetooth" \
  "$T/data/local/tmp/a9fw/qca" "$T/vendor/etc/bluetooth"
echo cpu4-thermal > "$T/sys/class/thermal/thermal_zone0/type"; echo 41000 > "$T/sys/class/thermal/thermal_zone0/temp"
echo battery > "$T/sys/class/thermal/thermal_zone1/type"; echo 31000 > "$T/sys/class/thermal/thermal_zone1/temp"
echo 0 > "$T/sys/class/thermal/cooling_device0/cur_state"
printf 'enable 0 waveform 0\nrail_fault 0 rail_faults 0\n' > "$T/sys/class/drm/card3-DSI-1/eink/status"
echo N > "$T/sys/module/a9_eink/parameters/enable"
ln -s ../../devices/platform/soc@0/4ac0000.geniqup/4a8c000.serial/serial0/serial0-0/bluetooth/hci0 "$T/sys/class/bluetooth/hci0"
for i in $(seq -w 1 22); do echo "fw$i" > "$T/data/local/tmp/a9fw/qca/f$i.bin"; done
( cd "$T/data/local/tmp/a9fw/qca" && sha256sum f*.bin ) > "$T/vendor/etc/bluetooth/firmware.sha256"
# a host process whose argv0 is the HAL path (comm is "sleep"): exercises the ps ARGS fallback
bash -c 'exec -a /vendor/bin/hw/android.hardware.bluetooth-service.default bash -c "while :; do sleep 1; done"' & BG=$!
sleep 0.3
PH() { qemu-aarch64 -L "$T" "$T/system/bin/sh" -c "PATH=$T/xbin; $1" 2>/dev/null; }
pass=0 fail=0
chk() { if printf '%s\n' "$2" | grep -qE "$3"; then pass=$((pass+1)); echo "ok   $1"; else fail=$((fail+1)); echo "FAIL $1: got [$(printf '%s' "$2" | head -c 200)]"; fi; }
cmd_of() { grep -o "R '$1[^']*'" "$S" | head -n1 | sed "s/^R '//; s/'\$//"; }
c=$(cmd_of 'for z in'); chk "thermal type/temp loop" "$(PH "$c")" '^battery 31000$'
chk "thermal glob cat" "$(PH "$(cmd_of 'cat /sys/class/thermal/thermal_zone')")" '^41000$'
chk "cooling glob" "$(PH "$(cmd_of 'cat /sys/class/thermal/cooling_device')")" '^0$'
chk "eink status glob" "$(PH "$(cmd_of 'cat /sys/class/drm/card')")" 'rail_fault 0'
chk "remoteproc glob, none present, quiet" "$(PH "$(cmd_of 'cat /sys/class/remoteproc')"; echo rc=$?)" '^rc='
# qemu-user does not remap chdir(): only the cd target is rewritten into the sandbox
c=$(cmd_of 'cd /data/local/tmp/a9fw/qca'); c=${c/cd \/data/cd $T\/data}
chk "sha256sum -c in qca (22 OK)" "$(PH "$c" | grep -c ': OK$')" '^22$'
echo tampered > "$T/data/local/tmp/a9fw/qca/f07.bin"
chk "sha256sum -c flags a changed file" "$(PH "$c" | grep -vc ': OK$')" '^[1-9]'

chk "ls -A | wc -l" "$(PH "$(cmd_of 'ls -A /data')" | tr -d ' ')" '^22$'
chk "ls -lid two inodes" "$(PH "$(cmd_of 'ls -lid')" | grep -cE '^ *[0-9]+ d')" '^2$'
chk "ls /sys/class/bluetooth" "$(PH "$(cmd_of 'ls /sys/class/bluetooth')")" '^hci0$'
chk "readlink -f hci0" "$(PH "$(cmd_of 'readlink -f')")" '/4a8c000.serial/'
p=$(PH "$(cmd_of 'for p in')")
chk "pids loop prints 4 name= lines" "$(printf '%s\n' "$p" | grep -c '=')" '^4$'
chk "pids: HAL found via the ps ARGS fallback" "$p" "^android.hardware.bluetooth-service.default=$BG$"
chk "pids: absent names print empty (caught by pids_ok)" "$p" '^audioserver=$'
echo "rehearse-phone-sh: $pass passed, $fail failed ($(PH 'echo $KSH_VERSION'))"
[ "$fail" = 0 ]
