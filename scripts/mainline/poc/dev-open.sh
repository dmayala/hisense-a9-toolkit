# root: E Ink gates via the vendor opener with the GPU-guard admission removed.
cp /vendor/bin/a9ml-eink.sh /data/local/tmp/eink-open.sh
sed -i 's/^\tGPUV=0; .*/\tGPUV=0/' /data/local/tmp/eink-open.sh
echo "gpuv_lines=$(grep -c '^.GPUV=0$' /data/local/tmp/eink-open.sh)"
p=/sys/module/a9_eink/parameters
echo 100 > $p/pg_settle_ms; echo "settle=$(cat $p/pg_settle_ms)"
echo "fw=$(ls /data/local/tmp/a9fw/hisense 2>&1 | tr '\n' ' ')"
setprop vendor.a9ml.eink.bins 0-13
setprop vendor.a9ml.eink.hv_hold default
setprop vendor.a9ml.eink.gate open
sh /data/local/tmp/eink-open.sh open 2>&1 | tail -n 5
echo "open_rc=$? gate=$(getprop vendor.a9ml.eink.gate) fault=$(getprop vendor.a9ml.eink.fault)"
m=/sys/module; echo "params a9_eink=$(cat $m/a9_eink/parameters/enable) panel=$(cat $m/panel_hisense_hlte556n_eink/parameters/enable) fp=$(cat $m/fp9929a/parameters/allow_power_on)"
