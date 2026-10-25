#!/usr/bin/env bash
#
# image-diff.sh — what changed between two system images, file by file.
#
# Run it before flashing a new build: PATCH_REPORT counts smali patches only
# (CLAUDE.md rule 12), so file replacements, overlays, rc injection and stale
# cached artefacts are invisible there. A file diff shows them (the A6L port
# caught a silently reused old module this way; A6L harvest §4).
#
# Builds a manifest per image (path, type, mode, uid:gid, size, sha256 or link
# target, SELinux label) by loop-mounting it READ-ONLY (sudo; ro,noload, so the
# image is never modified), and caches it next to the image as
# <image>.manifest.tsv (plain write: fine on the NFS share). Then diffs them.
#
# Usage:
#   image-diff.sh <old.img> <new.img>                  # summary + changed paths
#   image-diff.sh <old.img> <new.img> --expect <file>  # also fail on any changed
#                                                      # path not matching a line
#                                                      # (shell glob) in <file>
#   image-diff.sh --manifest <img>                     # (re)build one manifest
#
# Exit: 0 = diff printed (and all changes expected, with --expect); 1 = unexpected
# changes; 2 = usage/mount error.
#
set -uo pipefail

manifest() {
  local img="$1" out="$1.manifest.tsv" m
  if [ -s "$out" ] && [ "$out" -nt "$img" ]; then echo "$out"; return 0; fi
  m=$(mktemp -d) || return 2
  sudo -n mount -o loop,ro,noload "$img" "$m" || { rmdir "$m"; echo "ERROR: cannot mount $img read-only" >&2; return 2; }
  echo "-- building manifest for $(basename "$img") (a few minutes)" >&2
  sudo -n python3 -I - "$m" > "$out.tmp" <<'PY'
import os, sys, hashlib, stat
root = sys.argv[1]
def label(p):
    try: return os.getxattr(p, 'security.selinux', follow_symlinks=False).rstrip(b'\0').decode()
    except OSError: return '-'
for d, dirs, files in os.walk(root):
    dirs.sort()
    for n in sorted(dirs) + sorted(files):
        p = os.path.join(d, n); st = os.lstat(p); rel = '/' + os.path.relpath(p, root)
        if stat.S_ISLNK(st.st_mode): t, v = 'l', os.readlink(p)
        elif stat.S_ISDIR(st.st_mode): t, v = 'd', '-'
        elif stat.S_ISREG(st.st_mode):
            h = hashlib.sha256()
            with open(p, 'rb') as f:
                for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
            t, v = 'f', h.hexdigest()
        else: t, v = 'o', '-'
        size = st.st_size if t == 'f' else 0
        print(f"{rel}\t{t}\t{oct(st.st_mode & 0o7777)}\t{st.st_uid}:{st.st_gid}\t{size}\t{v}\t{label(p)}")
PY
  local rc=$?
  sudo -n umount "$m"; rmdir "$m"
  [ "$rc" = 0 ] || { rm -f "$out.tmp"; return 2; }
  # plain rewrite (NFS share: no rename tricks needed, but keep a complete file)
  cat "$out.tmp" > "$out"; rm -f "$out.tmp"
  echo "$out"
}

if [ "${1:-}" = "--manifest" ]; then [ -f "${2:-}" ] || exit 2; manifest "$2"; exit $?; fi
OLD="${1:-}"; NEW="${2:-}"; EXPECT=""
[ "${3:-}" = "--expect" ] && EXPECT="${4:-}"
[ -f "$OLD" ] && [ -f "$NEW" ] || { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

MO=$(manifest "$OLD") || exit 2
MN=$(manifest "$NEW") || exit 2

python3 -I - "$MO" "$MN" "$EXPECT" <<'PY'
import sys, fnmatch
mo, mn, expect = sys.argv[1], sys.argv[2], sys.argv[3]
def load(f):
    return {l.split('\t', 1)[0]: l.rstrip('\n').split('\t')[1:] for l in open(f)}
a, b = load(mo), load(mn)
added = sorted(set(b) - set(a)); removed = sorted(set(a) - set(b))
changed = []
for p in sorted(set(a) & set(b)):
    if a[p] != b[p]:
        what = [n for n, x, y in zip(('type', 'mode', 'owner', 'size', 'content', 'label'), a[p], b[p]) if x != y]
        if what == ['size'] or 'content' in what or what != []: changed.append((p, what))
print(f"files: old {len(a)}  new {len(b)}  added {len(added)}  removed {len(removed)}  changed {len(changed)}")
for p in added: print("A", p)
for p in removed: print("D", p)
for p, w in changed: print("M", p, "(" + ",".join(w) + ")")
if expect:
    pats = [l.strip() for l in open(expect) if l.strip() and not l.startswith('#')]
    bad = [p for p in added + removed + [p for p, _ in changed] if not any(fnmatch.fnmatch(p, x) for x in pats)]
    for p in bad: print("UNEXPECTED", p)
    print("RESULT:", "UNEXPECTED CHANGES" if bad else "all changes expected")
    sys.exit(1 if bad else 0)
PY
