#!/usr/bin/env bash
# Root via loopback root shell (stdin, not echo). Setup bypass, start Wi-Fi session, cap power, enable framework Wi-Fi.
ADB=$(python3 -c "import json;print(json.load(open('/home/user/A9-build/mainline/wifi-android/s2-87k/release.json'))['tools']['adb'])")
root() { $ADB shell 'timeout 60 nc 127.0.0.1 2324'; }
root <<'R'
settings --user 0 put global device_provisioned 1
settings --user 0 put secure user_setup_complete 1
pm disable-user --user 0 org.lineageos.setupwizard >/dev/null 2>&1
pm disable-user --user 0 com.google.android.setupwizard >/dev/null 2>&1
echo "session_before=$(getprop vendor.a9ml.wifi.session.state)"
setprop vendor.a9ml.wifi.s2.start 1
echo START_REQUESTED
R
for i in $(seq 1 100); do s=$($ADB shell getprop vendor.a9ml.wifi.session.state | tr -d '\r'); [ "$s" = serving ] && break; case "$s" in failed*|ended*) echo "session=$s"; exit 1;; esac; sleep 3; done
echo "session=$s"
root <<'R'
timeout 3 /mnt/a9-wifi/tools/iw dev wlan0 set txpower limit 2000 >/dev/null 2>&1; echo "cap_rc=$?"
R
$ADB shell 'cmd wifi set-wifi-enabled enabled; sleep 3; cmd wifi status | head -1'
