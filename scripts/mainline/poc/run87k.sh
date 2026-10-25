#!/usr/bin/env bash
# E Ink + Wi-Fi POC on the 87k images: flash/boot, take over from the wrapper, open gates, connect, soak.
H=/home/user/A9-build/mainline/eink-wifi-poc; P=/home/user/A9-build/mainline/wifi-poc; cd $H
SER=$(adb devices | awk 'NR==2{print $1}')
log() { echo "$(date -u +%T) $*" | sed -E 's/([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}/<mac>/g; s/\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b/<ip>/g' | tee -a $H/run87k.log; }
root() { adb shell 'timeout 90 nc 127.0.0.1 2324'; }
setsid ./boot87k.sh --write --code "$1" > boot87k.log 2>&1 & bp=$!
log "wrapper pid $bp"
until grep -q -- '-- HOLD' boot87k.log; do kill -0 $bp 2>/dev/null || { log "wrapper exited before hold"; tail -5 boot87k.log; exit 1; }; sleep 5; done
kill -TERM -$bp 2>/dev/null; sleep 3
setsid bash $H/keeper.sh adb "$SER" >/dev/null 2>&1 & echo $! > keeper87k.pid
log "wrapper stopped; keeper $(cat keeper87k.pid)"
printf 'settings --user 0 put global device_provisioned 1\nsettings --user 0 put secure user_setup_complete 1\npm disable-user --user 0 org.lineageos.setupwizard >/dev/null 2>&1\npm disable-user --user 0 com.google.android.setupwizard >/dev/null 2>&1\nsvc power stayon true\nam start -W -a android.intent.action.MAIN -c android.intent.category.HOME >/dev/null 2>&1\necho SETUP_OK\n' | root | tail -1 | while read -r l; do log "$l"; done
log "eink open: $(root < dev-open.sh | tr '\n' ' ')"
log "wifi start: $($P/start.sh 2>&1 | tr '\n' ' ')"
adb shell 'cmd wifi start-scan' >/dev/null 2>&1; sleep 8
log "$(python3 -I $P/connect.py 2>&1)"
sleep 20
log "status: $($P/status.sh 2>&1 | tr '\n' ' ')"
log "SOAK2 start"
$H/soak2.sh
