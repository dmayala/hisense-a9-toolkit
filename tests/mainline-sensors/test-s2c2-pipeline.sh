#!/usr/bin/env bash
# test-s2c2-pipeline.sh [attempt script] -- review 79n B1: drive THE EXACT sampler pipeline line of
# run-s2c2-attempt.sh (the line marked "# S2C2-PIPELINE", extracted verbatim and eval'ed) with a slow stub
# adb that prints one CRLF sampler line per second, like the phone. Require: the first line in samples.txt
# within 2 s, no CR in the file, and s2c2-judge.py stop = OK at 15 s (the attempt's first check).
# Negative control: the same line without `stdbuf -oL` must NOT deliver a line within 2 s (proves the test
# can see block buffering). Offline; no phone.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SCRIPT=${1:-$ROOT/scripts/mainline/sensors/run-s2c2-attempt.sh}
JUDGE=$ROOT/scripts/mainline/sensors/s2c2-judge.py
T=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$T"' EXIT
LINE=$(grep -E '# S2C2-PIPELINE$' "$SCRIPT")
[ "$(grep -cE '# S2C2-PIPELINE$' "$SCRIPT")" = 1 ] || { echo "FAIL: want exactly one S2C2-PIPELINE line"; exit 1; }
# stub adb: ignores its arguments, prints sampler-format lines with CRLF, one per second, from uptime 1000
cat > "$T/adb" <<'S'
#!/bin/sh
t=1000
while [ $t -lt 1040 ]; do
  printf 'S %s.00 short=640 open=0 wc=480 ww=0 state=up hal=2002 ss=1515\r\n' "$t"
  [ $((t % 5)) = 0 ] && printf 'D %s.10 mode=1 brt=104 lux=80.0 strat=AutomaticBrightnessStrategy\r\n' "$t"
  t=$((t + 1)); sleep 1
done
S
chmod +x "$T/adb"
printf '{"hal":2002,"ss":1515,"switch_uptime":999,"sampler_start":999,"short_before":300,"thermal":{},"eink":{"gate":"-","fault":"-"}}' > "$T/b.json"
fails=0
run() {   # $1 = label, $2 = the pipeline line; sets FIRST (s until the first line) and J15 (judge at 15 s)
  local d=$T/$1; mkdir -p "$d"
  ( ADB=$T/adb A9_TRANSPORT_ID=1 SOAK=40 SESSION=$d; eval "$2"; wait ) &
  local p=$! s=0; FIRST=none
  while [ $s -lt 30 ]; do
    [ -s "$d/samples.txt" ] && { FIRST=$(( s / 10 )).$(( s % 10 )); break; }
    sleep 0.1; s=$((s + 1))
  done
  sleep $(( 15 - s / 10 ))
  J15=$(python3 -I "$JUDGE" stop "$d/samples.txt" "$T/b.json" 1015)
  CR=$(grep -c $'\r' "$d/samples.txt")
  kill $p 2>/dev/null; pkill -f "$T/adb" 2>/dev/null; wait $p 2>/dev/null
}
run real "$LINE"
echo "pipeline: $LINE"
echo "first line after ${FIRST}s; judge at 15 s: $J15; CR in file: $CR"
if [ "$FIRST" != none ] && [ "${FIRST%%.*}" -lt 2 ] && [ "$J15" = OK ] && [ "$CR" = 0 ]; then echo "  ok   exact pipeline: a line within 2 s, judge OK at 15 s, no CR"
else echo "  FAIL exact pipeline"; fails=1; fi
run nostdbuf "${LINE/stdbuf -oL /}"
if [ "$FIRST" = none ]; then echo "  ok   negative control: without stdbuf no line within 3 s (block buffering is visible to this test)"
else echo "  FAIL negative control delivered a line after ${FIRST}s"; fails=1; fi
[ "$fails" = 0 ] && echo "S2C2-PIPELINE TEST PASS"
exit $fails
