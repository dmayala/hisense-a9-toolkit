#!/usr/bin/env python3
"""FP2 host rebuild: reuse fp1's checked kernel/DT/module, repair vendor policy."""
from pathlib import Path
import hashlib
import json
import os
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')
OLD = ROOT / 'integ-20261023-fp1'
OUT = ROOT / 'integ-20261023-fp2'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    os.umask(0o077)
    OUT.mkdir(parents=True, exist_ok=True)
    assert not (OUT / 'vendor.img').exists(), 'use fresh output; fp2 already exists'
    result = json.loads((OLD / 'integrated-build.json').read_text())
    source = 'scripts/mainline/fingerprint/driver/a9-sunwave.c'
    assert sha(HERE / 'driver/a9-sunwave.c') == result['source_sha256'][source]
    for name in ['boot.img', 'integ.dtb', 'module/a9-sunwave.ko', 'recovery_dtbo.img']:
        source = OLD / name
        if name in result['sha256']: assert sha(source) == result['sha256'][name]
        target = OUT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists(): target.hardlink_to(source)
        assert sha(target) == sha(source)
    for name in ['abl-check.log', 'abl-check.rc', 'dt-delta.json']:
        target = OUT / name
        if not target.exists(): target.hardlink_to(OLD / name)
    target = OUT / 'overlay/lib/modules/a9-sunwave.ko'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists(): target.hardlink_to(OLD / 'module/a9-sunwave.ko')
    for step in ['policy', 'vendor']:
        sp.run(['python3', HERE / 'build.py', '--out', OUT, '--step', step], check=True)
    print('FP2 host image rebuilt; boot/DT/module unchanged from fp1')


if __name__ == '__main__':
    main()
