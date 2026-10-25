#!/usr/bin/env bash
set -euo pipefail
S=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
O=${1:?usage: run.sh OUTPUT_DIRECTORY}
mkdir -p "$O"
"${HOSTCC:-gcc}" -Wall -Wextra -Werror -std=c11 "$S/ioctl.c" -o "$O/ufs-ioctl-host"
"$O/ufs-ioctl-host"
"${HOSTCC:-gcc}" -Wall -Wextra -Werror -std=c11 "$S/readonly.c" -o "$O/rpmb-readonly-host"
"$O/rpmb-readonly-host"
"${CROSS_COMPILE:-aarch64-unknown-linux-gnu-}gcc" -Wall -Wextra -Werror -std=c11 -c "$S/ioctl.c" -o "$O/ufs-ioctl-arm64.o"
"${CROSS_COMPILE:-aarch64-unknown-linux-gnu-}gcc" -Wall -Wextra -Werror -std=c11 -c "$S/readonly.c" -o "$O/rpmb-readonly-arm64.o"
printf '%s\n' 'PASS: ARM64 compiler independently accepted stock 8-byte ABI'
