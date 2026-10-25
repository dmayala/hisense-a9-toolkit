#!/usr/bin/env bash
set -euo pipefail
S=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
O=${1:?usage: run.sh OUTPUT_DIRECTORY}
mkdir -p "$O"
"${HOSTCC:-gcc}" -Wall -Wextra -Werror -std=c11 "$S/rpmb-observe.c" -o "$O/rpmb-observe-host"
"$O/rpmb-observe-host"
"${CROSS_COMPILE:-aarch64-unknown-linux-gnu-}gcc" -Wall -Wextra -Werror -std=c11 -c "$S/rpmb-observe.c" -o "$O/rpmb-observe-arm64.o"
"${HOSTCC:-gcc}" -Wall -Wextra -Werror -std=c11 "$S/rpmb-gk-policy.c" -o "$O/rpmb-gk-policy-host"
"${HOSTCC:-gcc}" -Wall -Wextra -Werror -std=c11 "$S/rpmb-kmtb-policy.c" -o "$O/rpmb-kmtb-policy-host"
"$O/rpmb-kmtb-policy-host"
"${CROSS_COMPILE:-aarch64-unknown-linux-gnu-}gcc" -Wall -Wextra -Werror -std=c11 -c "$S/rpmb-kmtb-policy.c" -o "$O/rpmb-kmtb-policy-arm64.o"
"$O/rpmb-gk-policy-host"
"${CROSS_COMPILE:-aarch64-unknown-linux-gnu-}gcc" -Wall -Wextra -Werror -std=c11 -c "$S/rpmb-gk-policy.c" -o "$O/rpmb-gk-policy-arm64.o"
python3 -I "$S/listener.py" "$O"
python3 -I "$S/bootstrap.py" "$O"
"${HOSTCC:-gcc}" -Wall -Wextra -Werror -std=c11 "$S/abi.c" -o "$O/abi-host"
"$O/abi-host"
"${CROSS_COMPILE:-aarch64-unknown-linux-gnu-}gcc" -Wall -Wextra -Werror -std=c11 -c "$S/abi.c" -o "$O/abi-arm64.o"
printf '%s\n' 'PASS: ARM64 compiler independently accepted all ABI assertions'
"${HOSTCC:-gcc}" -Wall -Wextra -Werror -std=c11 "$S/bounds.c" -o "$O/bounds-host"
"$O/bounds-host"
"${CROSS_COMPILE:-aarch64-unknown-linux-gnu-}gcc" -Wall -Wextra -Werror -std=c11 -c "$S/bounds.c" -o "$O/bounds-arm64.o"
"${HOSTCC:-gcc}" -Wall -Wextra -Werror -std=c11 "$S/log-ring.c" -o "$O/log-ring-host"
"$O/log-ring-host"
"${CROSS_COMPILE:-aarch64-unknown-linux-gnu-}gcc" -Wall -Wextra -Werror -std=c11 -c "$S/log-ring.c" -o "$O/log-ring-arm64.o"
