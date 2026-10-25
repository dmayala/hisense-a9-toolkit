#!/usr/bin/env python3
"""Host-only FIX11 vendor: FIX10 plus rebuilt modules/digests and casefold on."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import difflib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess as sp
import build as b


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    out = args.output.absolute()
    base = b.ROOT / 'plain-wrapped-fix10-20261023/vendor.img'
    expected = '21e0eba349e25bdd01800b08a704f8a5a401af7ccddc8614c8c09862878f6e94'
    assert b.sha(base) == expected
    image = out / 'vendor.img'
    assert not image.exists()
    meta = load('metadata', b.HERE.parent / 'android/refresh-integrated-vendor.py')
    helpers = load('startup', b.HERE / 'fix-wrapped-startup.py')
    targets = json.loads((out / 'module-targets.json').read_text())
    modules = {p: (out / 'modules' / Path(p).name).read_bytes() for p in targets}
    assert len(modules) == 100
    assert len(list((out / 'modules').glob('*.ko'))) == 100
    changes = dict(modules)
    hw = 'etc/init/hw/init.hlte556n.rc'
    before = helpers.read(base, hw).decode()
    after = helpers.replace(before,
        '    # Match this kernel: CONFIG_UNICODE is absent; do not format casefold.\n'
        '    setprop external_storage.casefold.enabled false\n',
        '    # FIX11 kernel and all vendor modules have CONFIG_UNICODE=y.\n'
        '    setprop external_storage.casefold.enabled true\n')
    changes[hw] = after.encode()
    for path in ('lib/modules/kernel-image.sha256', 'etc/thermal-a9.kernel-image.sha256'):
        changes[path] = (b.sha(out / 'Image.gz') + '\n').encode()
    # Real depmod computes the new dependency closure; preserve the intentional
    # empty auto-load alias/softdep files and every modules.load/profile/options.
    import re
    import gzip
    release = re.search(rb'Linux version ([^ ]+)', gzip.decompress((out / 'Image.gz').read_bytes())).group(1).decode()
    staging = out / 'depmod/lib/modules' / release
    staging.mkdir(parents=True)
    for p in (out / 'modules').glob('*.ko'):
        (staging / p.name).symlink_to(p)
    for name in ('modules.builtin', 'modules.builtin.modinfo'):
        shutil.copyfile(out / 'scratch/obj' / name, staging / name)
    with (out / 'depmod.log').open('wb') as log:
        sp.run(['depmod', '-b', str(out / 'depmod'), '-e', '-E', str(out / 'Module.symvers'), release],
               stdout=log, stderr=sp.STDOUT, check=True)
    changes['lib/modules/modules.dep'] = (staging / 'modules.dep').read_bytes()
    overlay = out / 'vendor-overlay'
    batch = []
    for i, (path, data) in enumerate(changes.items()):
        local = overlay / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        mt = meta.metadata(base, path)
        label = out / f'label-{i}'
        label.write_bytes(mt['label'].encode() + b'\0')
        batch += [f'rm /{path}', f'write {local} /{path}', f'sif /{path} mode 010{mt["mode"]}',
                  f'sif /{path} uid {mt["uid"]}', f'sif /{path} gid {mt["gid"]}',
                  f'ea_set -f {label} /{path} security.selinux']
        batch += [f'sif /{path} {field} @{int(mt[field], 16)}'
                  for field in ('atime', 'mtime', 'ctime', 'crtime')]
    verifier = b.ROOT / 'android/dl/cf_arm64_x/otatools/bin/host_init_verifier'
    with (out / 'init-verifier.log').open('wb') as log:
        sp.run([str(verifier), str(overlay / hw)], stdout=log, stderr=sp.STDOUT, check=True)
    script = out / 'install.debugfs'
    script.write_text('\n'.join(batch) + '\n')
    sp.run(['cp', '--reflink=auto', '--sparse=always', str(base), str(image)], check=True)
    with (out / 'install.log').open('wb') as log:
        sp.run(['debugfs', '-w', '-f', str(script), str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    with (out / 'changed-ea_get.txt').open('wb') as log:
        for path, data in changes.items():
            assert helpers.read(image, path) == data, path
            assert meta.metadata(image, path) == meta.metadata(base, path), path
            log.write((path + '\n').encode())
            log.write(sp.check_output(['debugfs', '-R', f'ea_get /{path} security.selinux', str(image)], stderr=sp.DEVNULL))
    with (out / 'fsck.log').open('wb') as log:
        sp.run(['e2fsck', '-fn', str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    old, new = set(meta.vendor_files(base)), set(meta.vendor_files(image))
    assert old == new

    def compare(path):
        return path if helpers.read(base, path) != helpers.read(image, path) else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        actual_changes = sorted(filter(None, pool.map(compare, sorted(old))))
    assert set(actual_changes) <= set(changes)
    assert all(p in actual_changes for p in targets), 'all 100 rebuilt modules must be replaced'
    audit_source = b.ROOT / 'plain-wrapped-fix9-20261023/audit-modules.py'
    shutil.copyfile(audit_source, out / 'audit-modules.py')
    with (out / 'module-audit.log').open('wb') as log:
        sp.run(['python3', str(out / 'audit-modules.py'), str(out), 'fix11', str(image)],
               stdout=log, stderr=sp.STDOUT, check=True)
    audit = json.loads((out / 'module-audit-fix11.json').read_text())
    checks = next(iter(audit['vendors'].values()))
    assert checks['modules'] == 100
    assert checks['failing_modules'] == checks['crc_mismatches'] == checks['vermagic_mismatches'] == 0
    (out / 'casefold.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
        fromfile='fix10/' + hw, tofile='fix11/' + hw)))
    result = dict(base=str(base), base_sha256=expected,
                  sha256={p: b.sha(out / p) for p in ('boot.img', 'vendor.img', 'Image.gz', 'kernel.config', 'Module.symvers')},
                  module_audit={k: v for k, v in checks.items() if k != 'details'},
                  changed_files=actual_changes, unchanged_regular_files=len(old)-len(actual_changes),
                  casefold_enabled=True, unicode_builtin=True, fresh_data_required=True,
                  nonkernel_boot_payloads_and_cmdline_unchanged=True,
                  fsck_rc=0, init_verifier_rc=0, abl_result='PASS',
                  hardware_tested=False, phone_access=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(''.join(d + '  ' + p + '\n' for p, d in result['sha256'].items()))
    (out / 'vendor.img.sha256').write_text(result['sha256']['vendor.img'] + '  vendor.img\n')
    print('UNICODE FIX11 READY ' + str(out / 'boot.img') + ' ' + str(image), flush=True)


if __name__ == '__main__':
    main()
