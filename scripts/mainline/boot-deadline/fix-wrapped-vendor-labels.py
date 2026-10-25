#!/usr/bin/env python3
"""Host-only fix5: correct four installed xattrs in the exact fix4 vendor.

The existing platform/vendor file_contexts already specify these labels.
No payload, policy, init behavior, key, module or kernel changes are needed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

SOURCE_SHA256 = 'ec3447a09264000ba3a35485ec9b99986e87b6263f33b011e24891e3566c4f38'
CORRECTIONS = {
    'etc/vintf/manifest/android.hardware.keymaster-qti.xml':
        ('u:object_r:vendor_file:s0', 'u:object_r:vendor_configs_file:s0'),
    'etc/init/init.a9-wrapped-keymaster.rc':
        ('u:object_r:vendor_file:s0', 'u:object_r:vendor_configs_file:s0'),
    'etc/gpfspath_oem_config.xml':
        ('u:object_r:vendor_file:s0', 'u:object_r:vendor_configs_file:s0'),
    'lib64/a9ml-adbd': (None, 'u:object_r:system_lib_file:s0'),
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def debug(image, command):
    return subprocess.run(['debugfs', '-R', command, str(image)],
                          capture_output=True, check=True)


def label(image, path):
    text = debug(image, 'ea_get /' + path + ' security.selinux').stdout.decode()
    match = re.search(r'security.selinux \(\d+\) = "([^"\\]+)\\000"', text)
    if match:
        return match.group(1)
    assert not text.strip() and b'Inode:' in debug(image, 'stat /' + path).stdout, path
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path, help='new, nonexistent image path')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    image = args.output.absolute()
    assert digest(source) == SOURCE_SHA256, 'expected exact fix4 vendor'
    assert not image.exists(), 'output already exists'
    image.parent.mkdir(parents=True, exist_ok=True)
    commands = []
    for i, (path, (before, after)) in enumerate(CORRECTIONS.items()):
        assert label(source, path) == before, path
        value = image.parent / ('fix5-label-' + str(i))
        value.write_bytes(after.encode() + b'\0')
        commands.append(f'ea_set -f {value} /{path} security.selinux')
    batch = image.parent / 'install-labels.debugfs'
    batch.write_text('\n'.join(commands) + '\n')
    subprocess.run(['cp', '--reflink=auto', '--sparse=always', str(source), str(image)], check=True)
    with (image.parent / 'install-labels.log').open('wb') as log:
        subprocess.run(['debugfs', '-w', '-f', str(batch), str(image)],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    with (image.parent / 'corrected-ea_get.txt').open('wb') as log:
        for path, (_, expected) in CORRECTIONS.items():
            assert label(image, path) == expected, path
            result = debug(image, 'ea_get /' + path + ' security.selinux')
            log.write(('ea_get /' + path + ' security.selinux\n').encode() + result.stdout + result.stderr)
            if path != 'lib64/a9ml-adbd':
                assert debug(image, 'cat /' + path).stdout == debug(source, 'cat /' + path).stdout, path
    with (image.parent / 'fsck.log').open('wb') as log:
        subprocess.run(['e2fsck', '-fn', str(image)], stdout=log,
                       stderr=subprocess.STDOUT, check=True)
    result = {'source': str(source), 'source_sha256': SOURCE_SHA256,
              'image': str(image), 'sha256': digest(image),
              'corrected_xattrs': {p: {'before': a, 'after': b} for p, (a, b) in CORRECTIONS.items()},
              'fsck_rc': 0, 'phone_access': False}
    (image.parent / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    Path(str(image) + '.sha256').write_text(result['sha256'] + '  ' + image.name + '\n')
    print('WRAPPED FIX5 READY ' + str(image), flush=True)


if __name__ == '__main__':
    main()
