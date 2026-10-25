#!/usr/bin/env bash
#
# lp-write.sh -- write an image into vendor_b (a LOGICAL partition of the
# inactive slot) from Android slot a, without fastbootd's flash. 2026-10-15.
# docs/mainline-android.md section 5.1.
#
# Why: on 2026-10-15 `fastboot flash vendor_b` in fastbootd (from slot a)
# resized vendor_b (OKAY) and then failed ("Command failed"); nothing was
# written (MEASURED by main: content = truncated old vendor). Resizing works,
# writing through fastbootd does not, so this script only uses fastbootd for
# the RESIZE (super metadata, liblp; never available from Android: no lpadd)
# and writes the data from Android root:
#
#   1. lpdump -s 0 and -s 1 on the phone: vendor_b's extents must be identical
#      in both metadata slots (slot b's first-stage init reads slot 1), must
#      not overlap any other partition's extents, must equal the kernel's own
#      current mapping (`dmctl table vendor_b`) when one exists, and the
#      partition must be >= the image (else --write first resizes it in
#      fastbootd to the image size, back to Android, and re-reads lpdump).
#   2. adb push the image to /data/local/tmp, sha256 checked on the phone.
#   3. `dmctl create a9_lpw linear ...` = a RW mapping of exactly those
#      extents (never a raw dd to super), dd, fsync, drop caches, read back
#      through the mapping, `dmctl delete`, rm the temp file.
#   4. read back again from super through the lpdump extents (android-boot.sh
#      lp_readback logic) after dropping caches.
#
# Only vendor_b is accepted. Slot a must be the booted slot; vendor_b must not
# be mounted.
#
# Usage:
#   lp-write.sh <image>                      CHECK (read-only): prints the plan and the code
#   lp-write.sh <image> --write --code <c>   do it
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../a9-env.sh
. "$REPO/scripts/a9-env.sh"
PART=vendor_b
IMG="" WRITE=0 CODE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --write) WRITE=1 ;; --code) CODE="${2:-}"; shift ;;
    -h|--help) sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) IMG="$1" ;;
  esac; shift
done
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok   $*"; }
[ -f "$IMG" ] || fail "no image: $IMG"
SIZE=$(stat -c %s "$IMG")
[ $(( SIZE % 512 )) = 0 ] || fail "image size $SIZE is not a multiple of 512"
WANT=$(awk '{print $1}' "$IMG.sha256" 2>/dev/null)
[ -n "$WANT" ] && [ "$(sha256sum "$IMG" | awk '{print $1}')" = "$WANT" ] || fail "$IMG.sha256 missing or mismatch"

# extents <slot> -> "lstart lend pstart" lines for $PART; all_extents <slot> -> "name lstart lend pstart"
lpd() { a9_su "lpdump -s $1"; }
parse_all() { awk '$1=="Name:" {cur=$2; next} $2==".." && $4=="linear" && $5=="super" {print cur, $1, $3, $6}'; }

