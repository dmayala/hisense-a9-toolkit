#!/usr/bin/env bash
#
# twrp-restore.sh — put a twrp-backup.sh backup back, keeping the phone's data
# as it was at backup time.
#
#   CHECK (default, no phone writes):
#     the backup's manifest and TWRP digests verify on the NAS; the running
#     phone has the SAME platform key as at backup time (else the restored
#     /data would bootloop: same rule as a9-compat.sh); shows the backup's date:
#     everything newer on the phone is lost by a restore.
#   --write:
#     enter TWRP; make sure the backup folder is on the phone (push it back from
#     the NAS if it is not); `twrp restore <name> DB` (Data + Boot; TWRP wipes
#     /data except internal storage first); twrp.sh exit; wait for Android;
#     compare packages, user apps, accounts, home app and app-data counts with
#     the backup's reference.
#   metadata/misc are NOT written: on the same phone without a format since the
#   backup they are unchanged. After a format (new keys) this restore path is
#   unproven: see docs/twrp-backup.md.
#   --storage: also push internal storage back, from Android via /sdcard (needed
#   after a format; normally untouched by a restore).
#
# Usage: twrp-restore.sh <name> [--write] [--storage]
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

NAME=""; WRITE=0; STORAGE=0
for a in "$@"; do
  case "$a" in --write) WRITE=1 ;; --storage) STORAGE=1 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option $a" >&2; exit 2 ;; *) NAME="$a" ;; esac
done
OUT="$A9_BACKUPS/twrp/$NAME"
[ -n "$NAME" ] && [ -d "$OUT" ] || { echo "usage: $0 <name> [--write]; backups: $(ls "$A9_BACKUPS/twrp" 2>/dev/null | tr '\n' ' ')" >&2; exit 2; }
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok   $*"; }

