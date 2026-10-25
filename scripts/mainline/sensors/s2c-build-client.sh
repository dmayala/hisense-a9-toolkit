#!/usr/bin/env bash
# s2c-build-client.sh -- build a9-sensor-read (the S2c NDK test client) with NDK r27, API 34.
# Offline. Output: $O/a9-sensor-read (+ .sha256); no host paths in the binary (N7 style).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
O=${1:-$HOME/A9-build/mainline/sensors-s2c/client}
case "$O" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2;; esac
: "${ANDROID_NDK_HOME:=/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}"
TC=$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64
mkdir -p "$O"
"$TC/bin/clang" --target=aarch64-linux-android34 -std=c11 -O2 -Wall -Wextra -Werror -fPIE -pie \
  -ffile-prefix-map="$REPO"=. -ffile-prefix-map="$ANDROID_NDK_HOME"=ndk \
  -o "$O/a9-sensor-read" "$HERE/a9-sensor-read.c" -landroid -llog -lm
"$TC/bin/llvm-strip" --strip-unneeded "$O/a9-sensor-read"
! grep -aqE "/home/|/nix/store/" "$O/a9-sensor-read" || { echo "host path in the binary" >&2; exit 1; }
NEED=$("$TC/bin/llvm-readelf" -d "$O/a9-sensor-read" | sed -n 's/.*Shared library: \[\(.*\)\]/\1/p' | sort | tr '\n' ' ')
[ "$NEED" = "libandroid.so libc.so libdl.so liblog.so libm.so " ] || { echo "unexpected NEEDED: $NEED" >&2; exit 1; }
(cd "$O" && sha256sum a9-sensor-read > a9-sensor-read.sha256)
echo "a9-sensor-read: $(cut -c1-16 "$O/a9-sensor-read.sha256") NEEDED: $NEED"
