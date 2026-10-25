#!/usr/bin/env bash
# Generic-image test: original integrated boot (no DT MAC) on boot_b, runtime factory MAC from persist.
H=/home/user/A9-build/mainline/eink-wifi-poc; P=/home/user/A9-build/mainline/wifi-poc; W=/home/user/A9/scripts/mainline/wifi
R4=/home/user/A9-build/mainline/integrated-latency-v3-runtime-probe-r4-20261019
SER=$(adb devices | awk 'NR==2{print $1}')
log() { echo "$(date -u +%T) $*" | sed -E 's/([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}/<mac>/g; s/\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b/<ip>/g' | tee -a $H/generic.log; }
root() { adb shell 'timeout 120 nc 127.0.0.1 2324'; }
[ "$(adb shell getprop ro.boot.slot_suffix | tr -d '\r')" = _b ] || { log "not on slot B"; exit 1; }
adb reboot bootloader; until fastboot devices | grep -q .; do sleep 2; done
[ "$(fastboot getvar current-slot 2>&1 | awk '/current-slot/{print $2}')" = b ] || { log "bootloader slot not b"; exit 1; }
fastboot flash boot_b $R4/images/boot.img 2>&1 | tail -1 | while read -r l; do log "flash: $l"; done
fastboot reboot >/dev/null 2>&1
until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; do sleep 5; done
log "booted slot $(adb shell getprop ro.boot.slot_suffix | tr -d '\r'); dt_mac=$(adb shell 'test -e /sys/firmware/devicetree/base/soc@0/wifi@c800000/local-mac-address && echo present || echo absent' | tr -d '\r')"
sleep 15
ADB=adb bash /home/user/A9-build/mainline/demo-integrated-20261019/repo/scripts/mainline/android/eink-fw-install.sh /home/user/A9-build/mainline/firmware/hisense/a9-waveform.bin /home/user/A9-build/mainline/firmware/hisense/a9-ctrl-template.bin > $H/generic-fw.log 2>&1; log "fw install rc=$?"
printf 'settings --user 0 put global device_provisioned 1\nsettings --user 0 put secure user_setup_complete 1\npm disable-user --user 0 org.lineageos.setupwizard >/dev/null 2>&1\npm disable-user --user 0 com.google.android.setupwizard >/dev/null 2>&1\nsvc power stayon true\nam start -W -a android.intent.action.MAIN -c android.intent.category.HOME >/dev/null 2>&1\necho SETUP_OK\n' | root | tail -1 | while read -r l; do log "$l"; done
log "eink: $(root < $H/dev-open.sh | tr '\n' ' ')"
log "eink direct: $(root < $H/dev-gates-direct.sh | tr '\n' ' ')"
log "adsp: $(printf 'setprop ctl.stop vendor.sensors-a9; setprop ctl.stop vendor.a9-sensors; sleep 2\nfor p in /sys/class/remoteproc/remoteproc*; do [ "$(cat $p/name)" = adsp ] && echo stop > $p/state; done; sleep 2\nfor p in /sys/class/remoteproc/remoteproc*; do echo "$(cat $p/name)=$(cat $p/state)"; done\n' | root | tr '\n' ' ')"
# patched starter + controller from the repo, loaded at runtime
adb push $W/session/a9ml-wifi-session.sh /data/local/tmp/ws.sh >/dev/null; adb push $W/coordinator-target.sh /data/local/tmp/ct.sh >/dev/null
log "session: $(printf '%s\n' 'sed -i "/^sums() /i cat /data/local/tmp/ct.sh > \"\$TOOLS/coordinator-target.sh\"; chmod 0700 \"\$TOOLS/coordinator-target.sh\"" /data/local/tmp/ws.sh' 'grep -c "data/local/tmp/ct.sh" /data/local/tmp/ws.sh' 'setsid nohup /system/bin/sh /data/local/tmp/ws.sh start > /data/local/tmp/ws.out 2>&1 < /dev/null &' 'sleep 1; echo LAUNCHED' | root | tr '\n' ' ')"
for i in $(seq 1 100); do s=$(adb shell getprop vendor.a9ml.wifi.session.state | tr -d '\r'); case "$s" in serving|failed*|ended*) break;; esac; sleep 3; done
log "session=$s; $(printf 'dmesg | grep a9wifi-session | grep -iE "factory|STOP|FAIL" | tail -4 | cut -c1-160\n' | root | tr '\n' ' ')"
[ "$s" = serving ] || exit 1
printf 'timeout 3 /mnt/a9-wifi/tools/iw dev wlan0 set txpower limit 2000; echo cap=$?\n' | root >/dev/null
adb shell 'cmd wifi set-wifi-enabled enabled' >/dev/null; sleep 6; adb shell 'cmd wifi start-scan' >/dev/null; sleep 8
log "$(python3 -I $P/connect.py 2>&1)"; sleep 20
log "status: $($P/status.sh 2>&1 | tr '\n' ' ')"
log "mac check: $(printf 'f=$(cat /mnt/a9-wifi/session/factory-mac); a=$(tr -d ":\\n" < /sys/class/net/wlan0/address); [ "$f" = "$a" ] && echo wlan0=factory || echo wlan0!=factory\n' | root | tr '\n' ' ')"
: > $H/soak2.log; SOAK_S=${SOAK_S:-600} $H/soak2.sh
