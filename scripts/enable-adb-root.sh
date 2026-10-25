#!/usr/bin/env bash
#
# enable-adb-root.sh — turn adb + root back on in an installed GSI, from TWRP.
#
# Why this is needed: Damian's patch_system_img.py deliberately sets
#   ro.build.type=user   ro.debuggable=0   ro.secure=1   ro.build.tags=release-keys
# in /system/build.prop to defeat root detection in banking apps. On a freshly
# flashed GSI with a wiped /data that also means:
#   * adbd does not start by default (no userdebug, no ADB_ENABLED setting)
#   * `adb root` is refused
#   * you cannot reach the device at all if the panel is not yet working
# which is a chicken-and-egg: the documented first-boot fix needs scrcpy, and
# scrcpy needs adb.
#
# Run this with the device in TWRP.  Re-run safe (idempotent).
#
# Usage:  ./enable-adb-root.sh [--revert]
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
BP="/system_root/system/build.prop"

state() { a9_adb_state; }

if [ "$(state)" != "recovery" ]; then
  echo "Device is not in TWRP (state='$(state)'). Boot recovery first." >&2
  exit 1
fi

if [ "${1:-}" = "--revert" ]; then
  declare -A PROPS=(
    [ro.build.type]=user
    [ro.debuggable]=0
    [ro.secure]=1
    [ro.adb.secure]=1
  )
  echo "Reverting to root-detection-evading values..."
else
  declare -A PROPS=(
    [ro.build.type]=userdebug
    [ro.debuggable]=1
    [ro.secure]=0
    [ro.adb.secure]=0
    [persist.sys.usb.config]=adb
  )
  echo "Enabling adb + root..."
fi

"$ADB" shell "twrp mount system" >/dev/null 2>&1
"$ADB" shell "mount -o remount,rw /system_root" >/dev/null 2>&1

if ! "$ADB" shell "[ -f $BP ] && echo ok" 2>/dev/null | tr -d '\r' | grep -q ok; then
  echo "ERROR: $BP not found — is system mounted / is a GSI installed?" >&2
  exit 1
fi

"$ADB" shell "cp -n $BP ${BP}.a9bak" >/dev/null 2>&1

for k in "${!PROPS[@]}"; do
  v="${PROPS[$k]}"
  # Replace in place if present, otherwise append. Escape . in the key.
  ek=$(printf '%s' "$k" | sed 's/\./\\./g')
  "$ADB" shell "
    if grep -q '^$ek=' $BP; then
      sed -i 's|^$ek=.*|$k=$v|' $BP
    else
      echo '$k=$v' >> $BP
    fi
  " >/dev/null 2>&1
  printf '  %-28s -> %s\n' "$k" "$v"
done

"$ADB" shell "sync" >/dev/null 2>&1

echo
echo "Result in $BP:"
"$ADB" shell "grep -E '^(ro\.build\.type|ro\.debuggable|ro\.secure|ro\.adb\.secure|persist\.sys\.usb\.config)=' $BP" 2>/dev/null | tr -d '\r' | sed 's/^/  /'
echo
echo "Backup of the original: ${BP}.a9bak"
echo "Now exit TWRP with ./twrp.sh exit"
