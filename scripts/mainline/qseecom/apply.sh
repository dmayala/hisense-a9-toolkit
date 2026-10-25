#!/usr/bin/env bash
set -euo pipefail
S=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
K=${1:?usage: apply.sh PRIVATE_STAGE2_KERNEL_TREE}
# Shared integration trees may contain source symlinks. Materialize ONLY these
# five touched files before patching, so no archived/shared source is modified.
for n in qcom_scm.c qcom_scm-smc.c qcom_scm.h Kconfig Makefile; do
 p="$K/drivers/firmware/qcom/$n"
 if [[ -L "$p" ]]; then
  t=$(mktemp "$K/drivers/firmware/qcom/.qsee-source.XXXXXX")
  cat -- "$p" > "$t"
  rm -- "$p"
  mv -- "$t" "$p"
 fi
done
patch --batch --fuzz=0 -p1 -d "$K" -i "$S/0001-qseecom-android-hook.patch"
patch --batch --fuzz=0 -p1 -d "$K" -i "$S/0002-a9-qseecom-resource-votes.patch"
patch --batch --fuzz=0 -p1 -d "$K" -i "$S/0003-rpmb-register-diagnostics.patch"
cp -- "$S"/driver/* "$K/drivers/firmware/qcom/"
