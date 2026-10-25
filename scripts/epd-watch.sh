#!/usr/bin/env bash
#
# epd-watch.sh — timeline of E Ink panel refreshes, with a panel snapshot and a
# SurfaceFlinger screencap after each one.
#
# The kernel EPD driver logs every refresh cycle: "epd power on" (PMIC up, pixels
# can change), "fp9929a active end", "fp9929a enter sleep end" (cycle finished),
# "epd 1v8 power off" (display off). After each finished cycle this saves:
#   NNN-<uptime>-panel.png   what the glass shows      (epd-snap.sh)
#   NNN-<uptime>-sf.png      what SurfaceFlinger composed (screencap)
# A difference between the two is a panel/SurfaceFlinger divergence. After
# "1v8 power off" the screencap is black but the glass keeps its last image.
#
#   events.log   the EPD kernel lines with host timestamps
#
# Usage: ./epd-watch.sh OUTDIR [seconds]       (default 60)
#
# Read-only apart from epd-snap.sh's one-time discovery, which runs BEFORE the
# watch starts. It follows dmesg only. Never read the fp9929a sysfs nodes:
# power_en logs a line per read and floods dmesg; temperature/thermal power the
# PMIC up even with the panel rail off (I2C -107 errors).
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

OUT=${1:?usage: epd-watch.sh OUTDIR [seconds]}; DUR=${2:-60}
mkdir -p "$OUT"

# Make sure the live buffer is known before watching, so discovery's own
# refreshes are not part of the timeline.
"$HERE/epd-snap.sh" "$OUT/000-start-panel.png" >/dev/null || exit 1

t0=$("$ADB" shell cat /proc/uptime | awk '{print $1}')
echo "start: kernel uptime $t0, host $(date +%T)" | tee "$OUT/events.log"
n=0
timeout "$DUR" "$ADB" shell su -c "'dmesg -w'" | tr -d '\r' \
  | grep --line-buffered -iE 'epd|tc358767|fp9929' |
while IFS= read -r line; do
  ts=$(sed -E 's/^\[ *([0-9.]+)\].*/\1/' <<<"$line")
  awk -v a="$ts" -v b="$t0" 'BEGIN { exit !(a > b) }' || continue   # skip the replayed buffer
  case "$line" in *pmic_active*) continue ;; esac
  echo "$(date +%T.%3N) $line" | tee -a "$OUT/events.log"
  case "$line" in *"enter sleep end"*)
    n=$((n + 1)); base=$(printf '%s/%03d-%s' "$OUT" "$n" "$ts")
    "$HERE/epd-snap.sh" "$base-panel.png" >/dev/null 2>&1 || echo "    epd-snap failed" | tee -a "$OUT/events.log"
    "$ADB" exec-out screencap -p > "$base-sf.png"
    echo "    -> $(basename "$base")-panel.png / -sf.png" | tee -a "$OUT/events.log" ;;
  esac
done
echo "refresh cycles completed: $(grep -c 'enter sleep end' "$OUT/events.log")" | tee -a "$OUT/events.log"
