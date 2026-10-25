#!/usr/bin/env python3
"""s2c-install-stage.py -- install an s2c-stage.py payload into a build-vendor.sh staging tree
(build-vendor.sh --sensors). The overlay path (s2c-vendor.py) writes the same stage into an
image instead; both read the same stage.json.

  s2c-install-stage.py --stage DIR --vendor-stage ST --kernel-image-sha256 SHA \
                       --fsconfig FILE [--props-only]

  * the stage is verified file by file against stage.json;
  * the kernel the vendor's modules belong to (sha256 of the snapshot's Image.gz, as
    build-vendor.sh writes to lib/modules/kernel-image.sha256) must be the stage's pin;
  * every stage file is copied (mode from stage.json); a path that already exists must be
    an identical module (lib/modules/*.ko), anything else is refused;
  * lib/modules/modules.options and etc/selinux/vendor_file_contexts get the stage lines
    (each line at most once); modules.dep / modules.alias / modules.load are left alone;
  * the stage's fsconfig lines are appended to FILE (pack-vendor.sh -C);
  * --props-only: only append the stage's build.prop lines to ST/build.prop (build-vendor.sh
    calls this after it has written build.prop).
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path


def die(msg):
    print('s2c-install-stage: ' + msg, file=sys.stderr)
    sys.exit(1)


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def append_lines(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text() if path.exists() else ''
    if old and not old.endswith('\n'):
        old += '\n'
    have = set(old.splitlines())
    add = [l for l in lines if l not in have or not l]
    path.write_text(old + ''.join(l + '\n' for l in add))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--vendor-stage', type=Path, required=True)
    ap.add_argument('--kernel-image-sha256')
    ap.add_argument('--fsconfig', type=Path)
    ap.add_argument('--props-only', action='store_true')
    a = ap.parse_args()
    S, ST = a.stage.resolve(), a.vendor_stage.resolve()
    st = json.loads((S / 'stage.json').read_text())
    if st.get('stage') != 's2c-sensors':
        die('not an s2c-stage.py stage')
    for rel, lines in st['appends'].items():
        if (S / 'append' / rel).read_text() != ''.join(l + '\n' for l in lines):
            die(f'stage append {rel} changed')
    if a.props_only:
        bp = ST / 'build.prop'
        if not bp.is_file():
            die('no build.prop in the vendor stage')
        if any(l.startswith(('ro.vendor.a9ml.sensors=', 'persist.a9.sensors=')) for l in bp.read_text().splitlines()):
            die('build.prop already has sensors properties')
        append_lines(bp, st['appends']['build.prop'])
        print(f'  sensors: build.prop + {sum(1 for l in st["appends"]["build.prop"] if l and not l.startswith("#"))} properties'
              f' (test switch {"ON" if st["test_switch"] else "off"})')
        return
    if not a.kernel_image_sha256 or not a.fsconfig:
        die('--kernel-image-sha256 and --fsconfig are required')
    if a.kernel_image_sha256 != st['pins']['kernel_image_sha256']:
        die(f'the vendor modules are for kernel {a.kernel_image_sha256[:12]}, the stage for {st["pins"]["kernel_image_sha256"][:12]}')
    ent = st['entries']
    for rel, e in ent.items():
        p = S / 'tree' / rel
        if e['kind'] == 'file' and (not p.is_file() or p.is_symlink() or sha(p) != e['sha256']):
            die(f'stage file {rel} changed')
    skipped = []
    for rel, e in ent.items():
        d = ST / rel
        if e['kind'] == 'dir':
            if d.exists() and not e.get('existing'):
                die(f'{rel} already exists in the vendor stage')
            d.mkdir(parents=True, exist_ok=True)
            continue
        if d.exists() or d.is_symlink():
            if rel.startswith('lib/modules/') and rel.endswith('.ko') and not d.is_symlink() and sha(d) == e['sha256']:
                skipped.append(rel)
                continue
            die(f'{rel} already exists in the vendor stage')
        shutil.copyfile(S / 'tree' / rel, d)
        os.chmod(d, int(e['mode'], 8))
    for rel in ('lib/modules/modules.options', 'etc/selinux/vendor_file_contexts'):
        if rel == 'etc/selinux/vendor_file_contexts' and not (ST / rel).is_file():
            die('no vendor_file_contexts in the vendor stage')
        append_lines(ST / rel, st['appends'][rel])
    append_lines(a.fsconfig, (S / 'fsconfig.txt').read_text().splitlines())
    n = sum(1 for e in ent.values() if e['kind'] == 'file') - len(skipped)
    print(f'  sensors: {n} files from stage {sha(S / "stage.json")[:12]} (bundle {st["pins"]["bundle_tuple"][:12]},'
          f' HAL {st["pins"]["hal"][:12]}), identical modules kept: {len(skipped)}')


if __name__ == '__main__':
    main()
