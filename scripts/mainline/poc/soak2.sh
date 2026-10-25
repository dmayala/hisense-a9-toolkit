#!/usr/bin/env bash
# 30-min joint soak: Wi-Fi HTTP 204 + E Ink redraw progress every 30 s. No identifiers logged.
L=/home/user/A9-build/mainline/eink-wifi-poc/soak2.log; end=$(( $(date +%s) + ${SOAK_S:-1800} )); ok=0; bad=0; last=0
while [ "$(date +%s)" -lt "$end" ]; do
  w=$(timeout 25 adb shell 'h=$(printf "GET /generate_204 HTTP/1.1\r\nHost: connectivitycheck.gstatic.com\r\nConnection: close\r\n\r\n" | timeout 10 nc connectivitycheck.gstatic.com 80 | head -1 | tr -d "\r"); s=$(cmd wifi status | grep -oE "Supplicant state: [A-Z]+" | head -1); echo "$h|$s"' 2>&1 | tr -d '\r')
  timeout 10 adb shell 'cmd statusbar expand-notifications; sleep 3; cmd statusbar collapse' >/dev/null 2>&1; sleep 4
  e=$(printf 'c=$(getprop vendor.a9ml.eink.card); s=$(cat /sys/class/drm/$c-*/eink/status); echo "$s" | head -1 | grep -oE "enable [0-9]|hv_on [0-9]|stall_fault [0-9]" | tr "\\n" " "; echo "$s" | sed -n 2p | grep -oE "frames [0-9]+"; echo "$s" | grep -oE "rail_fault [0-9]+" | head -1; echo "bin=$(echo "$s" | sed -n 3p | grep -oE "bin [0-9]+")"\n' | timeout 20 adb shell 'timeout 15 nc 127.0.0.1 2324' 2>&1 | tr '\n\r' '  ')
  fr=$(echo "$e" | grep -oE "frames [0-9]+" | head -1 | cut -d' ' -f2); fr=${fr:-0}
  wok=0; case "$w" in *"204 No Content"*COMPLETED*) wok=1;; esac
  eok=0; [ "$fr" -gt "$last" ] && echo "$e" | grep -q "rail_fault 0" && eok=1; last=$fr
  if [ $wok = 1 ] && [ $eok = 1 ]; then ok=$((ok+1)); else bad=$((bad+1)); fi
  echo "$(date -u +%T) ok=$ok bad=$bad wifi=$wok eink=$eok | $w | $e" >> $L
  sleep 20
done
echo "SOAK2 DONE ok=$ok bad=$bad" | tee -a $L
