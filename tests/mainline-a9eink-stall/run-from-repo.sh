#!/usr/bin/env bash
#
# run-from-repo.sh — run run.sh against the driver sources of THIS repository
# (scripts/mainline/eink/driver + scripts/mainline/tcon), overlaid on the
# shared kernel tree's HEAD for everything the harness takes from the kernel
# (msm_atomic.c, drm_atomic*.c, fp9929a, headers). Needed from eink-rail-diag
# on: the harness otherwise tests the kernel tree's HEAD driver (0042), which
# does not carry the repository-only patches 0047-0052.
# Usage: tests/mainline-a9eink-stall/run-from-repo.sh [-v]
set -euo pipefail
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; A9="$(dirname "$(dirname "$T")")"
KS=${A9_KSRC:-$HOME/A9-build/mainline/linux-7.2.10}
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
git -C "$KS" archive HEAD drivers/gpu/drm/a9_eink drivers/gpu/drm/msm/msm_atomic.c drivers/gpu/drm/drm_atomic_helper.c \
	drivers/gpu/drm/drm_atomic.c include/drm/drm_atomic.h include/drm/drm_auth.h include/drm/drm_atomic_profile.h \
	drivers/regulator/fp9929a.c include/linux/regulator/fp9929a.h | tar -x -C "$W"
D=$W/drivers/gpu/drm/a9_eink
cp "$A9"/scripts/mainline/eink/driver/*.[ch] "$D/"
cp "$A9"/scripts/mainline/tcon/a9tcon.c "$A9"/scripts/mainline/tcon/a9tcon.h "$D/"
python3 "$T/sysfs-size-check.py" "$D/a9_eink_drv.c"
exec "$T/run.sh" "$W" "$@"
