#!/usr/bin/env bash
# slot A -> check -> flash integrated+MAC -> gates/Wi-Fi -> joint soak.
H=/home/user/A9-build/mainline/eink-wifi-poc; cd $H
export POC_BOOT=$H/images-mac/boot-int-mac.img POC_R4=$H/images-mac/r4
true
echo "slot A: $(/home/user/A9/scripts/a9-health.sh 2>&1 | awk '$1=="slot"{print $2}')"
python3 -B run.py --check > check3.out 2>&1
code=$(grep -oE 'these images: [0-9a-f]{8}' check3.out | cut -d' ' -f3)
[ -n "$code" ] || { echo "check failed"; tail -5 check3.out; exit 1; }
echo "check PASS $code"
python3 -B run.py --write "$code" || exit 1
: > soak2.log
./soak2.sh
