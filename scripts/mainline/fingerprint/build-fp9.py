#!/usr/bin/env python3
"""FP9: restore init interface discovery on the current integrated image8."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import xml.etree.ElementTree as ET
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = Path('/home/user/A9-build/mainline')
BASE = ROOT / 'integ-20261024-8'
OUT = ROOT / 'integ-20261024-fp9'


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
    assert handoff['source_pins_match_main'] and handoff['runtime_pins_match_main']
    sp.run(['git', '-C', REPO, 'merge-base', '--is-ancestor', handoff['main_commit'], 'HEAD'], check=True)
    for name, digest in parent['sha256'].items():
        assert b.sha(BASE / name) == digest, name
    assert parent['boot_kernel_dt_unchanged'] and parent['fsck'] == 'PASS'
    audit = json.loads((BASE / 'module-audit-integ6r3.json').read_text())
    row = audit['vendors']['integ-20261023-6r3']
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
    sp.run(['git', '-C', REPO, 'diff', '--quiet', parent['source_commit'], 'HEAD', '--',
            'scripts/mainline/fingerprint/driver', 'scripts/mainline/fingerprint/policy',
            'scripts/mainline/qseecom/driver'], check=True)
    for relative, digest in parent['source_sha256'].items():
        if relative != 'fingerprint/vendor/etc/init/init.a9-fingerprint.rc':
            assert b.sha(HERE.parent / relative) == digest, relative
    # Existing fragment already declares exactly the required HIDL instance.
    manifest_name = 'etc/vintf/manifest/fingerprint-a9.xml'
    manifest = b.read(BASE / 'vendor.img', manifest_name)
    assert manifest == (HERE / 'vendor' / manifest_name).read_bytes()
    tree = ET.fromstring(manifest)
    hal = [n for n in tree.findall('hal') if n.findtext('name') == 'android.hardware.biometrics.fingerprint']
    assert len(hal) == 1 and hal[0].findtext('transport') == 'hwbinder' and hal[0].findtext('version') == '2.1'
    assert hal[0].findtext('interface/name') == 'IBiometricsFingerprint' and hal[0].findtext('interface/instance') == 'default'
    relative = 'etc/init/init.a9-fingerprint.rc'
    original, image = BASE / 'vendor.img', OUT / 'vendor.img'
    source = HERE / 'vendor' / relative
    old = b.read(original, relative).decode()
    interface = '    interface android.hardware.biometrics.fingerprint@2.1::IBiometricsFingerprint default\n'
    assert interface not in old and old.count('    disabled\n') == 1
    expected = old.replace('    disabled\n', interface + '    disabled\n').replace(
        '# Explicit property start keeps HIDL lazy-start from preceding the onb capture.\n'
        '# Registration still comes from the unmodified stock HAL and VINTF manifest.\n',
        '# Allow hwservicemanager to resolve the declared default instance to this service.\n')
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
        assert b.sha(BASE / name) == b.sha(ROOT / 'integ-20261023-6r3' / name), name
        (OUT / name).hardlink_to(BASE / name)
    b.run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3', '-P',
           REPO / 'scripts/abl-check.py', OUT / 'boot.img', 'boot', '--dtbo', OUT / 'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((OUT / 'abl-check.log').read_text())['result'] == 'PASS'
    b.run(['python3', '-I', HERE / 'smoke.py'], 'hook-smoke')
    # Retain image8's FBE diagnostics, assets and sleep logger on the same image.
    for directory in ('acceptance-assets', 'sleep-logger', 'hooks'):
        shutil.copytree(BASE / directory, OUT / directory, copy_function=os.link)
    (OUT / 'module-audit-integ6r3.json').hardlink_to(BASE / 'module-audit-integ6r3.json')
    for name in ('hooks/fingerprint/onb.py', 'hooks/boot-deadline/onb-root.py'):
        source_file = HERE / 'onb.py' if name.startswith('hooks/fingerprint') else HERE.parent / 'boot-deadline/onb-root.py'
        dest = OUT / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Existing FBE helper is hard-linked to its immutable parent; replace
        # the directory entry, never overwrite a shared inode.
        if dest.exists(): dest.unlink()
        shutil.copyfile(source_file, dest)
        if name.startswith('hooks/fingerprint'): dest.chmod(0o755)
    fbe_run = OUT / 'run-fbe.sh'
    fbe_run.write_text((BASE / 'run-fbe.sh').read_text().replace(str(BASE), str(OUT)))
    fbe_run.chmod(0o755)
    runner = OUT / 'run-fingerprint.sh'
    runner.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                      'image=' + str(OUT) + '\n'
                      'export A9_BTRY_SOUND=0 A9_BTRY_HOLD=240\n'
                      'export A9_BTRY_ONB="$image/hooks/fingerprint/onb.py"\n'
                      'exec /home/user/A9/scripts/mainline/boot-deadline/b-try.sh "$image/boot.img" "$image/vendor.img"\n')
    runner.chmod(0o755)
    b.run(['bash', '-n', runner], 'fingerprint-launcher-syntax')
    b.run(['bash', '-n', fbe_run], 'fbe-launcher-syntax')

    hashes = {name: b.sha(OUT / name) for name in ['vendor.img'] + artifacts}
    sources = {str(p.relative_to(REPO)): b.sha(p) for p in HERE.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    receipt = dict(parent=str(BASE), parent_sha256=parent['sha256'], sha256=hashes,
                   source_commit=sp.check_output(['git', '-C', REPO, 'rev-parse', 'HEAD'], text=True).strip(),
                   source_sha256=sources, host_only=True, hardware_tested=False, vendor_changes=changed,
                   other_vendor_files_identical=len(files)-1, vendor_metadata_preserved=True,
                   inherited_module_audit=str(BASE / 'module-audit-integ6r3.json'),
                   module_audit_sha256=b.sha(BASE / 'module-audit-integ6r3.json'),
                   kernel_and_boot_unchanged=True, secure_spi_unclaimed=True, fingerprint_manifest_verified=True,
                   fbe_fixed=False, inherited_unresolved=parent['unresolved'], free_bytes=b.room(),
                   runtime_sha256={str(p.relative_to(OUT)): b.sha(p) for p in
                       [runner, fbe_run, *sorted((OUT / 'hooks').rglob('*.py'))]})
    (OUT / 'integrated-build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (OUT / 'SHA256SUMS').write_text(''.join(v + '  ' + n + '\n' for n, v in hashes.items()))
    (OUT / 'READY.txt').write_text('FP9 host PASS; hardware registration qualification pending.\n'
                                  'Run: ' + str(runner) + '\n')
    print('FP9 integrated image host PASS; init interface restored; image8 diagnostics retained', flush=True)


if __name__ == '__main__':
    main()
