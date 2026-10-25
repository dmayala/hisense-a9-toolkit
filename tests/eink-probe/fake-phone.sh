#!/usr/bin/env bash
# Fake `adb` for the trial3b-perf offline tests. NEVER touches a phone: `shell` payloads run in a
# local sh whose PATH has shims (getprop, settings, dumpsys, dmesg, ...) and whose /sys, /proc and
# /data/local/tmp are a temp tree under $FAKE_DIR/phone. Screencap and input reuse fake-adb.sh.
: "${FAKE_DIR:?FAKE_DIR must point at the fake phone tree}"
H=$(cd "$(dirname "$0")" && pwd)
export PATH="$FAKE_DIR/bin:$PATH" S="$FAKE_DIR/phone/sys" PROCR="$FAKE_DIR/phone/proc" FAKE_DIR
case "$*" in
  *"screencap -p"*) exec bash "$H/fake-adb.sh" "$@";;
esac
echo "$*" | cut -c1-200 >> "$FAKE_DIR/phone.log"
case "$1" in
  shell)
    shift
    if [ $# -gt 0 ]; then
      exec sh -c "$(printf '%s' "$*" | sed "s#/data/local/tmp#$FAKE_DIR/phone/data#g")"
    fi
    exec sh
    ;;
  pull) cp "${2/\/data\/local\/tmp/$FAKE_DIR/phone/data}" "$3"; exit $?;;
esac
exit 0
