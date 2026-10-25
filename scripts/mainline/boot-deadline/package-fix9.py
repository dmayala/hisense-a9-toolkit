#!/usr/bin/env python3
"""Host-only FIX9 vendor: update only the two existing kernel digest markers."""
from pathlib import Path
import argparse
import importlib.util
import json
import subprocess as sp
import build as b


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    out = args.output.absolute()
    source = b.ROOT / 'plain-wrapped-fix8-20261023/vendor.img'
    image = out / 'vendor.img'
    assert b.sha(source) == 'cb848a7e92116f427e9c3746ad74e51bac11da8f37579f250663c60353e87d0f'
    assert not image.exists()
    spec = importlib.util.spec_from_file_location('metadata', b.HERE.parent / 'android/refresh-integrated-vendor.py')
    meta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(meta)
    data = (b.sha(out / 'Image.gz') + '\n').encode()
    paths = ['lib/modules/kernel-image.sha256', 'etc/thermal-a9.kernel-image.sha256']
    commands = []
    for i, path in enumerate(paths):
        mt = meta.metadata(source, path)
        local, label = out / f'marker-{i}', out / f'marker-label-{i}'
        local.write_bytes(data)
        label.write_bytes(mt['label'].encode() + b'\0')
        commands += [f'rm /{path}', f'write {local} /{path}',
                     f'sif /{path} mode 010{mt["mode"]}', f'sif /{path} uid {mt["uid"]}',
                     f'sif /{path} gid {mt["gid"]}', f'ea_set -f {label} /{path} security.selinux']
        commands += [f'sif /{path} {field} @{int(mt[field], 16)}'
                     for field in ('atime', 'mtime', 'ctime', 'crtime')]
    script = out / 'install-vendor.debugfs'
    script.write_text('\n'.join(commands) + '\n')
    sp.run(['cp', '--reflink=auto', '--sparse=always', str(source), str(image)], check=True)
    with (out / 'install-vendor.log').open('wb') as log:
        sp.run(['debugfs', '-w', '-f', str(script), str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    with (out / 'changed-ea_get.txt').open('wb') as log:
        for path in paths:
            assert meta.debug(image, 'cat /' + path).encode() == data
            assert meta.metadata(image, path) == meta.metadata(source, path)
            log.write(sp.check_output(['debugfs', '-R', f'ea_get /{path} security.selinux', str(image)], stderr=sp.DEVNULL))
    with (out / 'fsck.log').open('wb') as log:
        sp.run(['e2fsck', '-fn', str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    result = dict(source=str(source), sha256=b.sha(image), changed_files=paths,
                  fstab_unchanged=True, modules_unchanged=True, policy_unchanged=True,
                  phone_access=False, hardware_tested=False)
    (out / 'vendor-result.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'vendor.img.sha256').write_text(result['sha256'] + '  vendor.img\n')
    print('FIX9 vendor digest refresh complete', flush=True)


if __name__ == '__main__':
    main()
