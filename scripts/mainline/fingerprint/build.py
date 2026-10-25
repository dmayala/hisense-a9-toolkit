#!/usr/bin/env python3
"""Host-only row30 derivative of integ5; native module/modpost, DT, stock HAL.

Never changes the shared kernel cache. Replays its compiler/linker commands into
this output, as the outdoor FIX4 build does, against integ5's real exports.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import struct
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = Path('/home/user/A9-build/mainline')
BASE = ROOT / 'integ-20261023-5'
OLD = ROOT / 'plain-wrapped-unicode-fix11-20261023/scratch/obj'
DTC = Path('/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin')
SECILC = ROOT / 'android/dl/cf_arm64_x/otatools/bin/secilc'
POLICY = ROOT / 'integ-20261023-fp1/policy-command.json'
OUT = ROOT / 'integ-20261023-fp3'
FLOOR = 15_000_000_000  # repository 15 GB free-space floor; no kernel-tree copy


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def room(budget=0):
    free = shutil.disk_usage(OUT).free
    assert free >= FLOOR + budget, ('15 GB plus task budget', free, budget)
    return free


def run(args, label, cwd=None, env=None):
    room()
    print('FP_BUILD ' + label, flush=True)
    with (OUT / (label + '.log')).open('wb') as log:
        result = sp.run(list(map(str, args)), cwd=cwd, env=env, stdout=log, stderr=sp.STDOUT)
    (OUT / (label + '.rc')).write_text(str(result.returncode) + '\n')
    assert result.returncode == 0, label


def read(image, path):
    return sp.check_output(['debugfs', '-R', 'cat /' + path, str(image)], stderr=sp.DEVNULL)


def module():
    cached_config = (OLD / '.config').read_text()
    assert (BASE / 'kernel.config').read_text() == cached_config.replace('CONFIG_QCOM_STATS=m\n', 'CONFIG_QCOM_STATS=y\n')
    output = OUT / 'module'
    output.mkdir(exist_ok=True)
    env = dict(os.environ)
    tools = ['/nix/store/pv1k46ngzdixvmwnb481xk84i3ws8apv-aarch64-unknown-linux-gnu-binutils-wrapper-2.44/bin']
    matches = [p / 'bin' for p in Path('/nix/store').glob('*gcc-wrapper-14.3.0')
               if (p / 'bin/aarch64-unknown-linux-gnu-gcc').is_file()]
    assert len(matches) == 1, matches
    env['PATH'] = ':'.join(map(str, matches + tools)) + ':' + os.environ['PATH']
    commands = []

    def compile(suffix, source, dest):
        template = OLD / ('drivers/a9-fix11/.a9-usb-compat' + suffix + '.cmd')
        args = shlex.split(template.read_text().splitlines()[0].split(' := ', 1)[1])
        args = [a.replace('a9_usb_compat', 'a9_sunwave') for a in args]
        args[args.index('-o') + 1] = str(dest)
        args[-1] = str(source)
        args = [('-Wp,-MMD,' + str(dest) + '.d') if a.startswith('-Wp,-MMD,') else a for a in args]
        run(args, 'compile' + suffix, cwd=OLD, env=env)
        dependencies = shlex.split(Path(str(dest) + '.d').read_text().replace('\\\n', ' ').split(':', 1)[1])
        key = str(dest)
        (dest.parent / ('.' + dest.name + '.cmd')).write_text(
            'savedcmd_' + key + ' := ' + shlex.join(args) + '\n' +
            'source_' + key + ' := ' + str(source) + '\n' +
            'deps_' + key + ' := \\\n' + ''.join('  ' + p + ' \\\n' for p in dependencies) + '\n')
        commands.append(args)

    source = HERE / 'driver/a9-sunwave.c'
    compile('.o', source, output / 'a9-sunwave.o')
    (output / 'a9-sunwave.mod').write_text(str(output / 'a9-sunwave.o') + '\n')
    (output / 'modules.order').write_text(str(output / 'a9-sunwave.o') + '\n')
    run([OLD / 'scripts/mod/modpost', '-M', '-m', '-b', '-e', '-i', BASE / 'Module.symvers',
         '-o', output / 'Module.symvers', '-T', output / 'modules.order'], 'modpost', cwd=OLD, env=env)
    compile('.mod.o', output / 'a9-sunwave.mod.c', output / 'a9-sunwave.mod.o')
    template = OLD / 'drivers/a9-fix11/.a9-usb-compat.ko.cmd'
    args = shlex.split(template.read_text().split(' := ', 1)[1])
    args = [str(output / Path(a).name.replace('a9-usb-compat', 'a9-sunwave'))
            if a.startswith('drivers/a9-fix11/a9-usb-compat') else a for a in args]
    run(args, 'module-link', cwd=OLD, env=env)
    commands.append(args)
    run(['aarch64-unknown-linux-gnu-strip', '--strip-debug', output / 'a9-sunwave.ko'], 'module-strip', env=env)
    dest = OUT / 'overlay/lib/modules/a9-sunwave.ko'
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output / 'a9-sunwave.ko', dest)
    (OUT / 'module-commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    # Native generated import CRCs; no manufactured Module.symvers entries.
    exports = {r[1]: r[0].lower() for line in (BASE / 'Module.symvers').read_text().splitlines() if len(r := line.split()) >= 4}
    imports = sp.check_output(['modprobe', '--dump-modversions', str(dest)], text=True)
    for line in imports.splitlines():
        crc, name = line.split()
        assert exports[name] == crc.lower(), (name, crc, exports[name])
    (OUT / 'module-imports.txt').write_text(imports)



def check_fingerprint_dtb(path):
    """Require the device itself, not just pin states accepted by fdtoverlay."""
    def prop(node, key, kind='s'):
        return sp.check_output([DTC / 'fdtget', '-t', kind, path, node, key], text=True).strip()
    node = '/fingerprint'
    assert prop(node, 'compatible') == 'sunwave,fingerprint'
    tlmm = '/soc@0/pinctrl@500000'
    controller = int(prop(tlmm, 'phandle', 'i'))
    for name, pin in [('power', 100), ('reset', 19), ('irq', 97), ('logic', 70)]:
        assert list(map(int, prop(node, name + '-gpios', 'i').split())) == [controller, pin, 0]
    assert prop(node, 'pinctrl-names') == 'default'
    states = ['sf-power-default-state', 'sf-reset-default-state',
              'sf-irq-default-state', 'sf-logic-default-state']
    assert list(map(int, prop(node, 'pinctrl-0', 'i').split())) == [
        int(prop(tlmm + '/' + state, 'phandle', 'i')) for state in states]
    keys = sp.check_output([DTC / 'fdtget', '-p', path, node], text=True).splitlines()
    assert 'wakeup-source' in keys


def boot():
    raw = (BASE / 'boot.img').read_bytes()
    pg = struct.unpack_from('<I', raw, 36)[0]
    fields = (8, 16, 24, 1632, 1648)
    pos, blobs = pg, []
    for f in fields:
        size = struct.unpack_from('<I', raw, f)[0]
        blobs.append(raw[pos:pos+size]); pos += (size + pg - 1) // pg * pg
    original = blobs.copy()
    parent = OUT / 'parent.dtb'
    parent.write_bytes(blobs[4])
    tlmm = '/soc@0/pinctrl@500000'
    cells = sp.check_output([DTC / 'fdtget', '-t', 'i', parent, tlmm, 'gpio-reserved-ranges'], text=True)
    values = list(map(int, cells.split()))
    reserved = {n for first, count in zip(values[::2], values[1::2]) for n in range(first, first + count)}
    assert {19, 70, 97, 100} <= reserved
    new = sorted(reserved - {19, 70, 97, 100})
    ranges = []
    for n in new:
        if ranges and n == ranges[-1][0] + ranges[-1][1]: ranges[-1][1] += 1
        else: ranges.append([n, 1])
    assert {14, 15, 16, 17} <= set(new)
    dt = '/dts-v1/;\n/plugin/;\n' + (HERE / 'a9-fingerprint.dtsi').read_text()
    dt += '\n&tlmm { gpio-reserved-ranges = <' + ' '.join(str(v) for row in ranges for v in row) + '>; };\n'
    (OUT / 'fingerprint-overlay.dts').write_text(dt)
    run([DTC / 'dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', OUT / 'fingerprint.dtbo', OUT / 'fingerprint-overlay.dts'], 'dt-overlay')
    run([DTC / 'fdtoverlay', '-i', parent, '-o', OUT / 'integ.dtb', OUT / 'fingerprint.dtbo'], 'dt-apply')
    for n in ['parent', 'integ']:
        run([DTC / 'dtc', '-I', 'dtb', '-O', 'dts', '-o', OUT / (n + '.dts'), OUT / (n + '.dtb')], n + '-dt')
    # Compare a property inventory, not unstable dtc serialization/phandles.
    def inventory(path):
        result = {}
        def walk(node):
            for key in sp.check_output([DTC / 'fdtget', '-p', path, node], text=True).splitlines():
                result[node + ':' + key] = sp.check_output([DTC / 'fdtget', '-t', 'bx', path, node, key], text=True).strip()
            for child in sp.check_output([DTC / 'fdtget', '-l', path, node], text=True).splitlines():
                walk(node.rstrip('/') + '/' + child)
        walk('/')
        return result
    before, after = inventory(parent), inventory(OUT / 'integ.dtb')
    changed = {p for p in before if before[p] != after.get(p)}
    assert changed == {tlmm + ':gpio-reserved-ranges'}, changed
    added = set(after) - set(before)
    assert all(p.startswith(('/fingerprint:', tlmm + '/sf-', '/__symbols__:sf_')) for p in added), added
    check_fingerprint_dtb(OUT / 'integ.dtb')
    blobs[4] = (OUT / 'integ.dtb').read_bytes()
    header, body, digest = bytearray(raw[:pg]), bytearray(), hashlib.sha1()
    for f, blob in zip(fields, blobs):
        if f == 1632: struct.pack_into('<Q', header, 1636, pg + len(body))
        struct.pack_into('<I', header, f, len(blob))
        digest.update(blob); digest.update(struct.pack('<I', len(blob)))
        body += blob; body += bytes(-len(body) % pg)
    header[576:608] = digest.digest() + bytes(12)
    (OUT / 'boot.img').write_bytes(header + body)
    assert blobs[:4] == original[:4]
    assert header[64:576] == raw[64:576] and header[608:1632] == raw[608:1632]
    (OUT / 'recovery_dtbo.img').write_bytes(original[3])
    (OUT / 'dt-delta.json').write_text(json.dumps(dict(reopened=[19, 70, 97, 100], changed=sorted(changed), added=sorted(added),
        kernel_ramdisk_cmdline_unchanged=True), indent=2) + '\n')
    run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3', '-P',
         REPO / 'scripts/abl-check.py', OUT / 'boot.img', 'boot', '--dtbo', OUT / 'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((OUT / 'abl-check.log').read_text())['result'] == 'PASS'


def policy():
    overlay = OUT / 'overlay'
    dst = overlay / 'etc/selinux'
    dst.mkdir(parents=True, exist_ok=True)
    additions = (HERE / 'policy/fingerprint.cil').read_text()
    vendor = read(BASE / 'vendor.img', 'etc/selinux/vendor_sepolicy.cil') + b'\n' + additions.encode()
    (dst / 'vendor_sepolicy.cil').write_bytes(vendor)
    for name in ['vendor_file_contexts', 'vendor_hwservice_contexts']:
        data = read(BASE / 'vendor.img', 'etc/selinux/' + name)
        (dst / name).write_bytes(data + b'\n' + (HERE / 'policy' / (name + '.additions')).read_bytes())
    old = json.loads(POLICY.read_text())
    original = old[old.index('30') + 1:old.index('-o')]
    inputs = OUT / 'policy-inputs'
    inputs.mkdir(exist_ok=True)
    files = []
    for i, p in enumerate(original):
        data = vendor if i == 7 else read(BASE / 'vendor.img', 'etc/selinux/plat_pub_versioned.cil') if i == 6 else Path(p).read_bytes()
        dest = inputs / Path(p).name
        dest.write_bytes(data); files.append(dest)
    command = [SECILC, '-D', '-m', '-M', 'true', '-G', '-N', '-c', '30', *files,
               '-o', dst / 'precompiled_sepolicy', '-f', '/dev/null']
    run(command, 'policy-build')
    (OUT / 'policy-command.json').write_text(json.dumps(list(map(str, command)), indent=2) + '\n')
    (overlay / 'etc/ueventd.rc').write_bytes(read(BASE / 'vendor.img', 'etc/ueventd.rc') + b'\n/dev/sunwave_fp 0660 system system\n')


def vendor():
    meta = load('vendor_meta', HERE.parent / 'android/refresh-integrated-vendor.py')
    overlay = OUT / 'overlay'
    image = OUT / 'vendor.img'
    base = BASE / 'vendor.img'
    assert not image.exists(), 'candidate already exists'
    payload_budget = sum((p.stat().st_size + 4095) // 4096 * 4096 for p in overlay.rglob('*') if p.is_file())
    budget = base.stat().st_blocks * 512 + payload_budget + 16 * 1024**2
    room(budget)
    run(['cp', '--reflink=auto', '--sparse=always', base, image], 'vendor-copy')
    batch, directories, expected = [], set(), {}
    def exists(path): return 'Inode:' in meta.debug(base, 'stat /' + path)
    def label(path, kind):
        p = OUT / ('label-' + kind)
        p.write_bytes(('u:object_r:' + kind + ':s0\0').encode())
        batch.append(f'ea_set -f {p} /{path} security.selinux')
    def parent(path):
        name = str(Path(path).parent)
        if name == '.' or exists(name) or name in directories: return
        parent(name)
        batch.extend([f'mkdir /{name}', f'sif /{name} mode 040755', f'sif /{name} uid 0', f'sif /{name} gid 0'])
        kind = 'vendor_file' if name.startswith(('bin', 'lib')) else 'a9ml_firmware_file' if name.startswith('firmware') else 'vendor_configs_file'
        label(name, kind); directories.add(name)
    for source in sorted(p for p in overlay.rglob('*') if p.is_file()):
        path = str(source.relative_to(overlay))
        present = exists(path)
        if present and read(base, path) == source.read_bytes(): continue
        if present:
            info = meta.metadata(base, path)
            batch.append('rm /' + path)
        else:
            parent(path)
            info = meta.metadata(base, 'etc/init/init.a9ml-gpu.rc').copy()
            info.update(mode='0755' if path.startswith('bin/') else '0644', uid='0', gid='0')
            info['label'] = 'u:object_r:' + ('hal_fingerprint_default_exec' if path.startswith('bin/hw/') else
                            'a9ml_firmware_file' if path.startswith('firmware/') else
                            'vendor_file' if path.startswith(('lib/', 'lib64/')) else 'vendor_configs_file') + ':s0'
        batch += [f'write {source} /{path}', f'sif /{path} mode 010{info["mode"]}',
                  f'sif /{path} uid {info["uid"]}', f'sif /{path} gid {info["gid"]}']
        for field in ('atime', 'mtime', 'ctime', 'crtime'):
            batch.append(f'sif /{path} {field} @{int(info[field], 16)}')
        kind = info['label'].split(':')[2]
        label(path, kind)
        expected[path] = info
    script = OUT / 'install.debugfs'
    script.write_text('\n'.join(batch) + '\n')
    run(['debugfs', '-w', '-f', script, image], 'vendor-install')
    for path, info in expected.items():
        assert read(image, path) == (overlay / path).read_bytes(), path
        assert meta.metadata(image, path) == info, path
    for path in ['etc/init/hw/init.hlte556n.rc', 'etc/fstab.hlte556n', 'bin/a9ml-eink.sh', 'lib/modules/kernel-image.sha256']:
        assert read(image, path) == read(base, path), path
    run(['e2fsck', '-fn', image], 'vendor-fsck')
    original_modules = json.loads((ROOT / 'plain-wrapped-unicode-fix11-20261023/module-targets.json').read_text())
    assert len(original_modules) == 100
    for path in original_modules:
        assert read(image, path) == read(base, path), 'changed inherited module: ' + path
    # Same audit over the original 100 modules plus our one new native module.
    shutil.copyfile(BASE / 'audit-modules.py', OUT / 'audit-modules.py')
    audit_root = OUT / 'module-audit' / OUT.name
    audit_root.parent.mkdir(exist_ok=True)
    shutil.copytree(BASE / 'module-audit/integ-20261023-5', audit_root, copy_function=os.link)
    module_dir = next(audit_root.rglob('a9-usb-compat.ko')).parent
    shutil.copyfile(overlay / 'lib/modules/a9-sunwave.ko', module_dir / 'a9-sunwave.ko')
    audit_name = OUT.name.rsplit('-', 1)[-1]
    run(['python3', OUT / 'audit-modules.py', BASE, audit_name, image], 'module-audit')
    audit = json.loads((OUT / ('module-audit-' + audit_name + '.json')).read_text())['vendors'][OUT.name]
    assert audit['modules'] == 101 and all(audit[k] == 0 for k in ('failing_modules', 'crc_mismatches', 'vermagic_mismatches'))
    return dict(changed=expected, module_audit={k: v for k, v in audit.items() if k != 'details'})


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--step', choices=['module', 'boot', 'policy', 'vendor', 'all'], default='all')
    args = parser.parse_args()
    OUT = args.out.resolve(); OUT.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    if args.step in ('module', 'all'): module()
    if args.step in ('boot', 'all'): boot()
    if args.step in ('policy', 'all'):
        shutil.copytree(HERE / 'vendor', OUT / 'overlay', dirs_exist_ok=True)
        load('fp_assets', HERE / 'extract-assets.py').extract(OUT)
        policy()
        load('fp_link_audit', HERE / 'audit-libraries.py').audit(OUT)
    if args.step in ('vendor', 'all'):
        result = vendor()
        hashes = {n: sha(OUT / n) for n in ['boot.img', 'vendor.img', 'module/a9-sunwave.ko', 'integ.dtb']}
        (OUT / 'SHA256SUMS').write_text(''.join(v + '  ' + n + '\n' for n, v in hashes.items()))
        result.update(base=str(BASE), base_boot_sha256=sha(BASE / 'boot.img'), base_vendor_sha256=sha(BASE / 'vendor.img'),
                      sha256=hashes, host_only=True, hardware_tested=False, free_bytes=room(),
                      source_sha256={str(p.relative_to(REPO)): sha(p) for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts},
                      source_commit=sp.check_output(['git', '-C', REPO, 'rev-parse', 'HEAD'], text=True).strip())
        (OUT / 'integrated-build.json').write_text(json.dumps(result, indent=2) + '\n')
        print('FP host image ready: ' + str(OUT), flush=True)


if __name__ == '__main__':
    main()
