#!/usr/bin/env python3
"""s2c-vendor.py -- the S2c TEST vendor: the proven S2a vendor image plus the s2c-stage.py
payload, written with debugfs into a private copy (the S1 s1-kernel-images.py method), then
read back. Offline; never touches the phone, never writes under a9-assets.

  s2c-vendor.py --base IMG --base-sha256 SHA --stage DIR --boot IMG --out IMG [--blocks N]

Steps (any failure removes the partial output):
  1. base = the S2a vendor (sha pinned); stage.json verified file by file; the boot image's
     kernel == the vendor's /lib/modules/kernel-image.sha256 == the stage's kernel pin (the
     sensors modules are for exactly the S2a kernel).
  2. copy; e2fsck -fn; resize2fs to --blocks (default 98304 = 3 block groups: the base has
     only 130 free inodes, the payload needs ~215); e2fsck -fn.
  3. debugfs: mkdir / write each new path with mode, uid, gid, the 2009-01-01 timestamps of the
     base image and security.selinux; for the three appended files (build.prop,
     lib/modules/modules.options, etc/selinux/vendor_file_contexts) the base content + the
     stage lines, same metadata as before. A module already in the base must be identical
     (mdt_loader.ko is) and is not rewritten. modules.dep / modules.alias / modules.load are
     NOT changed: the new modules stay invisible to modprobe and to the E Ink / GPU loaders.
  4. e2fsck -fn; read back (rdump of base and out, debugfs metadata): out = base + exactly the
     stage files (sha256, mode, uid, gid, label), the three appended files = base + lines,
     every other base file identical with identical mode/uid/gid; hygiene (fstab names no
     protected partition, no rmtfs / tqftpserv / boot HAL, no protected block path in any new
     file); receipt <out>.receipt.json and <out>.sha256.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TS = 1230768000  # 2009-01-01 00:00:00 UTC, the base image's timestamps (debugfs stat /)
APPENDED = ['build.prop', 'lib/modules/modules.options', 'etc/selinux/vendor_file_contexts']
PROTECTED = re.compile(rb'/dev/block/(by-name/)?(userdata|metadata|misc|persist|modemst|fsg|frp|devinfo|diag)')


def die(msg):
    print('s2c-vendor: ' + msg, file=sys.stderr)
    sys.exit(1)


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def run(argv, **kw):
    # E2FSPROGS_FAKE_TIME: resize2fs / debugfs -w stamp the superblock write time with the base's
    # fixed time instead of now (as pack-vendor.sh does), so the same inputs give the same image
    env = dict(os.environ, E2FSPROGS_FAKE_TIME=str(TS))
    return subprocess.run(argv, capture_output=True, text=True, env=env, **kw)


def dfs(img, cmd):
    r = run(['debugfs', '-R', cmd, str(img)])
    return r.stdout


def meta(img, path):
    """(mode, uid, gid, label) of an inode, or None."""
    st = dfs(img, f'stat {path}')
    m = re.search(r'Mode:\s+(\d+)', st)
    u = re.search(r'User:\s+(\d+)\s+Group:\s+(\d+)', st)
    t = re.search(r'Type:\s+(\w+)', st)
    if not (m and u and t):
        return None
    ea = dfs(img, f'ea_get {path} security.selinux')
    lab = re.search(r'= "(.*?)(\\000)?"', ea)
    return {'type': t.group(1), 'mode': int(m.group(1), 8), 'uid': int(u.group(1)), 'gid': int(u.group(2)),
            'label': lab.group(1) if lab else None}


def listing(img):
    """{path: (type, mode, uid, gid)} for the whole image (debugfs ls -p, recursive)."""
    out, todo = {}, ['']
    while todo:
        d = todo.pop()
        for line in dfs(img, f'ls -p /{d}').splitlines():
            f = line.split('/')
            if len(f) < 7 or f[5] in ('.', '..'):
                continue
            mode = int(f[2], 8)
            p = (d + '/' + f[5]).lstrip('/')
            out[p] = (mode >> 12, mode & 0o7777, int(f[3]), int(f[4]))
            if mode >> 12 == 0o04:
                todo.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--base-sha256', required=True)
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--boot', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--blocks', type=int, default=98304)
    ap.add_argument('--work', type=Path)
    a = ap.parse_args()
    out = a.out.resolve()
    if 'a9-assets' in str(out):
        die('refusing to write under a9-assets')
    if out.exists():
        die(f'{out} exists')
    if sha(a.base) != a.base_sha256:
        die('base vendor sha256')
    S = a.stage.resolve()
    st = json.loads((S / 'stage.json').read_text())
    ent = st['entries']
    for rel, e in ent.items():
        p = S / 'tree' / rel
        if e['kind'] == 'file' and (not p.is_file() or p.is_symlink() or sha(p) != e['sha256']):
            die(f'stage file {rel} changed')
    tree_files = {p.relative_to(S / 'tree').as_posix() for p in (S / 'tree').rglob('*') if p.is_file()}
    if tree_files != {r for r, e in ent.items() if e['kind'] == 'file'}:
        die('stage tree differs from stage.json')
    for rel, lines in st['appends'].items():
        if (S / 'append' / rel).read_text() != ''.join(l + '\n' for l in lines):
            die(f'stage append {rel} changed')

    work = Path(tempfile.mkdtemp(prefix='s2c-vendor-', dir=a.work or out.parent))
    tmp = out.with_name(out.name + '.partial')
    try:
        # kernel pairing
        r = run(['unpack_bootimg', '--boot_img', str(a.boot), '--out', str(work / 'boot')])
        if r.returncode:
            die('unpack_bootimg: ' + r.stderr)
        kboot = sha(work / 'boot' / 'kernel')
        kvend = dfs(a.base, 'cat /lib/modules/kernel-image.sha256').strip()
        if not (kboot == kvend == st['pins']['kernel_image_sha256']):
            die(f'kernel pairing: boot {kboot[:12]} vendor {kvend[:12]} stage {st["pins"]["kernel_image_sha256"][:12]}')
        shutil.rmtree(work / 'boot')

        shutil.copyfile(a.base, tmp)
        for argv in (['e2fsck', '-fn', str(tmp)], ['resize2fs', str(tmp), str(a.blocks)], ['e2fsck', '-fn', str(tmp)]):
            r = run(argv)
            if r.returncode:
                die(f'{argv[0]} failed: {r.stdout}{r.stderr}')

        base_list = listing(a.base)
        cmds, skipped, written = [], [], []
        labels = {}

        def label_file(lab):
            if lab not in labels:
                f = work / f'label{len(labels)}'
                f.write_bytes(lab.encode() + b'\0')
                labels[lab] = f
            return labels[lab]

        def setmeta(rel, kind, mode, uid, gid, lab):
            t = 0o040000 if kind == 'dir' else 0o100000
            cmds.extend([f'sif /{rel} mode 0{t | mode:o}', f'sif /{rel} uid {uid}', f'sif /{rel} gid {gid}'])
            for fld in ('atime', 'mtime', 'ctime', 'crtime'):
                cmds.append(f'sif /{rel} {fld} @{TS}')
            cmds.append(f'ea_set -f {label_file(lab)} /{rel} security.selinux')

        for rel, e in ent.items():   # sorted: parents before children
            if e.get('existing'):
                if rel not in base_list or base_list[rel][0] != 0o04:
                    die(f'{rel} should be an existing directory in the base')
                continue
            if rel in base_list:
                if e['kind'] == 'file' and rel.startswith('lib/modules/') and rel.endswith('.ko'):
                    d = work / 'cmp.ko'
                    dfs(a.base, f'dump /{rel} {d}')
                    if sha(d) != e['sha256']:
                        die(f'{rel} exists in the base with different content')
                    skipped.append(rel)
                    continue
                die(f'{rel} already exists in the base')
            if e['kind'] == 'dir':
                cmds.append(f'mkdir /{rel}')
            else:
                cmds.append(f'write {S / "tree" / rel} /{rel}')
                written.append(rel)
            setmeta(rel, e['kind'], int(e['mode'], 8), e['uid'], e['gid'], e['label'])
        app_meta = {}
        for rel in APPENDED:
            m = meta(a.base, '/' + rel)
            if not m or m['type'] != 'regular' or not m['label']:
                die(f'base {rel} metadata')
            app_meta[rel] = m
            orig = work / ('orig-' + rel.replace('/', '_'))
            dfs(a.base, f'dump /{rel} {orig}')
            data = orig.read_bytes()
            if not data.endswith(b'\n'):
                die(f'base {rel} does not end with a newline')
            new = work / ('new-' + rel.replace('/', '_'))
            new.write_bytes(data + (S / 'append' / rel).read_bytes())
            cmds += [f'rm /{rel}', f'write {new} /{rel}']
            setmeta(rel, 'file', m['mode'], m['uid'], m['gid'], m['label'])
        # test hook (tests/mainline-sensors/test-s2c-vendor.sh only): one extra debugfs command,
        # to prove the read back catches a change outside the stage
        if os.environ.get('S2C_TEST_FAULT'):
            cmds.append(os.environ['S2C_TEST_FAULT'])
        (work / 'cmds').write_text('\n'.join(cmds) + '\n')
        r = run(['debugfs', '-w', '-f', str(work / 'cmds'), str(tmp)])
        errs = [l for l in (r.stdout + r.stderr).splitlines() if l and not l.startswith(('debugfs', 'Allocated inode:'))]
        if r.returncode or errs:
            die('debugfs: ' + '\n'.join(errs[:20]))
        r = run(['e2fsck', '-fn', str(tmp)])
        if r.returncode:
            die('e2fsck after write: ' + r.stdout)

        # --- read back
        out_list = listing(tmp)
        new_paths = {r_ for r_, e in ent.items() if not e.get('existing') and r_ not in skipped}
        if set(out_list) != set(base_list) | new_paths:
            die(f'path set: extra {sorted(set(out_list) - set(base_list) - new_paths)[:5]} missing {sorted((set(base_list) | new_paths) - set(out_list))[:5]}')
        for p, v in base_list.items():
            if out_list[p] != v:
                die(f'metadata of base path {p} changed: {v} -> {out_list[p]}')
        for rel in sorted(new_paths) + APPENDED:
            m = meta(tmp, '/' + rel)
            want = app_meta[rel] if rel in APPENDED else {
                'type': 'directory' if ent[rel]['kind'] == 'dir' else 'regular', 'mode': int(ent[rel]['mode'], 8),
                'uid': ent[rel]['uid'], 'gid': ent[rel]['gid'], 'label': ent[rel]['label']}
            if m != want:
                die(f'{rel}: {m} != {want}')
        rb, ro = work / 'rd-base', work / 'rd-out'
        rb.mkdir(); ro.mkdir()
        for img, d in ((a.base, rb), (tmp, ro)):
            r = run(['debugfs', '-R', f'rdump / {d}', str(img)])
            if r.returncode:
                die('rdump ' + r.stderr)
        files_b = {p.relative_to(rb).as_posix() for p in rb.rglob('*') if p.is_file() and not p.is_symlink()}
        files_o = {p.relative_to(ro).as_posix() for p in ro.rglob('*') if p.is_file() and not p.is_symlink()}
        if files_o != files_b | set(written):
            die('rdump file sets differ')
        for p in files_b:
            if p in APPENDED:
                if (ro / p).read_bytes() != (rb / p).read_bytes() + (S / 'append' / p).read_bytes():
                    die(f'{p}: not base + stage lines')
            elif (ro / p).read_bytes() != (rb / p).read_bytes():
                die(f'base file {p} changed')
        for p in written:
            if sha(ro / p) != ent[p]['sha256']:
                die(f'{p}: content')
        # hygiene (the vendor parts of android-boot.sh --offline, re-done here; the wrapper's own
        # scan runs in the S2c run script, never by this tool)
        fstab = [l for l in (ro / 'etc/fstab.hlte556n').read_text().splitlines() if not l.lstrip().startswith('#')]
        if any(re.search(r'(userdata|metadata|persist|modemst|fsg|misc)\s', l) for l in fstab):
            die('fstab names a protected partition')
        for b in ('rmtfs', 'tqftpserv', 'android.hardware.boot-service.default', 'android.hardware.boot@1.2-service'):
            if (ro / 'bin' / b).exists() or (ro / 'bin/hw' / b).exists():
                die(f'vendor ships {b}')
        for p in written + APPENDED:
            if not p.startswith(('lib/modules/', 'firmware/', 'etc/selinux/')) and PROTECTED.search((ro / p).read_bytes()):
                die(f'{p} names a protected block device')
        props = (ro / 'build.prop').read_text().splitlines()
        sens = [l for l in props if l.startswith(('ro.vendor.a9ml.sensors=', 'persist.a9.sensors='))]
        size = tmp.stat().st_size
        os.replace(tmp, out)
        digest = sha(out)
        Path(str(out) + '.sha256').write_text(f'{digest}  {out.name}\n')
        receipt = {
            'status': 'PASS-OFFLINE-S2C-VENDOR' if not os.environ.get('S2C_TEST_FAULT') else 'TEST-FAULT-INJECTED', 'out': out.name, 'sha256': digest, 'bytes': size, 'blocks': a.blocks,
            'base': {'path': str(a.base), 'sha256': a.base_sha256, 'bytes': a.base.stat().st_size},
            'boot': {'path': str(a.boot), 'sha256': sha(a.boot), 'kernel_sha256': kboot},
            'stage_json_sha256': sha(S / 'stage.json'), 'pins': st['pins'], 'test_switch': st['test_switch'],
            'written_files': len(written), 'new_dirs': sum(1 for p in new_paths if ent[p]['kind'] == 'dir'),
            'skipped_identical_modules': skipped, 'appended': {p: st['appends'][p] for p in APPENDED},
            'sensor_props': sens, 'base_paths_unchanged': len(base_list) - len(APPENDED),
            'inodes_free': re.search(r'Free inodes:\s+(\d+)', run(['dumpe2fs', '-h', str(out)]).stdout).group(1),
        }
        Path(str(out) + '.receipt.json').write_text(json.dumps(receipt, indent=1) + '\n')
        print(f'S2C-VENDOR PASS {out.name} {digest} bytes={size} written={len(written)} skipped={skipped} props={sens}')
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if tmp.exists():
            tmp.unlink()


if __name__ == '__main__':
    main()
