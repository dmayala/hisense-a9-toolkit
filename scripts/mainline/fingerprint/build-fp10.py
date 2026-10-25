#!/usr/bin/env python3
"""FP10: metadata-only diagnostic policy repair on the latest integrated10."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = Path('/home/user/A9-build/mainline')
BASE = ROOT / 'integ-20261024-10'
OUT = ROOT / 'integ-20261024-fp10'
STAGE = ROOT / 'fp10-host/policy-main'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    os.umask(0o077)
    assert (BASE / 'READY.txt').is_file() and not OUT.exists(), 'preserve published and partial candidates'
    OUT.mkdir()
    b = load('fp_build', HERE / 'build.py')
    b.OUT = OUT
    meta = load('fp_meta', HERE.parent / 'android/refresh-integrated-vendor.py')
    parent = json.loads((BASE / 'integrated-build.json').read_text())
    sp.run(['git', '-C', REPO, 'merge-base', '--is-ancestor', parent['source_commit'], 'HEAD'], check=True)
    for name, digest in parent['sha256'].items():
        assert b.sha(BASE / name) == digest, name
    assert parent['kernel_dt_ramdisk_modules_unchanged'] and parent['enforcing_root_console']
    for relative, digest in parent['source_sha256'].items():
        if relative not in ('fingerprint/onb.py', 'acceptance/onb.py', 'play/onb.py', 'android/sepolicy/rootsh-enforcing.cil'):
            assert b.sha(HERE.parent / relative) == digest, relative
    for relative, digest in parent['runtime_sha256'].items():
        assert b.sha(BASE / relative) == digest, relative
    pin = json.loads((STAGE / 'tests.json').read_text())
    assert pin['base_sha256'] == b.sha(BASE / 'vendor.img') and pin['passed'] and pin['permissive_domains'] == []
    for path in [HERE / 'policy-fp10.py', HERE / 'policy/fingerprint.cil', HERE / 'policy-check.c',
                 HERE.parent / 'acceptance/permissive.c', HERE.parent / 'android/sepolicy/rootsh-enforcing.cil']:
        assert b.sha(path) == pin['source_sha256'][path.name], path
    for name, digest in pin['payload'].items():
        assert b.sha(STAGE / name) == digest, name
    audit = parent['abi_audit']['vendors']['integ-20261023-6r3']
    assert audit['modules'] == 101 and audit['imports_checked'] == 6243
    assert not any(audit[k] for k in ('failing_modules', 'crc_mismatches', 'vermagic_mismatches'))
    b.check_fingerprint_dtb(BASE / 'integ.dtb')
    # No service, firmware, kernel, device-label or DT changes are needed.
    for name in ['etc/init/init.a9-fingerprint.rc', 'etc/vintf/manifest/fingerprint-a9.xml']:
        assert b.read(BASE / 'vendor.img', name) == (HERE / 'vendor' / name).read_bytes(), name
    original, image = BASE / 'vendor.img', OUT / 'vendor.img'
    b.room(original.stat().st_blocks * 512 + 32 * 1024**2)
    b.run(['cp', '--reflink=auto', '--sparse=always', original, image], 'vendor-copy')
    batch, metadata = [], {}
    for i, name in enumerate(pin['payload']):
        relative = 'etc/selinux/' + name
        info = meta.metadata(original, relative)
        metadata[relative] = info
        label = OUT / ('label-' + str(i))
        label.write_bytes((info['label'] + '\0').encode())
        batch += [f'rm /{relative}', f'write {STAGE / name} /{relative}', f'sif /{relative} mode 010{info["mode"]}',
                  f'sif /{relative} uid {info["uid"]}', f'sif /{relative} gid {info["gid"]}',
                  f'ea_set -f {label} /{relative} security.selinux']
        for field in ('atime', 'mtime', 'ctime', 'crtime'):
            batch.append(f'sif /{relative} {field} @{int(info[field], 16)}')
    script = OUT / 'install.debugfs'
    script.write_text('\n'.join(batch) + '\n')
    b.run(['debugfs', '-w', '-f', script, image], 'vendor-install')
    files = sorted(meta.vendor_files(original))
    assert files == sorted(meta.vendor_files(image)), 'vendor inventory'
    changed = [name for name in files if b.read(original, name) != b.read(image, name)]
    assert changed == sorted(metadata), changed
    for relative, info in metadata.items():
        assert b.read(image, relative) == (STAGE / Path(relative).name).read_bytes()
        assert meta.metadata(image, relative) == info
    b.run(['e2fsck', '-fn', image], 'vendor-fsck')
    artifacts = ['boot.img', 'Image.gz', 'integ.dtb', 'recovery_dtbo.img', 'kernel.config', 'Module.symvers', 'vmlinux.symvers']
    for name in artifacts:
        (OUT / name).hardlink_to(BASE / name)
    (OUT / 'module-audit-integ6r3.json').hardlink_to(BASE / 'module-audit-integ6r3.json')
    b.run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3', '-P',
           REPO / 'scripts/abl-check.py', OUT / 'boot.img', 'boot', '--dtbo', OUT / 'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((OUT / 'abl-check.log').read_text())['result'] == 'PASS'
    b.run(['python3', '-I', HERE / 'smoke.py'], 'hook-smoke')
    # Carry the complete latest integrated runtime, including stock boot
    # identity, FBE diagnostics, assets and policy inputs. Never edit a linked
    # parent inode when installing the updated fingerprint hook.
    for directory in ('acceptance-assets', 'sleep-logger', 'runtime'):
        shutil.copytree(BASE / directory, OUT / directory, copy_function=os.link)
    refreshed = ['fingerprint/onb.py', 'acceptance/onb.py', 'play/onb.py', 'android/sepolicy/rootsh-enforcing.cil']
    for relative in refreshed:
        dest = OUT / 'runtime' / relative
        dest.unlink()
        shutil.copyfile(HERE.parent / relative, dest)
        if dest.suffix == '.py': dest.chmod(0o755)
    for name in ('run.sh', 'run-fbe.sh', 'onb-integ10.py'):
        (OUT / name).hardlink_to(BASE / name)
    runner = OUT / 'run-fingerprint.sh'
    runner.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                      'image=$(cd -- "$(dirname -- "$0")" && pwd)\n'
                      'export A9_SWEEP_IMAGE="$image" A9_SWEEP_ASSETS="$image/acceptance-assets"\n'
                      'export A9_BTRY_SOUND=0 A9_BTRY_HOLD=240\n'
                      'export A9_BTRY_ONB="$image/runtime/fingerprint/onb.py"\n'
                      'exec "$image/runtime/boot-deadline/b-try.sh" "$image/boot.img" "$image/vendor.img"\n')
    runner.chmod(0o755)
    for name in ('run.sh', 'run-fbe.sh', 'run-fingerprint.sh'):
        b.run(['bash', '-n', OUT / name], name + '-syntax')
    hashes = {name: b.sha(OUT / name) for name in ['vendor.img'] + artifacts}
    sources = {str(p.relative_to(REPO)): b.sha(p) for p in HERE.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    sources.update({str((HERE.parent / n).relative_to(REPO)): b.sha(HERE.parent / n) for n in refreshed})
    runtime = [p for p in (OUT / 'runtime').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    runtime += [OUT / n for n in ('run.sh', 'run-fbe.sh', 'run-fingerprint.sh', 'onb-integ10.py')]
    receipt = dict(parent=str(BASE), parent_sha256=parent['sha256'], sha256=hashes,
                   source_commit=sp.check_output(['git', '-C', REPO, 'rev-parse', 'HEAD'], text=True).strip(),
                   source_sha256=sources, runtime_sha256={str(p.relative_to(OUT)): b.sha(p) for p in runtime},
                   host_only=True, hardware_tested=False, vendor_changes=changed, other_vendor_files_identical=len(files)-len(changed),
                   vendor_metadata_preserved=True, kernel_and_boot_unchanged=True, stock_boot_identity_preserved=True,
                   policy_tests=pin, inherited_module_audit=parent['abi_audit'], fbe_fixed=False,
                   inherited_unresolved='real FBE reopen and remaining integ9 hardware gaps still need owner runs', free_bytes=b.room())
    (OUT / 'integrated-build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (OUT / 'SHA256SUMS').write_text(''.join(v + '  ' + n + '\n' for n, v in hashes.items()))
    (OUT / 'vendor.img.sha256').write_text(hashes['vendor.img'] + '  vendor.img\n')
    (OUT / 'READY.txt').write_text('FP10 host PASS; hardware fingerprint qualification pending.\nRun: ' + str(runner) + '\n')
    print('FP10 integrated image host PASS; enforcing metadata-only policy repair; integrated10 runtime retained', flush=True)


if __name__ == '__main__':
    main()
