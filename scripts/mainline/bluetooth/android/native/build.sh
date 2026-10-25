#!/usr/bin/env bash
set -euo pipefail
# Glob order (gen/src/*.cpp) is link order: pin the locale or the binary changes.
export LC_ALL=C
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
NDK=${ANDROID_NDK_HOME:-/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}
AIDL=${AIDL:-/nix/store/svi7ppb6b21xqajvfc8598x9amwdk8lm-android-sdk-build-tools-35.0.0/libexec/android-sdk/build-tools/35.0.0/aidl}
API=$HERE/source/bluetooth/aidl/aidl_api/android.hardware.bluetooth/1
HASH=f0eb2eb6d3b970cef8fb1b6e22c4ec39a4da5119
python3 "$HERE/verify-source.py"
mkdir -p "$HERE/gen/include" "$HERE/gen/src" "$HERE/build" "$HERE/bundle/bin/hw"
"$AIDL" --lang=ndk --structured --stability=vintf --version=1 --hash="$HASH" --min_sdk_version=33 \
    --omit_invocation -I"$API" -h"$HERE/gen/include" -o"$HERE/gen/src" "$API"/android/hardware/bluetooth/*.aidl
TOOL=$NDK/toolchains/llvm/prebuilt/linux-x86_64
# NDK exports only the app API subset of libbinder_ndk. The real target-files
# platform library exports ServiceManager/threadpool entry points too.
BINDER=${A9_BINDER_NDK:-/home/user/A9-build/mainline/android/dl/cf_arm64_x/tf/VENDOR_BOOT/RAMDISK/system/lib64/libbinder_ndk.so}
"$TOOL/bin/aarch64-linux-android33-clang++" -std=c++17 -Wall -Wextra -Werror -O2 -g \
    -fPIE -pie -fstack-protector-strong -D_FORTIFY_SOURCE=2 -DBINDER_STABILITY_SUPPORT -D__ANDROID_VENDOR__ \
    -ffile-prefix-map="$HERE"=. \
    -I"$HERE/gen/include" -I"$HERE/source/platform-include" \
    "$HERE/a9_bluetooth_hal.cpp" "$HERE"/gen/src/android/hardware/bluetooth/*.cpp \
    "$BINDER" -llog -static-libstdc++ -Wl,--build-id=sha1,-z,relro,-z,now \
    -o "$HERE/bundle/bin/hw/android.hardware.bluetooth-service.default"
"$TOOL/bin/llvm-readelf" -h -d "$HERE/bundle/bin/hw/android.hardware.bluetooth-service.default" > "$HERE/build/elf.txt"
sha256sum "$HERE/bundle/bin/hw/android.hardware.bluetooth-service.default"

# Produce a complete actual-output bundle after every successful offline build.
python3 -I - "$HERE" "$NDK" "$AIDL" "$BINDER" <<'PYBUNDLE'
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1])
sha=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
binary=root/'bundle/bin/hw/android.hardware.bluetooth-service.default'
manifest={"schema":1,"transport":"linux-hci-user","android_release":"16",
 "source_build":{
  "hardware_interfaces_revision":"5688f7eb1e117ed26e642a695de300b7683acb87",
  "frameworks_native_revision":"2827a4a16b0340ecd07c2d5a6c89991799b362bb",
  "build_command":"bash scripts/mainline/bluetooth/android/native/build.sh; NDK arm64 API33; frozen AIDL V1; static libc++",
  "source_review":"native/README.md; Linux-only NDK session implementation, actual frozen AIDL dispatch/callback proxy; controller and pre/post USER-bind identity admission; runtime tests pending",
  "transport_failure_returns":True,"uart_fallback_disabled":True,
  "frozen_aidl_v1_hash":"f0eb2eb6d3b970cef8fb1b6e22c4ec39a4da5119",
  "source_manifest_sha256":sha(root/'source-manifest.json'),
  "implementation_sha256":sha(root/'a9_bluetooth_hal.cpp'),
  "packet_validator_sha256":sha(root/'hci_packets.h'),
  "build_script_sha256":sha(root/'build.sh'),
  "aidl_generator_sha256":sha(sys.argv[3]),
  "link_input_binder_ndk_sha256":sha(sys.argv[4])},
 "files":{"bin/hw/android.hardware.bluetooth-service.default":sha(binary)}}
(root/'bundle/manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
PYBUNDLE
