#!/usr/bin/env bash
#
# flash-recovery.sh — put a recovery (TWRP) image on BOTH recovery slots and
# prove it, with the traps of this device turned into checks.
#
#   CHECK (default, no phone writes):
#     image .sha256 matches; boot magic ANDROID!, header v2, page size 4096
#     (CLAUDE.md quick reference); recovery_dtbo present and non-empty (fact 3:
#     TWRP builds 1-2 had none and did not boot); fits recovery_a/_b (96 MiB);
#     scripts/abl-check.sh (the bootloader's own checks, emulated) if present;
#     phone reachable in Android or TWRP.
#   --write:
#     USB lock + run dir; adb reboot bootloader; REFUSE unless is-userspace = no
#     (rule 3: fastbootd "flashes" recovery and writes nothing); flash
#     recovery_a and recovery_b (rule 4: both, active slot untouched);
#     fastboot reboot recovery; must come up as an AUTHORIZED adb recovery (stock
#     recovery comes up unauthorized); hash both slots from the TWRP shell
#     against the image; then twrp.sh exit (clears the BCB) back to Android.
#     If the new recovery does not come up or a hash differs, it re-flashes the
#     rollback image (default: the known-good TWRP) the same way.
#
# Usage: flash-recovery.sh <recovery.img> [--write] [--rollback <img>]
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

IMG=""; WRITE=0; ROLLBACK="${A9_DATA}/recovery/twrp-HLTE556N-31434216263.img"
while [ $# -gt 0 ]; do
  case "$1" in
    --write) WRITE=1 ;; --rollback) ROLLBACK="${2:-}"; shift ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;; *) IMG="$1" ;;
  esac; shift
done
[ -f "$IMG" ] || { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok   $*"; }
PART_SIZE=100663296   # recovery_a / recovery_b, 0x6000000

# check_image <img>: header facts this bootloader needs (boot image header v2)
check_image() {
  python3 -I - "$1" "$PART_SIZE" <<'PY'
import struct, sys
p, part = sys.argv[1], int(sys.argv[2])
d = open(p, 'rb').read(4096)
size = __import__('os').path.getsize(p)
errs = []
if d[:8] != b'ANDROID!': errs.append('no ANDROID! magic')
ver = struct.unpack_from('<I', d, 40)[0]
page = struct.unpack_from('<I', d, 36)[0]
if ver != 2: errs.append(f'header version {ver}, need 2')
if page != 4096: errs.append(f'page size {page}, need 4096')
if ver >= 1:
    rd_size = struct.unpack_from('<I', d, 1632)[0]        # recovery_dtbo_size (v1+)
    if rd_size == 0: errs.append('recovery_dtbo_size is 0 (TWRP builds without it do not boot: CLAUDE.md fact 3)')
else:
    rd_size = 0
if size > part: errs.append(f'image {size} B larger than the partition {part} B')
print(f"header v{ver}, page {page}, recovery_dtbo {rd_size} B, image {size} B")
for e in errs: print('ERROR:', e)
sys.exit(1 if errs else 0)
PY
}

echo "== checks"
[ -f "$IMG.sha256" ] || fail "$IMG.sha256 missing"
[ "$(sha256sum "$IMG" | awk '{print $1}')" = "$(awk '{print $1}' "$IMG.sha256")" ] || fail "sha256 does not match $IMG.sha256"
ok "sha256 matches .sha256"
check_image "$IMG" | sed 's/^/       /'; [ "${PIPESTATUS[0]}" = 0 ] || fail "image header checks failed"
ok "header v2 / page 4096 / recovery_dtbo present / fits"
ABL_CHECK=${A9_ABL_CHECK:-$HERE/abl-check.sh}   # tests point this at a stub
if [ -x "$ABL_CHECK" ]; then
  "$ABL_CHECK" "$IMG" recovery --quiet 2>&1 | sed 's/^/       /'; [ "${PIPESTATUS[0]}" = 0 ] || fail "abl-check (emulated bootloader checks) failed"
  ok "abl-check passed"
else
  echo "  --   abl-check.sh not present, emulated bootloader checks skipped"
fi
if [ -n "$ROLLBACK" ]; then
  [ -f "$ROLLBACK" ] && check_image "$ROLLBACK" >/dev/null || fail "rollback image $ROLLBACK missing or invalid (--rollback <img>)"
  ok "rollback image: $(basename "$ROLLBACK")"
fi
S=$(a9_adb_state)
case "$S" in device|recovery) ok "phone reachable (adb $S)" ;; *) fail "phone must be in Android or TWRP (adb state '${S:-none}')" ;; esac

[ "$WRITE" = 1 ] || { echo "Check only. Re-run with --write to flash recovery_a and recovery_b."; exit 0; }

