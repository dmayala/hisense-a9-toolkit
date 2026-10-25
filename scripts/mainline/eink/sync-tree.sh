#!/usr/bin/env bash
# Copy the a9_eink driver (+ the a9tcon core it builds on) into a kernel tree
# as drivers/gpu/drm/a9_eink/, and hook it into drivers/gpu/drm/{Kconfig,Makefile}.
#   sync-tree.sh <kernel-src>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
K=${1:?kernel source tree}
D=$K/drivers/gpu/drm/a9_eink
mkdir -p "$D/kcompat"
cp "$HERE"/driver/{Kconfig,Makefile,a9_eink.h,a9_eink_drv.c,a9_eink_transport.c,a9_eink_guard.c,a9_eink_engine.c,a9_eink_damage.c,a9_eink_engine.h} "$D/"
cp "$HERE"/driver/kcompat/*.h "$D/kcompat/"
cp "$HERE"/../tcon/a9tcon.c "$HERE"/../tcon/a9tcon.h "$D/"
grep -q 'a9_eink/Kconfig' "$K/drivers/gpu/drm/Kconfig" ||
	sed -i 's|^source "drivers/gpu/drm/tiny/Kconfig"|source "drivers/gpu/drm/tiny/Kconfig"\nsource "drivers/gpu/drm/a9_eink/Kconfig"|' "$K/drivers/gpu/drm/Kconfig"
grep -q 'a9_eink/' "$K/drivers/gpu/drm/Makefile" ||
	sed -i 's|^obj-y\t\t\t+= tiny/$|&\nobj-$(CONFIG_DRM_A9_EINK) += a9_eink/|' "$K/drivers/gpu/drm/Makefile"
grep -q 'a9_eink/' "$K/drivers/gpu/drm/Makefile" || { echo "Makefile hook failed" >&2; exit 1; }
grep -q 'a9_eink/Kconfig' "$K/drivers/gpu/drm/Kconfig" || { echo "Kconfig hook failed" >&2; exit 1; }
echo "synced into $D"
