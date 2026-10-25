#!/system/bin/sh
# Release USB HAL handoff and generation-deduplicated compatibility uevents.
i=0
while [ -z "$(ls /sys/class/udc 2>/dev/null)" ] && [ "$i" -lt 60 ]; do
    sleep 1
    i=$((i+1))
done
UDC=$(ls /sys/class/udc 2>/dev/null | head -n1)
[ -n "$UDC" ] || exit 1
setprop sys.usb.controller "$UDC"
setprop sys.usb.configfs 2
G=/config/usb_gadget/g1
android_state_old=
android_generation_old=
while :; do
    ready=$(getprop sys.usb.ffs.ready)
    bound=$(cat "$G/UDC" 2>/dev/null)
    udc_state=$(cat "/sys/class/udc/$UDC/state" 2>/dev/null)
    case "$udc_state" in
        configured) android_state=CONFIGURED ;;
        "not attached") android_state=DISCONNECTED ;;
        attached|powered|default|addressed) android_state=CONNECTED ;;
        *) android_state= ;;
    esac
    # HAL switches can leave the property/bind/FFS values unchanged between polls.
    composition=$(cat "$G/configs/b.1/strings/0x409/configuration" 2>/dev/null)
    android_generation="$(getprop sys.usb.config)|$bound|$ready|$composition"
    android_event=$android_state
    if [ "$android_state" = CONFIGURED ] && [ "$android_generation" != "$android_generation_old" ]; then
        android_event=CONFIGURED_REFRESH
    fi
    if [ -n "$android_state" ] && { [ "$android_state" != "$android_state_old" ] || [ "$android_event" = CONFIGURED_REFRESH ]; }; then
        if printf '%s\n' "$android_event" > /sys/class/android_usb/android0/state; then
            android_state_old=$android_state
            android_generation_old=$android_generation
        fi
    fi
    sleep 2
done
