#!/system/bin/sh
# Opt-in IPA/rmnet only. The existing owner supplies MPSS and RAM-copy EFS.
set -eu
/vendor/bin/a9ml-wifi-firmware.sh
for module in mdt_loader qcom_common qmi_helpers qcom_sysmon ipa rmnet; do
    [ -d "/sys/module/$module" ] || /system/bin/insmod "/vendor/lib/modules/$module.ko"
done
