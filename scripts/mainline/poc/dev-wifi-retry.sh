# root: stop ADSP sensors (QRTR topology must be MPSS-only), clear the failed attempt, restart the session.
setprop ctl.stop vendor.sensors-a9; setprop ctl.stop vendor.a9-sensors; sleep 2
for p in /sys/class/remoteproc/remoteproc*; do [ "$(cat $p/name)" = adsp ] && echo stop > $p/state; done
sleep 2
for p in /sys/class/remoteproc/remoteproc*; do echo "rproc $(cat $p/name) $(cat $p/state)"; done
umount /mnt/a9-wifi/trial 2>/dev/null; umount /mnt/a9-wifi/tools 2>/dev/null
rm -rf /mnt/a9-wifi/trial /mnt/a9-wifi/tools /mnt/a9-wifi/session
setprop vendor.a9ml.wifi.session.stop 0
setprop vendor.a9ml.wifi.session.state awaiting-s2-audit
setprop vendor.a9ml.wifi.s2.start 0; sleep 1; setprop vendor.a9ml.wifi.s2.start 1
echo RETRY_REQUESTED
