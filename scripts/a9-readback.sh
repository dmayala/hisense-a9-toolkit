#!/usr/bin/env bash
#
# a9-readback.sh — prove a partition on the phone holds exactly an image.
# Read-only (root via Magisk su, Android only).
#
# fastboot gives no readback, and "OKAY" has lied on this device before
# (CLAUDE.md rule 3). So after every flash, hash the first <image size> bytes
# of the partition ON THE PHONE and compare with the image's sha256 (its
# .sha256 file if present, else computed). Logical partitions (system_a/b,
# vendor_a/b, ...) are read from /dev/block/mapper, the rest from by-name.
#
# Usage:
#   a9-readback.sh <partition> <image>     # MATCH / MISMATCH, exit 0 / 1
#   a9-readback.sh --golden                # compare system_a/b and boot_a/b with
#                                          # data/golden.tsv: says GOLDEN LAGS MAIN
#                                          # when slot A no longer equals the dump
# A 3.5 GB system partition takes about a minute to hash on the phone.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

[ "$(a9_adb_state)" = device ] || { echo "ERROR: phone not in Android (adb device)." >&2; exit 2; }
[ "$(a9_su id | grep -c 'uid=0')" = 1 ] || { echo "ERROR: su does not work on the phone." >&2; exit 2; }

dev_of() {
  case "$1" in
    system_[ab]|system_ext_[ab]|vendor_[ab]|product_[ab]|odm_[ab]) echo "/dev/block/mapper/$1" ;;
    *) echo "/dev/block/by-name/$1" ;;
  esac
}
# phone_hash <partition> <bytes>
phone_hash() { a9_su "head -c $2 $(dev_of "$1") | sha256sum" | awk '{print $1}'; }

if [ "${1:-}" = "--golden" ]; then
  G="$A9_HOME/data/golden.tsv"; lag=0
  while IFS=$'\t' read -r dir part sha size what image; do
    case "$dir" in \#*|"") continue ;; esac
    got=$(phone_hash "$part" "$size")
    if [ "$got" = "$sha" ]; then printf '  %-10s = golden (%s)\n' "$part" "$what"; continue; fi
    # A booted ext4 system partition is never byte-identical (mount metadata), so
    # compare its data blocks with the golden image when golden.tsv names one.
    if [ -n "${image:-}" ] && [ -f "$A9_DATA/$image" ] \
       && "$0" "$part" "$A9_DATA/$image" >/dev/null 2>&1 </dev/null; then
      printf '  %-10s = golden (%s; data blocks, mount metadata differs)\n' "$part" "$what"; continue
    fi
    printf '  %-10s DIFFERS from golden %s (phone %s…)\n' "$part" "$dir" "${got:0:12}"; [ "${part%_a}" != "$part" ] && lag=1
  done < "$G"
  if [ "$lag" = 1 ]; then
    echo "GOLDEN LAGS MAIN: slot A no longer matches the golden dump, so restore-golden.sh would roll it back."
    echo "  Take a new dump (scripts/backup-edl.sh), then update data/golden.tsv, GOLDEN.md and CLAUDE.md."
    exit 1
  fi
  echo "golden matches slot A"; exit 0
fi

