#!/usr/bin/env bash
#
# run.sh — host tests of the a9_eink frame-progress interlock and bin
# admission (Hisense A9 mainline, patch 0026): the REAL
# drivers/gpu/drm/a9_eink/a9_eink_transport.c and a9_eink_guard.c (with
# patches 0028 control template + 0029 drive-gap counters) compiled
# against mock.h (pthread hrtimer, controllable DRM commit/DPMS, a recorder
# for fp9929a_emergency_off), linked with the unchanged engine
# (a9_eink_engine.c + a9tcon.c) for a9e_frame_is_null. No phone; ~12 s.
# The control template here is SYNTHETIC (test.c): no vendor data.
#
# Usage: tests/mainline-a9eink-stall/run.sh [KERNEL_TREE] [-v]
#   no argument: ~/A9-build/mainline/linux-7.2.10 (A9_KSRC) HEAD with
#   repository patches 0026, 0027 and 0035-0042 applied to a scratch archive if absent;
#   KERNEL_TREE: that tree's files as they are on disk.
#
# Production a9_produce/a9_render_thread/a9_run_ring are extracted verbatim
# for concurrent job tests. Only engine rendering, hash updates and pipeline
# PMIC helpers are mocked; the actual dispatcher/producer/cleanup execute.
# Full a9_update and KMS admission remain covered by build/review.
# Exit 0 = all cases passed.
#
set -euo pipefail
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; A9="$(dirname "$(dirname "$T")")"
K=$A9/scripts/mainline/kernel
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
ARG=${1:-}
V=
case "$ARG" in
"" | -v)
	[ "$ARG" = -v ] && V=-v
	KS=${A9_KSRC:-$HOME/A9-build/mainline/linux-7.2.10}
	mkdir -p "$W/src"
	git -C "$KS" archive HEAD drivers/gpu/drm/a9_eink drivers/gpu/drm/msm/msm_atomic.c drivers/gpu/drm/drm_atomic_helper.c \
		drivers/gpu/drm/drm_atomic.c include/drm/drm_atomic.h include/drm/drm_auth.h \
		drivers/regulator/fp9929a.c \
		include/linux/regulator/fp9929a.h | tar -x -C "$W/src"
	# 0026 (interlock) and 0027 (progress-only deadline), unless HEAD has them
	for pm in "0026:a9x_stall_fn" "0027:confirmed progress: deadline from now"; do
		p=$(ls "$K"/patches/${pm%%:*}-*.patch)
		if grep -q "${pm#*:}" "$W/src/drivers/gpu/drm/a9_eink/a9_eink_transport.c"; then
			echo "already in $KS HEAD: $(basename "$p")"
		elif git -C "$W/src" apply --check "$p" 2>/dev/null; then
			git -C "$W/src" apply "$p"; echo "applied: $(basename "$p")"
		else
			echo "does not apply: $p" >&2; exit 2
		fi
	done
	# The coordinator may still have HEAD 0034. Update only this temporary
	# archive; NEVER apply patches to the supplied/shared source checkout.
	if ! grep -q 'smp_load_acquire(&a9->rjob)' "$W/src/drivers/gpu/drm/a9_eink/a9_eink_drv.c"; then
		p=$(ls "$K"/patches/0035-*.patch)
		git -C "$W/src" apply --check "$p"
		git -C "$W/src" apply "$p"
		echo "applied to scratch archive: $(basename "$p")"
	fi
	if ! grep -q 'static int a9x_flip' "$W/src/drivers/gpu/drm/a9_eink/a9_eink_transport.c"; then
		p=$(ls "$K"/patches/0036-*.patch)
		git -C "$W/src" apply --check "$p"
		git -C "$W/src" apply "$p"
		echo "applied to scratch archive: $(basename "$p")"
	else
		# Include the real profiling header when the archive already has 0036.
		git -C "$KS" archive HEAD include/drm/drm_atomic_profile.h | tar -x -C "$W/src"
	fi
	if ! grep -q 'static ssize_t test_pattern_store' "$W/src/drivers/gpu/drm/a9_eink/a9_eink_drv.c"; then
		p=$(ls "$K"/patches/0037-*.patch)
		git -C "$W/src" apply --check "$p"
		git -C "$W/src" apply "$p"
		echo "applied to scratch archive: $(basename "$p")"
	fi
	if ! grep -q 'module_param_cb(prefill_frames' "$W/src/drivers/gpu/drm/a9_eink/a9_eink_drv.c"; then
		p=$(ls "$K"/patches/0038-*.patch)
		git -C "$W/src" apply --check "$p"
		git -C "$W/src" apply "$p"
		echo "applied to scratch archive: $(basename "$p")"
	fi
	if ! grep -q 'a9x_rail_fault' "$W/src/drivers/gpu/drm/a9_eink/a9_eink_drv.c"; then
		p=$(ls "$K"/patches/0039-*.patch)
		git -C "$W/src" apply --check "$p"
		git -C "$W/src" apply "$p"
		echo "applied to scratch archive: $(basename "$p")"
	fi
	if ! grep -q 'static struct a9e_rect target_damage' "$W/src/drivers/gpu/drm/a9_eink/a9_eink_engine.c"; then
		p=$(ls "$K"/patches/0040-*.patch)
		git -C "$W/src" apply --check "$p"
		git -C "$W/src" apply "$p"
		echo "applied to scratch archive: $(basename "$p")"
	fi
	if ! grep -q 'static ssize_t manual_clear_store' "$W/src/drivers/gpu/drm/a9_eink/a9_eink_drv.c"; then
		p=$(ls "$K"/patches/0041-*.patch)
		git -C "$W/src" apply --check "$p"
		git -C "$W/src" apply "$p"
		echo "applied to scratch archive: $(basename "$p")"
	fi
	if ! grep -q 'static void a9_rail_report' "$W/src/drivers/gpu/drm/a9_eink/a9_eink_drv.c"; then
		p=$(ls "$K"/patches/0042-*.patch)
		git -C "$W/src" apply --check "$p"
		git -C "$W/src" apply "$p"
		echo "applied to scratch archive: $(basename "$p")"
	fi
	SRC=$W/src ;;
