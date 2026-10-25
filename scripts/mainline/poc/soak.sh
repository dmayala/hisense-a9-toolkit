#!/usr/bin/env bash
# 30-min soak: HTTP 204 + validation every 30 s. No identifiers logged.
end=$(( $(date +%s) + 1800 )); ok=0; bad=0
while [ "$(date +%s)" -lt "$end" ]; do
  r=$(timeout 25 adb shell 'h=$(printf "GET /generate_204 HTTP/1.1\r\nHost: connectivitycheck.gstatic.com\r\nConnection: close\r\n\r\n" | timeout 10 nc connectivitycheck.gstatic.com 80 | head -1 | tr -d "\r"); v=$(dumpsys connectivity | grep -c "WIFI.*VALIDATED\|VALIDATED.*WIFI"); s=$(cmd wifi status | grep -oE "Supplicant state: [A-Z]+" | head -1); echo "$h|$s|up=$(cut -d" " -f1 /proc/uptime)"' 2>&1 | tr -d '\r')
  case "$r" in *"204 No Content"*COMPLETED*) ok=$((ok+1));; *) bad=$((bad+1)); echo "$(date -u +%T) FAIL $r";; esac
  echo "$(date -u +%T) ok=$ok bad=$bad $r" >> /home/user/A9-build/mainline/wifi-poc/soak.log
  sleep 30
done
echo "SOAK DONE ok=$ok bad=$bad"