plan() {   # sets EXT (extents), PSIZE (bytes); returns 1 when inconsistent
  local d0 d1 e0 e1 cur
  d0=$(lpd 0 | parse_all); d1=$(lpd 1 | parse_all)
  [ -n "$d0" ] || { echo "  lpdump -s 0 gave no extents"; return 1; }
  e0=$(awk -v p=$PART '$1==p {print $2, $3, $4}' <<< "$d0"); e1=$(awk -v p=$PART '$1==p {print $2, $3, $4}' <<< "$d1")
  [ -n "$e0" ] || { echo "  $PART not in lpdump"; return 1; }
  [ "$e0" = "$e1" ] || { echo "  $PART extents differ between metadata slots 0 and 1:"; echo "$e0"; echo "--"; echo "$e1"; return 1; }
  # no overlap with any other partition (both slots' tables)
  python3 -I - "$PART" <<EOF || return 1
import sys
part = sys.argv[1]
rows = [l.split() for l in """$d0
$d1""".splitlines() if l.strip()]
mine = [(int(p), int(p) + int(b) - int(a) + 1) for n, a, b, p in rows if n == part]
other = [(n, int(p), int(p) + int(b) - int(a) + 1) for n, a, b, p in rows if n != part]
bad = [(n, s, e) for (ms, me) in set(mine) for (n, s, e) in other if s < me and ms < e]
if bad: print('  OVERLAP with', bad); sys.exit(1)
EOF
  # the kernel's current mapping of vendor_b (if any) must be the same extents on super
  cur=$(a9_su "dmctl table $PART" 2>/dev/null | awk '/linear/ {split($1,r,"-"); sub(":","",r[2]); print r[1], r[2], $NF}')
  if [ -n "$cur" ]; then
    local sup lpe
    sup=$(a9_su "stat -L -c %t:%T /dev/block/by-name/super")
    lpe=$(awk '{print $1, $2 + 1, $3}' <<< "$e0")
    [ "$cur" = "$lpe" ] || { echo "  dmctl table $PART ($cur) != lpdump extents ($lpe)"; return 1; }
    a9_su "dmctl table $PART" | grep -q "linear, $(printf '%d:%d' 0x${sup%%:*} 0x${sup##*:}) " \
      || { echo "  current $PART mapping is not on the super device ($sup)"; return 1; }
  fi
  EXT="$e0"
  PSIZE=$(awk '{s += $2 - $1 + 1} END {print s * 512}' <<< "$e0")
  return 0
}

phone_ok() {
  H=$("$REPO/scripts/a9-health.sh" --json); HRC=$?
  case "$H" in *'"slot":"_a"'*) ;; *) fail "phone must be in Android on slot a: $H" ;; esac
  [ "$HRC" = 0 ] || fail "Android on slot a has not finished booting"
  [ "$(a9_su id | grep -c 'uid=0')" = 1 ] || fail "su does not work"
  a9_su "grep -q /dev/block/mapper/$PART /proc/mounts && echo M" | grep -q M && fail "$PART is mounted"
  a9_su "ls /system/bin/dmctl /system/bin/lpdump" >/dev/null || fail "dmctl/lpdump missing"
  return 0
}

code() { printf '%s %s %s' "$WANT" "$PART" "$1" | sha256sum | cut -c1-8; }

echo "== lp-write: $(basename "$IMG") ($SIZE bytes, ${WANT:0:12}) -> $PART"
phone_ok; ok "Android slot a, root, $PART not mounted"
plan || fail "extent checks failed: nothing written"
ok "$PART extents identical in metadata slots 0/1, no overlap, = kernel mapping: $(tr '\n' ';' <<< "$EXT")"
NEED_RESIZE=0
if [ "$PSIZE" -lt "$SIZE" ]; then NEED_RESIZE=1; echo "  ..   $PART is $PSIZE bytes < image: --write resizes it to $SIZE in fastbootd first"; fi
if [ "$WRITE" = 0 ]; then
  echo "CHECK: PASS. code: $(code "$NEED_RESIZE")   run: $0 $IMG --write --code $(code "$NEED_RESIZE")"
  exit 0
fi
[ "$CODE" = "$(code "$NEED_RESIZE")" ] || fail "--code does not match (run the check first)"
[ "${A9_USB_LOCKED:-}" = 1 ] || a9_usb_lock   # android-boot.sh holds it already
[ -n "${A9_RUN:-}" ] || a9_run_dir mainline-lp-write "$IMG"

if [ "$NEED_RESIZE" = 1 ]; then
  echo "-- fastbootd: resize-logical-partition $PART $SIZE (metadata only; proven to work 2026-10-15)"
  "$ADB" reboot fastboot
  a9_wait_fastboot yes 180 || fail "fastbootd did not appear (phone may be in recovery: fastboot reboot / a9-recover)"
  [ "$(a9_fbvar current-slot)" = a ] || { "$FB" reboot; fail "fastbootd current-slot is not a"; }
  s=$(a9_fbvar snapshot-update-status); case "$s" in ""|none) ;; *) "$FB" reboot; fail "snapshot-update-status $s" ;; esac
  "$FB" resize-logical-partition "$PART" "$SIZE" || { "$FB" reboot; fail "resize failed"; }
  "$FB" reboot
  a9_wait_boot 600 || fail "Android slot a did not come back after fastbootd: a9-recover skill"
  sleep 15
  phone_ok
  plan || fail "extent checks failed after the resize: nothing written"
  [ "$PSIZE" -ge "$SIZE" ] || fail "$PART still smaller than the image after resize ($PSIZE)"
  ok "resized: $(tr '\n' ';' <<< "$EXT")"
