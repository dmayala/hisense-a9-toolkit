#!/usr/bin/env python3
"""Host-only fix7: start the wrapped-key daemons after metadata, before late-fs.

Patch the exact fix6 vendor. Keep storage commands, timeouts, policy and binaries
unchanged. Trace requests separately from init's observed service states.
"""
import argparse
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess as sp

SOURCE_HASH = '121ae3e45ec768cf2744e7633f4939b5c8fae919dac7f6394b2ad2d6f79d6d6c'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(image, path):
    return sp.check_output(['debugfs', '-R', 'cat /' + path, str(image)], stderr=sp.DEVNULL)


def replace(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source, out = args.source.resolve(strict=True), args.output.absolute()
    assert digest(source) == SOURCE_HASH, 'expected exact fix6 vendor'
    image = out / 'vendor.img'
    assert not image.exists(), 'destination already exists'
    out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('metadata', Path(__file__).resolve().parents[1] / 'android/refresh-integrated-vendor.py')
    meta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(meta)
    hal, hw, shell = ('etc/init/init.a9-wrapped-keymaster.rc',
                     'etc/init/hw/init.hlte556n.rc', 'bin/a9ml-wrapped-start.sh')
    before = {p: read(source, p).decode() for p in (hal, hw, shell)}
    after = dict(before)
    assert after[hal].count('    disabled\n') == 2
    after[hal] = after[hal].replace('    disabled\n', '')
    after[hal] += '\non init\n    setprop vendor.a9.trace.keymaster_rc loaded\n'
    # Literal values avoid reading a listeners property that vendor_init cannot
    # read. These callbacks observe the real property; they never set readiness.
    for state in ('true', 'false'):
        after[hal] += (f'\non property:vendor.sys.listeners.registered={state}\n'
                       f'    setprop vendor.a9.trace.listeners {state}\n')
    for service, name in (('vendor.qseecomd', 'qsee'), ('vendor.keymaster-4-1', 'keymaster')):
        for state in ('running', 'stopped', 'restarting'):
            after[hal] += (f'\non property:init.svc.{service}={state}\n'
                           f'    setprop vendor.a9.trace.{name}_state {state}\n')
    old = ('    mkdir /dev/disk 0755 root root\n'
           '    mkdir /dev/disk/by-partlabel 0755 root root\n'
           '    symlink /dev/block/by-name/ssd /dev/disk/by-partlabel/ssd\n'
           '    setprop vendor.sys.listeners.registered false\n'
           '    start vendor.qseecomd\n'
           '    start vendor.keymaster-4-1\n')
    after[hw] = replace(after[hw], old, '')
    start = old.split('    setprop vendor.sys.listeners.registered false\n')[0]
    start += ('    setprop vendor.a9.trace.qsee_request before_start\n'
              '    start vendor.qseecomd\n'
              '    setprop vendor.a9.trace.qsee_request issued\n'
              '    setprop vendor.a9.trace.keymaster_request before_start\n'
              '    start vendor.keymaster-4-1\n'
              '    setprop vendor.a9.trace.keymaster_request issued\n')
    after[hw] = replace(after[hw], '    exec_start a9ml_metadata_mount\n',
        '    setprop vendor.a9.trace.fs metadata_exec\n'
        '    exec_start a9ml_metadata_mount\n'
        '    setprop vendor.a9.trace.fs metadata_returned\n' + start)
    for event in ('early-init', 'early-fs', 'post-fs', 'late-fs', 'post-fs-data', 'boot'):
        after[hw] = replace(after[hw], f'on {event}\n',
            f'on {event}\n    setprop vendor.a9.trace.{event.replace("-", "_")} entered\n')
    after[hw] = replace(after[hw], '    exec_start a9ml_wrapped_mount\n',
        '    setprop vendor.a9.trace.late_fs data_exec\n'
        '    exec_start a9ml_wrapped_mount\n'
        '    setprop vendor.a9.trace.late_fs data_returned\n')
    after[hw] = replace(after[hw], 'on property:sys.boot_completed=1\n    write /dev/kmsg',
        'on property:sys.boot_completed=1\n    setprop vendor.a9.trace.boot_completed true\n    write /dev/kmsg')
    after[shell] = replace(after[shell], 'case "$1" in\n',
        'trace() { setprop "vendor.a9.trace.$1" "$2" || :; }\ncase "$1" in\n')
    # Mark the next operation before it runs; failure/timeout leaves that marker.
    markers = (
        ('    bounded /system/bin/mount -t ext4', 'metadata', 'mount'),
        ('    bounded /system/bin/restorecon /metadata ||', 'metadata', 'restorecon'),
        ('    mkdir -p /metadata/a9-tee-persist/data', 'metadata', 'persist_dirs'),
        ('    bounded /system/bin/restorecon -R /metadata/a9-tee-persist', 'metadata', 'persist_restorecon'),
        ('    bounded /system/bin/mount --bind', 'metadata', 'persist_bind'),
        ('    bounded /system/bin/restorecon -R /mnt/vendor/persist', 'metadata', 'bind_restorecon'),
        ('    log "real metadata mounted"', 'metadata', 'complete'),
        ('        /system/bin/timeout -s KILL 60 /system/bin/vdc cryptfs mountFstab', 'data', 'mount_request'),
        ('        /system/bin/timeout -s KILL 60 /system/bin/vdc cryptfs encryptFstab', 'data', 'encrypt_request'),
        ('    setprop ro.crypto.state encrypted', 'data', 'f2fs_mounted'),
        ('    bounded /system/bin/restorecon -R /data/misc/adb', 'data', 'adb_restorecon'),
    )
    for prefix, key, value in markers:
        lines = after[shell].splitlines(True)
        hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        assert len(hits) == 1, prefix
        i = hits[0]
        indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
        lines.insert(i, f'{indent}trace {key} {value}\n')
        after[shell] = ''.join(lines)
    after[shell] = replace(after[shell], '\ndata)\n', '\ndata)\n    trace data entered\n')
    after[shell] = replace(after[shell], '    status=$?\n',
        '    status=$?\n    trace data_rc "$status"\n')
    # Trace additions must not replace or reorder any existing shell operation.
    stripped = '\n'.join(line for line in after[shell].split('\n')
                         if not line.lstrip().startswith(('trace ', 'trace()')))
    assert stripped == before[shell]
    commands = []
    for i, path in enumerate(before):
        local = out / 'overlay' / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(after[path])
        metadata = meta.metadata(source, path)
        label = out / f'label-{i}'
        label.write_bytes(metadata['label'].encode() + b'\0')
        commands += [f'rm /{path}', f'write {local} /{path}',
                     f'sif /{path} mode 010{metadata["mode"]}',
                     f'sif /{path} uid {metadata["uid"]}', f'sif /{path} gid {metadata["gid"]}',
                     f'ea_set -f {label} /{path} security.selinux']
        commands += [f'sif /{path} {field} @{int(metadata[field], 16)}'
                     for field in ('atime', 'mtime', 'ctime', 'crtime')]
    batch = out / 'install.debugfs'
    batch.write_text('\n'.join(commands) + '\n')
    sp.run(['cp', '--reflink=auto', '--sparse=always', str(source), str(image)], check=True)
    with (out / 'install.log').open('wb') as log:
        sp.run(['debugfs', '-w', '-f', str(batch), str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    with (out / 'changed-ea_get.txt').open('wb') as log:
        for path in before:
            assert read(image, path) == after[path].encode(), path
            assert meta.metadata(image, path) == meta.metadata(source, path), path
            log.write(sp.check_output(['debugfs', '-R', f'ea_get /{path} security.selinux', str(image)], stderr=sp.DEVNULL))
    with (out / 'fsck.log').open('wb') as log:
        sp.run(['e2fsck', '-fn', str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    (out / 'vendor.patch').write_text(''.join(''.join(difflib.unified_diff(
        before[p].splitlines(True), after[p].splitlines(True),
        fromfile='fix6/' + p, tofile='fix7/' + p)) for p in before))
    result = dict(source=str(source), source_sha256=SOURCE_HASH, image=str(image),
                  sha256=digest(image), changed_files=list(before), fsck_rc=0,
                  phone_access=False, hardware_tested=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'vendor.img.sha256').write_text(result['sha256'] + '  vendor.img\n')
    print('WRAPPED FIX7 READY ' + str(image), flush=True)


if __name__ == '__main__':
    main()
