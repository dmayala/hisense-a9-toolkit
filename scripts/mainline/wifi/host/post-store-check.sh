#!/usr/bin/env bash
# Post-hoc PRE/POST real-store comparison for a trial that ended in a hard reset
# (run 12: the phone reset into the bootloader and the in-trial POST read never
# happened). READ-ONLY: it only hashes the four store block devices after the phone
# is back in Android. Needs the PRE digest file `pre.cmp` ("<store> <sha256>" lines).
#
#   post-store-check.sh <pre.cmp> <out-dir>
#
# Env: ADB (default adb), ADB_SERIAL (optional -s), A9_ROOT_CHANNEL (the shell command
# fragment that runs a root payload on the phone; default the trial driver's
# `nc 127.0.0.1 2324` channel), A9_BOOT_WAIT (seconds, default 300).
set -u
pre=${1:?pre.cmp}; out=${2:?out dir}
ADB=${ADB:-adb}; wait=${A9_BOOT_WAIT:-300}
chan=${A9_ROOT_CHANNEL:-nc 127.0.0.1 2324}
a=("$ADB"); [ -z "${ADB_SERIAL:-}" ] || a+=(-s "$ADB_SERIAL")
umask 077; mkdir -p "$out"
[ -s "$pre" ] || { echo "post-store-check: PRE digests missing or empty: $pre" >&2; exit 2; }
timeout "$wait" "${a[@]}" wait-for-device || { echo "post-store-check: phone not back within ${wait}s" >&2; exit 3; }
end=$((SECONDS + wait))
until [ "$("${a[@]}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; do
  [ "$SECONDS" -lt "$end" ] || { echo "post-store-check: Android did not finish booting" >&2; exit 3; }
  sleep 5
done
payload='for f in modemst1 modemst2 fsg fsc; do echo POST $f $(sha256sum < /dev/block/by-name/$f); done'
timeout 120 "${a[@]}" shell "echo '$payload' | $chan" > "$out/post-store.txt" 2> "$out/post-store.err" || true
grep '^POST ' "$out/post-store.txt" | awk '{print $2, $3}' | sort > "$out/post.cmp"
sort "$pre" > "$out/pre.sorted"
if [ "$(wc -l < "$out/post.cmp")" = 4 ] && cmp -s "$out/pre.sorted" "$out/post.cmp"; then
  echo "STORES SAME (post-boot read-only check)" | tee "$out/verdict.txt"; exit 0
fi
echo "STORES DIFFER or POST incomplete (post-boot read-only check)" | tee "$out/verdict.txt"; exit 1
