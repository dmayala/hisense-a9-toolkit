#!/usr/bin/env bash
# Offline NDK build of the read-only controller identity gate. Never runs it.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to the existing NDK}"
[ $# = 1 ] || { echo "usage: $0 OUTPUT_DIRECTORY" >&2; exit 2; }
case "$1" in *a9-assets*) echo 'refusing read-only archive output' >&2; exit 2 ;; esac
CC=$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang
[ -x "$CC" ] || { echo 'missing Android arm64 NDK compiler' >&2; exit 1; }
mkdir -p "$1"
"$CC" -std=c11 -D_GNU_SOURCE -O2 -Wall -Wextra -Werror -fPIE -pie \
    -D_FORTIFY_SOURCE=2 -fstack-protector-strong -Wl,-z,relro,-z,now \
    "$HERE/a9-bt-identity.c" -o "$1/a9-bt-identity"
