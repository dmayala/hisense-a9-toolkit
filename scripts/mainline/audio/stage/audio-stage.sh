#!/usr/bin/env bash
# audio-stage.sh -- HOST side of one bounded, attended ES9218 bring-up stage
# (runs/audio-stage-plan.md). For the coordinator, while the phone runs the
# audio stage image on slot B under `android-boot.sh --write ... --hold <s>`.
# It never flashes, reboots or switches slots; the restore stays with
# android-boot.sh.
#
#   audio-stage.sh pre|A|P1|P2|C|cleanup --bundle <dir> [--run-dir <dir>]
#     --bundle   the stage-A module bundle (build-stage-a.sh output
#                $T/android/audio-bundle: modules.order, SHA256SUMS,
#                kernel.release, kernel-image.sha256, the 7 .ko files)
#     --run-dir  where the log goes (default ~/A9/runs/<date>-audio-<stage>/,
#                gitignored)
#
# Checks before anything runs on the phone: bundle hashes; adb sees exactly one
# device; it runs the mainline vendor (ro.a9ml.vendor=1) whose
# /vendor/lib/modules/kernel-image.sha256 equals the bundle's (= the boot
# kernel the bundle was built with); a root shell (adbd root or the loopback
# shell on 127.0.0.1:2324, as android-boot.sh). Then pushes the phone script
# (+ bundle for pre/A/P1/P2) to /data/local/tmp/a9audio (RAM /data) and runs the stage
# with a 150 s limit (the phone enforces its own 90 s deadline + containment).
# Exit code = the phone verdict (0 PASS, 1 FAIL, 2 STOP, 3 NOGO,
# 6 CONTAINMENT-UNVERIFIED), 4 = host precondition, 5 = no verdict line.
# On 5, or Ctrl-C/TERM while a stage runs, the host runs the phone's
# `cleanup` (which verifies removal + 65/66/68 low) once, bounded. A transport
# loss proves nothing about the phone: if cleanup cannot PASS, hold Power
# 10-15 s (PON reason 2 -> bootloader fastboot), then android-boot.sh --restore.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
ADB=${ADB:-adb}
STAGE=${1:-}; shift || true
BUNDLE="" RUN=""
while [ $# -gt 0 ]; do
  case $1 in
    --bundle) BUNDLE=${2:-}; shift ;;
    --run-dir) RUN=${2:-}; shift ;;
    *) echo "unknown argument $1" >&2; exit 64 ;;
  esac; shift
