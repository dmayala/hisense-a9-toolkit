#!/usr/bin/env bash
#
# run.sh — host fault tests of the REAL FP9929A driver (Hisense A9 mainline,
# drivers/regulator/fp9929a.c) against a userspace shim of the kernel APIs
# it uses (kshim.h: pthread mutexes/workqueue/wait, a fake GPIO timeline,
# fake I2C with injectable stalls/failures, callable panic notifier and
# module_param_cb). No phone, no kernel build; ~6 s.
#
# Usage: tests/mainline-fp9929a/run.sh [KERNEL_TREE | path/to/fp9929a.c] [-v]
#   no argument: the driver of ~/A9-build/mainline/linux-7.2.10 (A9_KSRC) HEAD
#   with scripts/mainline/kernel/patches/0021*..0027* (and the regulator part of
#   0039 and 0042, ex-0039b) applied unless HEAD has them;
#   KERNEL_TREE / file: that tree's (or file's) driver as it is on disk.
# The PG-wait cases (g, h) run when the driver has pg_timeout_ms (patch 0022).
# Exit 0 = all cases passed.
#
set -euo pipefail
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; A9="$(dirname "$(dirname "$T")")"
K=$A9/scripts/mainline/kernel
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
ARG=${1:-}
case "$ARG" in
"" | -v)
	[ "$ARG" = -v ] && set -- "" -v
	KS=${A9_KSRC:-$HOME/A9-build/mainline/linux-7.2.10}
	mkdir -p "$W/src/drivers/regulator" "$W/src/include/linux/regulator" \
		"$W/src/drivers/gpu/drm/a9_eink"
	for f in drivers/regulator/fp9929a.c include/linux/regulator/fp9929a.h \
		drivers/gpu/drm/a9_eink/a9_eink_drv.c drivers/gpu/drm/a9_eink/a9_eink.h \
		drivers/gpu/drm/a9_eink/a9_eink_transport.c drivers/gpu/drm/a9_eink/Makefile; do
		git -C "$KS" show "HEAD:$f" > "$W/src/$f"
	done
	# patches 0021 (gate interlock) .. 0026 (emergency off), unless HEAD has them
	for pm in "0021:fp9929a_gate_lock" "0022:pg_timeout_ms" "0023:vcom_on" "0024:fp9929a_max_on_set" "0025:fp9929a_min_off_wait" "0026:fp9929a_emergency_off" "0027:a repeated emergency_off() could"; do
		p=$(ls "$K"/patches/${pm%%:*}-*.patch)
		if grep -q "${pm#*:}" "$W/src/drivers/regulator/fp9929a.c"; then
			echo "already in $KS HEAD: $(basename "$p")"
		elif git -C "$W/src" apply --check "$p" 2>/dev/null; then
			git -C "$W/src" apply "$p"; echo "applied: $(basename "$p")"
		else
			echo "does not apply: $p" >&2; exit 2
		fi
	done
	# 0039 (pg_raw/energized in the status): regulator files only, unless HEAD has it
	if ! grep -q 'st->pg_raw' "$W/src/drivers/regulator/fp9929a.c"; then
		p=$(ls "$K"/patches/0039-*.patch)
		inc="--include=drivers/regulator/fp9929a.c --include=include/linux/regulator/fp9929a.h"
		git -C "$W/src" apply --check $inc "$p"
		git -C "$W/src" apply $inc "$p"
		echo "applied (regulator part) to scratch archive: $(basename "$p")"
	fi
	# 0042 (ex-0039b) passive power-down PG timeline, regulator/header only.
	if ! grep -q 'st->pg_at_us' "$W/src/drivers/regulator/fp9929a.c"; then
		p=$(ls "$K"/patches/0042-*.patch)
		inc="--include=drivers/regulator/fp9929a.c --include=include/linux/regulator/fp9929a.h"
		git -C "$W/src" apply --check $inc "$p"
		git -C "$W/src" apply $inc "$p"
		echo "applied (regulator part) to scratch archive: $(basename "$p")"
	fi
	# 0057 (review 89d F6) read-only VCOM readback, unless HEAD has it
	if ! grep -q '^int fp9929a_read_vcom' "$W/src/drivers/regulator/fp9929a.c"; then
		p=$(ls "$K"/patches/0057-*.patch)
		git -C "$W/src" apply --check "$p"
		git -C "$W/src" apply "$p"
		echo "applied to scratch archive: $(basename "$p")"
	fi
	# A9_KEEP=dir: copy the patched baseline tree there (to make a candidate diff).
	# A9_CAND=file: apply a POWER-PATH candidate on top (unreviewed, never default).
	[ -z "${A9_KEEP:-}" ] || { rm -rf "$A9_KEEP"; cp -a "$W/src" "$A9_KEEP"; }
	if [ -n "${A9_CAND:-}" ]; then
		git -C "$W/src" apply --check "$A9_CAND"
		git -C "$W/src" apply "$A9_CAND"
		echo "applied CANDIDATE: $(basename "$A9_CAND")"
	fi
	SRC=$W/src ;;
