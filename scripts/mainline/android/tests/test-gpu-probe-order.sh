#!/usr/bin/env bash
# test-gpu-probe-order.sh -- host check of the GPU probe ordering vs the
# kernel's deferred-probe timeout (G1 attempt 1, runs/gpu.md). Uses the GPU
# kernel snapshot: the real config/DTB/module lists must PASS, and three
# injected regressions must FAIL: gpucc as a module (the attempt-1 config),
# dispcc loaded after msm (E Ink list), and msm without any GPU supplier
# order (gpucc module listed after msm).
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
K=${A9_GPU_SNAP:-$HOME/A9-build/mainline/gpu/android/kernel-snap-gpu}
C=$HERE/gpu/check-probe-order.py DTB=$K/sm6115-hisense-hlte556n-android-gpu.dtb
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
expect() { local want=$1 name=$2; shift 2; python3 -I "$C" "$@" > "$T/out" 2>&1; local rc=$?
  if { [ "$want" = PASS ] && [ $rc = 0 ]; } || { [ "$want" = FAIL ] && [ $rc != 0 ]; }; then echo "  ok   $name: $want"
  else echo "  FAIL $name: wanted $want, rc $rc"; sed 's/^/     /' "$T/out"; fails=$((fails+1)); fi; }
expect PASS "vkms image (modules.gpu)" "$DTB" "$K/config" "$K/modules/modules.gpu"
expect PASS "E Ink image (modules.load, + MDSS)" "$DTB" "$K/config" "$K/modules/modules.load" --mdss
sed 's/^CONFIG_SM_GPUCC_6115=y/CONFIG_SM_GPUCC_6115=m/' "$K/config" > "$T/cfg-m"
{ echo gpucc-sm6115.ko; cat "$K/modules/modules.gpu"; } > "$T/list-m"
expect FAIL "attempt-1 replay: gpucc module, SMMU built in" "$DTB" "$T/cfg-m" "$T/list-m"
{ grep -v dispcc "$K/modules/modules.load"; echo dispcc-sm6115.ko; } > "$T/list-late"
expect FAIL "dispcc after msm (E Ink list)" "$DTB" "$K/config" "$T/list-late" --mdss
{ cat "$K/modules/modules.gpu"; echo gpucc-sm6115.ko; } > "$T/list-gpucc-late"
expect FAIL "gpucc module after msm" "$DTB" "$T/cfg-m" "$T/list-gpucc-late"
[ $fails = 0 ] && echo "test-gpu-probe-order: PASS" || { echo "test-gpu-probe-order: $fails FAILED"; exit 1; }
