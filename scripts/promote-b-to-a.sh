#!/usr/bin/env bash
#
# promote-b-to-a.sh — make the build that passed on slot B the main build on A.
#
# Automates docs/slots-and-promotion.md "Promote B → A". Slot A's firmware,
# vendor, dtbo and vbmeta are byte copies of B's, so promotion writes two
# partitions at most: system_a (fastbootd) and, only if asked, boot_a
# (bootloader fastboot).
#
#   CHECK (default, no phone writes):
#     phone in Android on slot b, booted; the image's .sha256 matches;
#     a9-readback proves system_b on the phone IS this image (so we promote
#     exactly what was tested); a9-compat says COMPATIBLE.
#   --write:
#     USB lock + run dir, adb reboot bootloader → set_active a → [flash boot_a]
#     → reboot fastboot (current-slot a) → flash system (= system_a) → reboot
#     → wait → a9-health → readback system_a → golden check: if slot A no longer
#     matches data/golden.tsv it says GOLDEN LAGS MAIN (take a new EDL dump).
#     If A does not boot, the bootloader falls back to B after its retries
#     (B is known good at that point).
#
# Usage: promote-b-to-a.sh <system.img> [--boot <boot.img>] [--write]
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

IMG=""; BOOT=""; WRITE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --boot) BOOT="${2:-}"; shift ;; --write) WRITE=1 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;; *) IMG="$1" ;;
  esac; shift
done
[ -f "$IMG" ] || { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok   $*"; }

echo "== checks"
H=$("$HERE/a9-health.sh" --json); HRC=$?
case "$H" in *'"slot":"_b"'*) ;; *) fail "phone must be in Android on slot b (the tested build): $H" ;; esac
[ "$HRC" = 0 ] || fail "slot b has not finished booting"
ok "phone in Android on slot b"
[ -f "$IMG.sha256" ] && [ "$(sha256sum "$IMG" | awk '{print $1}')" = "$(awk '{print $1}' "$IMG.sha256")" ] || fail "image sha256 does not match its .sha256"
ok "image sha256"
"$HERE/a9-readback.sh" system_b "$IMG" || fail "system_b is not this image: promote only what was tested on B"
ok "system_b on the phone is this image"
if [ -n "$BOOT" ]; then
  "$HERE/a9-readback.sh" boot_b "$BOOT" || fail "boot_b is not this boot image"
  ok "boot_b on the phone is this boot image"
fi
"$HERE/a9-compat.sh" "$IMG" | sed 's/^/       /'; [ "${PIPESTATUS[0]}" = 0 ] || fail "a9-compat refused"
ok "compatible with /data"

if [ "$WRITE" != 1 ]; then echo "Check only. Re-run with --write to promote to slot A."; exit 0; fi

a9_usb_lock
a9_run_dir promote-b-to-a "$IMG" "${BOOT:+--boot $BOOT}"
exec > >(tee -a "$A9_RUN/log.txt") 2>&1
echo "== run dir $A9_RUN"; echo "$H" > "$A9_RUN/health-before.json"
echo "system $(basename "$IMG") $(awk '{print $1}' "$IMG.sha256")" >> "$A9_RUN/meta.txt"

"$ADB" reboot bootloader
a9_wait_fastboot no 120 || fail "bootloader fastboot did not appear"
"$FB" set_active a && [ "$(a9_fbvar current-slot)" = a ] || fail "set_active a failed"
if [ -n "$BOOT" ]; then "$FB" flash boot_a "$BOOT" || fail "flash boot_a failed (phone is in bootloader, current-slot a)"; fi
"$FB" reboot fastboot
a9_wait_fastboot yes 180 || fail "fastbootd did not appear"
[ "$(a9_fbvar current-slot)" = a ] || fail "fastbootd current-slot is not a"
"$FB" flash system "$IMG" || fail "flash system failed (phone in fastbootd on slot a; re-run or follow a9-recover)"
"$FB" reboot
a9_wait_boot "${A9_BOOT_TIMEOUT:-600}" || fail "slot a did not finish booting in 10 min: the bootloader should fall back to b; follow a9-recover"
sleep "${A9_SETTLE:-20}"
"$HERE/a9-health.sh" --json > "$A9_RUN/health-after.json"
grep -q '"slot":"_a"' "$A9_RUN/health-after.json" || fail "booted, but not on slot a (fallback to b?)"
RES=0
"$HERE/a9-readback.sh" system_a "$IMG" || RES=1
[ -n "$BOOT" ] && { "$HERE/a9-readback.sh" boot_a "$BOOT" || RES=1; }
"$HERE/a9-readback.sh" --golden || echo "(golden lags main: expected right after a promotion)"
[ "$RES" = 0 ] && echo "RESULT: promoted to slot A and verified" || echo "RESULT: promoted, but a readback FAILED"
echo "result: $RES" >> "$A9_RUN/meta.txt"
exit "$RES"
