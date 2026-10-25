#!/usr/bin/env bash
# pack-vendor.sh -- build an Android vendor image (ext4, raw + sparse) from a
# staging tree, offline. No phone access.
#
#   pack-vendor.sh [options] <staging_dir>
#       <staging_dir>      the vendor tree as it appears under /vendor
#                          (bin/, lib64/, etc/init/, etc/vintf/, build.prop, ...)
#       -C <fs_config>     overrides: "<path> <uid> <gid> <mode> [capabilities=0x..]"
#                          one per line, path relative to /vendor (with or without
#                          a leading "vendor/"); shell globs allowed (fnmatch, "*"
#                          also crosses "/"). Later lines win. Everything not
#                          listed gets the defaults below.
#       -S <file_contexts> vendor_file_contexts (default:
#                          <staging>/etc/selinux/vendor_file_contexts). The GSI's
#                          plat_file_contexts is prepended, as AOSP merges them
#                          for image labelling.
#       -o <out_dir>       default ~/A9-build/mainline/android/out
#       -n <name>          output base name (default vendor)
#       --size <bytes>     fixed image size instead of content + ~10%
#       --no-plat-fc       label with the given file_contexts only
#       --check-sepolicy   also run the sepolicy compile check on
#                          <staging>/etc/selinux (see below)
#
#   pack-vendor.sh --check-sepolicy <vendor_selinux_dir> [--neverallow] [-c <polver>]
#       Compile <vendor_selinux_dir>'s plat_pub_versioned.cil + vendor_sepolicy.cil
#       (+ odm_sepolicy.cil if present) together with the GSI's plat, system_ext
#       and product policy and the mapping files for plat_sepolicy_vers.txt,
#       exactly as Android init's LoadSplitPolicy does (secilc -m -M true -G -N
#       -c <polver>). --neverallow drops -N (build-time strictness). secilc is
#       AOSP's (external/selinux android-16.0.0_r4, static) from the podman
#       image a9-android-tools (Containerfile next to this script; built on demand).
#       Env: A9_GSI_IMG (default the v7 image), A9_GENFS_VER (default: the
#       vendor build.prop's ro.board.genfs_labels_version, else none, as init).
#
# Defaults (stock vendor_a, measured 2026-10-14 with debugfs on the golden
# super.bin): directories 0755 0:2000; bin, bin/hw, xbin dirs 0751 0:2000;
# files under bin/ and xbin/ 0755 0:2000; build.prop, default.prop 0600 0:0;
# etc/fs_config_{dirs,files} 0444 0:0; everything else 0644 0:0. With these the
# repacked stock tree differs from stock only in 12 binaries (11 daemons with
# system/radio/gps/bluetooth owners, one 0751 test tool): set those with -C. Labels: 0 differences.
# Filesystem: ext4, no journal, 4 KiB blocks, 256-byte inodes, 0 reserved,
# features as stock vendor (ext_attr dir_index filetype extent sparse_super
# large_file huge_file uninit_bg dir_nlink extra_isize), timestamps 2009-01-01
# (stock's 1230768000), deterministic UUID/hash seed: same input -> same bytes.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
W=${A9_ANDROID_WORK:-$HOME/A9-build/mainline/android}
GSI=${A9_GSI_IMG:-$HOME/a9-assets/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v7.img}
PODIMG=localhost/a9-android-tools
TS=1230768000
die() { echo "pack-vendor: $*" >&2; exit 2; }
refuse_nas() { local p; p=$(realpath -m "$1"); case "$p" in "$(realpath -m "$HOME/a9-assets")"*|/share/*|*a9-assets*) die "refusing to write under ~/a9-assets ($p)";; esac; }
for t in mke2fs e2fsdroid img2simg debugfs dumpe2fs python3 sha256sum; do command -v $t >/dev/null || die "missing tool: $t"; done

# ---- GSI policy cache (read with debugfs, no mount, no root) -------------
gsi_cache() {
	[ -r "$GSI" ] || die "GSI image not readable: $GSI"
	local key; key=$(stat -c '%s-%Y' "$GSI")
	GC="$W/cache/gsi-sepolicy/$(basename "$GSI" .img)-$key"
	[ -f "$GC/.done" ] && return 0
	refuse_nas "$GC"; rm -rf "$GC"; mkdir -p "$GC"
	local d
	for d in system/etc/selinux system/system_ext/etc/selinux system/product/etc/selinux; do
		mkdir -p "$GC/$(dirname "$d")"
		debugfs -R "rdump /$d $GC/$(dirname "$d")" "$GSI" >/dev/null 2>&1 || true
	done
	[ -s "$GC/system/etc/selinux/plat_sepolicy.cil" ] || die "could not read plat_sepolicy.cil from $GSI"
	touch "$GC/.done"
}

ensure_podimg() {
	command -v podman >/dev/null || die "podman missing"
	podman image exists "$PODIMG" || {
		echo "  building $PODIMG (once, ~2 min)"
		podman build -q -t a9-android-tools -f "$HERE/Containerfile" "$HERE" >/dev/null
	}
}

check_sepolicy() {
	local vdir=$1 polver=$2 strict=$3
	[ -f "$vdir/plat_pub_versioned.cil" ] || die "$vdir/plat_pub_versioned.cil missing"
	[ -f "$vdir/vendor_sepolicy.cil" ] || die "$vdir/vendor_sepolicy.cil missing"
	[ -f "$vdir/plat_sepolicy_vers.txt" ] || die "$vdir/plat_sepolicy_vers.txt missing"
	local ver; ver=$(tr -d ' \n\r' < "$vdir/plat_sepolicy_vers.txt")
	gsi_cache; ensure_podimg
	local S=/g/system/etc/selinux SE=/g/system/system_ext/etc/selinux P=/g/system/product/etc/selinux
	[ -f "$GC/system/etc/selinux/mapping/$ver.cil" ] || die "GSI has no mapping/$ver.cil (vendor policy version $ver unsupported)"
	local -a a=("$S/plat_sepolicy.cil" -m -M true -G)
	[ "$strict" = 1 ] || a+=(-N)
	a+=(-c "$polver" "$S/mapping/$ver.cil")
	local genfs=${A9_GENFS_VER:-}
	# init adds plat_sepolicy_genfs_<v>.cil only when the vendor sets
	# ro.board.genfs_labels_version (Android 16 selinux.cpp); unset = none
	if [ -z "$genfs" ] && [ -f "$vdir/../../build.prop" ]; then
		genfs=$(sed -n 's/^ro\.board\.genfs_labels_version=//p' "$vdir/../../build.prop" | tail -1)
	fi
	genfs=${genfs:-none}
	[ "$genfs" != none ] && [ -n "$genfs" ] && [ -f "$GC/system/etc/selinux/plat_sepolicy_genfs_$genfs.cil" ] && a+=("$S/plat_sepolicy_genfs_$genfs.cil")
	[ -f "$GC/system/etc/selinux/mapping/$ver.compat.cil" ] && a+=("$S/mapping/$ver.compat.cil")
	[ -f "$GC/system/system_ext/etc/selinux/system_ext_sepolicy.cil" ] && a+=("$SE/system_ext_sepolicy.cil")
	[ -f "$GC/system/system_ext/etc/selinux/mapping/$ver.cil" ] && a+=("$SE/mapping/$ver.cil")
	[ -f "$GC/system/system_ext/etc/selinux/mapping/$ver.compat.cil" ] && a+=("$SE/mapping/$ver.compat.cil")
	[ -f "$GC/system/product/etc/selinux/product_sepolicy.cil" ] && a+=("$P/product_sepolicy.cil")
	[ -f "$GC/system/product/etc/selinux/mapping/$ver.cil" ] && a+=("$P/mapping/$ver.cil")
	a+=(/v/plat_pub_versioned.cil /v/vendor_sepolicy.cil)
	[ -f "$vdir/odm_sepolicy.cil" ] && a+=(/v/odm_sepolicy.cil)
	local out; out=$(mktemp -d "${TMPDIR:-/tmp}/a9-sepol.XXXXXX")
	echo "== sepolicy check: vendor $ver vs $(basename "$GSI"), genfs ${genfs:-none}, policy v$polver$([ "$strict" = 1 ] && echo ", neverallows ON")"
	echo "   secilc ${a[*]}"
	local rc=0
	podman run --rm --network=none -v "$GC:/g:ro" -v "$(realpath "$vdir"):/v:ro" -v "$out:/o" "$PODIMG" \
		secilc "${a[@]}" -o /o/sepolicy -f /dev/null > "$out/log" 2>&1 || rc=$?
	head -n 40 "$out/log" | sed 's/^/   | /'
	[ "$(wc -l < "$out/log")" -le 40 ] || echo "   | ... ($(wc -l < "$out/log") lines, $(grep -c 'neverallow check failed' "$out/log" || true) neverallow failures)"
	if [ $rc = 0 ] && [ -s "$out/sepolicy" ]; then
		echo "   PASS: compiled policy $(stat -c %s "$out/sepolicy") bytes (secilc $(podman run --rm "$PODIMG" cat /opt/secilc-version.txt | cut -c1-30))"
	else
		echo "   FAIL: secilc exit $rc"
	fi
	rm -rf "$out"
	return $rc
}

# ---- argument parsing ----------------------------------------------------
STAGE= FSC= FC= OUT=$W/out NAME=vendor SIZE= PLATFC=1 CHECK=0 POLVER=30 STRICT=0 CHECKONLY=
while [ $# -gt 0 ]; do
	case $1 in
		-C) FSC=$2; shift 2;;
		-S) FC=$2; shift 2;;
		-o) OUT=$2; shift 2;;
		-n) NAME=$2; shift 2;;
		--size) SIZE=$2; shift 2;;
		--no-plat-fc) PLATFC=0; shift;;
		--check-sepolicy) CHECK=1; if [ $# -gt 1 ] && [ -f "$2/plat_pub_versioned.cil" ] && [ -z "$STAGE" ]; then CHECKONLY=$2; shift; fi; shift;;
		--neverallow) STRICT=1; shift;;
		-c) POLVER=$2; shift 2;;
		-h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0;;
		-*) die "unknown option $1";;
		*) [ -z "$STAGE" ] || die "one staging dir only"; STAGE=$1; shift;;
	esac
done
if [ -n "$CHECKONLY" ] && [ -z "$STAGE" ]; then
	check_sepolicy "$CHECKONLY" "$POLVER" "$STRICT"; exit $?
fi
[ -n "$STAGE" ] && [ -d "$STAGE" ] || die "usage: pack-vendor.sh [options] <staging_dir> (or --check-sepolicy <dir>); -h for help"
STAGE=$(realpath "$STAGE")
refuse_nas "$OUT"
[ -n "$FC" ] || FC=$STAGE/etc/selinux/vendor_file_contexts
[ -f "$FC" ] || die "file_contexts not found: $FC (use -S)"
mkdir -p "$OUT"
T=$(mktemp -d "${TMPDIR:-/tmp}/a9-packvendor.XXXXXX"); trap 'rm -rf "$T"' EXIT

# ---- labels: plat_file_contexts + vendor_file_contexts -------------------
if [ "$PLATFC" = 1 ]; then
	gsi_cache
	cat "$GC/system/etc/selinux/plat_file_contexts" "$FC" > "$T/file_contexts"
else
	cp "$FC" "$T/file_contexts"
fi

# ---- fs_config: defaults + overrides, every path listed ------------------
python3 -I - "$STAGE" "${FSC:-}" "$T/fs_config" "$T/stats" <<'PY'
import os, sys, fnmatch
stage, ovr, out, stats = sys.argv[1:5]
rules = []
if ovr:
    for n, l in enumerate(open(ovr), 1):
        l = l.split('#', 1)[0].strip()
        if not l: continue
        f = l.split()
        if len(f) < 4: sys.exit(f"{ovr}:{n}: need '<path> <uid> <gid> <mode> [capabilities=0x..]'")
        p = f[0].lstrip('/')
        if p.startswith('vendor/'): p = p[7:]
        if p == 'vendor': p = ''
        caps = '0'
        for x in f[4:]:
            if x.startswith('capabilities='): caps = x.split('=', 1)[1]
            else: sys.exit(f"{ovr}:{n}: unknown field {x}")
        rules.append((p, int(f[1]), int(f[2]), int(f[3], 8), caps))
def default(rel, isdir):
    top = rel.split('/', 1)[0]
    if isdir:
        return (0, 2000, 0o751 if rel in ('bin', 'bin/hw', 'xbin') else 0o755, '0')
    if top in ('bin', 'xbin'):
        return (0, 2000, 0o755, '0')
    if rel in ('build.prop', 'default.prop', 'odm/etc/build.prop'):
        return (0, 0, 0o600, '0')
    if rel in ('etc/fs_config_dirs', 'etc/fs_config_files'):
        return (0, 0, 0o444, '0')
    return (0, 0, 0o644, '0')
lines, used, nfiles, ndirs, blocks = [], set(), 0, 0, 0
entries = [('', True)]
for root, dirs, files in os.walk(stage):
    dirs.sort(); files.sort()
    r = os.path.relpath(root, stage)
    r = '' if r == '.' else r
    for d in dirs: entries.append((os.path.join(r, d), True))
    for f in files:
        p = os.path.join(root, f)
        if not (os.path.isfile(p) or os.path.islink(p)): sys.exit(f"unsupported file type: {p}")
        entries.append((os.path.join(r, f), False))
for rel, isdir in entries:
    uid, gid, mode, caps = default(rel, isdir)
    for i, (pat, u, g, m, c) in enumerate(rules):
        if rel == pat or (pat and fnmatch.fnmatchcase(rel, pat)):
            uid, gid, mode, caps = u, g, m, c; used.add(i)
    # e2fsdroid looks the root up as "" and everything else as vendor/<rel>
    path = 'vendor/' + rel if rel else ''
    lines.append(f"{path} {uid} {gid} {mode:o}" + (f" capabilities={caps}" if caps not in ('0', '0x0') else ''))
    full = os.path.join(stage, rel)
    if isdir: ndirs += 1; blocks += 1 + len(os.listdir(full)) // 64
    else:
        nfiles += 1
        sz = len(os.readlink(full)) if os.path.islink(full) else os.path.getsize(full)
        blocks += (sz + 4095) // 4096
for i, r in enumerate(rules):
    if i not in used: print(f"  warning: fs_config override matched nothing: {r[0]}", file=sys.stderr)
open(out, 'w').write('\n'.join(lines) + '\n')
open(stats, 'w').write(f"{nfiles} {ndirs} {blocks}\n")
PY
read -r NFILES NDIRS DBLOCKS < "$T/stats"
INODES=$(( (NFILES + NDIRS) * 11 / 10 + 64 ))
INODES=$(( (INODES + 7) / 8 * 8 ))

# AOSP-equivalent mke2fs.conf (system/extras/ext4_utils/mke2fs.conf), so the
# host's /etc/mke2fs.conf (metadata_csum, 64bit, orphan_file ...) does not leak in.
cat > "$T/mke2fs.conf" <<'EOF'
[defaults]
	base_features = sparse_super,large_file,filetype,dir_index,ext_attr
	default_mntopts = acl,user_xattr
	enable_periodic_fsck = 0
	blocksize = 4096
	inode_size = 256
	inode_ratio = 16384
	reserved_ratio = 1.0
	lazy_itable_init = false
	lazy_journal_init = false
[fs_types]
	ext4 = {
		features = extent,huge_file,dir_nlink,extra_isize,uninit_bg
		inode_size = 256
	}
EOF
SEED=$( (cat "$T/fs_config"; echo "$NAME") | sha256sum | cut -c1-32)
UUID=$(echo "$SEED" | sed -E 's/(.{8})(.{4})(.{4})(.{4})(.{12})/\1-\2-\3-\4-\5/')
HSEED=$( (echo hash; echo "$SEED") | sha256sum | cut -c1-32 | sed -E 's/(.{8})(.{4})(.{4})(.{4})(.{12})/\1-\2-\3-\4-\5/')

build() { # $1 = blocks
	local img=$T/$NAME.img
	rm -f "$img"
	MKE2FS_CONFIG=$T/mke2fs.conf E2FSPROGS_FAKE_TIME=$TS mke2fs -q -F -t ext4 -T default -O ^has_journal -b 4096 -I 256 \
		-N "$INODES" -m 0 -L "$NAME" -M "/$NAME" -U "$UUID" -E "hash_seed=$HSEED" "$img" "$1" >/dev/null || return 1
	E2FSPROGS_FAKE_TIME=$TS e2fsdroid -e -T "$TS" -C "$T/fs_config" -S "$T/file_contexts" -f "$STAGE" -a "/$NAME" "$img" 2>"$T/e2fsdroid.log" || return 1
}
if [ -n "$SIZE" ]; then
	BLOCKS=$(( SIZE / 4096 ))
	build "$BLOCKS" || { cat "$T/e2fsdroid.log" >&2; die "content does not fit in --size $SIZE"; }
else
	# content blocks + inode table + bitmaps/group descriptors, then +10%
	META=$(( INODES * 256 / 4096 + 64 ))
	BLOCKS=$(( (DBLOCKS + META) * 11 / 10 + 256 ))
	until build "$BLOCKS"; do
		grep -qi 'space\|Could not allocate\|No free' "$T/e2fsdroid.log" || { cat "$T/e2fsdroid.log" >&2; die "e2fsdroid failed"; }
		BLOCKS=$(( BLOCKS * 5 / 4 ))
		[ $BLOCKS -lt 2621440 ] || die "image grew beyond 10 GiB, giving up"
	done
fi
[ -s "$T/e2fsdroid.log" ] && sed 's/^/  e2fsdroid: /' "$T/e2fsdroid.log"

# ---- verify: modes/owners/caps vs fs_config, security.selinux everywhere --
python3 -I - "$T/$NAME.img" "$T/fs_config" "$T/file_contexts" "$NAME" <<'PY'
import subprocess, sys, os, re
img, fsc, fc, name = sys.argv[1:5]
want = {}
for l in open(fsc):
    if l.startswith(' '): l = '/' + l      # root entry: empty path
    f = l.split()
    rel = '/' if f[0] == '/' else f[0][len('vendor'):]
    want[rel] = (int(f[1]), int(f[2]), int(f[3], 8), any(x.startswith('capabilities=') for x in f[4:]))
cmds = []
for p in sorted(want):
    cmds += [f'stat "{p}"', f'ea_get "{p}" security.selinux', f'ea_get "{p}" security.capability']
open(img + '.cmds', 'w').write('\n'.join(cmds) + '\n')
out = subprocess.run(['debugfs', '-f', img + '.cmds', img], capture_output=True, text=True).stdout
os.unlink(img + '.cmds')
got = {}
for b in ('\n' + out).split('\ndebugfs: ')[1:]:
    cmd, _, body = b.partition('\n')
    m = re.match(r'(stat|ea_get) "(.*)"( security\.\w+)?$', cmd)
    if not m: continue
    g = got.setdefault(m.group(2), {})
    if m.group(1) == 'stat':
        m1 = re.search(r'Mode:\s+([0-7]+)', body); m2 = re.search(r'User:\s+(\d+)\s+Group:\s+(\d+)', body)
        if m1 and m2: g['mode'], g['uid'], g['gid'] = int(m1.group(1), 8), int(m2.group(1)), int(m2.group(2))
    elif m.group(3) == ' security.selinux':
        m3 = re.match(r'security\.selinux \(\d+\) = "(.*?)(\\000)?"', body)
        if m3: g['ctx'] = m3.group(1)
    else:
        g['cap'] = body.startswith('security.capability (')
bad, nolabel, labels = [], [], {}
for p, (u, gi, mo, c) in sorted(want.items()):
    g = got.get(p, {})
    if 'mode' not in g: bad.append(f"{p}: not found in image"); continue
    if (g['uid'], g['gid'], g['mode'] & 0o7777, g.get('cap', False)) != (u, gi, mo, c):
        bad.append(f"{p}: got {g['uid']}:{g['gid']} {oct(g['mode'] & 0o7777)} cap={g.get('cap', False)}, want {u}:{gi} {oct(mo)} cap={c}")
    if not g.get('ctx'): nolabel.append(p)
    else: labels[g['ctx']] = labels.get(g['ctx'], 0) + 1
print(f"  verify: {len(want)} inodes checked against fs_config, {len(bad)} mismatches, {len(nolabel)} without security.selinux")
for x in bad[:20]: print("   MISMATCH", x)
for x in nolabel[:20]: print("   NOLABEL", x)
for k, v in sorted(labels.items(), key=lambda kv: -kv[1])[:12]: print(f"    {v:5d}  {k}")
sys.exit(1 if bad or nolabel else 0)
PY

# ---- outputs ---------------------------------------------------------------
img2simg "$T/$NAME.img" "$T/$NAME.sparse.img"
mv -f "$T/$NAME.img" "$OUT/$NAME.img"
mv -f "$T/$NAME.sparse.img" "$OUT/$NAME.sparse.img"
cp -f "$T/fs_config" "$OUT/$NAME.fs_config"
(cd "$OUT" && sha256sum "$NAME.img" > "$NAME.img.sha256" && sha256sum "$NAME.sparse.img" > "$NAME.sparse.img.sha256")
USED=$(dumpe2fs -h "$OUT/$NAME.img" 2>/dev/null | awk -F: '/^Block count/{b=$2}/^Free blocks/{f=$2}END{printf "%d/%d", b-f, b}')
echo "pack-vendor: produced"
echo "  $OUT/$NAME.img         $(stat -c %s "$OUT/$NAME.img") bytes raw ext4, blocks used/total $USED, inodes $INODES, $NFILES files $NDIRS dirs  sha256 $(cut -c1-16 "$OUT/$NAME.img.sha256")"
echo "  $OUT/$NAME.sparse.img  $(stat -c %s "$OUT/$NAME.sparse.img") bytes android sparse  sha256 $(cut -c1-16 "$OUT/$NAME.sparse.img.sha256")"
echo "  $OUT/$NAME.fs_config   (the full canned fs_config used)"
if [ "$CHECK" = 1 ]; then
	check_sepolicy "$STAGE/etc/selinux" "$POLVER" "$STRICT"
fi
