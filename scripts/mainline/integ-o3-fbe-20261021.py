#!/usr/bin/env python3
"""Host-only O3 + explicit ROW29 FBE; preserve the exact O3 boot/module pair."""
from pathlib import Path
import hashlib, importlib.util, json, os, shutil, subprocess as sp, tempfile

R = Path(__file__).resolve().parents[2]
B = Path('/home/user/A9-build/mainline/integrated-20261021o3')
O = B.with_name('integrated-20261021o3-fbe')
F = R / 'scripts/mainline/storage-row29/data/fbe'
BOOT = '09bb30c4cdf350707873d541de58f153290d47b3be87f01e936ef2b3ebf4abcb'
BASE = '4d29d46ba18e4348af0eb0986c1b3156ca7179f3ead9aadb3f97ecd6071e1399'

def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def save(name, value): (O / name).write_text(json.dumps(value, indent=2) + '\n')

def run(cmd, name):
    with (O / (name + '.log')).open('w') as log:
        p = sp.run(list(map(str, cmd)), stdout=log, stderr=sp.STDOUT)
    (O / (name + '.rc')).write_text(str(p.returncode) + '\n')
    assert p.returncode == 0, name

def load(path, name):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def main():
    os.umask(0o077); O.mkdir(exist_ok=True)
    assert sha(B / 'vendor.img') == BASE and sha(B / 'boot.img') == BOOT
    if not (O / 'vendor.img').exists():
        run(['python3', '-I', F.parent / 'integrate.py', '--base-vendor', B / 'vendor.img',
             '--out', O, '--kernel-config', B / 'kernel.config', '--data-mode', 'row29-fbe'], 'vendor-build')
    build = json.loads((O / 'build-result.json').read_text())
    assert build['base_sha256'] == BASE and build['sha256'] == sha(O / 'vendor.img')
    assert build['data_mode'] == 'row29-fbe' and not build['boot_changed']
    for name in ('boot.img', 'recovery_dtbo.img', 'kernel.config', 'vmlinux.symvers', 'Module.symvers', 'module-abi-audit.json'):
        shutil.copyfile(B / name, O / name)
    assert sha(O / 'boot.img') == BOOT
    final = O / 'final'; final.mkdir(exist_ok=True)
    run(['debugfs', '-R', 'rdump / ' + str(final), O / 'vendor.img'], 'extract-final')
    before = json.loads((B / 'final-file-hashes.json').read_text())
    after = {str(p.relative_to(final)): sha(p) for p in final.rglob('*') if p.is_file() and not p.is_symlink()}
    delta = {p: {'before': before.get(p), 'after': after.get(p)} for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
    assert set(delta) == {'etc/fstab.hlte556n', 'etc/init/hw/init.hlte556n.rc', 'bin/a9ml-row29-map',
                          'etc/selinux/vendor_sepolicy.cil', 'etc/selinux/vendor_file_contexts',
                          'etc/selinux/precompiled_sepolicy', 'bin/a9ml-deadman.sh', 'bin/a9ml-ctl.sh'}, delta
    save('vendor-delta.json', delta); save('final-file-hashes.json', after)
    abi = json.loads((O / 'module-abi-audit.json').read_text())
    assert len(abi) == 100 and sum(v['checked'] for v in abi.values()) == 6200
    for name, v in abi.items(): assert sha(final / 'lib/modules' / name) == v['sha256']
    assert sha(final / 'bin/a9-sensors.sh') == before['bin/a9-sensors.sh']
    assert sha(final / 'bin/a9ml-test-data.sh') == before['bin/a9ml-test-data.sh']
    assert (final / 'etc/selinux/vendor_sepolicy.cil').read_text() == (B / 'final/etc/selinux/vendor_sepolicy.cil').read_text() + '\n' + (F / 'hold.cil').read_text()
    cfg = (O / 'kernel.config').read_text().splitlines()
    for key in ('BLK_DEV_DM', 'F2FS_FS', 'F2FS_FS_SECURITY', 'FS_ENCRYPTION', 'CRYPTO_AES', 'CRYPTO_XTS', 'CRYPTO_CTS', 'CRYPTO_SHA256'):
        assert 'CONFIG_' + key + '=y' in cfg
    for target in ('boot', 'recovery'):
        for ext in ('json', 'err', 'rc'): shutil.copyfile(B / f'abl-{target}.{ext}', O / f'abl-{target}.{ext}')
        receipt = json.loads((O / f'abl-{target}.json').read_text())
        assert receipt['image_sha256'] == BOOT and all(x['pass'] for x in receipt['stages'].values())
    # Link a query-only copy for older host libsepol. Production retains this capability.
    cmd = json.loads((O / 'policy-command.json').read_text())
    plat = next(Path(x) for x in cmd if x.endswith('0-plat_sepolicy.cil'))
    text = plat.read_text(); assert text.count('(policycap functionfs_seclabel)') == 1
    qp = O / 'query-only-plat.cil'; qp.write_text(text.replace('(policycap functionfs_seclabel)', ''))
    cmd = [str(qp) if x == str(plat) else x for x in cmd]
    binary = O / 'query-only-policy'; cmd[cmd.index('-o') + 1] = str(binary)
    run(cmd, 'query-policy')
    rows = json.loads((B.with_name('storage-row29-fbe-20261021') / 'policy-audit.json').read_text())['rows']
    rows += json.loads((B.with_name('integrated-20261021o2') / 'policy-access.json').read_text())['rows']
    for row in json.loads((B / 'policy-read-audit.json').read_text())['rows']:
        rows.append(dict(row, source='a9ml_sensors', expected=True))
    checker = '/nix/store/smdkw130d7bg30ran2c1l0p1x9h42ydn-libsepol-3.8.1-bin/bin/sepol_check_access'
    for row in rows:
        p = sp.run([checker, str(binary), 'u:r:' + row['source'] + ':s0', 'u:object_r:' + row['target'] + ':s0', row['cls'], row['perms']], capture_output=True, text=True)
        row.update(rc=p.returncode, output=p.stdout.strip(), stderr=p.stderr.strip())
        assert p.returncode in (0, 7) and (p.returncode == 0) == row['expected'], row
    save('policy-access.json', dict(limitation='Query-only copy omits solely unsupported functionfs_seclabel capability. Production retains it. TE/RBAC/MLS unchanged; no xperm query claim.', rows=rows))
    run(['python3', '-I', F / 'build.py', '--out', O / 'tools'], 'tools-build')
    shutil.copyfile(F / 'keeper-fbe.sh', O / 'keeper-fbe.sh')
    run(['bash', '-n', O / 'keeper-fbe.sh'], 'keeper-syntax')
    # Upstream test expects tools/ beside image/. Supply a tiny temporary package,
    # pointing its image fixture at the actual final overlay render.
    with tempfile.TemporaryDirectory(prefix='o3-fbe-tests-') as tmp:
        root = Path(tmp); shutil.copytree(O / 'tools', root / 'tools'); (root / 'image').symlink_to(O)
        run(['python3', '-I', F / 'test.py', '--out', root / 'tools'], 'fbe-tests')
    run(['python3', '-I', F.parent / 'test-integration.py'], 'integration-tests')
    launcher = Path('/home/user/A9-build/mainline/vendor-test-20261020/direct-b.sh')
    text = launcher.read_text(); old = '/home/user/A9/scripts/mainline/poc/keeper.sh'
    assert text.count(old) == 1
    text = text.replace(old, str(O / 'keeper-fbe.sh'))
    old = 'D=/home/user/A9-build/mainline/vendor-test-20261020;'
    assert text.count(old) == 1
    text = text.replace(old, 'D=' + str(O / 'phone') + ';')
    (O / 'phone').mkdir(exist_ok=True); (O / 'direct-b-fbe.sh').write_text(text)
    run(['bash', '-n', O / 'direct-b-fbe.sh'], 'launcher-syntax')
    save('launcher-provenance.json', dict(base=str(launcher), sha256=sha(launcher), changes=['keeper path only', 'output/evidence directory only'], executed=False))
    code = hashlib.sha256((sha(O / 'vendor.img') + ' vendor_b 0').encode()).hexdigest()[:8]
    (O / 'boot-command.txt').write_text('bash ' + str(O / 'direct-b-fbe.sh') + ' \\\n  ' + str(O / 'boot.img') + ' \\\n  ' + str(O / 'vendor.img') + ' ' + code + '\n')
    save('validation.json', dict(regular_files=len(after), changed_paths=sorted(delta), modules_unchanged=100, inherited_CRCs_checked=6200,
        inherited_vermagic='7.2.10-a9+ SMP preempt mod_unload modversions aarch64', sensor_fix_unchanged=True, row29_guard_unchanged=True,
        O3_policy_retained=True, policy_access_queries=len(rows), kernel_config_identical=True,
        ABL_boot='PASS (inherited exact O2/O3 boot receipt)', ABL_recovery='PASS (inherited exact O2/O3 boot receipt)',
        fbe_test_groups=5, integration_test_groups=3, phone_access=False, free_bytes=shutil.disk_usage(O).free))
    assert shutil.disk_usage(O).free > 2 * 1024**3
    for name in ('boot.img', 'vendor.img', 'recovery_dtbo.img'):
        (O / (name + '.sha256')).write_text(sha(O / name) + '  ' + str(O / name) + '\n')
    print(json.dumps(json.loads((O / 'validation.json').read_text()), indent=2))

if __name__ == '__main__': main()
