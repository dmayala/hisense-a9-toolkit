#!/usr/bin/env bash
# Host tests of the sensors HAL (no phone, no NDK). ASan + UBSan.
#  1. test_core: sensors_core against a fake SSC behind the s2_link transport
#  2. test_fmq:  the FMQ writer against a simulated libfmq reader (memfd)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SNS=$HERE/../../../sensors
O=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}/sensors-hal-test
case "$O" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2;; esac
mkdir -p "$O"
CXX=${CXX:-g++}; CC=${CC:-gcc}
command -v "$CXX" >/dev/null || { echo "no $CXX" >&2; exit 2; }
SAN="-fsanitize=address,undefined -fno-sanitize-recover=all"
for c in s2-sns.c s2-client.c; do
  "$CC" -std=c11 -O1 -g -Wall -Wextra -Werror $SAN -D_GNU_SOURCE -I"$SNS" -c "$SNS/$c" -o "$O/${c%.c}.o"
done
F="-std=c++17 -O1 -g -Wall -Wextra -Werror $SAN -pthread -I$HERE/.. -I$SNS"
"$CXX" $F -o "$O/test_core" "$HERE/test_core.cpp" "$HERE/../sensors_core.cpp" "$O/s2-sns.o" "$O/s2-client.o"
"$CXX" $F -o "$O/test_fmq" "$HERE/test_fmq.cpp" "$HERE/../fmq_writer.cpp"
"$O/test_core" > "$O/test_core.log" 2>&1 || { tail -20 "$O/test_core.log"; exit 1; }
grep '^PASS\|TESTS PASS' "$O/test_core.log"
"$O/test_fmq"
echo "SENSORS-HAL HOST TESTS PASS"