done
case $STAGE in pre|A|P1|P2|C|cleanup) ;; *) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 64 ;; esac
die() { echo "audio-stage: $*" >&2; exit 4; }
[ -n "$BUNDLE" ] && [ -d "$BUNDLE" ] || die "--bundle <dir> required"
case "$(readlink -f "$BUNDLE")" in *a9-assets*|"$REPO"/*) die "bundle must live in a private build dir, not a9-assets or the repo" ;; esac
for f in modules.order SHA256SUMS kernel.release kernel-image.sha256; do [ -s "$BUNDLE/$f" ] || die "bundle lacks $f"; done
(cd "$BUNDLE" && sha256sum -c --quiet SHA256SUMS) || die "bundle SHA256SUMS mismatch"
grep -qx 'snd-soc-es9218-a9.ko' "$BUNDLE/modules.order" || die "bundle has no codec module"
[ "$(tail -n 1 "$BUNDLE/modules.order")" = snd-soc-es9218-a9.ko ] || die "codec module must load last"
for k in $(cat "$BUNDLE/modules.order"); do
  case $k in snd*.ko) ;; *) die "bundle module $k is not part of the stage-A closure (sound core + codec only)" ;; esac
done
RUN=${RUN:-$REPO/runs/$(date +%Y%m%d-%H%M%S)-audio-$STAGE}
mkdir -p "$RUN"; LOG=$RUN/stage-$STAGE.log
echo "audio-stage $STAGE: bundle $(cut -c1-12 "$BUNDLE/kernel-image.sha256") log $LOG"

n=$(timeout 10 "$ADB" devices 2>/dev/null | awk 'NR>1 && $2=="device"' | wc -l)
[ "$n" = 1 ] || die "need exactly one adb device in 'device' state (have $n)"
sh_() { timeout "${2:-20}" "$ADB" shell "$1" </dev/null 2>&1 | tr -d '\r'; }
[ "$(sh_ 'getprop ro.a9ml.vendor' | tail -n 1)" = 1 ] || die "the phone is not on the mainline vendor (ro.a9ml.vendor != 1): refusing (slot A / daily ROM?)"
k=$(sh_ 'cat /vendor/lib/modules/kernel-image.sha256' | tail -n 1 | tr -d ' ')
[ "$k" = "$(tr -d ' \n' < "$BUNDLE/kernel-image.sha256")" ] || die "vendor kernel-image.sha256 ${k:0:12} != bundle's: not the audio stage image pair"
ROOT=""
[ "$(sh_ 'id -u' | tail -n 1)" = 0 ] && ROOT=adbd
[ -z "$ROOT" ] && [ "$(sh_ 'echo id -u | nc -w 3 127.0.0.1 2324' 10 | tail -n 1)" = 0 ] && ROOT=nc
[ -n "$ROOT" ] || die "no root shell (adbd uid != 0 and no loopback root shell on 127.0.0.1:2324)"
echo "  device ok: mainline vendor, kernel ${k:0:12}, root via $ROOT"

D=/data/local/tmp/a9audio
sh_ "mkdir -p $D" >/dev/null
timeout 30 "$ADB" push "$HERE/a9-audio-stage.sh" "$D/a9-audio-stage.sh" >/dev/null || die "push script"
if [ "$STAGE" = A ] || [ "$STAGE" = pre ] || [ "$STAGE" = P1 ] || [ "$STAGE" = P2 ]; then
  for f in modules.order SHA256SUMS kernel.release kernel-image.sha256 $(cat "$BUNDLE/modules.order"); do
    timeout 30 "$ADB" push "$BUNDLE/$f" "$D/$f" >/dev/null || die "push $f"
  done
fi
want=$(sha256sum "$HERE/a9-audio-stage.sh" | cut -c1-64)
[ "$(sh_ "sha256sum $D/a9-audio-stage.sh" | tail -n 1 | cut -c1-64)" = "$want" ] || die "phone script hash mismatch after push"
phone() {   # phone <stage> <limit s> -> output on stdout
  local c="sh $D/a9-audio-stage.sh $1 $D"
  if [ "$ROOT" = adbd ]; then timeout "$2" "$ADB" shell "$c" </dev/null 2>&1
  else timeout "$2" "$ADB" shell "echo '$c' | nc -w $(($2 - 10)) 127.0.0.1 2324" </dev/null 2>&1; fi
  echo "# shell exit $?"
}
FALLBACK="hold Power 10-15 s (PON reason 2 -> bootloader fastboot), then scripts/mainline/android/android-boot.sh --restore"
emergency_cleanup() {
  echo "== containment: running the phone's cleanup (bounded 60 s)" | tee -a "$LOG"
  phone cleanup 60 | tr -d '\r' | tee -a "$LOG"
  if grep -q '^AUDIO-STAGE-cleanup PASS' "$LOG"; then echo "containment verified (codec removed, 65/66/68 low)"; return 0; fi
  echo "CONTAINMENT NOT VERIFIED: $FALLBACK" | tee -a "$LOG" >&2; return 1
}
on_host_signal() { trap - INT TERM; echo "interrupted" >&2; [ "$STAGE" = pre ] || emergency_cleanup; exit 5; }
[ "$STAGE" = cleanup ] || trap on_host_signal INT TERM
{
  echo "# audio-stage $STAGE $(date -Is) root=$ROOT bundle=$(cut -c1-16 "$BUNDLE/kernel-image.sha256") script=${want:0:16}"
  phone "$STAGE" 150
} | tr -d '\r' | tee "$LOG"
trap - INT TERM
v=$(grep -E "^AUDIO-STAGE-$STAGE (PASS|FAIL|STOP|NOGO|CONTAINMENT-UNVERIFIED)" "$LOG" | tail -n 1)
echo "verdict: ${v:-none}"
case $v in
  *" PASS "*) exit 0 ;; *" FAIL "*) exit 1 ;; *" STOP "*) exit 2 ;; *" NOGO "*) exit 3 ;;
  *" CONTAINMENT-UNVERIFIED "*) echo "$FALLBACK" >&2; exit 6 ;;
  *) echo "no verdict line (transport loss proves nothing about the phone)" >&2
     [ "$STAGE" = pre ] || [ "$STAGE" = cleanup ] || emergency_cleanup; exit 5 ;;
esac
