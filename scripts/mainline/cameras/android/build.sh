#!/usr/bin/env bash
# Host-only Android/bionic HAL + CPU SoftISP. Incremental, no AOSP/kernel build.
# Run inside nix-shell -p meson ninja cmake pkg-config python3Packages.pyyaml
# python3Packages.jinja2 python3Packages.ply --run 'bash .../build.sh'
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
W=${A9_CAMERA_ANDROID_OUT:-/home/user/A9-build/mainline/cameras-20261020/android}
NDK=/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973
TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
export CC=$TC/aarch64-linux-android30-clang CXX=$TC/aarch64-linux-android30-clang++ AR=$TC/llvm-ar RANLIB=$TC/llvm-ranlib
export CFLAGS='-O2 -fPIC' CXXFLAGS='-O2 -fPIC'
mkdir -p "$W/sysroot"
cd "$W"
# Sources: fetch.py plus libcamera tag v0.7.2, exact commit checked here.
[ "$(git -C libcamera rev-parse HEAD)" = 191e202178f02430b5942397c70d215cdd2056fa ]
PATCH_SHA=$(cat "$HERE"/patches/*.patch | sha256sum | cut -d' ' -f1)
if [ -e .patched ] && [ "$(cat .patched)" != "$PATCH_SHA" ]; then
    echo 'Camera patchset changed: prepare a clean local libcamera source and build directory.' >&2
    exit 1
fi
if [ ! -e .patched ]; then
    for p in "$HERE"/patches/*.patch; do git -C libcamera apply --unidiff-zero --whitespace=nowarn "$p"; done
    printf '%s\n' "$PATCH_SHA" > .patched
fi
if [ ! -e sysroot/lib/libyaml.a ]; then
    (cd yaml-0.2.5; ./configure --host=aarch64-linux-android --prefix="$W/sysroot" --disable-shared --enable-static --with-pic; make -j4; make install) > yaml-build.log 2>&1
fi
if [ ! -e sysroot/lib/libjpeg.a ]; then
    cmake -S libjpeg-turbo-3.0.4 -B build-jpeg -G Ninja -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=30 -DCMAKE_BUILD_TYPE=Release -DENABLE_SHARED=OFF -DENABLE_STATIC=ON -DWITH_TURBOJPEG=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_INSTALL_PREFIX="$W/sysroot" -DCMAKE_INSTALL_LIBDIR=lib > jpeg-build.log 2>&1
    ninja -C build-jpeg -j4 >> jpeg-build.log 2>&1
    ninja -C build-jpeg install >> jpeg-build.log 2>&1
fi
if [ ! -e sysroot/lib/libexif.a ]; then
    (cd libexif-0.6.25; ./configure --host=aarch64-linux-android --prefix="$W/sysroot" --disable-shared --enable-static --disable-nls --disable-docs --with-pic; make -j4; make install) > exif-build.log 2>&1
fi
if [ ! -d libcamera/subprojects/libyuv ]; then cp -a libyuv libcamera/subprojects/libyuv; fi
cat > android.cross <<CROSS
[binaries]
c = '$CC'
cpp = '$CXX'
ar = '$AR'
strip = '$TC/llvm-strip'
pkg-config = 'pkg-config'
cmake = 'cmake'
[properties]
pkg_config_libdir = '$W/sysroot/lib/pkgconfig'
[built-in options]
cpp_link_args = ['-static-libstdc++', '-L$W/sysroot/lib']
[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8-a'
endian = 'little'
[cmake]
CMAKE_SYSTEM_NAME = 'Linux'
CMAKE_SYSTEM_PROCESSOR = 'aarch64'
CROSS
if [ ! -f build/build.ninja ]; then
    meson setup build libcamera --cross-file android.cross --wrap-mode=nodownload --prefix=/vendor --libdir=lib64 --sysconfdir=etc --datadir=share --libexecdir=libexec --buildtype=release -Db_ndebug=false \
      -Dpipelines=simple -Dipas=simple -Dsoftisp-gpu=disabled -Dcam=disabled -Dandroid=enabled -Dandroid_platform=generic \
      -Dgstreamer=disabled -Dqcam=disabled -Dlc-compliance=disabled -Dpycamera=disabled -Dv4l2=false -Dudev=disabled \
      -Dtracing=disabled -Dlibdw=disabled -Dlibunwind=disabled -Dapps-output-dng=disabled -Ddocumentation=disabled -Dtest=false > hal-setup.log 2>&1
fi
ninja -C build -j4 > hal-build.log 2>&1
DESTDIR="$W/stage" meson install -C build --no-rebuild > hal-install.log 2>&1
printf 'Android HAL built at %s/stage/vendor\n' "$W"
