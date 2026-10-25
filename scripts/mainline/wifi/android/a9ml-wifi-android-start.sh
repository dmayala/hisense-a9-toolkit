#!/system/bin/sh
# Lazy framework starts pass the same first-run gate as explicit init starts.
# Fixed roles only: no caller-controlled executable, options, paths or eval.
set -eu
[ "$#" -eq 1 ] || exit 1
case "$1" in hal|supplicant|hostapd) ;; *) exit 1 ;; esac
[ "$(/system/bin/getprop ro.a9ml.wifi.android)" = 1 ] || exit 1
[ "$(/system/bin/getprop vendor.a9ml.wifi.android.ready)" = 1 ] || exit 1
[ -d /sys/class/net/wlan0/phy80211 ] || exit 1
case "$1" in
    hal)
        exec /vendor/bin/hw/android.hardware.wifi-service ;;
    supplicant)
        exec /vendor/bin/hw/wpa_supplicant -Dnl80211 -O/data/vendor/wifi/wpa/sockets -m/vendor/etc/wifi/p2p_supplicant.conf -g@android:wpa_wlan0 ;;
    hostapd)
        [ "$(/system/bin/getprop vendor.a9ml.wifi.android.ap)" = 1 ] || exit 1
        exec /vendor/bin/hw/hostapd ;;
esac
