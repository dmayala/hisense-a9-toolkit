#!/usr/bin/env bash
# S1 coordinator check of an integrated Wi-Fi session boot (docs: runs/wifi-android-plan.md).
# READ-ONLY toward the phone: getprop, cmd wifi status/scan list (counts only), dumpsys wifi (filtered),
# dmesg/kmsg lines (filtered), remoteproc state. It enables Wi-Fi scanning ONLY with --enable (probe
# requests on the air; never associates, never reads or writes a credential).
#
#   session-verify.sh <out-dir> [--enable] [--wait <s>]
#
# Env: ADB (default adb), ADB_SERIAL (optional -s). Output files never hold a MAC address or an SSID:
# every phone line is filtered and scan results are reduced to counts and channel histograms.
# Exit 0 = serving + (with --enable) Wi-Fi on and >=1 scan result; 1 = a check failed; 2 = usage/no device.
set -u
umask 077
out=${1:?out dir}; shift
enable=0; wait_s=600
while [ $# -gt 0 ]; do case $1 in --enable) enable=1;; --wait) wait_s=${2:?}; shift;; *) echo "usage: $0 <out> [--enable] [--wait s]" >&2; exit 2;; esac; shift; done
ADB=${ADB:-adb}
a=("$ADB"); [ -z "${ADB_SERIAL:-}" ] || a+=(-s "$ADB_SERIAL")
mkdir -p "$out"
# strip MACs, and anything shaped like an SSID line, from everything that reaches a file
filter() { sed -E -e 's/([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}/xx:xx:xx:xx:xx:xx/g' -e '/[Ss][Ss][Ii][Dd]/d'; }
sh_() { timeout 30 "${a[@]}" shell "$@" 2>/dev/null | tr -d '\r'; }
# root payload through the loopback root shell (payload on stdin: no quoting through adb)
root_() { printf '%s\n' "$1" | timeout 40 "${a[@]}" shell 'timeout 30 nc 127.0.0.1 2324' 2>/dev/null | tr -d '\r'; }
fail=0
say() { echo "$*" | tee -a "$out/verdict.txt"; }
: > "$out/verdict.txt"

timeout "$wait_s" "${a[@]}" wait-for-device 2>/dev/null || { say "FAIL no device within ${wait_s}s"; exit 2; }
end=$((SECONDS + wait_s))
until [ "$(sh_ getprop sys.boot_completed)" = 1 ]; do [ "$SECONDS" -lt "$end" ] || { say "FAIL boot not completed"; exit 1; }; sleep 5; done
say "boot_completed after $SECONDS s (kernel $(sh_ uname -r))"

state=
end=$((SECONDS + 300))
while [ "$SECONDS" -lt "$end" ]; do
    state=$(sh_ getprop vendor.a9ml.wifi.session.state)
    case $state in serving|failed*|ended*) break;; esac
    sleep 3
done
say "session state: ${state:-none}"
[ "$state" = serving ] || fail=1

sh_ 'for p in /sys/class/remoteproc/remoteproc*; do echo "$(cat $p/name 2>/dev/null) $(cat $p/state 2>/dev/null) recovery=$(cat $p/recovery 2>/dev/null)"; done' | filter > "$out/remoteproc.txt"
grep -q 'running recovery=disabled' "$out/remoteproc.txt" || { say "FAIL no remoteproc running with recovery disabled"; fail=1; }
sh_ 'dmesg | grep -E "a9wifi|A9_WIFI|ath10k|cfg80211|regulatory" | tail -n 120' | filter > "$out/dmesg-wifi.txt"
grep -Eq 'fatal error|remoteproc.*crash|a9wifi-fallback' "$out/dmesg-wifi.txt" && { say "FAIL fault line in dmesg (see dmesg-wifi.txt)"; fail=1; }
root_ 'for f in /mnt/a9-wifi/session/run.out; do tail -n 80 $f; done; cat /mnt/a9-wifi/trial/first-fault 2>/dev/null' | filter > "$out/session-run.txt"
root_ '/mnt/a9-wifi/tools/iw reg get; /mnt/a9-wifi/tools/iw dev' | filter > "$out/iw-reg-dev.txt"
grep -q '^country' "$out/iw-reg-dev.txt" && say "regulatory: $(grep -m1 '^country' "$out/iw-reg-dev.txt")" || { say "FAIL regulatory state unreadable"; fail=1; }
sh_ 'getprop init.svc.vendor.a9ml_wifi_hal; getprop init.svc.wpa_supplicant; getprop init.svc.hostapd; getprop init.svc.wificond' | tr '\n' ' ' > "$out/services.txt"
say "services (hal supplicant hostapd wificond): $(cat "$out/services.txt")"
grep -q 'running running' "$out/services.txt" || { say "FAIL HAL/supplicant not running"; fail=1; }

if [ "$enable" = 1 ]; then
    sh_ 'cmd wifi set-wifi-enabled enabled' > /dev/null
    for _ in $(seq 20); do sh_ 'cmd wifi status' | grep -q 'Wifi is enabled' && break; sleep 2; done
    sh_ 'cmd wifi status' | filter | head -n 20 > "$out/wifi-status.txt"
    grep -q 'Wifi is enabled' "$out/wifi-status.txt" && say "Wi-Fi enabled in the framework" || { say "FAIL framework Wi-Fi did not come on"; fail=1; }
    sh_ 'cmd wifi start-scan' > /dev/null
    sleep 12
    # counts only: row count and a frequency histogram; the rows themselves never reach a file
    sh_ 'cmd wifi list-scan-results' > "$out/.scan.raw"
    rows=$(grep -cE '^[[:space:]]*([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}' "$out/.scan.raw")
    awk '{for(i=1;i<=NF;i++) if ($i ~ /^2[0-9][0-9][0-9]$|^5[0-9][0-9][0-9]$/) print $i}' "$out/.scan.raw" | sort | uniq -c > "$out/scan-freq-histogram.txt"
    rm -f "$out/.scan.raw"
    say "scan results: $rows rows; frequencies in scan-freq-histogram.txt"
    [ "$rows" -ge 1 ] || { say "FAIL no scan results"; fail=1; }
    sh_ 'dumpsys wifi' | filter | grep -E 'Wi-Fi is|mWifiState|supportedFeatures|isStaApConcurrency|Country|country' | head -n 20 > "$out/dumpsys-wifi.txt"
fi
[ "$fail" = 0 ] && say "S1 VERIFY PASS" || say "S1 VERIFY FAIL"
exit "$fail"
