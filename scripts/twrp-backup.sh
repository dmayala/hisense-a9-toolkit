#!/usr/bin/env bash
#
# twrp-backup.sh — a TWRP backup that keeps your data, copied to the NAS.
#
# What it saves (one folder per backup, $A9_BACKUPS/twrp/<name>/):
#   twrp/          TWRP's own backup of Data (excl. internal storage) + Boot,
#                  compressed, with TWRP's per-file digests (`twrp backup DBO`)
#   raw/           metadata.img and misc.img (raw dd, for the record: on a
#                  restore to the SAME phone without a format in between they
#                  have not changed; see twrp-restore.sh)
#   internal/      internal storage (photos, downloads, ...), unless --no-storage:
#                  TWRP's Data backup leaves it out
#   reference/     what the phone ran and held at backup time (build, platform
#                  key, app list, accounts, home app): twrp-restore.sh refuses a
#                  platform-key mismatch and compares against this after a restore
#   manifest.txt   sha256 of every file
#
# The OpenRecoveryScript `backup` command cannot select metadata or misc (only
# S D C R B A E letters; openrecoveryscript.cpp), hence the raw copies.
# Needs: phone in Android (preferred: the reference is taken there) or TWRP.
#
# Usage: twrp-backup.sh [name] [--no-storage] [--stay]   (default name golden-<UTC>)
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

NAME=""; STORAGE=1; STAY=0
for a in "$@"; do
  case "$a" in
    --no-storage) STORAGE=0 ;; --stay) STAY=1 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option $a" >&2; exit 2 ;; *) NAME="$a" ;;
  esac
done
NAME="${NAME:-golden-$(date -u +%Y%m%dT%H%M%SZ)}"
case "$NAME" in *[!A-Za-z0-9._-]*) echo "name: letters, digits, . _ - only" >&2; exit 2 ;; esac
OUT="$A9_BACKUPS/twrp/$NAME"
[ -e "$OUT" ] && { echo "ERROR: $OUT already exists" >&2; exit 1; }
fail() { echo "FAIL: $*" >&2; exit 1; }

a9_usb_lock
a9_run_dir twrp-backup "$NAME"
exec > >(tee -a "$A9_RUN/log.txt") 2>&1
mkdir -p "$OUT/twrp" "$OUT/raw" "$OUT/reference" || fail "cannot create $OUT"
echo "== backup $NAME -> $OUT (run $A9_RUN)"

# ---- reference, taken in Android (root via Magisk)
S=$(a9_adb_state)
if [ "$S" = device ]; then
  echo "-- reference (Android)"
  a9_su 'cat /system/etc/selinux/plat_mac_permissions.xml' > "$OUT/reference/plat_mac_permissions.xml"
  "$ADB" shell 'for p in ro.lineage.version ro.build.display.id ro.build.version.sdk_full ro.build.version.security_patch ro.boot.slot_suffix; do echo "$p=$(getprop $p)"; done' </dev/null | tr -d '\r' > "$OUT/reference/props.txt"
  "$ADB" shell 'pm list packages --show-versioncode' </dev/null | tr -d '\r' | sort > "$OUT/reference/packages.txt"
  "$ADB" shell 'pm list packages -3' </dev/null | tr -d '\r' | sort > "$OUT/reference/packages-user.txt"
  "$ADB" shell 'dumpsys account | grep -E "^ +Account \{" | sed "s/name=[^,]*/name=<redacted>/"' </dev/null | tr -d '\r' | sort > "$OUT/reference/accounts.txt"
  "$ADB" shell 'cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN | tail -1' </dev/null | tr -d '\r' > "$OUT/reference/home.txt"
  a9_su 'ls /data/data | wc -l; ls /data/user_de/0 | wc -l' > "$OUT/reference/appdata-counts.txt"
  echo "   $(wc -l < "$OUT/reference/packages.txt") packages ($(wc -l < "$OUT/reference/packages-user.txt") user), home $(cat "$OUT/reference/home.txt")"
  echo "-- entering TWRP"
  "$HERE/twrp.sh" enter >/dev/null || fail "could not enter TWRP"
elif [ "$S" = recovery ]; then
  echo "-- already in TWRP: no Android reference taken (restore will skip the comparison)"
else
  fail "phone must be in Android or TWRP (adb state '${S:-none}')"
fi
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

# ---- TWRP backup of Data + Boot, compressed, with digests
echo "-- twrp backup DBO $NAME (several minutes)"
twrp_cmd backup DBO "$NAME" | tail -4
BDIR=$("$ADB" shell "ls -d /data/media/0/TWRP/BACKUPS/*/$NAME 2>/dev/null | head -1" </dev/null | tr -d '\r')
[ -n "$BDIR" ] || fail "TWRP did not produce /data/media/0/TWRP/BACKUPS/*/$NAME"
echo "   on phone: $BDIR"
echo "$BDIR" > "$OUT/reference/twrp-path.txt"
"$ADB" pull "$BDIR/." "$OUT/twrp/" >/dev/null || fail "pull of the TWRP backup failed"

# TWRP writes <file>.sha2 (sha256) next to each archive: check them on the host
bad=0; n=0
for f in "$OUT"/twrp/*.sha2; do
  [ -e "$f" ] || continue
  n=$((n + 1)); want=$(awk '{print $1}' "$f"); got=$(sha256sum "${f%.sha2}" | awk '{print $1}')
  [ "$want" = "$got" ] || { echo "   DIGEST MISMATCH $(basename "${f%.sha2}")"; bad=1; }
done
[ "$n" -gt 0 ] || fail "no .sha2 digests in the TWRP backup (digest generation off?)"
[ "$bad" = 0 ] || fail "TWRP digests do not match on the host copy"
echo "   $n archives, TWRP digests verified on the host"

# ---- raw metadata + misc
for p in metadata misc; do
  "$ADB" shell "dd if=/dev/block/bootdevice/by-name/$p of=/tmp/$p.img bs=1048576 2>/dev/null" </dev/null
  "$ADB" pull "/tmp/$p.img" "$OUT/raw/$p.img" >/dev/null && "$ADB" shell "rm -f /tmp/$p.img" </dev/null || fail "raw copy of $p failed"
done
echo "   raw: metadata $(stat -c %s "$OUT/raw/metadata.img") B, misc $(stat -c %s "$OUT/raw/misc.img") B"

# ---- internal storage (TWRP's Data backup excludes it)
if [ "$STORAGE" = 1 ]; then
  echo "-- internal storage (excluding TWRP/)"
  mkdir -p "$OUT/internal"
  for d in $("$ADB" shell 'ls -A /data/media/0' </dev/null | tr -d '\r'); do
    [ "$d" = TWRP ] && continue
    "$ADB" pull "/data/media/0/$d" "$OUT/internal/" >/dev/null 2>&1 || echo "   WARN: could not pull $d"
  done
  echo "   $(du -sh "$OUT/internal" | cut -f1) copied"
fi

( cd "$OUT" && find . -type f ! -name manifest.txt | sort | xargs -d '\n' sha256sum > manifest.txt )
{ echo "name: $NAME"; echo "taken: $(date -u +%FT%TZ)"; echo "phone path: $BDIR"; echo "run: $A9_RUN"; } > "$OUT/reference/backup.txt"
echo "-- $(du -sh "$OUT" | cut -f1) in $OUT, manifest $(wc -l < "$OUT/manifest.txt") files"
if [ "$STAY" = 0 ]; then "$HERE/twrp.sh" exit >/dev/null && a9_wait_boot 600 && echo "-- back in Android"; fi
echo "RESULT: backup $NAME complete"
