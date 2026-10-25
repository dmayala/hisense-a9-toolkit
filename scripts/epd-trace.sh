#!/usr/bin/env bash
#
# epd-trace.sh — does a screen change make it from SurfaceFlinger to the glass?
#
# Sends one scroll (input swipe) and counts, over the next few seconds:
#   SF      frame counter of the topmost app layer (dumpsys SurfaceFlinger)
#   engine  E Ink engine updates ("epd:: ... total frame num" in logcat)
#   panel   EPD refresh power-ons ("epd power on" in dmesg)
#
# Reading:  SF climbs, engine 0, panel 0  -> frames are not reaching the HWC's
#           E Ink engine (the 2026-10-11 freeze: HWC composition enabled).
#           SF climbs, engine >0, panel 0 on one run only -> the PMIC was still
#           up from the previous update; run it twice.
#
# Usage: ./epd-trace.sh [label] [seconds-to-wait]   (default 5)
# Scrolls whatever is on screen. Needs root (su) for logcat/dmesg.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

LABEL=${1:-trace}; WAIT=${2:-5}

top_layer() {
  "$ADB" shell dumpsys SurfaceFlinger | grep -oE "^  Layer \[[0-9]+\] [^ ]+|frame=[0-9]+" | paste - - \
    | grep -vE "Wallpaper|StatusBar|Taskbar|NavigationBar|Toast|Pointer|InputSink" | head -1 \
    | sed -E 's/^ *Layer \[[0-9]+\] //; s/\t/ /'
}

U0=$("$ADB" shell cat /proc/uptime | awk '{print $1}')
T0=$("$ADB" shell date +%H:%M:%S | tr -d '\r')
a=$(top_layer)
"$ADB" shell input swipe 400 1300 400 600 300
sleep "$WAIT"
b=$(top_layer)
e=$("$ADB" shell su -c "logcat -d" | awk -v t="$T0" '$2 >= t' | grep -c "epd::.*total frame num")
k=$("$ADB" shell su -c dmesg | grep "epd power on" | awk -F'[][]' -v u="$U0" '$2+0 > u' | wc -l)
hwc=$("$ADB" shell su -c "service call SurfaceFlinger 1010" | tr -d '\n' | grep -o "0x00000010: [0-9a-f]*" | sed 's/0x00000010: 0000000//')
echo "$LABEL: SF [${a:-?}] -> [${b:-?}] | engine updates: $e | panel power-ons: $k | mDebugDisableHWC=$hwc"