fi

TABLE=""
while read -r ls le ps; do TABLE="$TABLE linear $ls $(( le - ls + 1 )) /dev/block/by-name/super $ps"; done <<< "$EXT"
T=/data/local/tmp/a9-lpw.img
FREE=$(a9_su "df -k /data/local/tmp | tail -n 1" | awk '{print $4}')
[ -n "$FREE" ] && [ $(( FREE * 1024 )) -gt $(( SIZE + 1073741824 )) ] || fail "not enough free space in /data for the temp copy"
echo "-- adb push ($SIZE bytes)"
"$ADB" push "$IMG" "$T" >/dev/null || fail "adb push failed"
cat > "$A9_RUN/a9-lpw.sh" <<'SH'
# runs on the phone as root: <img> <sha256> <size> <table...>
img=$1; want=$2; size=$3; shift 3
n=a9_lpw
[ "$(sha256sum "$img" | cut -d' ' -f1)" = "$want" ] || { echo "PHONE: temp image sha mismatch"; exit 11; }
dmctl delete $n >/dev/null 2>&1
dmctl create $n "$@" || { echo "PHONE: dmctl create failed"; exit 12; }
dev=$(dmctl getpath $n | tr -d '\r')
i=0; while [ ! -b "$dev" ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i+1)); done
[ -b "$dev" ] || { dmctl delete $n; echo "PHONE: no node for $n"; exit 13; }
dd if="$img" of="$dev" bs=1048576 conv=fsync 2>/dev/null || { dmctl delete $n; echo "PHONE: dd failed"; exit 14; }
sync; echo 3 > /proc/sys/vm/drop_caches
got=$(head -c "$size" "$dev" | sha256sum | cut -d' ' -f1)
dmctl delete $n
[ "$got" = "$want" ] && echo "PHONE: written and read back through $dev: MATCH" || { echo "PHONE: readback MISMATCH $got"; exit 15; }
SH
"$ADB" push "$A9_RUN/a9-lpw.sh" /data/local/tmp/a9-lpw.sh >/dev/null || fail "push helper failed"
echo "-- dmctl create a9_lpw$TABLE; dd; readback"
R=$(a9_su "sh /data/local/tmp/a9-lpw.sh $T $WANT $SIZE$TABLE; echo rc=\$?")
echo "$R" | sed 's/^/   /'
a9_su "rm -f $T /data/local/tmp/a9-lpw.sh"
echo "$R" | grep -q 'rc=0' || fail "phone-side write failed (see above); a9_lpw deleted"
# independent readback from super via the extents, caches dropped
a9_su "echo 3 > /proc/sys/vm/drop_caches"
cmd=""
while read -r ls le ps; do
  n=$(( le - ls + 1 ))
  if [ $(( ps % 2048 )) = 0 ] && [ $(( n % 2048 )) = 0 ]; then
    cmd="$cmd dd if=/dev/block/by-name/super bs=1048576 skip=$(( ps / 2048 )) count=$(( n / 2048 )) 2>/dev/null;"
  elif [ $(( ps % 8 )) = 0 ] && [ $(( n % 8 )) = 0 ]; then
    cmd="$cmd dd if=/dev/block/by-name/super bs=4096 skip=$(( ps / 8 )) count=$(( n / 8 )) 2>/dev/null;"
  else
    cmd="$cmd dd if=/dev/block/by-name/super bs=512 skip=$ps count=$n 2>/dev/null;"
  fi
done <<< "$EXT"
got=$(a9_su "( $cmd ) | head -c $SIZE | sha256sum" | awk '{print $1}')
[ "$got" = "$WANT" ] || fail "readback from super MISMATCH (${got:0:16})"
echo "RESULT: $PART = $(basename "$IMG") (MATCH from super, $SIZE bytes)"
