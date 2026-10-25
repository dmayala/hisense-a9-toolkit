#!/usr/bin/env bash
# Offline host tests. Never starts the daemon, accesses QRTR or a target device.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
src=${A9_TQFTPSERV_SRC:-$HOME/A9-build/mainline/sol-wifi/src/tqftpserv}
qrtr=${A9_QRTR_SRC:-$HOME/A9-build/mainline/sol-wifi/src/qrtr}
cc=${CC:-cc}
nm=${NM:-nm}
command -v "$cc" >/dev/null
command -v "$nm" >/dev/null
test "$(git -C "$src" rev-parse HEAD)" = b31de6db4f31d09119293203d39636b827f16321
tmp=$(mktemp -d /tmp/a9-tqftp-tests-XXXXXX)
trap 'rm -rf "$tmp"' EXIT
cp "$src"/translate.c "$src"/translate.h "$src"/tqftpserv.c "$src"/list.h "$tmp"/
patch --batch --fuzz=0 -d "$tmp" -p1 < "$root/scripts/mainline/wifi/patches/tqftpserv.patch"
flags=(-D_GNU_SOURCE -DA9_TQFTP_SANDBOX -g -O1 -Wall -Wextra -Wno-sign-compare -Wno-unused-variable
       -I"$tmp" -I"$qrtr/include")
if [[ ${A9_SANITIZE:-0} == 1 ]]; then flags+=(-fsanitize=address,undefined -fno-omit-frame-pointer); fi
# log_err only logs; log_fault latches the sandbox fault (run 13: upstream ignores what we refused).
awk '/static void log_err\(/,/^}/' "$tmp/tqftpserv.c" | grep -q a9_fault && { echo 'log_err must not latch a fault' >&2; exit 1; }
awk '/static void log_fault\(/,/^}/' "$tmp/tqftpserv.c" | grep -q a9_fault || { echo 'log_fault must latch the fault' >&2; exit 1; }
"$cc" "${flags[@]}" -c "$tmp/translate.c" -o "$tmp/translate.o"
# The capability branch may import only non-path descriptor operations.
"$nm" -u "$tmp/translate.o" > "$tmp/translate-imports"
if awk '{print $NF}' "$tmp/translate-imports" | rg '^(open|openat|fopen|opendir|mkdir|stat|access|memfd_create|write|ftruncate|fsync)$'; then
  echo 'unexpected filesystem/path API in A9 translation object' >&2; exit 1
fi
"$cc" "${flags[@]}" "$root/tests/mainline-wifi/tqftp-translate.c" "$tmp/translate.o" -o "$tmp/translate-test"
"$cc" "${flags[@]}" "$root/tests/mainline-wifi/tqftp-protocol.c" "$tmp/translate.o" -o "$tmp/protocol-test"
"$tmp/translate-test"
"$tmp/protocol-test"
