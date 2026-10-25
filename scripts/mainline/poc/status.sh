#!/usr/bin/env bash
# Sanitized Wi-Fi status: no SSID/BSSID/MAC/IP printed.
ADB=$(python3 -c "import json;print(json.load(open('/home/user/A9-build/mainline/wifi-android/s2-87k/release.json'))['tools']['adb'])")
$ADB shell '
echo "slot=$(getprop ro.boot.slot_suffix) boot=$(getprop sys.boot_completed) session=$(getprop vendor.a9ml.wifi.session.state)"
st=$(cmd wifi status 2>/dev/null)
echo "$st" | head -1
echo "$st" | grep -q "Wifi is connected" && echo connected=yes || echo connected=no
echo "$st" | grep -o "Supplicant state: [A-Z_]*\|Frequency: [0-9]*MHz\|Link speed: [0-9]*Mbps\|RSSI: -[0-9]*" | sort -u | tr "\n" " "; echo
dumpsys connectivity 2>/dev/null | grep -m1 -o "NetworkAgentInfo{.*WIFI[^}]*" | grep -o "VALIDATED\|INTERNET\|PARTIAL_CONNECTIVITY" | sort -u | tr "\n" " "; echo
ping -c 3 -W 3 8.8.8.8 >/dev/null 2>&1 && echo ping=ok || echo ping=fail
'
