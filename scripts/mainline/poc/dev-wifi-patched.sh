# root: rerun the vendor Wi-Fi session with the private-DT-MAC checks removed (integrated boot DT has no MAC).
for p in /sys/class/remoteproc/remoteproc*; do echo "rproc $(cat $p/name) $(cat $p/state)"; done
umount /mnt/a9-wifi/trial/stores 2>/dev/null; umount /mnt/a9-wifi/trial 2>/dev/null; umount /mnt/a9-wifi/tools 2>/dev/null
rm -rf /mnt/a9-wifi/trial /mnt/a9-wifi/tools /mnt/a9-wifi/session
setprop vendor.a9ml.wifi.session.stop 0
setprop vendor.a9ml.wifi.android.ready 0
cp /vendor/bin/a9ml-wifi-session.sh /data/local/tmp/ws.sh
sed -i '/^sums() /i sed -i -e "s/|| die .private DT MAC missing./|| :/" -e "s/|| die .wlan0 MAC differs from provisioned private DT MAC./|| :/" "$TOOLS/coordinator-target.sh"; echo "a9wifi-session dtmac-patch $(grep -c "private DT MAC missing" $TOOLS/coordinator-target.sh)" > /dev/kmsg' /data/local/tmp/ws.sh
grep -c dtmac-patch /data/local/tmp/ws.sh
setsid nohup /system/bin/sh /data/local/tmp/ws.sh start > /data/local/tmp/ws.out 2>&1 < /dev/null &
sleep 1; echo LAUNCHED
