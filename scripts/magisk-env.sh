#!/usr/bin/env bash
#
# magisk-env.sh — after a /data wipe, give Magisk its /data/adb/magisk so `su`
# works again, WITHOUT the app's "Requires Additional Setup" (which re-flashes
# the boot partition).
#
# Why: boot_b is Magisk-patched (docs/root-magisk.md), so magiskd starts on a
# fresh /data and even installs its stub manager. But post-fs-data aborts with
# an incomplete environment while /data/adb/magisk is empty, so no su is ever
# mounted: `su` -> "inaccessible or not found". Seen 2026-10-11 after the
# Doze-off GAPPS wipe. The cure is the same files Direct Install copies.
#
# What it does:
#   1. extracts magisk/magiskinit/magiskboot/magiskpolicy/init-ld/busybox
#      (lib/arm64-v8a/lib*.so) and boot_patch.sh/util_functions.sh/stub.apk/
#      addon.d.sh/app_functions.sh (assets/) from the official APK on the host
#   2. twrp.sh enter; pushes them to /data/adb/magisk (root:root 0755,
#      restorecon /data/adb -> adb_data_file); twrp.sh exit
# Then run `los-skip-setup.sh root` to install the full manager and grant
# root to the adb shell (after the setup wizard: Magisk denies su while it
# is in front).
#
# Usage:
#   ./magisk-env.sh            # check: does this phone need it?
#   ./magisk-env.sh --apply    # do it (reboots to TWRP and back)
#   MAGISK_APK=<apk>           # default: newest Magisk-v*.apk under $A9_DATA
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

APK="${MAGISK_APK:-$(find "$A9_DATA/" -maxdepth 4 -type f -name 'Magisk-v*.apk' 2>/dev/null | sort -V | tail -1)}"
[ -f "$APK" ] || { echo "ERROR: no Magisk APK (set MAGISK_APK=)" >&2; exit 1; }

[ "$(a9_adb_state)" = "device" ] || { echo "ERROR: need booted Android on adb." >&2; exit 1; }
su_out=$("$ADB" shell 'su -c id' 2>&1 | tr -d '\r' || true)
magiskd=$("$ADB" shell 'ps -A -o NAME' 2>/dev/null | grep -c '^magiskd' || true)
echo "magiskd running : $([ "$magiskd" -gt 0 ] && echo yes || echo no)"
echo "su              : $su_out"
case "$su_out" in
  *uid=0*)               echo "su already works; nothing to do."; exit 0 ;;
  *"Permission denied"*) echo "su exists but is denied: run ./los-skip-setup.sh root (after the setup wizard)."; exit 0 ;;
esac
[ "$magiskd" -gt 0 ] || { echo "ERROR: magiskd is not running; boot is not Magisk-patched?" >&2; exit 1; }
[ "${1:-}" = "--apply" ] || { echo "Needs the environment. Run: $0 --apply"; exit 0; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/x" "$T/env"
unzip -q -o "$APK" 'lib/arm64-v8a/*' 'assets/*' -d "$T/x"
for b in magisk magiskinit magiskboot magiskpolicy init-ld busybox; do
  cp "$T/x/lib/arm64-v8a/lib$b.so" "$T/env/$b"
done
for a in boot_patch.sh util_functions.sh stub.apk addon.d.sh app_functions.sh; do
  cp "$T/x/assets/$a" "$T/env/$a"
done
echo "environment from $(basename "$APK"): $(ls "$T/env" | tr '\n' ' ')"

a9_usb_lock_inherit
"$HERE/twrp.sh" enter
"$ADB" shell 'ls /data/adb >/dev/null' || { echo "ERROR: /data not mounted/decrypted in TWRP" >&2; exit 1; }
"$ADB" shell 'mkdir -p /data/adb/magisk'
for f in "$T"/env/*; do "$ADB" push "$f" /data/adb/magisk/ >/dev/null; done
# restorecon is right for /data/adb (file_contexts: adb_data_file). Do NOT use
# TWRP's restorecon on app data (/data/data/...): it applies file_contexts, not
# seapp_contexts, and mislabels it system_data_file (docs/twrp-port-findings.md).
"$ADB" shell 'chmod -R 755 /data/adb/magisk; chown -R 0:0 /data/adb/magisk; restorecon -R /data/adb >/dev/null 2>&1; ls -lZ /data/adb/magisk' | sed 's/^/   /'
"$HERE/twrp.sh" exit
a9_wait_stable device 300 || true
echo "Done. After the setup wizard: ./los-skip-setup.sh root"