echo "== checks for $NAME"
( cd "$OUT" && sha256sum -c --quiet manifest.txt ) || fail "manifest does not verify"
ok "manifest verifies ($(wc -l < "$OUT/manifest.txt") files)"
for f in "$OUT"/twrp/*.sha2; do
  [ "$(awk '{print $1}' "$f")" = "$(sha256sum "${f%.sha2}" | awk '{print $1}')" ] || fail "TWRP digest mismatch: $(basename "${f%.sha2}")"
done
ok "TWRP digests verify"
echo "  ..   taken $(sed -n 's/^taken: //p' "$OUT/reference/backup.txt"): anything changed on the phone since then is LOST by a restore"

key_of() {  # framework platform signer hashes from a mac_permissions file
  python3 -I -c '
import re,sys,hashlib
x=open(sys.argv[1]).read()
print(" ".join(sorted(hashlib.sha256(bytes.fromhex(s)).hexdigest()[:16] for s,b in re.findall(r"<signer signature=\"([0-9a-fA-F]+)\"\s*>(.*?)</signer>",x,re.S) if "value=\"platform\"" in b)))' "$1"
}
S=$(a9_adb_state)
if [ -s "$OUT/reference/plat_mac_permissions.xml" ]; then
  [ "$S" = device ] || fail "phone must be in Android for the platform-key check (adb state '${S:-none}')"
  T=$(mktemp); a9_su 'cat /system/etc/selinux/plat_mac_permissions.xml' > "$T" 2>/dev/null
  if [ -s "$T" ]; then
    [ "$(key_of "$T")" = "$(key_of "$OUT/reference/plat_mac_permissions.xml")" ] || { rm -f "$T"; fail "platform key differs from the backup's: the restored /data would not boot on this build"; }
    ok "same platform key as at backup time"
  else
    # No root (e.g. a fresh /data after a format: no Magisk app yet). framework-res.apk
    # is world-readable; its signing cert is the platform key /data is bound to.
    "$ADB" pull /system/framework/framework-res.apk "$T" >/dev/null 2>&1 || { rm -f "$T"; fail "cannot read framework-res.apk for the platform-key check"; }
    python3 -I - "$T" "$OUT/reference/plat_mac_permissions.xml" <<'PY' || { rm -f "$T"; fail "platform key differs from the backup's (framework-res cert): the restored /data would not boot on this build"; }
import re, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
rsa = b"".join(z.read(n) for n in z.namelist() if re.match(r"META-INF/.*\.(RSA|EC|DSA)$", n))
x = open(sys.argv[2]).read()
plat = [bytes.fromhex(s) for s, b in re.findall(r'<signer signature="([0-9a-fA-F]+)"\s*>(.*?)</signer>', x, re.S) if 'value="platform"' in b]
sys.exit(0 if any(d in rsa for d in plat) else 1)
PY
    ok "same platform key as at backup time (framework-res cert; no root yet)"
  fi
  rm -f "$T"
else
  echo "  --   backup has no Android reference: platform key NOT checked"
fi
[ "$WRITE" = 1 ] || { echo "Check only. Re-run with --write to restore Data + Boot."; exit 0; }

a9_usb_lock
a9_run_dir twrp-restore "$NAME"
exec > >(tee -a "$A9_RUN/log.txt") 2>&1
echo "== restore $NAME (run $A9_RUN)"
[ "$S" = recovery ] || { "$HERE/twrp.sh" enter >/dev/null || fail "could not enter TWRP"; }
a9_wait_stable recovery 120 || fail "TWRP adb not stable"
# TWRP starts MTP a few seconds after it decrypts /data, which re-enumerates USB
# and drops adb (seen 2026-10-14: the first `twrp backup` hit "device not found").
# Wait for "MTP Enabled" in its log, then for adb to be stable again.
for _ in $(seq 1 30); do "$ADB" shell 'grep -q "MTP Enabled" /tmp/recovery.log' </dev/null 2>/dev/null && break; sleep 2; done
sleep 3; a9_wait_stable recovery 120 || fail "TWRP adb not stable after MTP start"
# twrp_cmd <args>: run a TWRP command, once more if adb dropped before it started
twrp_cmd() {
  local out; out=$("$ADB" shell "twrp $*" </dev/null 2>&1 | tr -d '\r')
  case "$out" in *"not found"*|*"no devices"*) a9_wait_stable recovery 120; out=$("$ADB" shell "twrp $*" </dev/null 2>&1 | tr -d '\r') ;; esac
  printf '%s\n' "$out"
}
"$ADB" shell 'grep -q " /data " /proc/mounts' </dev/null || fail "/data is not mounted (decrypted) in TWRP"

BDIR=$(cat "$OUT/reference/twrp-path.txt")
have=$("$ADB" shell "cd $BDIR 2>/dev/null && for f in *.sha2; do cat \$f; done" </dev/null | tr -d '\r' | awk '{print $1}' | sort | tr '\n' ' ')
want=$(cat "$OUT"/twrp/*.sha2 | awk '{print $1}' | sort | tr '\n' ' ')
if [ "$have" != "$want" ]; then
  echo "-- backup not on the phone (or different): pushing it back"
  "$ADB" shell "mkdir -p $BDIR" </dev/null
  "$ADB" push "$OUT/twrp/." "$BDIR/" >/dev/null || fail "push of the backup failed"
fi
ok "backup present on the phone: $BDIR"

echo "-- twrp restore $NAME DB (several minutes)"
twrp_cmd restore "$BDIR" DB | tail -6
"$ADB" shell 'grep -E "E:|Error|Failed|failed" /tmp/recovery.log | tail -5' </dev/null | tr -d '\r'
"$HERE/twrp.sh" exit >/dev/null || fail "twrp.sh exit failed"
a9_wait_boot 900 || fail "Android did not finish booting after the restore: follow the a9-recover skill (ask the user before any wipe)"
sleep "${A9_SETTLE:-30}"
if [ "$STORAGE" = 1 ]; then
  # From Android through /sdcard (FUSE), so files get media_rw ownership and labels;
  # a push into /data/media/0 from TWRP leaves them root-owned.
  echo "-- pushing internal storage back (via /sdcard)"
  # Per entry, skipping MediaProvider's own caches: adb cannot create them through
  # FUSE ("Machine is not on the network") and a failure stops a whole-folder push.
  for e in $(ls -A "$OUT/internal"); do
    case "$e" in .transforms|.picker_transcoded|.thumbnails) continue ;; esac
    "$ADB" push "$OUT/internal/$e" /sdcard/ >/dev/null 2>&1 || echo "   WARN: could not push $e"
  done
fi

echo "-- comparing with the backup's reference"
R="$OUT/reference"; C="$A9_RUN/after"; mkdir -p "$C"; rc=0
"$ADB" shell 'pm list packages --show-versioncode' </dev/null | tr -d '\r' | sort > "$C/packages.txt"
"$ADB" shell 'pm list packages -3' </dev/null | tr -d '\r' | sort > "$C/packages-user.txt"
"$ADB" shell 'dumpsys account | grep -E "^ +Account \{" | sed "s/name=[^,]*/name=<redacted>/"' </dev/null | tr -d '\r' | sort > "$C/accounts.txt"
"$ADB" shell 'cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN | tail -1' </dev/null | tr -d '\r' > "$C/home.txt"
a9_su 'ls /data/data | wc -l; ls /data/user_de/0 | wc -l' > "$C/appdata-counts.txt"
for f in packages.txt packages-user.txt accounts.txt home.txt appdata-counts.txt; do
  [ -f "$R/$f" ] || continue
  if diff -q "$R/$f" "$C/$f" >/dev/null; then echo "  same  $f"; else echo "  DIFF  $f"; diff "$R/$f" "$C/$f" | head -6 | sed 's/^/        /'; rc=1; fi
done
"$HERE/a9-health.sh" --json > "$A9_RUN/health-after.json"
[ "$rc" = 0 ] && echo "RESULT: restored $NAME; phone matches the backup's reference" \
             || echo "RESULT: restored $NAME; some reference items differ (see above)"
exit "$rc"
