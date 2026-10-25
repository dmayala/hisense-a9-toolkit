#!/usr/bin/env python3
"""Host-only enforcing derivative: linked policy, real inode labels and boot mode.

Input images are immutable. Keep all generated files local (private vendor).
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess as sp

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / 'A9-build/mainline/android/cache/gsi-sepolicy/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8-3569860608-1792259254'


def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def debug(image, command):
    return sp.run(['debugfs', '-R', command, str(image)], check=True,
                  capture_output=True).stdout


def entries(image, directory=''):
    for row in debug(image, 'ls -p /' + directory).decode().splitlines():
        fields = row.split('/')
        if len(fields) < 7 or fields[5] in ('.', '..'):
            continue
        path = directory + '/' + fields[5] if directory else fields[5]
        mode = int(fields[2], 8)
        yield path, mode
        if mode & 0o170000 == 0o040000:
            yield from entries(image, path)


def boot_enforcing(source, target, qualification_audit=False):
    old = source.read_bytes()
    page = struct.unpack_from('<I', old, 36)[0]
    assert page == 4096 and struct.unpack_from('<I', old, 40)[0] == 2
    header = bytearray(old[:page])
    primary = header[64:576].split(b'\0')[0].decode().split()
    extra = header[608:1632].split(b'\0')[0].decode().split()
    remove = ('enforcing', 'selinux_force_permissive', 'androidboot.selinux', 'selinux',
              'androidboot.a9ml.audit_qualification', 'androidboot.a9audit',
              'audit', 'audit_backlog_limit')
    primary = [a for a in primary if a.split('=')[0] not in remove]
    extra = [a for a in extra if a.split('=')[0] not in remove]
    # ABL ignores extra_cmdline. Preserve every unrelated primary argument and
    # put the actual mode/switch here. selinux_force_permissive is a zero-init
    # bool in this kernel: removing its old =1 restores false without a =0.
    # Android's explicit enforcing property is redundant with its default;
    # R6/R7 already booted enforcing with all their mode flags in ignored extra.
    primary += ['enforcing=1']
    if qualification_audit:
        primary += ['androidboot.a9audit=1']
    primary = (' '.join(primary) + (' ' if extra else '')).encode()
    extra = ' '.join(extra).encode()
    if len(primary) >= 512 or len(extra) >= 1024:
        raise RuntimeError('enforcing/audit switch must fit ABL primary cmdline; do not spill critical arguments')
    header[64:576] = primary.ljust(512, b'\0')
    header[608:1632] = extra.ljust(1024, b'\0')
    # The boot ID covers segments/sizes, not cmdline. Every segment is retained.
    target.write_bytes(header + old[page:])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--vendor', required=True, type=Path)
    ap.add_argument('--boot', required=True, type=Path)
    ap.add_argument('--platform-cache', type=Path, default=CACHE)
    ap.add_argument('--libselinux', type=Path, default=Path('/nix/store/34lr4lwh8wxk86zrbhw3c91cjcxfwd80-libselinux-3.8.1/lib/libselinux.so.1'))
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--min-free-gib', type=int, default=15, help='free-space reserve after vendor copy (R6 user authorizes8 GiB)')
    ap.add_argument('--qualification-audit', action='store_true',
                    help='primary boot switch, read-back audit rate0/backlog8192, no dontaudit')
    a = ap.parse_args()
    os.umask(0o077)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if a.min_free_gib < 8:
        ap.error('--min-free-gib must be at least8')
    if shutil.disk_usage(out).free <= a.min_free_gib*1024**3 + a.vendor.stat().st_size:
        raise RuntimeError(f'need more than {a.min_free_gib} GiB reserve plus copied vendor size')
    policy = out / 'policy'
    command = ['python3', '-I', HERE / 'build-draft.py', '--enforcing',
               '--vendor-image', a.vendor, '--platform-cache', a.platform_cache, '--out', policy]
    if a.qualification_audit:
        command.append('--qualification-audit')
    with (out / 'policy-build.log').open('w') as log:
        sp.run(list(map(str, command)), check=True, stdout=log, stderr=sp.STDOUT)
    vendor = out / 'vendor.img'
    # Preserve holes in ext4 free space on a shared, low-disk host. The output
    # remains an ordinary raw image; no sparse Android image format is used.
    sp.run(['cp', '--reflink=auto', '--sparse=always', str(a.vendor), str(vendor)], check=True)
    # Existing f has ample free blocks/inodes for the small policy replacement.
    # Keep filesystem geometry; only write policy/init and xattrs in this copy.
    batch = []
    payload = policy / 'overlay'
    labels = out / 'labels'
    labels.mkdir()
    for i, src in enumerate(sorted(payload.rglob('*'))):
        if not src.is_file():
            continue
        path = str(src.relative_to(payload))
        present = b'Inode:' in debug(vendor, 'stat /' + path)
        if present:
            batch += ['rm /' + path]
        mode = '0100755' if path.startswith('bin/') else '0100644'
        batch += [f'write {src} /{path}', f'sif /{path} mode {mode}',
                  f'sif /{path} uid 0', f'sif /{path} gid 0']
    install = out / 'install.debugfs'
    install.write_text('\n'.join(batch) + '\n')
    with (out / 'install.log').open('w') as log:
        sp.run(['debugfs', '-w', '-f', str(install), str(vendor)], check=True, stdout=log, stderr=sp.STDOUT)
    # Use Android's real regex matcher for every existing inode, preserving modes,
    # uid/gid/capabilities. This is more than copying file_contexts into the image.
    fc = out / 'file_contexts'
    plat = a.platform_cache / 'system/etc/selinux/plat_file_contexts'
    fc.write_bytes(plat.read_bytes() + b'\n' + (payload / 'etc/selinux/vendor_file_contexts').read_bytes())
    lib = ctypes.CDLL(str(a.libselinux))
    class Option(ctypes.Structure):
        _fields_ = [('type', ctypes.c_int), ('value', ctypes.c_char_p)]
    lib.selabel_open.argtypes = [ctypes.c_uint, ctypes.POINTER(Option), ctypes.c_uint]
    lib.selabel_open.restype = ctypes.c_void_p
    lib.selabel_lookup_raw.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p), ctypes.c_char_p, ctypes.c_int]
    lib.selabel_close.argtypes = [ctypes.c_void_p]
    lib.freecon.argtypes = [ctypes.c_void_p]
    opts = (Option*1)(Option(3, os.fsencode(fc)))
    handle = lib.selabel_open(0, opts, 1)
    if not handle:
        raise RuntimeError('cannot open merged file contexts')
    commands, inventory, label_files = [], {}, {}
    try:
        for path, mode in [('', 0o040755), *entries(vendor)]:
            context = ctypes.c_char_p()
            if lib.selabel_lookup_raw(handle, ctypes.byref(context), ('/vendor/' + path).rstrip('/').encode(), mode):
                raise RuntimeError('missing file context: ' + path)
            value = context.value.decode()
            lib.freecon(context)
            if value not in label_files:
                label = labels / str(len(label_files))
                label.write_bytes(value.encode() + b'\0')
                label_files[value] = label
            commands += [f'ea_set -f {label_files[value]} /{path} security.selinux']
            inventory[path] = value
    finally:
        lib.selabel_close(handle)
    script = out / 'labels.debugfs'
    script.write_text('\n'.join(commands) + '\n')
    with (out / 'labels.log').open('w') as log:
        sp.run(['debugfs', '-w', '-f', str(script), str(vendor)], check=True, stdout=log, stderr=sp.STDOUT)
    for src in sorted(payload.rglob('*')):
        if src.is_file():
            assert debug(vendor, 'cat /'+str(src.relative_to(payload))) == src.read_bytes(), src
    for path, expected in inventory.items():
        attr = debug(vendor, 'ea_get /'+path+' security.selinux').decode()
        assert expected in attr, path
    with (out / 'fsck.log').open('w') as log:
        sp.run(['e2fsck', '-fn', str(vendor)], check=True, stdout=log, stderr=sp.STDOUT)
    boot_enforcing(a.boot, out / 'boot.img', a.qualification_audit)
    result = {'status': 'HOST ENFORCING CANDIDATE; phone untested',
              'inputs': {str(p): sha(p) for p in (a.vendor, a.boot)},
              'inode_labels': inventory,
              'outputs': {name: sha(out / name) for name in ('boot.img', 'vendor.img')},
              'qualification_audit': a.qualification_audit,
              'platform_cache': str(a.platform_cache)}
    (out / 'build-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(result['status'])


if __name__ == '__main__':
    main()
