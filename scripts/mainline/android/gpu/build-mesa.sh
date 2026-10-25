#!/usr/bin/env bash
# build-mesa.sh -- cross-build Mesa (freedreno gallium EGL/GLES + optional turnip
# Vulkan) for Android arm64 with the NDK, for the A9 mainline vendor's GPU mode
# (Adreno 610, msm render node). Offline; no phone. docs: runs/gpu.md.
#
#   scripts/mainline/android/gpu/build-mesa.sh [--no-vulkan]
#
# Sources (fetched once, pinned, local disk only):
#   Mesa   mesa-26.2.5   https://gitlab.freedesktop.org/mesa/mesa.git
#   libdrm libdrm-2.4.129 https://gitlab.freedesktop.org/mesa/drm.git (static, freedreno only)
# Same recipe as the A6L project (tools/build-mesa-freedreno.sh there): Mesa's
# android-stub mode (no AOSP tree), static libdrm, static libc++.
# Output: $GW/out/mesa/{lib64/egl,lib64,lib64/hw} + SHA256SUMS, the layout the
# vendor expects (build-vendor.sh --gpu copies it verbatim).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
GW=${A9_GPU_WORK:-$HOME/A9-build/mainline/gpu}
case "$GW" in *a9-assets*) echo "refusing to build under a9-assets" >&2; exit 2 ;; esac
MESA_TAG=${MESA_TAG:-mesa-26.2.5}
DRM_TAG=${DRM_TAG:-libdrm-2.4.129}
VK=freedreno; [ "${1:-}" = --no-vulkan ] && VK=
: "${ANDROID_NDK_HOME:=/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}"
TC=$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin
API=34 SDK=35   # NDK 27 sysroot; Mesa's platform-sdk-version (A16 = 36 is a superset of what 35 uses)
[ -x "$TC/aarch64-linux-android$API-clang" ] || { echo "NDK not found: $ANDROID_NDK_HOME" >&2; exit 1; }
mkdir -p "$GW"
[ -d "$GW/mesa-src/.git" ] || git clone -q --depth 1 -b "$MESA_TAG" https://gitlab.freedesktop.org/mesa/mesa.git "$GW/mesa-src"
[ -d "$GW/libdrm-src/.git" ] || git clone -q --depth 1 -b "$DRM_TAG" https://gitlab.freedesktop.org/mesa/drm.git "$GW/libdrm-src"
[ "$(git -C "$GW/mesa-src" describe --tags)" = "$MESA_TAG" ] || { echo "mesa-src is not $MESA_TAG" >&2; exit 1; }
# local patches (A9): applied once, recorded
if [ -d "$HERE/mesa-patches" ]; then
  for p in "$HERE"/mesa-patches/*.patch; do
    [ -e "$p" ] || continue
    if git -C "$GW/mesa-src" apply --check "$p" 2>/dev/null; then git -C "$GW/mesa-src" apply "$p"; echo "  applied $(basename "$p")"; fi
  done
fi

B=$GW/build-mesa SYS=$GW/sysroot OUT=$GW/out/mesa
mkdir -p "$B"
cat > "$B/cross.ini" <<EOF
[binaries]
ar = '$TC/llvm-ar'
c = ['$TC/aarch64-linux-android$API-clang']
cpp = ['$TC/aarch64-linux-android$API-clang++']
c_ld = 'lld'
cpp_ld = 'lld'
strip = '$TC/llvm-strip'
pkg-config = 'pkg-config'
[built-in options]
cpp_link_args = ['-static-libstdc++']
c_args = ['-D__ANDROID_VENDOR__']
cpp_args = ['-D__ANDROID_VENDOR__']
[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8'
endian = 'little'
[properties]
needs_exe_wrapper = true
pkg_config_libdir = '$SYS/lib/pkgconfig'
EOF

NIXPKGS='meson ninja pkg-config bison flex python3 python3Packages.mako python3Packages.pyyaml python3Packages.packaging python3Packages.ply glslang'
run() { nix-shell -p $NIXPKGS --run "$*"; }

if [ ! -f "$SYS/lib/pkgconfig/libdrm_freedreno.pc" ]; then
  rm -rf "$B/libdrm"
  run "meson setup '$B/libdrm' '$GW/libdrm-src' --cross-file '$B/cross.ini' --prefix='$SYS' --libdir=lib \
    -Ddefault_library=static -Dfreedreno=enabled -Dintel=disabled -Dradeon=disabled -Damdgpu=disabled \
    -Dnouveau=disabled -Dvmwgfx=disabled -Detnaviv=disabled -Dexynos=disabled -Dtegra=disabled -Dvc4=disabled \
    -Domap=disabled -Dtests=false -Dman-pages=disabled -Dvalgrind=disabled -Dcairo-tests=disabled \
    -Dudev=false -Dinstall-test-programs=false" > "$B/libdrm-setup.log" 2>&1 || { tail -20 "$B/libdrm-setup.log"; exit 1; }
  run "ninja -C '$B/libdrm' install" > "$B/libdrm-build.log" 2>&1 || { tail -30 "$B/libdrm-build.log"; exit 1; }
fi

rm -rf "$B/mesa"
run "cd '$GW/mesa-src' && meson setup '$B/mesa' --cross-file '$B/cross.ini' --wrap-mode=nofallback --prefix=/vendor \
  -Dplatforms=android -Dplatform-sdk-version=$SDK -Dandroid-stub=true -Dandroid-libbacktrace=disabled \
  -Dandroid-strict=false -Dgallium-drivers=freedreno -Dvulkan-drivers=$VK -Dfreedreno-kmds=msm \
  -Degl=enabled -Dgles1=enabled -Dgles2=enabled -Dopengl=true -Dgbm=disabled -Dglx=disabled -Dllvm=disabled \
  -Dshared-glapi=enabled -Dcpp_rtti=false -Dbuildtype=release -Db_ndebug=true -Dzstd=disabled -Dxmlconfig=disabled \
  -Dexpat=disabled -Dvalgrind=disabled -Dlibunwind=disabled -Dlmsensors=disabled -Dperfetto=false \
  -Dtools= -Dbuild-tests=false -Dvideo-codecs= -Dspirv-tools=disabled -Dmesa-clc=auto" \
  > "$B/mesa-setup.log" 2>&1 || { tail -40 "$B/mesa-setup.log"; exit 1; }
run "ninja -C '$B/mesa' -j$(nproc)" > "$B/mesa-build.log" 2>&1 || { grep -m3 -A20 FAILED "$B/mesa-build.log"; exit 1; }

# --- stage in the vendor layout
rm -rf "$OUT"; mkdir -p "$OUT/lib64/egl" "$OUT/lib64/hw"
cp_so() { "$TC/llvm-strip" --strip-unneeded -o "$2" "$1"; }
for f in $(find "$B/mesa" -name '*.so' -type f); do
  n=$(basename "$f")
  case "$n" in
    libEGL.so)       cp_so "$f" "$OUT/lib64/egl/libEGL_mesa.so" ;;
    libGLESv1_CM.so) cp_so "$f" "$OUT/lib64/egl/libGLESv1_CM_mesa.so" ;;
    libGLESv2.so)    cp_so "$f" "$OUT/lib64/egl/libGLESv2_mesa.so" ;;
    libgallium*.so|libglapi.so) cp_so "$f" "$OUT/lib64/$n" ;;
    libvulkan_freedreno.so) cp_so "$f" "$OUT/lib64/hw/vulkan.adreno.so" ;;
    *) ;;
  esac
done
ls "$OUT/lib64/egl/libEGL_mesa.so" "$OUT/lib64/egl/libGLESv2_mesa.so" >/dev/null
# every NEEDED must be an LLNDK/system lib, a vendor lib the Cuttlefish vendor ships, or one of ours
ALLOWED=' libc.so libm.so libdl.so liblog.so libsync.so libnativewindow.so libcutils.so libhardware.so libz.so libutils.so libvndksupport.so '
bad=0
for f in $(find "$OUT" -name '*.so'); do
  for n in $(nix-shell -p binutils --run "readelf -d '$f'" 2>/dev/null | sed -n 's/.*Shared library: \[\(.*\)\]/\1/p'); do
    case "$ALLOWED" in *" $n "*) continue ;; esac
    [ -e "$OUT/lib64/$n" ] && continue
    echo "  $f NEEDS $n (not provided)" >&2; bad=1
  done
done
[ $bad = 0 ] || { echo "unresolved NEEDED entries" >&2; exit 1; }
# provenance (review 27): commit AND tree of exactly what was built; keep
# $GW/mesa-src (do not delete it) so the binaries can be re-derived and audited
{ echo "mesa $MESA_TAG $(git -C "$GW/mesa-src" rev-parse HEAD) dirty=$(git -C "$GW/mesa-src" status --porcelain | wc -l)";
  echo "mesa-tree $(git -C "$GW/mesa-src" rev-parse 'HEAD^{tree}') libdrm-tree $(git -C "$GW/libdrm-src" rev-parse 'HEAD^{tree}')";
  echo "recipe $(sha256sum "$0" | cut -c1-16) (sha256 of build-mesa.sh)";
  echo "libdrm $DRM_TAG $(git -C "$GW/libdrm-src" rev-parse HEAD)"; echo "ndk $ANDROID_NDK_HOME api $API sdk $SDK"; } > "$OUT/BUILDINFO"
(cd "$OUT" && find . -type f ! -name SHA256SUMS | sort | xargs sha256sum > SHA256SUMS)
echo "mesa: $OUT"; cat "$OUT/SHA256SUMS"
