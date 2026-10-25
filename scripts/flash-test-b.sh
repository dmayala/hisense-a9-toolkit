#!/usr/bin/env bash
#
# flash-test-b.sh — put a test build on slot B (the TEST slot) and prove it.
#
# Automates docs/slots-and-promotion.md "Test a build on B", with the checks
# that page leaves to the operator. Slot A (main) is never written.
#
#   CHECK (default, no phone writes):
#     image .sha256 matches; image is raw ext4; a9-compat.sh says COMPATIBLE
#     with the running /data (platform key, SDK, patch) — a build that needs a
#     /data wipe is REFUSED, the phone is in daily use; phone is in Android on
#     slot a (main) with boot completed; the image fits super (system_b + free);
#     optional boot image: Android boot magic, header v2, fits boot_b.
#   --write:
#     USB lock + run dir (runs/<UTC>-flash-test-b/), then:
#     adb reboot bootloader → set_active b → [flash boot_b] → reboot fastboot
#     (is-userspace yes, current-slot b) → flash system (= system_b) → reboot
#     → wait for Android → a9-health → readback system_b (and boot_b) on the
#     phone → E Ink checks (HWC 1010 = 1, accessibility service).
#     If B does not come up, it switches back to slot a by itself.
#   --back: after a successful test, return to slot a (main).
#
# Usage:
#   flash-test-b.sh <system.img> [--boot <boot.img>] [--write] [--back] [--no-readback]
#   flash-test-b.sh --boot <boot.img> --write     # TRY-BOOT: boot image only, system_b untouched
#                                                 # (the stand-in for `fastboot boot`, which this
#                                                 #  bootloader does not have: CLAUDE.md rule 2)
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

IMG=""; BOOT=""; WRITE=0; BACK=0; READBACK=1
while [ $# -gt 0 ]; do
  case "$1" in
    --boot) BOOT="${2:-}"; shift ;;
    --write) WRITE=1 ;; --back) BACK=1 ;; --no-readback) READBACK=0 ;;
    -h|--help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) IMG="$1" ;;
  esac
  shift
done
[ -n "$IMG$BOOT" ] || { sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok   $*"; }

echo "== checks"
H=$("$HERE/a9-health.sh" --json); HRC=$?
case "$H" in *'"slot":"_a"'*) ;; *) fail "phone must be in Android on slot a (main) first: $H" ;; esac
[ "$HRC" = 0 ] || fail "Android on slot a has not finished booting (a9-health exit $HRC)"
ok "phone in Android, slot a, boot completed"

if [ -n "$IMG" ]; then
  [ -f "$IMG" ] || fail "no such image: $IMG"
  [ -f "$IMG.sha256" ] || fail "$IMG.sha256 missing (every image in roms/ carries one)"
  echo "  ..   sha256 of $(basename "$IMG")"
  [ "$(sha256sum "$IMG" | awk '{print $1}')" = "$(awk '{print $1}' "$IMG.sha256")" ] || fail "sha256 does not match $IMG.sha256"
  ok "sha256 matches .sha256"
  [ "$(od -A n -t x1 -j 1080 -N 2 "$IMG" | tr -d ' ')" = 53ef ] || fail "not a raw ext4 image (sparse or other)"
  ok "raw ext4"
  if [ "${A9_BTRY_FORMAT:-0}" = 1 ] && [ "$WRITE" = 0 ]; then
    # Disposable formatted B does not reuse A's /data. Stock A may have no su;
    # validate the actual write in recovery instead of requiring root on A.
    echo "  ..   FORMAT=1: shared-data compatibility and rooted Android super-layout checks skipped; recovery write/readback required"
  else
    "$HERE/a9-compat.sh" "$IMG" | sed 's/^/       /' ; [ "${PIPESTATUS[0]}" = 0 ] || fail "a9-compat refused this image (see above). No /data wipes: the phone is in daily use."
    ok "compatible with the running /data"
    # super space: everything allocated except system_b, plus the new image, must fit
    SZ=$(stat -c %s "$IMG")
    # sizes summed on the host: the phone's mksh arithmetic is 32-bit and overflows on 3.5 GB
    L=$(a9_su 'echo super $(blockdev --getsize64 /dev/block/by-name/super); for p in /dev/block/mapper/*_[ab]; do echo ${p##*/} $(blockdev --getsize64 $p); done')
    SUPER=$(printf '%s\n' "$L" | awk '$1=="super"{print $2}')
    USED=$(printf '%s\n' "$L" | awk '$1!="super"{s+=$2} END{printf "%d", s}')
    SYSB=$(printf '%s\n' "$L" | awk '$1=="system_b"{print $2}')
    [ -n "$SUPER" ] && [ -n "$SYSB" ] || fail "could not read super layout (su?)"
    FREE=$(( SUPER - 1048576 - USED )); ROOM=$(( SYSB + FREE ))
    [ "$SZ" -le $(( ROOM - 4194304 )) ] || fail "image $SZ B does not fit: system_b $SYSB + free $FREE (4 MiB margin). See slots-and-promotion.md 'super space'."
    ok "fits super (image $SZ, room $ROOM)"
  fi
