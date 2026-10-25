#!/usr/bin/env python3
"""Explicit host key preparation: add missing APEX keys, never rotate a key.

Run once on the private release signing host, before build.py. No image writes.
Existing releasekey.pk8/x509.pem must already be supplied by the key owner.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess as sp

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    env_keys = os.environ.get('A9_RELEASE_KEYS')  # private signing dir, never in-tree
    ap.add_argument('--keys', type=Path, default=Path(env_keys) if env_keys else None,
                    required=not env_keys)
    ap.add_argument('--vendor', type=Path, required=True)
    ap.add_argument('--system-trust', type=Path, default=HERE/'los-v8-trust.json')
    ap.add_argument('--avbtool', type=Path, required=True)
    a = ap.parse_args()
    os.umask(0o077)
    if not a.keys.is_dir() or stat.S_IMODE(a.keys.stat().st_mode) != 0o700:
        ap.error('existing private signing directory must be 0700')
    for name in ('releasekey.pk8', 'releasekey.x509.pem'):
        p = a.keys/name
        if not p.is_file() or stat.S_IMODE(p.stat().st_mode) != 0o600:
            ap.error('supply existing private releasekey.pk8/x509.pem, mode0600')
    spec = importlib.util.spec_from_file_location('release', HERE/'build.py')
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    paths = json.loads(a.system_trust.read_text())['resign_apexes']
    paths += [p for p in build.meta.vendor_files(a.vendor) if p.endswith('.apex')]
    directory = a.keys/'apex'; directory.mkdir(mode=0o700, exist_ok=True)
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        ap.error('APEX key directory must be0700')
    for name in sorted({Path(p).stem for p in paths}):
        if not name.replace('.', '').replace('_', '').replace('-', '').isalnum():
            ap.error('invalid APEX key name')
        key = directory/(name+'.pem')
        if key.exists():
            if not key.is_file() or stat.S_IMODE(key.stat().st_mode) != 0o600:
                ap.error('existing key must be a private0600 file: '+name)
            continue
        # Exclusive creation, so a concurrent preparation cannot overwrite keys.
        fd = os.open(key, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as output:
                sp.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt',
                        'rsa_keygen_bits:4096'], stdout=output, stderr=sp.DEVNULL, check=True)
        except BaseException:
            key.unlink()  # only the incomplete file created by this invocation
            raise
        sp.run(list(map(str, [a.avbtool, 'extract_public_key', '--key', key,
                              '--output', directory/(name+'.avbpubkey')])), check=True)
        print('Created APEX signing key: '+name)


if __name__ == '__main__':
    main()
