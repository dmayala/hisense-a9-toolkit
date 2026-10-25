#!/usr/bin/env bash
# Offline only: prepare an isolated source snapshot/O= tree, build Image.gz.
# Does not call the coordinator build script or any device transport.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
M=${A9_MAINLINE:-$HOME/A9-build/mainline}
WORK=$M/sol-smp
SRC=$WORK/source-C
OBJ=$WORK/build-C
NS=${A9_SMP_CACHE_NAMESPACE:-0}
if [ "$NS" = 1 ]; then OBJ=$WORK/build-C-cached; fi
BASE=$M/linux-7.2.10
BASE_OBJ=$M/build-7.2.10-android
BASE_COMMIT=${A9_SMP_SOURCE_COMMIT:-bd6d5b239}
ASHMEM=$REPO/scripts/mainline/android/kernel-requests/0001-misc-ashmem-legacy-C-dev-ashmem-driver-for-Android-1.patch
case "$WORK" in *a9-assets*|*Backups*|/share/*) echo "refusing reference/NFS output" >&2; exit 2;; esac
case "$(realpath -m "$WORK")" in "$REPO"/*) echo "refusing build output inside repository" >&2; exit 2;; esac
mkdir -p "$WORK" "$SRC" "$OBJ" "$WORK/kernel-C1"
if [ -f "$SRC/Makefile" ]; then
    test "$(cat "$WORK/kernel-C1/source-commit.txt")" = "$(git -C "$BASE" rev-parse "$BASE_COMMIT")" || {
        echo "existing private source has a different pin; choose a fresh workspace" >&2; exit 2;
    }
fi
if [ ! -f "$SRC/Makefile" ]; then
    git -C "$BASE" rev-parse "$BASE_COMMIT" > "$WORK/kernel-C1/source-commit.txt"
    git -C "$BASE" archive "$(cat "$WORK/kernel-C1/source-commit.txt")" | tar -x -C "$SRC"
    if [ ! -f "$SRC/drivers/misc/ashmem/ashmem-legacy.c" ]; then
        patch -s -p1 -d "$SRC" -i "$ASHMEM"
        sha256sum "$ASHMEM" > "$WORK/kernel-C1/ashmem-patch.sha256"
    fi
    # The reused output cache may come from a newer tree: force dependency
    # rebuilds after materializing this older, pinned source snapshot.
    find "$SRC" -type f -exec touch {} +
fi
if [ ! -f "$OBJ/.config" ]; then
    cp -a --reflink=auto "$BASE_OBJ/." "$OBJ/"
fi
if [ "$NS" = 1 ]; then
    # Reuse normal Kbuild metadata without editing it: identical source bytes
    # retain the shared source's mtime; differing pinned bytes become newer.
    # This touches ONLY our snapshot. No .cmd or cached object is rewritten.
    python3 -I - "$SRC" "$BASE" "$WORK/kernel-C1/cache-source-differences.json" <<'PY'
import filecmp, json, os, pathlib, sys, time
src, base, report = map(pathlib.Path, sys.argv[1:])
changed = []
now = time.time_ns()
for f in src.rglob('*'):
    if f.is_symlink() or not f.is_file():
        continue
    rel = f.relative_to(src)
    other = base / rel
    if other.is_file() and filecmp.cmp(f, other, shallow=False):
        os.utime(f, ns=(now, other.stat().st_mtime_ns))
    else:
        os.utime(f, ns=(now, now))
        changed.append(str(rel))
report.write_text(json.dumps(sorted(changed), indent=2) + '\n')
print('Compared pinned/private source bytes; differing files:', len(changed))
PY
fi
# Exact tested kernel config, rather than a concurrently changing shared config.
python3 -I - "$WORK/images/manifest.json" "$WORK/images/components/Image.gz" "$OBJ/.config" <<'PY'
import gzip, json, pathlib, sys
manifest, kernel, config = map(pathlib.Path, sys.argv[1:])
assert json.loads(manifest.read_text())['base_sha256'] == 'd719592576dcda102043490175e632428ed0185bee927628d1a114a401544520', 'C1 source provenance is pinned to Android boot 2'
raw = gzip.decompress(kernel.read_bytes())
config.write_bytes(gzip.decompress(raw[raw.index(b'IKCFG_ST') + 8:raw.index(b'IKCFG_ED')]))
PY
"$SRC/scripts/config" --file "$OBJ/.config" --enable ARM_PSCI_CPUIDLE --enable ARM_PSCI_CPUIDLE_DOMAIN
# Preserve the tested kernel release 7.2.10-a9+ in this non-git source snapshot.
if [ "$NS" = 1 ]; then
    # A private, unprivileged mount namespace hides the shared source ONLY
    # inside this subprocess. Read-only bind and private propagation prevent
    # writes or mount propagation to the shared source/parent namespace.
    cat > "$WORK/build-C1-namespace.sh" <<'SH'
set -euo pipefail
unshare --user --map-root-user --mount bash -c '
    set -euo pipefail
    mount --make-rprivate /
    mount --bind "$1" "$2"
    mount -o remount,bind,ro "$2"
    test ! -e "$2/.git"
    make -C "$2" O="$3" ARCH=arm64 LOCALVERSION=+ CROSS_COMPILE=aarch64-unknown-linux-gnu- olddefconfig
    make -C "$2" O="$3" ARCH=arm64 LOCALVERSION=+ CROSS_COMPILE=aarch64-unknown-linux-gnu- -j"$4" Image.gz
' -- "$A9_SMP_PRIVATE_SRC" "$A9_SMP_SHARED_PATH" "$A9_SMP_PRIVATE_OBJ" "$A9_SMP_JOBS"
SH
    export A9_SMP_PRIVATE_SRC="$SRC" A9_SMP_SHARED_PATH="$BASE" A9_SMP_PRIVATE_OBJ="$OBJ" A9_SMP_JOBS="${J:-8}"
    nix-shell "$REPO/scripts/mainline/kernel/shell.nix" --run "bash '$WORK/build-C1-namespace.sh'"
else
    nix-shell "$REPO/scripts/mainline/kernel/shell.nix" --run \
        "make -C '$SRC' O='$OBJ' ARCH=arm64 LOCALVERSION=+ CROSS_COMPILE=aarch64-unknown-linux-gnu- olddefconfig && make -C '$SRC' O='$OBJ' ARCH=arm64 LOCALVERSION=+ CROSS_COMPILE=aarch64-unknown-linux-gnu- -j${J:-8} Image.gz"
fi
cp "$OBJ/arch/arm64/boot/Image.gz" "$OBJ/.config" "$WORK/kernel-C1/"
echo "Private C1 kernel: $WORK/kernel-C1/Image.gz"
