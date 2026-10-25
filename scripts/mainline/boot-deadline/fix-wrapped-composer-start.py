#!/usr/bin/env python3
"""Host-only FIX10: start composer after APEX configuration, before class hal.

FIX9 starts vendor.hwcomposer-3 at early-fs, permanently selecting init's
bootstrap mount namespace. Its restarts cannot load mapper.minigbm.so from
the vendor sphal namespace. Restore plain-integ's early-boot events for both
E Ink and vkms, keeping the successful wrapped-key storage path and FIX9 boot.

Android init explains the persistent namespace selection in SetMountNamespace:
https://android.googlesource.com/platform/system/core/+/refs/heads/main/init/service.cpp
The supplied GSI performs perform_apex_config in post-fs-data, before early-boot.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import difflib
import importlib.util
import json
from pathlib import Path
import subprocess as sp


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    helpers = load('startup', here / 'fix-wrapped-startup.py')
    meta = load('metadata', here.parent / 'android/refresh-integrated-vendor.py')
    root = Path.home() / 'A9-build/mainline'
    source = root / 'plain-wrapped-fix9-20261023/vendor.img'
    boot = source.with_name('boot.img')
    integ = root / 'plain-integ-20261022/vendor.img'
    expected = 'c1ecb4faf3ff2bb206f60d399e903571b5b8e9c5c7ffa36b6d201d9bebc905c9'
    boot_sha = '6ca1418318c6fd88eb0b36dcd7750bbee458b67ac49d941f229c2725019539a3'
    assert helpers.digest(source) == expected, 'expected exact FIX9 vendor'
    assert helpers.digest(boot) == boot_sha, 'expected exact FIX9 boot'
    out = args.output.absolute()
    out.mkdir(parents=True, exist_ok=True)
    image = out / 'vendor.img'
    assert not image.exists(), 'output already exists'
    paths = ('etc/init/init.a9ml-eink.rc', 'etc/init/hw/init.hlte556n.rc')
    before = {p: helpers.read(source, p).decode() for p in paths}
    after = {}
    for path, eink in zip(paths, ('1', '0')):
        after[path] = helpers.replace(before[path],
            f'on early-fs && property:ro.boot.a9ml.eink={eink}\n',
            f'on early-boot && property:ro.boot.a9ml.eink={eink}\n')
    # E Ink now exactly matches the already booting plain-integ RC.
    assert after[paths[0]].encode() == helpers.read(integ, paths[0])
    # Native init parser checks the actual replacement files, without device access.
    verifier = root / 'android/dl/cf_arm64_x/otatools/bin/host_init_verifier'
    commands = []
    with (out / 'init-verifier.log').open('wb') as log:
        for i, path in enumerate(paths):
            local = out / 'overlay' / path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(after[path])
            sp.run([str(verifier), str(local)], stdout=log, stderr=sp.STDOUT, check=True)
            mt = meta.metadata(source, path)
            label = out / f'label-{i}'
            label.write_bytes(mt['label'].encode() + b'\0')
            commands += [f'rm /{path}', f'write {local} /{path}',
                         f'sif /{path} mode 010{mt["mode"]}',
                         f'sif /{path} uid {mt["uid"]}', f'sif /{path} gid {mt["gid"]}',
                         f'ea_set -f {label} /{path} security.selinux']
            commands += [f'sif /{path} {field} @{int(mt[field], 16)}'
                         for field in ('atime', 'mtime', 'ctime', 'crtime')]
    batch = out / 'install.debugfs'
    batch.write_text('\n'.join(commands) + '\n')
    sp.run(['cp', '--reflink=auto', '--sparse=always', str(source), str(image)], check=True)
    with (out / 'install.log').open('wb') as log:
        sp.run(['debugfs', '-w', '-f', str(batch), str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    with (out / 'changed-ea_get.txt').open('wb') as log:
        for path in paths:
            assert helpers.read(image, path) == after[path].encode(), path
            assert meta.metadata(image, path) == meta.metadata(source, path), path
            log.write(sp.check_output(['debugfs', '-R', f'ea_get /{path} security.selinux', str(image)], stderr=sp.DEVNULL))
    with (out / 'fsck.log').open('wb') as log:
        sp.run(['e2fsck', '-fn', str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    old_files, new_files = set(meta.vendor_files(source)), set(meta.vendor_files(image))
    assert old_files == new_files

    def changed(path):
        return path if helpers.read(source, path) != helpers.read(image, path) else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        changes = sorted(filter(None, pool.map(changed, sorted(old_files))))
    assert changes == sorted(paths), changes
    sp.run(['cp', '--reflink=auto', str(boot), str(out / 'boot.img')], check=True)
    assert helpers.digest(out / 'boot.img') == boot_sha
    (out / 'vendor.patch').write_text(''.join(''.join(difflib.unified_diff(
        before[p].splitlines(True), after[p].splitlines(True),
        fromfile='fix9/' + p, tofile='fix10/' + p)) for p in paths))
    result = dict(source=str(source), source_sha256=expected, image=str(image),
                  sha256=helpers.digest(image), boot_source=str(boot), boot_sha256=boot_sha,
                  changed_files=changes, unchanged_regular_files=len(old_files)-len(paths),
                  unchanged_modules=sum(p.startswith('lib/modules/') and p.endswith('.ko') for p in old_files),
                  kernel_modified=False, policy_modified=False,
                  fsck_rc=0, init_verifier_rc=0, phone_access=False, hardware_tested=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    for name, digest in [('vendor.img', result['sha256']), ('boot.img', boot_sha)]:
        (out / (name + '.sha256')).write_text(digest + '  ' + name + '\n')
    print('WRAPPED FIX10 READY ' + str(image), flush=True)


if __name__ == '__main__':
    main()
