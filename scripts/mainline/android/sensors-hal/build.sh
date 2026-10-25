#!/usr/bin/env bash
# build.sh -- cross-compile the A9 sensors HAL (android.hardware.sensors V3, NDK backend)
# without an AOSP tree, exactly like thermal-hal/build.sh. Offline, no phone.
#   scripts/mainline/android/sensors-hal/build.sh
# Inputs: NDK r27 (nix store), build-tools 36 aidl, the frozen AIDL APIs from
# android.googlesource.com android16-release (downloaded once into $O/aosp:
# sensors V3, common V2, common.fmq V1), the libbinder_ndk headers (as thermal),
# and libbinder_ndk.so + liblog.so from the GSI (debugfs, read-only).
# Output: $O/out/android.hardware.sensors-service.a9 (+ .sha256)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SNS=$HERE/../../sensors
O=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}
case "$O" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2;; esac
: "${ANDROID_NDK_HOME:=/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}"
AIDL=${A9_AIDL:-/nix/store/iqyx3zkviyc1ik9snyhhw77aanc4b3zx-android-sdk-build-tools-36.0.0/libexec/android-sdk/build-tools/36.0.0/aidl}
GSI=${A9_GSI_IMG:-$HOME/a9-assets/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v7.img}
TC=$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64
API=34
mkdir -p "$O/aosp" "$O/gsi-libs" "$O/out"
fetch() {   # $1 = path below hardware/interfaces, $2 = local dir name
  [ -d "$O/aosp/$2" ] && return 0
  curl -fsS -m 120 -o "$O/aosp/$2.tgz" \
    "https://android.googlesource.com/platform/hardware/interfaces/+archive/refs/heads/android16-release/$1.tar.gz"
  mkdir -p "$O/aosp/$2" && tar -xzf "$O/aosp/$2.tgz" -C "$O/aosp/$2"
}
fetch sensors/aidl sensors-aidl
fetch common/aidl common-aidl
fetch common/fmq/aidl common-fmq-aidl
C=$O/aosp/common-aidl/aidl_api/android.hardware.common/2
F=$O/aosp/common-fmq-aidl/aidl_api/android.hardware.common.fmq/1
S=$O/aosp/sensors-aidl/aidl_api/android.hardware.sensors/3
# frozen hashes (the framework checks the interface hash of what we register)
[ "$(tail -1 "$S/.hash")" = b051523be857524c99057b69f253a7d0bd295400 ] || { echo "unexpected sensors V3 hash" >&2; exit 1; }
[ "$(tail -1 "$C/.hash")" = c32ddfdeb69c6e4a8a45519e6f9a39c4b66fd99f ] || { echo "unexpected common V2 hash" >&2; exit 1; }
[ "$(tail -1 "$F/.hash")" = 6a780550f6e6965d6969fd7964c3ca81b6b0ccdf ] || { echo "unexpected common.fmq V1 hash" >&2; exit 1; }
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
G=$O/sensors-gen; rm -rf "$G"; mkdir -p "$G/include" "$G/src"
gen() { local d=$1 v=$2; shift 2
  (cd "$d" && "$AIDL" --lang=ndk --structured --stability=vintf --version="$v" --hash="$(tail -1 .hash)" \
     --min_sdk_version=$API -I . "$@" -h "$G/include" -o "$G/src" $(find android -name '*.aidl' | sort)); }
gen "$C" 2
gen "$F" 1 -I "$C"
gen "$S" 3 -I "$C" -I "$F"
# N7 (review 79k): no host paths in the binary (assert strings, __FILE__): map the build
# dir, the repo and the NDK to relative names; checked after the link.
REPO=$(cd "$HERE/../../../.." && pwd)
PMAP="-ffile-prefix-map=$O=. -ffile-prefix-map=$REPO=. -ffile-prefix-map=$ANDROID_NDK_HOME=ndk"
CC="$TC/bin/clang --target=aarch64-linux-android$API $PMAP"
CXX="$TC/bin/clang++ --target=aarch64-linux-android$API $PMAP"
OBJ=$O/sensors-obj; rm -rf "$OBJ"; mkdir -p "$OBJ"
for c in s2-sns.c s2-client.c s2-qrtr.c; do
  $CC -std=c11 -O2 -Wall -Wextra -Werror -fPIE -D_GNU_SOURCE -I"$SNS" -c "$SNS/$c" -o "$OBJ/${c%.c}.o"
done
FLAGS="-std=c++17 -O2 -Wall -Wextra -Werror -Wno-unused-parameter -fPIE -DBINDER_STABILITY_SUPPORT -D_GNU_SOURCE
  -I$B/include_platform -I$B/include_cpp -I$B/include_ndk -I$G/include -I$SNS -I$HERE -ffunction-sections -fdata-sections"
$CXX $FLAGS -pie -static-libstdc++ -Wl,--gc-sections -Wl,--allow-shlib-undefined -Wl,-z,relro,-z,now \
  -L"$O/gsi-libs" -o "$O/out/android.hardware.sensors-service.a9" \
  "$HERE/Sensors.cpp" "$HERE/sensors_core.cpp" "$HERE/fmq_writer.cpp" "$OBJ"/*.o \
  $(find "$G/src" -name '*.cpp' | sort) -lbinder_ndk -llog
"$TC/bin/llvm-strip" --strip-unneeded "$O/out/android.hardware.sensors-service.a9"
NEED=$("$TC/bin/llvm-readelf" -d "$O/out/android.hardware.sensors-service.a9" | sed -n 's/.*Shared library: \[\(.*\)\]/\1/p' | sort | tr '\n' ' ')
[ "$NEED" = "libbinder_ndk.so libc.so libdl.so liblog.so libm.so " ] || { echo "unexpected NEEDED: $NEED" >&2; exit 1; }
if grep -aqE "$O|$REPO|/home/|/nix/store/" "$O/out/android.hardware.sensors-service.a9"; then
  echo "host path left in the binary (N7):" >&2
  grep -aoE "($O|$REPO|/home/|/nix/store/)[^[:space:][:cntrl:]]*" "$O/out/android.hardware.sensors-service.a9" | sort -u | head >&2
  exit 1
fi
(cd "$O/out" && sha256sum android.hardware.sensors-service.a9 > android.hardware.sensors-service.a9.sha256)
echo "sensors HAL: $O/out/android.hardware.sensors-service.a9 $(cut -c1-16 "$O/out/android.hardware.sensors-service.a9.sha256") NEEDED: $NEED"
