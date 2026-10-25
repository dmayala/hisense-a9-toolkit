#!/usr/bin/env bash
set -euo pipefail
S=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
K=${1:?usage: apply.sh PRIVATE_R1_KERNEL_TREE}
for n in ufshcd.c Kconfig; do
 p="$K/drivers/ufs/core/$n"
 if [[ -L "$p" ]]; then
  t=$(mktemp "$K/drivers/ufs/core/.rpmb-source.XXXXXX")
  cat -- "$p" > "$t"
  rm -- "$p"
  mv -- "$t" "$p"
 fi
done
patch --batch --fuzz=0 -p1 -d "$K" -i "$S/0001-ufs-query-hook.patch"
patch --batch --fuzz=0 -p1 -d "$K" -i "$S/0002-rpmb-readonly-guard.patch"
cp -- "$S"/driver/* "$K/drivers/ufs/core/"