a9_usb_lock
a9_run_dir flash-recovery "$IMG"
exec > >(tee -a "$A9_RUN/log.txt") 2>&1
echo "== run dir $A9_RUN"
echo "recovery $(basename "$IMG") $(awk '{print $1}' "$IMG.sha256")" >> "$A9_RUN/meta.txt"
"$HERE/a9-health.sh" --json > "$A9_RUN/health-before.json"

flash_both() {  # flash_both <img>: from bootloader fastboot only
  [ "$(a9_fbvar is-userspace)" = no ] || { echo "REFUSED: not in bootloader fastboot (is-userspace != no): recovery writes from fastbootd are silently dropped (rule 3)"; return 1; }
  "$FB" flash recovery_a "$1" && "$FB" flash recovery_b "$1"
}
to_bootloader() {
  # From Android or an authorized recovery adb can reboot to the bootloader. A
  # broken or stock-like (unauthorized) recovery cannot be driven from here: wait
  # for the phone to fall back by itself, or ask the human for the one manual
  # step (one of the two "call a human" cases in CLAUDE.md: invisible on USB).
  local end=$(( $(date +%s) + ${A9_HUMAN_WAIT:-900} )) asked=0
  while [ "$(date +%s)" -lt "$end" ]; do
    case "$(a9_adb_state)" in device|recovery) "$ADB" reboot bootloader; a9_wait_fastboot no 120 && return 0 ;; esac
    if a9_wait_fastboot no 5; then return 0; fi
    if a9_wait_fastboot yes 1; then "$FB" reboot bootloader; a9_wait_fastboot no 120 && return 0; fi
    if [ "$asked" = 0 ]; then
      echo ">> HUMAN: the phone is not reachable over adb/fastboot (recovery unauthorized or not booting)."
      echo ">> Hold Power until it is off, then Power + Volume Down to enter the bootloader. Waiting..."
      asked=1
    fi
    sleep 5
  done
  return 1
}
# boot_and_verify <img>: reboot to recovery, require an authorized adb recovery,
# then hash both slots from the recovery shell
boot_and_verify() {
  local img="$1" sz want
  sz=$(stat -c %s "$img"); want=$(sha256sum "$img" | awk '{print $1}')
  "$FB" reboot recovery || return 1
  local end=$(( $(date +%s) + 180 )) st=""
  while [ "$(date +%s)" -lt "$end" ]; do
    st=$(a9_adb_state); [ "$st" = recovery ] && break
    "$ADB" devices 2>/dev/null | grep -q unauthorized && { echo "recovery came up UNAUTHORIZED: that is stock recovery, not the new image"; return 1; }
    sleep 3
  done
  [ "$st" = recovery ] || { echo "no adb recovery within 180 s"; return 1; }
  a9_wait_stable recovery 60 || { echo "recovery adb not stable"; return 1; }
  echo "-- recovery up (TWRP $("$ADB" shell getprop ro.twrp.version </dev/null | tr -d '\r'))"
  local p got rc=0
  for p in recovery_a recovery_b; do
    got=$("$ADB" shell "head -c $sz /dev/block/bootdevice/by-name/$p | sha256sum" </dev/null | tr -d '\r' | awk '{print $1}')
    if [ "$got" = "$want" ]; then echo "  MATCH $p"; else echo "  MISMATCH $p (${got:0:16}…)"; rc=1; fi
  done
  return $rc
}

echo "-- to bootloader fastboot"
to_bootloader || fail "bootloader fastboot did not appear"
if flash_both "$IMG" && boot_and_verify "$IMG"; then
  echo "-- new recovery verified on both slots; back to Android"
  "$HERE/twrp.sh" exit && a9_wait_boot "${A9_BOOT_TIMEOUT:-600}" || echo "WARNING: Android did not come back; follow the a9-recover skill"
  echo "result: 0" >> "$A9_RUN/meta.txt"
  echo "RESULT: recovery flashed to both slots and verified"
  exit 0
fi

echo "!! new recovery failed: rolling back to $(basename "$ROLLBACK")"
to_bootloader || fail "bootloader fastboot did not appear for the rollback: follow the a9-recover skill"
if flash_both "$ROLLBACK" && boot_and_verify "$ROLLBACK"; then
  "$HERE/twrp.sh" exit && a9_wait_boot "${A9_BOOT_TIMEOUT:-600}"
  echo "result: 1 (rolled back)" >> "$A9_RUN/meta.txt"
  echo "RESULT: new recovery FAILED; rollback recovery flashed and verified"
  exit 1
fi
echo "result: 2 (rollback failed)" >> "$A9_RUN/meta.txt"
fail "rollback also failed: follow the a9-recover skill (the system slots are untouched, Android still boots)"
