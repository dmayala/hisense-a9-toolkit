#!/usr/bin/env python3
"""Host-only FIX4 sensor module build and outdoor vendor rebase onto integ5."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
ROOT = Path('/home/user/A9-build/mainline')
INTEG = ROOT / 'integ-20261023-5'
BASE = ROOT / 'plain-wrapped-unicode-fix11-20261023'
OLD = BASE / 'scratch/obj'
OUT = INTEG / 'fix4'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def run(args, label, cwd=OLD, env=None):
    assert shutil.disk_usage(OUT).free >= 15 * 1024**3
    with (OUT / (label + '.log')).open('wb') as f:
        sp.run(list(map(str, args)), cwd=cwd, env=env, check=True, stdout=f, stderr=sp.STDOUT)


def compile_saved(name, suffix, source, dest, env):
    # Replay actual native compiler arguments, redirecting source/output/deps
    # into this task. Never invoke make or modify the shared output cache.
    command = (OLD / f'drivers/media/i2c/.{name}{suffix}.cmd').read_text().splitlines()[0].split(' := ', 1)[1]
    args = shlex.split(command)
    args[args.index('-o') + 1] = str(dest)
    args[-1] = str(source)
    args = [('-Wp,-MMD,' + str(dest) + '.d') if a.startswith('-Wp,-MMD,') else a for a in args]
    run(args, name + suffix, env=env)
    # GCC emitted the real dependency list. modpost uses Kbuild .cmd format
    # to compute srcversion, so record that list alongside the actual command.
    dependencies = shlex.split(Path(str(dest) + '.d').read_text().replace('\\\n', ' ').split(':', 1)[1])
    key = str(dest)
    (dest.parent / ('.' + dest.name + '.cmd')).write_text(
        'savedcmd_' + key + ' := ' + shlex.join(args) + '\n' +
        'source_' + key + ' := ' + str(source) + '\n' +
        'deps_' + key + ' := \\\n' + ''.join('  ' + p + ' \\\n' for p in dependencies) + '\n')
    return args


def main():
    os.umask(0o077)
    OUT.mkdir(exist_ok=True)
    assert not (OUT / 'result.json').exists(), 'completed candidate already exists'
    modules = OUT / 'modules'
    modules.mkdir(exist_ok=True)
    tools = []
    for pattern, tool in [('*gcc-wrapper-14.3.0', 'aarch64-unknown-linux-gnu-gcc'),
                          ('*binutils-wrapper*', 'aarch64-unknown-linux-gnu-ld')]:
        matches = [p / 'bin' for p in Path('/nix/store').glob(pattern) if (p / 'bin' / tool).is_file()]
        assert len(matches) == 1, (tool, matches)
        tools.extend(map(str, matches))
    env = dict(os.environ, PATH=':'.join(tools) + ':' + os.environ['PATH'])
    commands = {}
    for name in ('s5k3l6-a9', 'hi556-a9'):
        source = REPO / 'scripts/mainline/cameras' / (name + '.c')
        # Include lookup retains the original private stock tables; no firmware
        # or stock tables are copied into the repository.
        commands[name] = [compile_saved(name, '.o', source, modules / (name + '.o'), env)]
        (modules / (name + '.mod')).write_text(str(modules / (name + '.o')) + '\n')
    order = modules / 'modules.order'
    order.write_text(''.join(str(modules / (n + '.o')) + '\n' for n in commands))
    # Fresh modpost resolves imports from the exact integ5 kernel/providers.
    run([OLD / 'scripts/mod/modpost', '-M', '-m', '-b', '-e', '-i', INTEG / 'Module.symvers',
         '-o', modules / 'Module.symvers', '-T', order], 'modpost', env=env)
    for name in commands:
        commands[name].append(compile_saved(name, '.mod.o', modules / (name + '.mod.c'), modules / (name + '.mod.o'), env))
        args = shlex.split((OLD / f'drivers/media/i2c/.{name}.ko.cmd').read_text().split(' := ', 1)[1].strip())
        args = [str(modules / Path(a).name) if a.startswith('drivers/media/i2c/' + name) else a for a in args]
        commands[name].append(args)
        run(args, name + '-link', env=env)
        run(['aarch64-unknown-linux-gnu-strip', '--strip-debug', modules / (name + '.ko')], name + '-strip', env=env)
    (OUT / 'module-commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    # Rebase only the current assistance helper and changed sensors onto integ5.
    # The obsolete native outdoor observer/job are not used by the combined hook.
    overlay = OUT / 'overlay'
    (overlay / 'bin').mkdir(parents=True, exist_ok=True)
    helper = ROOT / 'gnss-outdoor-fix1-20261023/out/a9-gnss-loc'
    assert sha(helper) == '318a27fc4dd0ccdc8b687863ec44185b7e9d8122bc0c606ed9ce7ecaa5ee7f4c'
    shutil.copyfile(helper, overlay / 'bin/a9-gnss-loc')
    deadman = sp.check_output(['debugfs','-R','cat /bin/a9ml-deadman.sh',str(INTEG/'vendor.img')],stderr=sp.DEVNULL)
    outdoor_deadman = (ROOT/'gnss-outdoor-fix1-20261023/a9ml-deadman.sh').read_bytes()
    assert outdoor_deadman == deadman.replace(b'HOLD=$(flag a9ml.hold 1200)', b'HOLD=$(flag a9ml.hold 2700)')
    (overlay/'bin/a9ml-deadman.sh').write_bytes(outdoor_deadman)
    (overlay / 'lib/modules').mkdir(parents=True, exist_ok=True)
    for name in commands:
        shutil.copyfile(modules / (name + '.ko'), overlay / 'lib/modules' / (name + '.ko'))
    # All four destinations already exist; no growth/inode reserve is needed.
    # Use the established debugfs replacement recipe with an exact sparse-copy
    # budget rather than the general refresh tool's extra 512 MiB allowance.
    spec = importlib.util.spec_from_file_location('vendor_meta', REPO/'scripts/mainline/android/refresh-integrated-vendor.py')
    meta = importlib.util.module_from_spec(spec); spec.loader.exec_module(meta)
    vendor_dir = OUT / 'vendor'; vendor_dir.mkdir(exist_ok=True)
    image = OUT / 'vendor/vendor.img'
    base = INTEG / 'vendor.img'
    if not image.exists():
        assert shutil.disk_usage(OUT).free >= 15*1024**3 + base.stat().st_blocks*512 + 8*1024**2
        run(['cp','--reflink=auto','--sparse=always',base,image], 'vendor-copy', cwd=OUT)
    batch = []; expected = {}
    for i, source in enumerate(sorted(p for p in overlay.rglob('*') if p.is_file())):
        path = str(source.relative_to(overlay)); info = meta.metadata(base,path); expected[path] = info
        label = OUT / ('label-'+str(i)); label.write_bytes(info['label'].encode()+b'\0')
        batch += [f'rm /{path}',f'write {source} /{path}',f'sif /{path} mode 010{info["mode"]}',
                  f'sif /{path} uid {info["uid"]}',f'sif /{path} gid {info["gid"]}',
                  f'ea_set -f {label} /{path} security.selinux']
        batch += [f'sif /{path} {k} @{int(info[k],16)}' for k in ('atime','mtime','ctime','crtime')]
    script = OUT / 'vendor.debugfs';script.write_text('\n'.join(batch)+'\n')
    run(['debugfs','-w','-f',script,image],'vendor-install',cwd=OUT)
    for path,info in expected.items():
        data = sp.check_output(['debugfs','-R','cat /'+path,str(image)],stderr=sp.DEVNULL)
        assert data == (overlay/path).read_bytes() and meta.metadata(image,path) == info,path
    run(['e2fsck','-fn',image],'vendor-fsck',cwd=OUT)
    # Reuse the prior audit's unchanged extracted modules as hardlinks; replace
    # only our two modules in the new audit directory before the full import scan.
    audit_root = OUT/'module-audit/vendor'
    if not audit_root.exists():
        shutil.copytree(INTEG/'module-audit/integ-20261023-5',audit_root,copy_function=os.link)
    for name in commands:
        matches=list(audit_root.rglob(name+'.ko'));assert len(matches)==1
        matches[0].unlink();shutil.copyfile(modules/(name+'.ko'),matches[0])
    shutil.copyfile(INTEG / 'audit-modules.py', OUT / 'audit-modules.py')
    run(['python3', OUT / 'audit-modules.py', INTEG, 'fix4', image], 'module-audit', cwd=OUT)
    audit = json.loads((OUT / 'module-audit-fix4.json').read_text())['vendors']['vendor']
    assert audit['modules'] == 100 and all(audit[k] == 0 for k in ('failing_modules', 'crc_mismatches', 'vermagic_mismatches'))
    result = dict(host_only=True, hardware_tested=False, boot=str(INTEG / 'boot.img'), boot_sha256=sha(INTEG / 'boot.img'),
                  base_vendor_sha256=sha(INTEG / 'vendor.img'), vendor=str(image), vendor_sha256=sha(image),
                  changed=['bin/a9-gnss-loc', 'bin/a9ml-deadman.sh', 'lib/modules/hi556-a9.ko', 'lib/modules/s5k3l6-a9.ko'],
                  module_audit={k:v for k,v in audit.items() if k != 'details'},
                  source_sha256={str(p.relative_to(REPO)):sha(p) for p in [Path(__file__), *(REPO / 'scripts/mainline/cameras' / (n + '.c') for n in commands)]})
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS FIX4 native modules/modpost, 100-module ABI audit, rebased vendor fsck')


if __name__ == '__main__':
    main()
