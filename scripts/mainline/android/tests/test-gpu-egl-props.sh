#!/usr/bin/env bash
# test-gpu-egl-props.sh <raw vendor ext4 image> -- host test of the BUILT vendor's graphics props
# (eink-trial3). Read-only (debugfs). Why: the A16 GraphicsEnvironment (use_query_angle_choice
# compiled OFF in the v7 GSI) turns ANGLE on for every app whenever ro.hardware.egl reads "angle",
# whatever persist.graphics.egl says, so a GPU vendor must NOT define ro.hardware.egl in
# build.prop (a9ml-gpu.sh select sets it once; init.a9ml-gpu.rc falls back to angle).
# A vendor without the GPU mode keeps ro.hardware.egl=angle (SwiftShader, as before).
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
V=${1:?usage: test-gpu-egl-props.sh <vendor.img>}
fails=0
ok() { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
debugfs -R "cat /build.prop" "$V" > "$T/build.prop" 2>/dev/null
[ -s "$T/build.prop" ] || { echo "cannot read /build.prop from $V"; exit 2; }
has() { grep -qx "$1" "$T/build.prop"; }
count() { grep -c "^$1=" "$T/build.prop"; }
if has 'ro.vendor.a9ml.gpu=1'; then
  [ "$(count ro.hardware.egl)" = 0 ] && ok "GPU vendor: ro.hardware.egl is not defined in build.prop (apps are not pinned to ANGLE)" || bad "GPU vendor defines ro.hardware.egl: $(grep '^ro.hardware.egl=' "$T/build.prop")"
  has 'ro.vendor.a9ml.gpu.egl=mesa' && ok "build opt-in ro.vendor.a9ml.gpu.egl=mesa" || bad "ro.vendor.a9ml.gpu.egl=mesa missing"
  has 'ro.vendor.a9ml.gpu.app_egl=mesa' && ok "ro.vendor.a9ml.gpu.app_egl=mesa (apps use Mesa when selected)" || bad "ro.vendor.a9ml.gpu.app_egl=mesa missing"
  has 'ro.hardware.vulkan=adreno' && ok "Vulkan selects Mesa Turnip" || bad "GPU vendor does not select Turnip"
  [ "$(count ro.cpuvulkan.version)" = 0 ] && [ "$(count ro.vulkan.apex)" = 0 ] && ok "no inherited CPU Vulkan/APEX selection" || bad "stale Vulkan CPU/APEX selection"
  debugfs -R 'stat /apex/com.google.cf.vulkan.apex' "$V" 2>/dev/null | grep -q Inode && bad "Cuttlefish Vulkan APEX still publishes conflicting features"
  for f in android.hardware.vulkan.level-1.xml android.hardware.vulkan.version-1_0_3.xml; do
    debugfs -R "dump /etc/permissions/$f $T/feature" "$V" >/dev/null 2>&1
    cmp -s "$T/feature" "$HERE/gpu/vulkan-features/$f" && ok "A610 feature $f" || bad "missing or incorrect $f"
  done
  debugfs -R 'stat /lib64/hw/vulkan.adreno.so' "$V" 2>/dev/null | grep -q Inode && ok "Turnip HAL installed" || bad "Turnip HAL missing"
  has 'debug.hwui.renderer=skiagl' && ok "HWUI on GL (skiagl)" || bad "debug.hwui.renderer is not skiagl"
  for f in etc/init/init.a9ml-gpu.rc bin/a9ml-gpu.sh bin/a9ml-eink.sh; do
    debugfs -R "dump /$f $T/f" "$V" >/dev/null 2>&1
    if [ "$f" = etc/init/init.a9ml-gpu.rc ]; then
      # R4 integrated composition starts Wi-Fi firmware before GPU setup.
      sed '/^    mkdir \/mnt\/a9-wifi 0700 root root$/d; /^    mkdir \/mnt\/a9-wifi\/firmware 0755 root root$/d; /^    exec_start a9ml_wifi_firmware$/d' "$T/f" > "$T/rc"
      mv "$T/rc" "$T/f"
    fi
    if [ "$f" = bin/a9ml-gpu.sh ]; then
      # R4 verifies the shared Wi-Fi firmware symlink instead of copying over it.
      # Accept exactly that existing integration change, keeping GPU setup intact.
      python3 -I - "$HERE/vendor/$f" "$T/f" <<'PY' && ok "installed /$f = source or R4 shared-firmware variant" || bad "installed /$f differs from source (rebuild the vendor)"
from pathlib import Path
import sys
source, actual = [Path(p).read_bytes() for p in sys.argv[1:]]
original = b'\tif mkdir -p "$FWD" && cp -R $VND/firmware/a9gpu/. "$FWD/" && (cd "$FWD" && sha256sum -c $VND/firmware/a9gpu.SHA256SUMS >/dev/null 2>&1); then\n'
integrated = b'''\tif { if [ "$FWD" = /mnt/a9-wifi/firmware ]; then
        [ -L "$FWD/qcom" ] && [ "$(readlink "$FWD/qcom")" = /vendor/firmware/qcom ] &&
        (cd "$FWD" && sha256sum -c $VND/firmware/a9gpu.SHA256SUMS >/dev/null 2>&1)
    else
        mkdir -p "$FWD" && cp -R $VND/firmware/a9gpu/. "$FWD/" && (cd "$FWD" && sha256sum -c $VND/firmware/a9gpu.SHA256SUMS >/dev/null 2>&1)
    fi; }; then
'''
sys.exit(0 if actual in (source, source.replace(original, integrated, 1)) else 1)
PY
    else
      cmp -s "$T/f" "$HERE/vendor/$f" && ok "installed /$f = source" || bad "installed /$f differs from source (rebuild the vendor)"
    fi
  done
  debugfs -R "cat /etc/init/init.a9ml-gpu.rc" "$V" 2>/dev/null | grep -qx '    setprop ro.hardware.egl angle' && ok "installed rc has the fail-closed 'setprop ro.hardware.egl angle' after select" || bad "installed rc lacks the angle fallback"

else
  [ "$(count ro.hardware.egl)" = 1 ] && has 'ro.hardware.egl=angle' && ok "non-GPU vendor: ro.hardware.egl=angle (software path unchanged)" || bad "non-GPU vendor lost ro.hardware.egl=angle"
fi
[ $fails = 0 ] && echo "test-gpu-egl-props: PASS" || { echo "test-gpu-egl-props: $fails FAILED"; exit 1; }
