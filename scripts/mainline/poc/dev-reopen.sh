# root: clear a latched rail fault and reopen the E Ink gates (POC, no harness).
m=/sys/module
dmesg | grep -iE "fp9929a|rail|power-good|pg " | tail -4 | cut -c1-180
setprop vendor.a9ml.eink.gate closed
for g in a9_eink/parameters/enable fp9929a/parameters/allow_power_on panel_hisense_hlte556n_eink/parameters/enable; do echo N > $m/$g 2>/dev/null || echo 0 > $m/$g; done
echo 1 > $m/a9_eink/parameters/clear_fault
setprop vendor.a9ml.eink.fault ""
sleep 1
setprop vendor.a9ml.eink.gate open
sh /data/local/tmp/eink-open.sh open 2>&1 | tail -n 2
c=$(getprop vendor.a9ml.eink.card); echo "after: $(grep -oE 'rail_fault [0-9]+' /sys/class/drm/$c-*/eink/status | head -1) gate=$(getprop vendor.a9ml.eink.gate) params $(cat $m/a9_eink/parameters/enable)$(cat $m/panel_hisense_hlte556n_eink/parameters/enable)$(cat $m/fp9929a/parameters/allow_power_on)"