PART="${1:-}"; IMG="${2:-}"
[ -n "$PART" ] && [ -f "$IMG" ] || { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
SIZE=$(stat -c %s "$IMG")
if [ -f "$IMG.sha256" ]; then WANT=$(awk '{print $1}' "$IMG.sha256")
else WANT=$(sha256sum "$IMG" | awk '{print $1}'); fi
PSIZE=$(a9_su "blockdev --getsize64 $(dev_of "$PART")")
[ -n "$PSIZE" ] && [ "$PSIZE" -ge "$SIZE" ] 2>/dev/null \
  || { echo "MISMATCH $PART: partition is ${PSIZE:-?} bytes, image $SIZE"; exit 1; }
GOT=$(phone_hash "$PART" "$SIZE")
if [ "$GOT" = "$WANT" ]; then echo "MATCH $PART = $(basename "$IMG") (${WANT:0:16}…)"; exit 0; fi

# Not byte-identical. For an ext4 image that has been MOUNTED (any booted
# system partition) that is expected: mounting rewrites the superblock (mount
# time/count, last-mounted path) and inode-table blocks (inode times), never
# data blocks (measured on system_a 2026-10-13: blocks 0, 4, 25, 32784, ...,
# all superblock or inode tables). So compare 1 MiB chunks, then 4 KiB blocks
# inside the differing chunks, and accept only superblock/inode-table blocks,
# plus directory blocks whose decoded entries are identical (only the unused
# slack after the last entry differs; seen in the root directory, block 25).
if [ "$(od -A n -t x1 -j 1080 -N 2 "$IMG" | tr -d ' ')" != "53ef" ]; then
  echo "MISMATCH $PART: phone ${GOT:0:16}… image ${WANT:0:16}… (not ext4, no metadata allowance)"; exit 1
fi
echo "-- not byte-identical (normal after a mount); comparing data blocks, ~2 min" >&2
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
DEV=$(dev_of "$PART")
debugfs -R stats "$IMG" 2>/dev/null > "$W/stats"
python3 -I - "$IMG" "$W" "$PART" "$DEV" "$ADB" <<'PY2'
import sys, hashlib, re, subprocess
img, w, part, dev, adb = sys.argv[1:6]
st = open(w + '/stats').read()
size = __import__('os').path.getsize(img)
def phone_hashes(start_mib, count, bs_mib):
    # sha256 of `count` chunks of bs_mib MiB starting at start_mib, hashed ON the phone
    n = size
    cmd = (f"i=0; while [ $i -lt {count} ]; do dd if={dev} bs={bs_mib << 20} skip=$(( {start_mib // bs_mib} + i )) count=1 2>/dev/null"
           f" | head -c $(( {n} - ({start_mib} + i * {bs_mib}) * 1048576 )) | sha256sum; i=$((i+1)); done")
    out = subprocess.run([adb, 'shell', f"su -c '{cmd}'"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    return [l.split()[0] for l in out.splitlines() if l.strip()]
def local_hash(start_mib, bs_mib):
    f.seek(start_mib << 20); return hashlib.sha256(f.read(bs_mib << 20)).hexdigest()
f = open(img, 'rb')
nmib = (size + (1 << 20) - 1) >> 20
big = phone_hashes(0, (nmib + 63) // 64, 64)
phone = {}
for k, h in enumerate(big):
    if h == local_hash(k * 64, 64): continue
    cnt = min(64, nmib - k * 64)
    for m, h1 in enumerate(phone_hashes(k * 64, cnt, 1)): phone[k * 64 + m] = h1
ipg = int(re.search(r'Inode blocks per group:\s+(\d+)', st).group(1))
tables = [int(x) for x in re.findall(r'inode table at (\d+)', st)]
allowed = lambda b: b == 0 or any(t <= b < t + ipg for t in tables)
import struct
def dirents(blk):
    # (inode, name, type) of every live entry; ignores rec_len and slack bytes
    out, off = [], 0
    while off + 8 <= len(blk):
        ino, rl, nl, ft = struct.unpack_from('<IHBB', blk, off)
        if rl < 8: break
        if ino: out.append((ino, blk[off+8:off+8+nl], ft))
        off += rl
    return out
def is_dir_block(b):
    # owner inode of block b in the IMAGE, and whether it is a directory
    r = subprocess.run(['debugfs', '-R', f'icheck {b}', img], capture_output=True, text=True).stdout.split()
    if len(r) < 4 or not r[3].isdigit(): return False
    t = subprocess.run(['debugfs', '-R', f'stat <{r[3]}>', img], capture_output=True, text=True).stdout
    return 'Type: directory' in t
bad, meta, slack = [], 0, 0
for i, ph in sorted(phone.items()):
    f.seek(i << 20); chunk = f.read(1 << 20)
    if hashlib.sha256(chunk).hexdigest() == ph: continue
    # never `adb exec-out` a byte stream (a9-backup skill): dd to a file, pull it
    subprocess.run([adb, 'shell', f"su -c 'dd if={dev} of=/data/local/tmp/a9-readback.bin bs=1048576 skip={i} count=1 2>/dev/null'"], stdin=subprocess.DEVNULL)
    subprocess.run([adb, 'pull', '/data/local/tmp/a9-readback.bin', w + '/c.bin'], capture_output=True)
    got = open(w + '/c.bin', 'rb').read()[:len(chunk)]
    for j in range(0, len(chunk), 4096):
        if chunk[j:j+4096] != got[j:j+4096]:
            b = (i << 8) + j // 4096
            if allowed(b): meta += 1
            elif is_dir_block(b) and dirents(chunk[j:j+4096]) == dirents(got[j:j+4096]): slack += 1
            else: bad.append(b)
if bad:
    print(f"MISMATCH {part}: {len(bad)} data block(s) differ, first {bad[:8]}"); sys.exit(1)
subprocess.run([adb, 'shell', "su -c 'rm -f /data/local/tmp/a9-readback.bin'"], stdin=subprocess.DEVNULL)
print(f"MATCH {part} = {img.rsplit('/',1)[-1]}: all data blocks identical; {meta} metadata block(s) differ (superblock/inode tables, from mounts), {slack} directory block(s) differ only in unused slack bytes")
PY2