*) SRC=$ARG; V=${2:-} ;;
esac
D=$SRC/drivers/gpu/drm/a9_eink
PROFILE=$SRC/include/drm/drm_atomic_profile.h
[ -f "$PROFILE" ] || { echo "missing $PROFILE (patch 0036 required)" >&2; exit 2; }
for f in a9_eink_drv.c a9_eink_transport.c a9_eink_guard.c a9_eink_ring.c a9_eink.h a9_eink_engine.c a9_eink_engine.h a9tcon.c a9tcon.h; do
	[ -f "$D/$f" ] || { echo "no $D/$f (patch 0026 missing?)" >&2; exit 2; }
done
grep -q a9x_stall_fn "$D/a9_eink_transport.c" || { echo "$D: no interlock (a9x_stall_fn)" >&2; exit 2; }

mkdir -p "$W/b"
cp "$D"/a9_eink_transport.c "$D"/a9_eink_guard.c "$D"/a9_eink_ring.c "$D"/a9_eink.h "$D"/a9_eink_engine.[ch] \
	"$D"/a9tcon.[ch] "$D"/a9_eink_pattern.h "$D"/a9_eink_damage.c "$T/mock.h" "$T/test.c" "$T/job-test.inc" "$T/power-test.inc" \
	"$T/profile-test.c" "$T/msm-profile-test.c" "$W/b/"
python3 "$T/extract-msm.py" "$SRC/drivers/gpu/drm/msm/msm_atomic.c" "$W/b/msm-tail.inc"
python3 "$T/extract-job.py" "$D/a9_eink_drv.c" "$W/b/driver-job.inc" "$W/b/driver-policy.inc"
python3 "$T/extract-power.py" "$D/a9_eink_drv.c" "$SRC/include/linux/regulator/fp9929a.h" "$W/b/driver-power.inc"
# every <linux/...> / <drm/...> include resolves to the mock
for h in $(cat "$D"/a9_eink_transport.c "$D"/a9_eink_guard.c "$D"/a9_eink_ring.c "$D"/a9_eink.h "$PROFILE" |
	   grep -oE '^#include <(linux|drm)/[^>]+>' | sed -E 's/#include <(.*)>/\1/' | sort -u); do
	mkdir -p "$W/inc/$(dirname "$h")"
	echo '#include "mock.h"' > "$W/inc/$h"
done
cp "$PROFILE" "$W/inc/drm/drm_atomic_profile.h"
echo "transport: $D/a9_eink_transport.c (sha256 $(sha256sum "$D/a9_eink_transport.c" | cut -c1-12))"

CC_CMD="gcc -std=gnu11 -O1 -g -Wall -Wno-unused-function -pthread -I$W/b -I$W/inc -o $W/t $W/b/test.c $W/b/a9_eink_ring.c $W/b/a9_eink_engine.c $W/b/a9_eink_damage.c $W/b/a9tcon.c"
MSM_CC_CMD="gcc -std=gnu11 -O1 -g -Wall -Wno-unused-function -pthread -I$W/b -I$W/inc -o $W/msm-t $W/b/msm-profile-test.c"
PROFILE_CC_CMD="gcc -std=gnu11 -O1 -g -Wall -pthread -I$W/b -I$W/inc -o $W/profile-t $W/b/profile-test.c"
if command -v gcc >/dev/null; then
	$CC_CMD
	$PROFILE_CC_CMD
	$MSM_CC_CMD
else
	nix-shell -p gcc --run "$CC_CMD && $PROFILE_CC_CMD && $MSM_CC_CMD"
fi
"$W/profile-t"
"$W/msm-t"
"$W/t" $V
python3 "$A9/scripts/mainline/android/tests/test-eink-priority.py"
