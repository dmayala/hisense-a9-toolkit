#!/usr/bin/env bash
# Feed the vendor dead-man so slot B stays up (replaces the wrapper hold loop). usage: keeper.sh ADB SERIAL
while :; do
  "$1" -s "$2" shell 'touch /data/local/tmp/a9-stay; setprop vendor.a9ml.maxstay $(( $(cut -d. -f1 /proc/uptime) + 7200 ))' >/dev/null 2>&1
  sleep 60
done
