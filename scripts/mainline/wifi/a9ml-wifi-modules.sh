#!/system/bin/sh
# Default-off offline draft. ONLY reads modules; no remoteproc state writes.
set -eu
[ "$(getprop ro.a9ml.wifi)" = 1 ] || exit 1
[ "$(getprop vendor.a9ml.wifi.modules.request)" = 1 ] || exit 1
/vendor/bin/a9ml-wifi-firmware.sh
setprop vendor.a9ml.wifi.modules loading
. /vendor/bin/a9ml-module-load.sh
while IFS= read -r module; do
    case "$module" in
        ''|'#'*) continue ;;
        *[!a-zA-Z0-9_.-]*|.*|*..*) setprop vendor.a9ml.wifi.modules failed; exit 1 ;;
        *.ko) ;;
        *) setprop vendor.a9ml.wifi.modules failed; exit 1 ;;
    esac
    a9ml_module_load "$module" || {
        setprop vendor.a9ml.wifi.modules failed; exit 1;
    }
done < /vendor/lib/modules/modules.wifi
setprop vendor.a9ml.wifi.modules ready
