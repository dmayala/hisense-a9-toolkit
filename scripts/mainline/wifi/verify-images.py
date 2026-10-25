#!/usr/bin/env python3
"""Read-only offline Wi-Fi deployment gate. Never invokes a device tool.

Bind the vendor's selected binaries to current reviewed source/patch records and
the local build output; bind its modules and boot payload to one Wi-Fi snapshot.
The build/toolchain is trusted. This is provenance validation, not a signature.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
# Pinned ath10k feature-flag file (see stage-vendor.py): without it ath10k stops
# at "Failed to find firmware-N.bin" right after the modem WLAN PD is up.
FW5_PATH = 'firmware/ath10k/WCN3990/hw1.0/hisense/hlte556n/firmware-5.bin'
FW5_BYTES = 60
FW5_SHA256 = '80daf4f3a89e6ea8222e45387bb362c854fcd30c02e9da59ca723b39b269cdd2'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Image:
    def __init__(self, path):
        self.path = Path(path)

    def read(self, path):
        if not path.startswith('/') or any(c.isspace() for c in path) or '"' in path or '\\' in path:
            raise ValueError('invalid image lookup')
        with tempfile.TemporaryDirectory(prefix='a9-wifi-image-check-') as d:
            output = Path(d) / 'object'
            result = subprocess.run(['debugfs', '-R', f'dump -p {path} {output}', str(self.path)],
                                    capture_output=True, check=True)
            if not output.is_file() or output.is_symlink():
                raise ValueError('missing image object: ' + path)
            return output.read_bytes()


def boot_payloads(data):
    if data[:8] != b'ANDROID!' or len(data) < 1660:
        raise ValueError('not an Android boot image')
    page = struct.unpack_from('<I', data, 36)[0]
    version = struct.unpack_from('<I', data, 40)[0]
    if page != 4096 or version != 2:
        raise ValueError('expected A9 header-v2/page4096 boot')
    cmdline = data[64:576].split(b'\0')[0].split()
    if data[608:1632].split(b'\0')[0] or [x for x in cmdline if x.startswith(b'firmware_class.path=')] != [b'firmware_class.path=/mnt/a9-wifi/firmware']:
        raise ValueError('effective command line must select only the reviewed root-owned firmware bridge')
    kernel, ramdisk, second = (struct.unpack_from('<I', data, n)[0] for n in (8, 16, 24))
    recovery = struct.unpack_from('<I', data, 1632)[0]
    dtb = struct.unpack_from('<I', data, 1648)[0]
    align = lambda n: (n + page - 1) // page * page
    dtboff = page + align(kernel) + align(ramdisk) + align(second) + align(recovery)
    if not kernel or not dtb or dtboff + dtb > len(data):
        raise ValueError('truncated boot payload')
    return data[page:page + kernel], data[dtboff:dtboff + dtb]


def verify(image, boot, dtbo, snapshot, binaries, here=HERE):
    manifest = json.loads(image.read('/etc/a9-wifi/build-manifest.json'))
    build = manifest['userspace']
    local_build = json.loads((binaries / 'build-manifest.json').read_text())
    if build != local_build or build.get('abi') != 'aarch64' or build.get('static') is not True:
        raise ValueError('vendor does not match the selected local static ARM64 userspace build')
    lock = json.loads((here / 'sources.lock.json').read_text())
    if len(build['sources']) != len(lock):
        raise ValueError('incomplete source provenance')
    for want, got in zip(lock, build['sources']):
        if any(got.get(k) != v for k, v in want.items()):
            raise ValueError('source pin/archive mismatch')
        patch = here / 'patches' / (want['name'] + '.patch')
        if patch.exists() and got.get('patch_sha256') != sha(patch.read_bytes()):
            raise ValueError('reviewed patch changed; rebuild userspace')
    for name in ('sandbox.c', 'sandbox-policy.h', 'sha256.h', 'postexec.c'):
        if build['local_sources'].get(name) != sha((here / name).read_bytes()):
            raise ValueError('reviewed native boundary changed; rebuild userspace')
    installed = {}
    for row in manifest['installed']:
        path = PurePosixPath(row['path'])
        if path.is_absolute() or '..' in path.parts or str(path) != row['path'] or row['path'] in installed:
            raise ValueError('unsafe/duplicate installed inventory path')
        data = image.read('/' + row['path'])
        if len(data) != row['bytes'] or sha(data) != row['sha256']:
            raise ValueError('vendor payload differs from inventory: ' + row['path'])
        installed[row['path']] = data
    fw5 = installed.get(FW5_PATH)
    if fw5 is None or len(fw5) != FW5_BYTES or sha(fw5) != FW5_SHA256 or not fw5.startswith(b'QCA-ATH10K\0'):
        raise ValueError('vendor lacks the pinned ath10k firmware-5.bin')
    roles = ['rmtfs', 'tqftpserv', 'diag-router', 'a9-wifi-sandbox', 'qrtr-lookup']
    if manifest['pd_mapper'] == 'userspace':
        roles.append('pd-mapper')
    elif manifest['pd_mapper'] != 'kernel':
        raise ValueError('unknown mapper mode')
    for role in roles:
        data = installed.get('bin/' + role, b'')
        if not data or sha(data) != build['binaries'].get(role) or data != (binaries / role).read_bytes():
            raise ValueError('unreviewed/missing radio executable: ' + role)
    rc = installed.get('etc/init/init.a9ml-wifi.rc', b'').decode()
    expected_rc = (here / 'a9-wifi.rc').read_text()
    if manifest['pd_mapper'] == 'kernel':
        expected_rc = '\n\n'.join(b for b in expected_rc.split('\n\n')
                                  if 'start vendor.pd_mapper' not in b and 'service vendor.pd_mapper ' not in b)
    if rc != expected_rc:
        raise ValueError('radio init differs from the reviewed source')
    for script in ('a9ml-wifi-modules.sh', 'a9ml-wifi-firmware.sh', 'a9ml-module-load.sh'):
        if installed.get('bin/' + script) != (here / script).read_bytes():
            raise ValueError('radio module/firmware preparation differs from reviewed source')
    for line in rc.splitlines():
        words = line.split()
        if words[:1] == ['service'] and words[1].startswith('vendor.'):
            if words[2] != '/vendor/bin/a9-wifi-sandbox' or words[3:] not in [[r] for r in roles if r != 'a9-wifi-sandbox']:
                raise ValueError('radio init bypasses native boundary')
        if words[:1] == ['write'] and any('remoteproc' in w for w in words):
            raise ValueError('radio init starts remoteproc implicitly')
    if not rc or b'ro.a9ml.wifi=1\n' not in image.read('/build.prop'):
        raise ValueError('opt-in init/vendor marker absent')
    for line in image.read('/etc/fstab.hlte556n').decode().splitlines():
        if line.lstrip().startswith('#'):
            continue
        if any(x in line for x in ('modemst', '/persist', '/fsg', '/fsc', '/misc', 'userdata', 'metadata')):
            raise ValueError('protected partition in vendor fstab')
    snap = json.loads((snapshot / 'wifi-manifest.json').read_text())
    if snap['version'] != 1 or snap['pd_mapper'] != manifest['pd_mapper']:
        raise ValueError('snapshot/vendor mapper mismatch')
    for rel, digest in snap['sha256'].items():
        name = PurePosixPath(rel)
        if name.is_absolute() or '..' in name.parts or sha((snapshot / rel).read_bytes()) != digest:
            raise ValueError('snapshot artifact changed')
        if rel.startswith('modules/') and image.read('/lib/' + rel) != (snapshot / rel).read_bytes():
            raise ValueError('vendor module/list differs from selected snapshot')
    kernel, dtb = boot_payloads(boot.read_bytes())
    if sha(kernel) != snap['sha256']['Image.gz'] or sha(dtb) != snap['sha256']['sm6115-hisense-hlte556n-android.dtb']:
        raise ValueError('boot kernel/DT does not match Wi-Fi snapshot')
    if dtbo.read_bytes() != (snapshot / 'recovery_dtbo.img').read_bytes():
        raise ValueError('deployment DTBO differs from selected snapshot')
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--boot', type=Path, required=True)
    p.add_argument('--vendor', type=Path, required=True)
    p.add_argument('--dtbo', type=Path, required=True)
    p.add_argument('--snapshot', type=Path, default=Path(os.environ.get('A9_WIFI_SNAPSHOT', str(Path.home() / 'A9-build/mainline/android/kernel-snap-wifi'))))
    p.add_argument('--binaries', type=Path, default=Path(os.environ.get('A9_WIFI_BINARIES', str(Path.home() / 'A9-build/mainline/sol-wifi/build/android'))))
    a = p.parse_args()
    verify(Image(a.vendor), a.boot, a.dtbo, a.snapshot, a.binaries)
    print('Wi-Fi provenance gate PASS (host only; no phone access).')


if __name__ == '__main__':
    main()
