#!/usr/bin/env python3
"""Host-only two-RC correction of the signed row27 release vendor.

Enforce the existing ro.adb.secure=1 setting before adbd starts, and explicitly
start the stock-timed watchdog under its own init service name. Reuse all signed
payloads, keys, GSI and boot bytes. Never execute the included phone runner.
"""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import shutil
import subprocess as sp

ROOT = Path.home() / 'A9-build/mainline'
BASE = ROOT / 'row27-release-20261022'
SOURCE_SHA = 'a0740c6810882e6cbf845036a492bae83954359858f05a565dc097894c73ee3d'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(image, path):
    return sp.check_output(['debugfs', '-R', 'cat /' + path, str(image)], stderr=sp.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='directory without a vendor.img')
    out = parser.parse_args().output.absolute()
    source = BASE / 'vendor.img'
    image = out / 'vendor.img'
    assert sha(source) == SOURCE_SHA
    assert not image.exists()
    out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('metadata', Path(__file__).resolve().parents[1] / 'android/refresh-integrated-vendor.py')
    meta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(meta)
    props = read(source, 'build.prop').decode().splitlines()
    assert props.count('ro.adb.secure=1') == 1
    assert props.count('ro.secure=1') == 1 and props.count('ro.debuggable=0') == 1
    hardware = 'etc/init/hw/init.hlte556n.rc'
    watchdog = 'etc/init/init.a9-watchdog.rc'
    before = {p: read(source, p).decode() for p in (hardware, watchdog)}
    assert before[hardware].count('\non init\n') == 1
    # on init follows bootstrap APEX setup and precedes class-core/USB adbd.
    # exec runs in init, not its vendor filesystem subcontext; use the GSI's
    # existing enforcing resetprop domain and binary, with no new grants.
    after = {hardware: before[hardware].replace('\non init\n', '''
on init
    # Enforce authenticated ADB before adbd reads its startup properties.
    exec u:r:phhsu_daemon:s0 root -- /system/bin/resetprop_phh -n ro.adb.secure 1
''')}
    assert 'service watchdogd-gsi /system/bin/watchdogd 9 5\n' in before[watchdog]
    after[watchdog] = '''# Same stock timing: 9s pet request + 5s margin = 14s bite.
# Kernel retains 11s bark and 9360ms minimum pet spacing.
# A vendor override cannot replace the platform watchdogd-gsi service.
service watchdogd /system/bin/watchdogd 9 5
    class core
    oneshot
    disabled
    user root
    group root
    seclabel u:r:watchdogd:s0

on post-fs
    stop watchdogd-gsi
    wait_for_prop init.svc.watchdogd-gsi stopped
    start watchdogd
'''
    commands = []
    for i, (path, text) in enumerate(after.items()):
        local = out / 'overlay' / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(text)
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
    for path, text in after.items():
        assert read(image, path) == text.encode(), path
        assert meta.metadata(image, path) == meta.metadata(source, path), path
    with (out / 'fsck.log').open('wb') as log:
        sp.run(['e2fsck', '-fn', str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    (out / 'vendor.patch').write_text(''.join(''.join(difflib.unified_diff(
        before[p].splitlines(True), after[p].splitlines(True),
        fromfile='release/' + p, tofile='release2/' + p)) for p in after))
    for name in ('boot.img', 'boot-plain.img'):
        shutil.copyfile(BASE / name, out / name)
    hashes = {name: sha(out / name) for name in ('boot.img', 'boot-plain.img', 'vendor.img')}
    for name, value in hashes.items():
        (out / (name + '.sha256')).write_text(value + '  ' + name + '\n')
    result = {'source': str(source), 'source_sha256': SOURCE_SHA, 'sha256': hashes,
              'changed_files': list(after), 'metadata_labels_preserved': True,
              'adb_auth_enforced_at': 'on init, before core/USB adbd starts',
              'watchdog_service': 'watchdogd', 'watchdog_arguments': [9, 5],
              'watchdog_previous_owner': 'stop watchdogd-gsi; wait for stopped; start watchdogd',
              'system_modified': False, 'policy_modified': False, 'keys_modified': False,
              'signed_apexes_modified': False, 'boot_modified': False,
              'fsck_rc': 0, 'phone_access': False, 'hardware_tested': False}
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print('ROW27 RELEASE2 READY ' + str(image), flush=True)


if __name__ == '__main__':
    main()
