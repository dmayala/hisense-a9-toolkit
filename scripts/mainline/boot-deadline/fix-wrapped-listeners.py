#!/usr/bin/env python3
"""Fix6 vendor-only repair for the observed SSD EACCES and missing HAL alias.

Uses the exact fix5 image and existing matching policy compiler inputs. Stock
listener binaries/libraries and system remain unchanged. No device operations.
"""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import subprocess as sp

ROOT = Path.home() / 'A9-build/mainline'
SOURCE_HASH = '69346de08abe1d6bcbd32c5e57630957680ac868d9bb3856a741e90bfb18f568'
SSD_HASH = '8db9eedf1984723ac9811e476478edbebaf4c9f5af734b8e832bfb8a8faec982'
OLD_POLICY = ROOT / 'wrapped-fbe-test-r3-20261021/wrapped-activation/policy'
SECILC = ROOT / 'android/dl/cf_arm64_x/otatools/bin/secilc'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(image, path):
    return sp.check_output(['debugfs', '-R', 'cat /' + path, str(image)], stderr=sp.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path, help='fresh output directory')
    args = parser.parse_args()
    source, out = args.source.resolve(strict=True), args.output.absolute()
    assert file_digest(source) == SOURCE_HASH, 'expected exact fix5 vendor'
    image = out / 'vendor.img'
    assert not image.exists(), 'destination image already exists'
    out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('metadata', Path(__file__).resolve().parents[1] / 'android/refresh-integrated-vendor.py')
    meta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(meta)
    assert digest(read(source, 'lib64/a9-wrapped-stock/libssd.so')) == SSD_HASH
    # The current HW init already starts qseecomd before keymaster and before
    # the vold worker. Preserve that actual sequence and stock readiness wait.
    hardware = read(source, 'etc/init/hw/init.hlte556n.rc').decode()
    assert '    start vendor.qseecomd\n    start vendor.keymaster-4-1\n    exec_start a9ml_wrapped_mount\n' in hardware
    paths = ('etc/init/init.a9-wrapped-keymaster.rc', 'etc/ueventd.rc',
             'etc/selinux/vendor_file_contexts', 'etc/selinux/vendor_sepolicy.cil')
    original = {p: read(source, p).decode() for p in paths}
    text = dict(original)
    rc = paths[0]
    alias = '    interface android.hardware.keymaster@4.1::IKeymasterDevice default\n'
    assert text[rc].count(alias) == 1
    text[rc] = text[rc].replace(alias, '    interface android.hardware.keymaster@4.0::IKeymasterDevice default\n' + alias)
    # Kernel disk letters are not a partition identity. Ueventd matches both
    # DAC and SELinux labels against the node's partition-name symlinks.
    uevent = paths[1]
    old = '/dev/block/sda6 0660 root system\n'
    assert text[uevent].count(old) == 1
    text[uevent] = text[uevent].replace(old, '/dev/block/by-name/ssd 0660 root system\n')
    contexts = paths[2]
    old = '/dev/block/sda6 u:object_r:a9ml_qsee_ssd_device:s0\n'
    assert text[contexts].count(old) == 1
    text[contexts] = text[contexts].replace(old,
        '/dev/block/by-name/ssd u:object_r:a9ml_qsee_ssd_device:s0\n'
        '/dev/block/bootdevice/by-name/ssd u:object_r:a9ml_qsee_ssd_device:s0\n')
    policy = paths[3]
    old = '(allow tee_202504 sysfs_wake_lock_202504 (file (open write getattr)))'
    assert text[policy].count(old) == 1
    text[policy] = text[policy].replace(old, '(allow tee_202504 sysfs_wake_lock_202504 (file (open write append getattr)))')
    payload = {}
    for path, data in text.items():
        local = out / 'overlay' / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(data)
        payload[path] = local
    # Reuse the matching, frozen platform policy inputs already used by this
    # development vendor; substitute its CURRENT vendor/public CIL, including
    # existing logger rules. Keep original permissive-domain decisions intact.
    inputs = out / 'policy-inputs'
    inputs.mkdir()
    old_cmd = json.loads((OLD_POLICY / 'policy-command.json').read_text())
    old_inputs = old_cmd[old_cmd.index('30') + 1:old_cmd.index('-o')]
    assert len(old_inputs) == 8
    current = []
    for i, old_path in enumerate(old_inputs):
        name = Path(old_path).name
        data = (read(source, 'etc/selinux/plat_pub_versioned.cil') if i == 6 else
                text[policy].encode() if i == 7 else Path(old_path).read_bytes())
        local = inputs / name
        local.write_bytes(data)
        current.append(local)
    binary_path = 'etc/selinux/precompiled_sepolicy'
    binary = out / 'overlay' / binary_path
    command = [str(SECILC), '-D', '-m', '-M', 'true', '-G', '-N', '-c', '30',
               *map(str, current), '-o', str(binary), '-f', '/dev/null']
    (out / 'policy-command.json').write_text(json.dumps(command, indent=2) + '\n')
    with (out / 'policy-build.log').open('wb') as log:
        sp.run(command, stdout=log, stderr=sp.STDOUT, check=True)
    payload[binary_path] = binary
    commands = []
    for i, (path, local) in enumerate(payload.items()):
        metadata = meta.metadata(source, path)
        label = out / ('label-' + str(i))
        label.write_bytes(metadata['label'].encode() + b'\0')
        commands += ['rm /' + path, f'write {local} /{path}',
                     f'sif /{path} mode 010{metadata["mode"]}',
                     f'sif /{path} uid {metadata["uid"]}', f'sif /{path} gid {metadata["gid"]}',
                     f'ea_set -f {label} /{path} security.selinux']
        for field in ('atime', 'mtime', 'ctime', 'crtime'):
            commands.append(f'sif /{path} {field} @{int(metadata[field], 16)}')
    batch = out / 'install.debugfs'
    batch.write_text('\n'.join(commands) + '\n')
    sp.run(['cp', '--reflink=auto', '--sparse=always', str(source), str(image)], check=True)
    with (out / 'install.log').open('wb') as log:
        sp.run(['debugfs', '-w', '-f', str(batch), str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    for path, local in payload.items():
        assert read(image, path) == local.read_bytes(), path
        assert meta.metadata(image, path) == meta.metadata(source, path), path
    with (out / 'fsck.log').open('wb') as log:
        sp.run(['e2fsck', '-fn', str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    (out / 'vendor.patch').write_text(''.join(''.join(difflib.unified_diff(
        original[p].splitlines(True), text[p].splitlines(True), fromfile='fix5/' + p,
        tofile='fix6/' + p)) for p in paths))
    result = {'source': str(source), 'source_sha256': SOURCE_HASH,
              'image': str(image), 'sha256': file_digest(image),
              'changed_files': {p: digest(local.read_bytes()) for p, local in payload.items()},
              'policy_inputs': {str(p): file_digest(p) for p in current},
              'stock_ssd_library_unchanged_sha256': SSD_HASH,
              'keystore2_observed_start_pid_in_fix5_log': 1282,
              'system_modified': False, 'boot_modified': False,
              'fsck_rc': 0, 'phone_access': False, 'hardware_tested': False}
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'vendor.img.sha256').write_text(result['sha256'] + '  vendor.img\n')
    print('WRAPPED FIX6 READY ' + str(image), flush=True)


if __name__ == '__main__':
    main()
