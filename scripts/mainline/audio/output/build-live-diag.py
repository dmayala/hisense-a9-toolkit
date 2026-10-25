#!/usr/bin/env python3
"""Replace only the diagnostic adapter in the proven plain-integ vendor. Host only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')
BASE = ROOT / 'plain-integ-20261022'
OUT = ROOT / 'plain-integ-audiodiag-20261022'
OVERLAY = ROOT / 'audio-silent-fix-20261022/vendor-overlay'
MEMBER = 'lib64/a9-audio/liba9audioroute.so'
BASE_SHA = '8aa652cae9b511d8e5d55eba1edf79ea3f10828bd6cd2c94ab6e92de20bddc76'
ADAPTER_SHA = 'b550cbd73a406b7b2a35866df50042119558f10492216ab17123b5f77a4ba769'


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    image = OUT / 'vendor.img'
    if image.exists():
        raise RuntimeError('output vendor already exists; do not overwrite a handed-off image')
    # Same 2GiB floor used by the selected plain-integ recipe; sparse copy
    # allocates ~400MiB. No full rebuild, image growth or unrelated cleanup.
    if shutil.disk_usage(OUT).free < 2 * 1024**3 + 450 * 1024**2:
        raise RuntimeError('need existing plain-integ 2GiB floor plus 450MiB copy budget')
    assert sha(BASE / 'vendor.img') == BASE_SHA
    assert sha(OVERLAY / MEMBER) == ADAPTER_SHA
    helper = HERE.parents[1] / 'android/refresh-integrated-vendor.py'
    spec = importlib.util.spec_from_file_location('metadata', helper)
    meta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(meta)
    before = meta.metadata(BASE / 'vendor.img', MEMBER)
    label = OUT / 'adapter.label'
    label.write_bytes(before['label'].encode() + b'\0')
    recipe = [f'rm /{MEMBER}', f'write {OVERLAY / MEMBER} /{MEMBER}',
              f'sif /{MEMBER} mode 010{before["mode"]}',
              f'sif /{MEMBER} uid {before["uid"]}', f'sif /{MEMBER} gid {before["gid"]}',
              f'ea_set -f {label} /{MEMBER} security.selinux']
    for field in ('atime', 'mtime', 'ctime', 'crtime'):
        recipe.append(f'sif /{MEMBER} {field} @{int(before[field], 16)}')
    (OUT / 'install.debugfs').write_text('\n'.join(recipe) + '\n')
    sp.run(['cp', '--reflink=auto', '--sparse=always', BASE / 'vendor.img', image], check=True)
    with (OUT / 'install.log').open('w') as log:
        sp.run(['debugfs', '-w', '-f', OUT / 'install.debugfs', image], check=True,
               stdout=log, stderr=sp.STDOUT)
    data = sp.check_output(['debugfs', '-R', f'cat /{MEMBER}', image], stderr=sp.DEVNULL)
    assert hashlib.sha256(data).hexdigest() == ADAPTER_SHA
    assert meta.metadata(image, MEMBER) == before
    # Confirm that the existing, unchanged FE helper is the one the capture uses.
    trace = sp.check_output(['debugfs', '-R', 'cat /bin/a9-audio-fe-trace.sh', image],
                            stderr=sp.DEVNULL)
    assert trace == (HERE / 'a9-audio-fe-trace.sh').read_bytes()
    with (OUT / 'fsck.log').open('w') as log:
        sp.run(['e2fsck', '-fn', image], check=True, stdout=log, stderr=sp.STDOUT)
    assert sha(BASE / 'vendor.img') == BASE_SHA
    boot = OUT / 'boot.img'
    boot.symlink_to(BASE / 'boot.img')
    shutil.copyfile(HERE / 'audio-live.sh', OUT / 'audio-live.sh')
    (OUT / 'audio-live.sh').chmod(0o755)
    hashes = {n: sha(OUT / n) for n in ('vendor.img', 'boot.img', 'audio-live.sh')}
    (OUT / 'SHA256SUMS').write_text(''.join(v + '  ' + n + '\n' for n, v in hashes.items()))
    (OUT / 'vendor.img.sha256').write_text(hashes['vendor.img'] + '  vendor.img\n')
    result = dict(base=str(BASE / 'vendor.img'), base_sha256=BASE_SHA,
                  changed=[MEMBER], adapter_sha256=ADAPTER_SHA, metadata_preserved=before,
                  boot_reused=str(BASE / 'boot.img'), filesystem_check='PASS',
                  capture_executed=False, hardware_tested=False, sha256=hashes,
                  free_after=shutil.disk_usage(OUT).free)
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
