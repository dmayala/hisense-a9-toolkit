#!/usr/bin/env bash
# build.sh -- cross-compile the A9 thermal HAL (android.hardware.thermal V3,
# NDK backend) without an AOSP tree. Offline, no phone.
#   scripts/mainline/android/thermal-hal/build.sh
# Inputs: NDK r27 (nix store, as build-vendor.sh: clang, libc, libc++), the
# build-tools 36 aidl compiler, from android.googlesource.com android16-release
# (downloaded once into $O/aosp): the frozen thermal V3 AIDL API and the
# libbinder_ndk headers (include_cpp/_ndk/_platform: aidl 36 output needs the
# platform ones, e.g. asBinderReference, AServiceManager_addService,
# AIBinder_markVintfStability), and libbinder_ndk.so + liblog.so from the v7
# GSI (debugfs, read-only) to link against the device's own LLNDK exports.
# Output: $O/out/android.hardware.thermal-service.a9 (+ .sha256)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
O=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}
case "$O" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2;; esac
: "${ANDROID_NDK_HOME:=/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}"
AIDL=${A9_AIDL:-/nix/store/iqyx3zkviyc1ik9snyhhw77aanc4b3zx-android-sdk-build-tools-36.0.0/libexec/android-sdk/build-tools/36.0.0/aidl}
GSI=${A9_GSI_IMG:-$HOME/a9-assets/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v7.img}
TC=$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64
API=34
mkdir -p "$O/aosp" "$O/gsi-libs" "$O/out"
# frozen V3 API (its .hash is the interface hash the framework checks)
A=$O/aosp/thermal-aidl/aidl_api/android.hardware.thermal/3
if [ ! -f "$A/.hash" ]; then
  curl -fsS -m 120 -o "$O/aosp/thermal-aidl.tgz" \
    'https://android.googlesource.com/platform/hardware/interfaces/+archive/refs/heads/android16-release/thermal/aidl.tar.gz'
  mkdir -p "$O/aosp/thermal-aidl" && tar -xzf "$O/aosp/thermal-aidl.tgz" -C "$O/aosp/thermal-aidl"
fi
[ "$(cat "$A/.hash")" = 4c4fc474c40b64963eb8d78b713b1095fecd72f0 ] || { echo "unexpected thermal V3 hash" >&2; exit 1; }
B=$O/aosp/binder-ndk
if [ ! -f "$B/include_platform/android/binder_manager.h" ]; then
  curl -fsS -m 120 -o "$O/aosp/binder-ndk.tgz" \
    'https://android.googlesource.com/platform/frameworks/native/+archive/refs/heads/android16-release/libs/binder/ndk.tar.gz'
  mkdir -p "$B" && tar -xzf "$O/aosp/binder-ndk.tgz" -C "$B"
fi
for l in libbinder_ndk.so liblog.so; do
  [ -s "$O/gsi-libs/$l" ] || debugfs -R "dump /system/lib64/$l $O/gsi-libs/$l" "$GSI" >/dev/null 2>&1
  [ -s "$O/gsi-libs/$l" ] || { echo "could not read $l from $GSI" >&2; exit 1; }
done
G=$O/thermal-gen; rm -rf "$G"; mkdir -p "$G/include" "$G/src"
(cd "$O/aosp/thermal-aidl/aidl_api/android.hardware.thermal/3" &&
 "$AIDL" --lang=ndk --structured --stability=vintf --version=3 --hash="$(cat .hash)" --min_sdk_version=$API \
   -I . -h "$G/include" -o "$G/src" $(find android -name '*.aidl' | sort))
CXX="$TC/bin/clang++ --target=aarch64-linux-android$API"
FLAGS="-std=c++17 -O2 -Wall -Wextra -Werror -Wno-unused-parameter -fPIE -DBINDER_STABILITY_SUPPORT
  -I$B/include_platform -I$B/include_cpp -I$B/include_ndk -I$G/include -ffunction-sections -fdata-sections"
$CXX $FLAGS -pie -static-libstdc++ -Wl,--gc-sections -Wl,--allow-shlib-undefined -Wl,-z,relro,-z,now \
  -L"$O/gsi-libs" -o "$O/out/android.hardware.thermal-service.a9" \
  "$HERE/Thermal.cpp" "$HERE/thermal_core.cpp" $(find "$G/src" -name '*.cpp' | sort) -lbinder_ndk -llog
"$TC/bin/llvm-strip" --strip-unneeded "$O/out/android.hardware.thermal-service.a9"
NEED=$("$TC/bin/llvm-readelf" -d "$O/out/android.hardware.thermal-service.a9" | sed -n 's/.*Shared library: \[\(.*\)\]/\1/p' | sort | tr '\n' ' ')
[ "$NEED" = "libbinder_ndk.so libc.so libdl.so liblog.so libm.so " ] || { echo "unexpected NEEDED: $NEED" >&2; exit 1; }
(cd "$O/out" && sha256sum android.hardware.thermal-service.a9 > android.hardware.thermal-service.a9.sha256)
echo "thermal HAL: $O/out/android.hardware.thermal-service.a9 $(cut -c1-16 "$O/out/android.hardware.thermal-service.a9.sha256") NEEDED: $NEED"
