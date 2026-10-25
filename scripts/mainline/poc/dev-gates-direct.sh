# root: open the E Ink gates directly (panel -> fp9929a -> a9_eink), bins 0-13. POC, no opener.
m=/sys/module; p=$m/a9_eink/parameters
echo 1 > $p/clear_fault
echo 0 > $p/bin_min; echo 13 > $p/bin_max; echo 1 > $p/init_flash
echo Y > $m/panel_hisense_hlte556n_eink/parameters/enable
echo Y > $m/fp9929a/parameters/allow_power_on
echo Y > $p/enable
setprop vendor.a9ml.eink.fault ""; setprop vendor.a9ml.eink.gate open
c=$(getprop vendor.a9ml.eink.card); echo "params $(cat $p/enable)$(cat $m/panel_hisense_hlte556n_eink/parameters/enable)$(cat $m/fp9929a/parameters/allow_power_on) bins $(cat $p/bin_min)-$(cat $p/bin_max) $(grep -oE 'rail_fault [0-9]+' /sys/class/drm/$c-*/eink/status | head -1)"
