#!/system/bin/sh
# Load only the existing IPA/rmnet prerequisites in the opt-in data phase.
# The owner already prepared firmware; do not run its preparation again.
set -eu
failed() {
    trap - 0
    setprop vendor.a9ml.cellular.stock.ready 0 || :
    setprop vendor.a9ml.cellular.stock.state failed || :
    exit 1
}
trap failed 0
setprop vendor.a9ml.cellular.stock.phase modules
[ "$(getprop vendor.a9ml.cellular)" = 1 ] || failed
[ "$(getprop ro.a9ml.wifi.session)" = 1 ] || failed
[ "$(getprop ro.boot.slot_suffix)" = _b ] || failed
# qcom_common imports the GLINK SSR registration helpers.
. /vendor/bin/a9ml-module-load.sh
for module in mdt_loader qcom_glink_smem qcom_common qmi_helpers qcom_sysmon ipa rmnet; do
    a9ml_module_load "$module.ko"
done
trap - 0
