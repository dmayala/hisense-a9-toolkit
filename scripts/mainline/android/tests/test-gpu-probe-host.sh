#!/usr/bin/env bash
# test-gpu-probe-host.sh -- host ASan test of a9-gpu-probe's EGL lifetime
# (review 27 item 5): the probe, built for the host with AddressSanitizer,
# runs `render` against gpu-probe-fake-egl.c, whose eglTerminate frees the
# renderer string. PASS = probe PASS and no ASan report. No device access.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
NDK=${ANDROID_NDK_HOME:-/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/inc" "$T/egl"
for d in EGL GLES2 KHR; do cp -r --no-preserve=mode "$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/include/$d" "$T/inc/"; done
nix-shell -p gcc --run "
  gcc -O1 -g -fPIC -shared -I'$T/inc' -DUSE_OZONE '$HERE/tests/gpu-probe-fake-egl.c' -o '$T/egl/libEGL_mesa.so' &&
  ln -s libEGL_mesa.so '$T/egl/libGLESv2_mesa.so' &&
  gcc -O1 -g -std=c11 -fsanitize=address,undefined -fno-omit-frame-pointer -I'$T/inc' -DUSE_OZONE '$HERE/gpu/a9-gpu-probe.c' -ldl -o '$T/probe' &&
  ASAN_OPTIONS=detect_leaks=0 '$T/probe' render '$T/egl'" > "$T/out" 2>&1
rc=$?
# the padding classification (G1 attempt 2): unit test of pad_allowed()
nix-shell -p gcc --run "gcc -O1 -std=c11 -DA9_GPU_PROBE_UNIT -fsanitize=address,undefined -I'$T/inc' -DUSE_OZONE '$HERE/gpu/a9-gpu-probe.c' -ldl -o '$T/unit' && '$T/unit'" > "$T/unit.out" 2>&1 \
  && grep -qx 'pad_allowed unit: PASS' "$T/unit.out" && echo "  ok   pad_allowed(): 16x4 store band allowed, everything else outside W x H refused" \
  && grep -qx 'sysfs_is_vkms unit: PASS' "$T/unit.out" && echo "  ok   sysfs_is_vkms(): vkms picked by sysfs path, a9_eink/msm cards never opened (review 38 R5)" \
  || { cat "$T/unit.out"; rc=1; }
if [ $rc = 0 ] && grep -q 'A9GPU result PASS render' "$T/out" && ! grep -q 'ERROR: AddressSanitizer' "$T/out"; then
  echo "  ok   probe render: PASS against a fake EGL that frees glGetString storage at eglTerminate, no ASan report"
  echo "test-gpu-probe-host: PASS"
else
  sed 's/^/     /' "$T/out" | tail -30; echo "test-gpu-probe-host: FAIL"; exit 1
fi