*) SRC=$ARG ;;
esac
case "$SRC" in
*.c) DRV=$SRC; HDR=$(dirname "$SRC")/../../include/linux/regulator/fp9929a.h ;;
*) DRV=$SRC/drivers/regulator/fp9929a.c; HDR=$SRC/include/linux/regulator/fp9929a.h ;;
esac
[ -f "$DRV" ] && [ -f "$HDR" ] || { echo "no driver/header under $SRC" >&2; exit 2; }

cp "$DRV" "$W/fp9929a.c"
mkdir -p "$W/inc/linux/regulator" "$W/inc/linux/gpio"
cp "$HDR" "$W/inc/linux/regulator/fp9929a.h"
# every other <linux/...> the driver includes resolves to the shim
for h in $(grep -oE '^#include <linux/[^>]+>' "$DRV" | sed -E 's/#include <(.*)>/\1/'); do
	[ "$h" = linux/regulator/fp9929a.h ] && continue
	mkdir -p "$W/inc/$(dirname "$h")"
	echo '#include "kshim.h"' > "$W/inc/$h"
done
echo '#include "kshim.h"' > "$W/inc/linux/types.h"
cp "$T/kshim.h" "$T/test.c" "$W/"

DEFS=
grep -q pg_timeout_ms "$DRV" && DEFS=-DHAVE_PG_WAIT
grep -q fp9929a_max_on_set "$DRV" && DEFS="$DEFS -DHAVE_MAX_ON_PARAM"
grep -q fp9929a_min_off_wait "$DRV" && DEFS="$DEFS -DHAVE_0025"
grep -q fp9929a_emergency_off "$DRV" && DEFS="$DEFS -DHAVE_0026"
grep -q 'st->pg_raw' "$DRV" && DEFS="$DEFS -DHAVE_0039"
grep -q 'st->pg_at_us' "$DRV" && DEFS="$DEFS -DHAVE_PDTRACE"
grep -q 'min_off_ms = 500;' "$DRV" && DEFS="$DEFS -DMINOFF_500"
grep -q '^int fp9929a_read_vcom' "$DRV" && DEFS="$DEFS -DHAVE_READ_VCOM"
echo "driver: $DRV (sha256 $(sha256sum "$DRV" | cut -c1-12))${DEFS:+, PG-wait cases on}"

CC_CMD="gcc -std=gnu11 -O1 -g -Wall -Wno-unused-function -Wno-unused-variable -pthread $DEFS -I$W -I$W/inc -o $W/t $W/test.c"
if command -v gcc >/dev/null; then
	$CC_CMD
else
	nix-shell -p gcc --run "$CC_CMD"
fi
"$W/t" "${2:-}"
