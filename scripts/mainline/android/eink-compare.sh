#!/usr/bin/env bash
# eink-compare.sh [outdir] -- does the glass content match Android's frame?
# Pulls a9_eink's last-update grey (/sys/kernel/debug/a9_eink/grey, debugfs
# mounted if needed) through the test vendor's loopback root shell
# (127.0.0.1:2324), takes `screencap -p`, and compares them with
# eink-compare.py (the driver's own luma + rotation). Read-only on the phone
# (two temp files in /data/local/tmp, removed after). Writes into outdir
# (default: the newest runs/*-mainline-android/android/compare-<UTC>/).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/../../.." && pwd)"
. "$REPO/scripts/a9-env.sh"
OUT=${1:-}
if [ -z "$OUT" ]; then
  last=$(ls -d "$REPO"/runs/*-mainline-android 2>/dev/null | tail -n1)
  OUT=${last:-$REPO/runs/eink-compare}/android/compare-$(date -u +%H%M%S)
fi
mkdir -p "$OUT"
R() { timeout 60 "$ADB" shell "echo '$1' | nc -w 50 127.0.0.1 2324" </dev/null 2>&1; }
case "$(timeout 10 "$ADB" shell uname -r </dev/null | tr -d '\r')" in 7.*) ;; *) echo "not the mainline test image (adb uname -r)"; exit 2 ;; esac
[ "$(R 'id -u' | tr -d '\r' | tail -n1)" = 0 ] || { echo "no loopback root shell on 127.0.0.1:2324"; exit 2; }
R 'mountpoint -q /sys/kernel/debug || mount -t debugfs debugfs /sys/kernel/debug; cp /sys/kernel/debug/a9_eink/grey /data/local/tmp/a9-grey.bin && chmod 0644 /data/local/tmp/a9-grey.bin; cat /sys/class/drm/card*-*/eink/status 2>/dev/null' > "$OUT/eink-status.txt"
timeout 30 "$ADB" shell screencap -p /data/local/tmp/a9-cap.png </dev/null
timeout 30 "$ADB" pull /data/local/tmp/a9-grey.bin "$OUT/driver-grey.bin" >/dev/null || { echo "pull grey failed"; exit 1; }
timeout 30 "$ADB" pull /data/local/tmp/a9-cap.png "$OUT/screencap.png" >/dev/null || { echo "pull screencap failed"; exit 1; }
R 'rm -f /data/local/tmp/a9-grey.bin /data/local/tmp/a9-cap.png' >/dev/null
head -n1 "$OUT/eink-status.txt"; sed -n 3p "$OUT/eink-status.txt" | cut -c1-160
python3 -I "$HERE/eink-compare.py" "$OUT/screencap.png" "$OUT/driver-grey.bin" "$OUT"; rc=$?
echo "outdir: $OUT"; exit $rc
