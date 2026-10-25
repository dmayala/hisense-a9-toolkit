#!/usr/bin/env python3
"""FP8: capture-gated stock HAL startup on the complete 6r3 integrated image."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = Path('/home/user/A9-build/mainline')
BASE = ROOT / 'integ-20261023-6r3'
OUT = ROOT / 'integ-20261024-fp8'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    os.umask(0o077)
    OUT.mkdir(parents=True, exist_ok=True)
    assert not (OUT / 'boot.img').exists() and not (OUT / 'vendor.img').exists(), 'preserve existing candidate'
    b = load('fp_build', HERE / 'build.py')
    b.OUT = OUT
    meta = load('fp_meta', HERE.parent / 'android/refresh-integrated-vendor.py')
    parent = json.loads((BASE / 'integrated-build.json').read_text())
    handoff = json.loads((BASE / 'source-handoff.json').read_text())
    assert handoff['artifact_hashes_match'] and handoff['source_pins_match_main']
    sp.run(['git', '-C', REPO, 'merge-base', '--is-ancestor', handoff['main_commit'], 'HEAD'], check=True)
    for name, digest in parent['sha256'].items():
        assert b.sha(BASE / name) == digest, name
    assert parent['boot_kernel_dt_unchanged'] and parent['fsck'] == 'PASS'
    audit = json.loads((BASE / 'module-audit-integ6r3.json').read_text())
    row = audit['vendors'][BASE.name]
    assert row['modules'] == 101 and row['imports_checked'] == 6243
    assert not any(row[key] for key in ('failing_modules', 'crc_mismatches', 'vermagic_mismatches'))
    # Unchanged stock control ABI, secure SPI ownership and pin reservations.
    b.check_fingerprint_dtb(BASE / 'integ.dtb')
    def prop(node, key, kind='s'):
        return sp.check_output([b.DTC / 'fdtget', '-t', kind, BASE / 'integ.dtb', node, key], text=True).strip()
    cells = list(map(int, prop('/soc@0/pinctrl@500000', 'gpio-reserved-ranges', 'i').split()))
    reserved = {pin for start, count in zip(cells[::2], cells[1::2]) for pin in range(start, start + count)}
    assert set(range(14, 18)) <= reserved and not ({19, 70, 97, 100} & reserved)
    for alias in ('spi5', 'i2c5'):
        assert prop(prop('/__symbols__', alias), 'status') == 'disabled'
    fp7 = json.loads((ROOT / 'integ-20261023-fp7/integrated-build.json').read_text())
    assert b.sha(HERE / 'driver/a9-sunwave.c') == fp7['source_sha256']['scripts/mainline/fingerprint/driver/a9-sunwave.c']
    integ7 = json.loads((ROOT / 'integ-20261023-7/integrated-build.json').read_text())
    for path in (HERE.parent / 'qseecom/driver').iterdir():
        if path.is_file():
            assert b.sha(path) == integ7['source_sha256'][str(path.relative_to(REPO))], path
    # Only the init service declaration changes. Never restart an already-live
    # HAL to manufacture logs: remove its lazy-start route in the new image.
    relative = 'etc/init/init.a9-fingerprint.rc'
    original, image = BASE / 'vendor.img', OUT / 'vendor.img'
    source = HERE / 'vendor' / relative
    old = b.read(original, relative).decode()
    interface = '    interface android.hardware.biometrics.fingerprint@2.1::IBiometricsFingerprint default\n'
    assert old.count(interface) == 1
    expected = old.replace(interface, '').replace('# Keep actual stock listener readiness;',
        '# Explicit property start keeps HIDL lazy-start from preceding the onb capture.\n'
        '# Registration still comes from the unmodified stock HAL and VINTF manifest.\n'
        '# Keep actual stock listener readiness;')
    assert source.read_text() == expected
    b.run([ROOT / 'android/dl/cf_arm64_x/otatools/bin/host_init_verifier', source], 'init-verifier')
    b.room(original.stat().st_blocks * 512 + 32 * 1024**2)
    b.run(['cp', '--reflink=auto', '--sparse=always', original, image], 'vendor-copy')
    info = meta.metadata(original, relative)
    label = OUT / 'init-label'
    label.write_bytes((info['label'] + '\0').encode())
    batch = [f'rm /{relative}', f'write {source} /{relative}', f'sif /{relative} mode 010{info["mode"]}',
             f'sif /{relative} uid {info["uid"]}', f'sif /{relative} gid {info["gid"]}',
             f'ea_set -f {label} /{relative} security.selinux']
    for field in ('atime', 'mtime', 'ctime', 'crtime'):
        batch.append(f'sif /{relative} {field} @{int(info[field], 16)}')
    script = OUT / 'install.debugfs'
    script.write_text('\n'.join(batch) + '\n')
    b.run(['debugfs', '-w', '-f', script, image], 'vendor-install')
    files = sorted(meta.vendor_files(original))
    assert files == sorted(meta.vendor_files(image)), 'vendor inventory'
    changed = [name for name in files if b.read(original, name) != b.read(image, name)]
    assert changed == [relative], changed
    assert b.read(image, relative) == source.read_bytes()
    assert meta.metadata(image, relative) == info
    b.run(['e2fsck', '-fn', image], 'vendor-fsck')
    artifacts = ['boot.img', 'Image.gz', 'integ.dtb', 'recovery_dtbo.img', 'kernel.config', 'Module.symvers', 'vmlinux.symvers']
    for name in artifacts:
        (OUT / name).hardlink_to(BASE / name)
    b.run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3', '-P',
           REPO / 'scripts/abl-check.py', OUT / 'boot.img', 'boot', '--dtbo', OUT / 'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((OUT / 'abl-check.log').read_text())['result'] == 'PASS'
    b.run(['python3', '-I', HERE / 'smoke.py'], 'hook-smoke')
    hashes = {name: b.sha(OUT / name) for name in ['vendor.img'] + artifacts}
    sources = {str(p.relative_to(REPO)): b.sha(p) for p in HERE.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    receipt = dict(parent=str(BASE), parent_sha256=parent['sha256'], sha256=hashes,
                   source_commit=sp.check_output(['git', '-C', REPO, 'rev-parse', 'HEAD'], text=True).strip(),
                   source_sha256=sources, host_only=True, hardware_tested=False, vendor_changes=changed,
                   other_vendor_files_identical=len(files)-1, vendor_metadata_preserved=True,
                   inherited_module_audit=str(BASE / 'module-audit-integ6r3.json'),
                   module_audit_sha256=b.sha(BASE / 'module-audit-integ6r3.json'),
                   kernel_and_boot_unchanged=True, secure_spi_unclaimed=True, free_bytes=b.room())
    (OUT / 'integrated-build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (OUT / 'SHA256SUMS').write_text(''.join(v + '  ' + n + '\n' for n, v in hashes.items()))
    (OUT / 'READY.txt').write_text('FP8 host PASS; hardware sensor-ID qualification pending.\n'
                                  'Run: scripts/mainline/fingerprint/run.sh\n')
    print('FP8 integrated image host PASS; only capture-gated HAL init changed', flush=True)


if __name__ == '__main__':
    main()
