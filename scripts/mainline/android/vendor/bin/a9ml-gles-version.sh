#!/system/bin/sh
# Read-only GLES capability property for the driver apps actually selected.
# Saved A9 dumps: Mesa FD610 26.2.5 = ES 3.2; ANGLE/SwiftShader = ES 3.1.
# init supplies the 3.1 default if this helper fails before setting the prop.
gles=196609
if [ "$(getprop ro.hardware.egl)" = mesa ]; then
    gles=196610
fi
setprop ro.opengles.version "$gles"
