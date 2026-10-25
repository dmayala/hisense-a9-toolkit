#!/usr/bin/env bash
# Host-only; same NDK/binder cache as the integrated thermal HAL.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
O=${A9_VIBRATION_OUT:-$HOME/A9-build/mainline/vibration-20261020}
CACHE=${A9_BINDER_CACHE:-$HOME/A9-build/mainline/sensors}
NDK=${ANDROID_NDK_HOME:-/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}
AIDL=${A9_AIDL:-/nix/store/iqyx3zkviyc1ik9snyhhw77aanc4b3zx-android-sdk-build-tools-36.0.0/libexec/android-sdk/build-tools/36.0.0/aidl}
A=$O/aosp/aidl_api/android.hardware.vibrator/3
B=$CACHE/aosp/binder-ndk
TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64
mkdir -p "$O/hal-gen/include" "$O/hal-gen/src" "$O/out"
if [ ! -f "$A/.hash" ]; then
  curl -fL -o "$O/vibrator-aidl.tgz" 'https://android.googlesource.com/platform/hardware/interfaces/+archive/refs/heads/android16-release/vibrator/aidl.tar.gz'
  mkdir -p "$O/aosp"
  tar -xzf "$O/vibrator-aidl.tgz" -C "$O/aosp"
fi
if [ ! -s "$O/aidl-imports/android/os/PersistableBundle.aidl" ]; then
  mkdir -p "$O/aidl-imports/android/os"
  curl -fL 'https://android.googlesource.com/platform/frameworks/native/+/refs/heads/android16-release/aidl/binder/android/os/PersistableBundle.aidl?format=TEXT' | base64 -d > "$O/aidl-imports/android/os/PersistableBundle.aidl"
fi
(cd "$A" && "$AIDL" --lang=ndk --structured --stability=vintf --version=3 --hash="$(cat .hash)" \
  --min_sdk_version=35 -I . -I "$O/aidl-imports" -h "$O/hal-gen/include" -o "$O/hal-gen/src" $(find android -name '*.aidl' | sort))
"$TC/bin/clang++" --target=aarch64-linux-android35 -std=c++17 -O2 -Wall -Wextra -Werror \
  -Wno-unused-parameter -Wno-deprecated-declarations -fPIE -DBINDER_STABILITY_SUPPORT \
  -I"$B/include_platform" -I"$B/include_cpp" -I"$B/include_ndk" -I"$O/hal-gen/include" \
  -ffunction-sections -fdata-sections -pie -static-libstdc++ -Wl,--gc-sections \
  -Wl,--allow-shlib-undefined -Wl,-z,relro,-z,now -L"$CACHE/gsi-libs" \
  -o "$O/out/android.hardware.vibrator-service.a9" "$HERE/Vibrator.cpp" \
  $(find "$O/hal-gen/src" -name '*.cpp' | sort) -lbinder_ndk -llog
"$TC/bin/llvm-strip" --strip-unneeded "$O/out/android.hardware.vibrator-service.a9"
(cd "$O/out" && sha256sum android.hardware.vibrator-service.a9 > android.hardware.vibrator-service.a9.sha256)
