#!/usr/bin/env bash
# mesa-load-test.sh -- offline load test of the GPU vendor's Mesa set under the
# REAL Android 16 bionic linker and LLNDK libraries (qemu-aarch64 user mode),
# the A6L project's method. Reads the v7 GSI (read-only, debugfs) and the
# Cuttlefish vendor libs; writes only $GW/qemu-root. No phone. runs/gpu.md.
#
#   scripts/mainline/android/gpu/mesa-load-test.sh
#
# PASS = libEGL_mesa / libGLESv1_CM_mesa / libGLESv2_mesa / libgallium_dri and
# vulkan.adreno.so dlopen with RTLD_NOW (every relocation resolved, constructors
# run) and a9-gpu-probe render gets as far as eglInitialize, which must fail
# with EGL_NOT_INITIALIZED (0x3001) here: the host has no msm render node.
set -euo pipefail
GW=${A9_GPU_WORK:-$HOME/A9-build/mainline/gpu}
GSI=${A9_GSI_IMG:-$HOME/a9-assets/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v7.img}
CFV=${A9_CF_VENDOR:-$HOME/A9-build/mainline/android/dl/cf_arm64_x/tf/VENDOR}
NDK=${ANDROID_NDK_HOME:-/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}
CC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang
Q=$(nix-build '<nixpkgs>' -A qemu --no-out-link 2>/dev/null)/bin/qemu-aarch64
HERE=$(cd "$(dirname "$0")" && pwd)
R=$GW/qemu-root MS=${A9_MESA_OUT:-$GW/out/mesa}
case "$R" in *a9-assets*) echo "refusing" >&2; exit 2 ;; esac
[ -x "$Q" ] && [ -r "$GSI" ] && [ -d "$CFV/lib64" ] && [ -f "$MS/SHA256SUMS" ] || { echo "missing qemu, GSI, Cuttlefish vendor or Mesa build" >&2; exit 1; }
rm -rf "$R"; mkdir -p "$R/system/bin" "$R/system/lib64" "$R/vendor/lib64/egl" "$R/vendor/lib64/hw" "$R/x"
debugfs -R "dump /system/apex/com.android.runtime.apex $R/x/runtime.apex" "$GSI" 2>/dev/null
(cd "$R/x" && unzip -q runtime.apex apex_payload.img)
debugfs -R "dump /bin/linker64 $R/system/bin/linker64" "$R/x/apex_payload.img" 2>/dev/null; chmod +x "$R/system/bin/linker64"
for l in libc.so libdl.so libm.so libdl_android.so; do debugfs -R "dump /lib64/bionic/$l $R/system/lib64/$l" "$R/x/apex_payload.img" 2>/dev/null; done
for l in libhardware.so libcutils.so libutils.so libbase.so libz.so libc++.so; do cp "$CFV/lib64/$l" "$R/vendor/lib64/"; done
cp "$MS/lib64/libgallium_dri.so" "$R/vendor/lib64/"; cp "$MS"/lib64/egl/*.so "$R/vendor/lib64/egl/"; cp "$MS/lib64/hw/vulkan.adreno.so" "$R/vendor/lib64/hw/"
"$CC" -O2 -std=c11 -Wall -Wextra -Werror -fPIE -pie "$HERE/a9-gpu-probe.c" -ldl -o "$R/system/bin/a9-gpu-probe"
printf '#include <dlfcn.h>\n#include <stdio.h>\nint main(int c,char**v){int r=0;for(int i=1;i<c;i++){void*h=dlopen(v[i],RTLD_NOW|RTLD_LOCAL);printf("%%s: %%s\\n",v[i],h?"OK":dlerror());r|=!h;}return r;}\n' > "$R/x/dl.c"
"$CC" -fPIE -pie "$R/x/dl.c" -o "$R/system/bin/a9dl"
# run: output without the linker's linkerconfig warning (review 27 item 6:
# the TARGET program's exit status is returned, not grep's)
run() { local o rc; o=$(cd /tmp && timeout 120 "$Q" -L "$R" -E LD_LIBRARY_PATH=/vendor/lib64:/system/lib64 "$@" 2>&1); rc=$?
        printf '%s\n' "$o" | grep -v linkerconfig; return $rc; }
# pull the system (LLNDK + their deps) libraries the linker asks for, from the GSI
for i in $(seq 1 80); do
  m=$(run "$R/system/bin/a9dl" /vendor/lib64/egl/libEGL_mesa.so | sed -n 's/.*library "\([^"]*\)" not found.*/\1/p' | head -1 || true)
  [ -n "$m" ] || break
  debugfs -R "dump /system/lib64/$m $R/system/lib64/$m" "$GSI" 2>/dev/null
  [ -s "$R/system/lib64/$m" ] || { echo "FAIL: $m is in neither the GSI /system/lib64 nor the vendor" >&2; exit 1; }
done
rc=0
LIBS="/vendor/lib64/egl/libEGL_mesa.so /vendor/lib64/egl/libGLESv1_CM_mesa.so /vendor/lib64/egl/libGLESv2_mesa.so /vendor/lib64/libgallium_dri.so /vendor/lib64/hw/vulkan.adreno.so"
dlrc=0; out=$(run "$R/system/bin/a9dl" $LIBS) || dlrc=$?
printf '%s\n' "$out" > "$GW/mesa-load-test.log"
# exactly one "<lib>: OK" per library, nothing else, and a9dl exit 0
want=$(for l in $LIBS; do echo "$l: OK"; done)
[ "$dlrc" = 0 ] && [ "$out" = "$want" ] || { echo "dlopen check failed (rc $dlrc):"; printf '%s\n' "$out"; rc=1; }
prc=0; out=$(run "$R/system/bin/a9-gpu-probe" render /vendor/lib64/egl) || prc=$?
printf '%s\n' "$out" >> "$GW/mesa-load-test.log"
# the probe must get exactly to eglInitialize and fail there (no DRM device on the host), exit 1
printf '%s\n' "$out" | grep -qx 'A9GPU egl_error 0x3001' && printf '%s\n' "$out" | grep -qx 'A9GPU result FAIL eglInitialize' && [ "$prc" = 1 ] \
  || { echo "probe check failed (rc $prc):"; printf '%s\n' "$out"; rc=1; }
[ $rc = 0 ] && echo "mesa-load-test: PASS (all Mesa libraries load under A16 bionic; eglInitialize reached, no DRM device on the host)" || { echo "mesa-load-test: FAIL"; exit 1; }