fi
if [ -n "$BOOT" ]; then
  [ -f "$BOOT" ] || fail "no such boot image: $BOOT"
  [ "$(head -c 8 "$BOOT")" = "ANDROID!" ] || fail "boot image has no ANDROID! magic"
  [ "$(od -A n -t u4 -j 40 -N 4 "$BOOT" | tr -d ' ')" = 2 ] || fail "boot header is not version 2 (this device uses v2)"
  [ "$(stat -c %s "$BOOT")" -le 100663296 ] || fail "boot image larger than boot_b (96 MiB)"
  ok "boot image: magic, header v2, fits"
fi

if [ "$WRITE" != 1 ]; then
  echo "Check only. Re-run with --write to flash slot B${BOOT:+ (boot_b)}${IMG:+ (system_b)}."
  exit 0
fi

# ------------------------------------------------------------------ write
a9_usb_lock
a9_run_dir flash-test-b "$IMG" "${BOOT:+--boot $BOOT}"
exec > >(tee -a "$A9_RUN/log.txt") 2>&1
echo "== run dir $A9_RUN"
echo "$H" > "$A9_RUN/health-before.json"
[ -n "$IMG" ] && echo "system $(basename "$IMG") $(awk '{print $1}' "$IMG.sha256")" >> "$A9_RUN/meta.txt"
[ -n "$BOOT" ] && echo "boot $(basename "$BOOT") $(sha256sum "$BOOT" | awk '{print $1}')" >> "$A9_RUN/meta.txt"

back_to_a() {
  echo "-- returning to slot a"
  case "$(a9_adb_state)" in device|recovery) "$ADB" reboot bootloader ;; esac
  if ! a9_wait_fastboot no 90; then
    if a9_wait_fastboot yes 5; then "$FB" reboot bootloader; a9_wait_fastboot no 90; fi
  fi
  a9_wait_fastboot no 5 || { echo "!! cannot reach bootloader fastboot: follow the a9-recover skill"; return 1; }
  "$FB" set_active a && [ "$(a9_fbvar current-slot)" = a ] || { echo "!! set_active a failed"; return 1; }
  "$FB" reboot
  a9_wait_boot "${A9_BOOT_TIMEOUT:-600}" && echo "-- back on slot a" || { echo "!! slot a did not boot: a9-recover skill"; return 1; }
}

echo "-- adb reboot bootloader"
"$ADB" reboot bootloader
a9_wait_fastboot no 120 || fail "bootloader fastboot did not appear (phone may still be on slot a: run a9-health)"
"$FB" set_active b || fail "set_active b failed"
[ "$(a9_fbvar current-slot)" = b ] || fail "current-slot is not b after set_active b"
echo "-- current-slot b"
if [ -n "$BOOT" ]; then
  echo "-- flash boot_b (bootloader fastboot)"
  "$FB" flash boot_b "$BOOT" || { echo "flash boot_b failed"; back_to_a; exit 1; }
fi
if [ -n "$IMG" ]; then
  echo "-- reboot to fastbootd"
  "$FB" reboot fastboot
  a9_wait_fastboot yes 180 || { echo "fastbootd did not appear"; back_to_a; exit 1; }
  [ "$(a9_fbvar current-slot)" = b ] || { echo "fastbootd current-slot is not b"; back_to_a; exit 1; }
  echo "-- flash system (= system_b), $(stat -c %s "$IMG") bytes"
  "$FB" flash system "$IMG" || { echo "flash system failed"; back_to_a; exit 1; }
fi
echo "-- reboot into slot b"
"$FB" reboot
if ! a9_wait_boot "${A9_BOOT_TIMEOUT:-600}"; then
  echo "FAIL: slot b did not finish booting in 10 min"
  "$HERE/a9-health.sh" --json > "$A9_RUN/health-fail.json" 2>&1
  back_to_a; exit 1
fi
sleep "${A9_SETTLE:-20}"   # let the boot guard (HWC) and first-boot checks run
"$HERE/a9-health.sh" --json > "$A9_RUN/health-after.json"
grep -q '"slot":"_b"' "$A9_RUN/health-after.json" || { echo "FAIL: booted, but not on slot b (fallback?)"; cat "$A9_RUN/health-after.json"; exit 1; }
echo "-- Android up on slot b"

RES=0
if [ "$READBACK" = 1 ]; then
  [ -n "$IMG" ]  && { "$HERE/a9-readback.sh" system_b "$IMG"  || RES=1; }
  [ -n "$BOOT" ] && { "$HERE/a9-readback.sh" boot_b "$BOOT" || RES=1; }
fi
HWC=$("$ADB" shell su -c "service call SurfaceFlinger 1010" </dev/null | tr -d '\n\r' | grep -o "0x00000010: [0-9a-f]*" | sed 's/0x00000010: 0000000//')
echo "  mDebugDisableHWC = ${HWC:-?} (must be 1 on this panel)"; [ "$HWC" = 1 ] || RES=1
ACC=$("$ADB" shell settings get secure enabled_accessibility_services </dev/null | tr -d '\r')
case "$ACC" in *A9AccessibilityService*) echo "  A9 accessibility service enabled" ;; *) echo "  A9 accessibility service NOT enabled ($ACC)"; RES=1 ;; esac
echo "  next: scripts/epd-trace.sh and scripts/epd-snap.sh to see frames reach the glass"

[ "$RES" = 0 ] && echo "RESULT: slot b PASSED the automatic checks" || echo "RESULT: slot b FAILED a check (see above)"
echo "result: $RES" >> "$A9_RUN/meta.txt"
if [ "$BACK" = 1 ]; then back_to_a || RES=1; fi
exit "$RES"
