#!/usr/bin/env python3
"""Host-only FP3: apply the fingerprint root fragment to integ5's boot DT.

The driver, kernel, vendor policy and HAL bytes are unchanged from FP2. Reuse
those checked artifacts; rebuild the DT and boot image and rerun ABL selection.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = Path('/home/user/A9-build/mainline')
PARENT = ROOT / 'integ-20261023-fp2'
OUT = ROOT / 'integ-20261023-fp3'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    os.umask(0o077)
    OUT.mkdir(parents=True, exist_ok=True)
    assert not (OUT / 'boot.img').exists(), 'FP3 already built; preserve the candidate'
    spec = importlib.util.spec_from_file_location('fp_build', HERE / 'build.py')
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    build.OUT = OUT
    build.room(32 * 1024**2)
    old = json.loads((PARENT / 'integrated-build.json').read_text())
    assert sha(HERE / 'driver/a9-sunwave.c') == old['source_sha256']['scripts/mainline/fingerprint/driver/a9-sunwave.c']
    for name in ['vendor.img', 'module/a9-sunwave.ko']:
        assert sha(PARENT / name) == old['sha256'][name]
        target = OUT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        assert not target.exists(), str(target)
        target.hardlink_to(PARENT / name)
    # DT/boot are generated from integ5, with the repaired root fragment.
    build.boot()
    build.check_fingerprint_dtb(OUT / 'integ.dtb')
    hashes = {name: sha(OUT / name) for name in ['boot.img', 'vendor.img', 'module/a9-sunwave.ko', 'integ.dtb']}
    (OUT / 'SHA256SUMS').write_text(''.join(value + '  ' + name + '\n' for name, value in hashes.items()))
    result = dict(base=str(build.BASE), parent=str(PARENT), sha256=hashes,
                  host_only=True, hardware_tested=False, kernel_ramdisk_cmdline_unchanged=True,
                  vendor_module_policy_unchanged=True,
                  reused_vendor_checks=str(PARENT / 'integrated-build.json'),
                  reused_policy_checks=str(PARENT / 'policy-smoke.json'),
                  source_commit=sp.check_output(['git', '-C', REPO, 'rev-parse', 'HEAD'], text=True).strip(),
                  source_sha256={str(p.relative_to(REPO)): sha(p) for p in HERE.rglob('*')
                                 if p.is_file() and '__pycache__' not in p.parts})
    (OUT / 'integrated-build.json').write_text(json.dumps(result, indent=2) + '\n')
    print('FP3 host image ready: ' + str(OUT), flush=True)


if __name__ == '__main__':
    main()
