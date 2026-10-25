#!/usr/bin/env python3
"""Host-only native incremental Kbuild replay over integ5's frozen objects.

Recompile SCM/QSEE plus integ5's built-in statistics and embedded config. Use
native genksyms, modpost, linker and kallsyms; preserve every other object. This
also works after the shared cache's source aliases have been archived.
"""
from pathlib import Path
import gzip
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = Path('/home/user/A9-build/mainline')
OUT = ROOT / 'integ-20261023-fp4'
OLD = ROOT / 'plain-wrapped-unicode-fix11-20261023/scratch/obj'
OLDK = OLD.parent / 'kernel'
CLEAN = ROOT / 'linux-7.2.10-gpu'
BASE = ROOT / 'integ-20261023-5'
K = OUT / 'kernel-source'
OBJ = OUT / 'obj'
BIN = '/nix/store/vgcifiwvqk663pmk7d70jdyffi1p5y78-aarch64-unknown-linux-gnu-binutils-2.44/bin'
ENV = dict(os.environ)
ENV['PATH'] = ':'.join(str(p/'bin') for p in Path('/nix/store').glob('*gcc-wrapper-14.3.0')
                       if (p/'bin/aarch64-unknown-linux-gnu-gcc').is_file()) + ':' + BIN + ':' + ENV['PATH']
COMMANDS = []
TARGET_CONFIG = None
EXTRA_OBJECTS = []
ALLOWED_NEW_EXPORTS = set()
ALLOWED_CHANGED_EXPORTS = set()


def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def room(budget=0):
    assert shutil.disk_usage(OUT).free >= 15_000_000_000 + budget, '15 GB plus build budget'


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    path.write_bytes(data)


def run(args, label, env=ENV, data=None):
    room()
    args = list(map(str, args)); COMMANDS.append(dict(label=label, args=args))
    print('FP4_KERNEL ' + label, flush=True)
    with (OUT/(label+'.log')).open('wb') as log:
        r = sp.run(args, cwd=OBJ, env=env, input=data, stdout=log, stderr=sp.STDOUT)
    (OUT/(label+'.rc')).write_text(str(r.returncode)+'\n')
    assert r.returncode == 0, label


def command(name):
    p = OLD / name
    lexer = shlex.shlex(p.read_text().splitlines()[0].split(' := ', 1)[1], posix=True, punctuation_chars=';')
    lexer.whitespace_split = True; lexer.commenters = ''
    return [a.replace(str(OLDK), str(K)) for a in lexer]


def prepare():
    assert not OBJ.exists(), 'private output already exists; preserve recorded build'
    room(1_000_000_000)
    # Only headers and local driver includes are needed for the small recompile.
    for subtree in ['include', 'arch/arm64/include', 'drivers/firmware/qcom', 'drivers/soc/qcom', 'kernel']:
        for p in (CLEAN/subtree).rglob('*'):
            if not p.is_file(): continue
            rel = p.relative_to(CLEAN); target = K/rel
            target.parent.mkdir(parents=True, exist_ok=True)
            source = OLDK/rel
            target.symlink_to(source if source.is_file() else p)
    for name in ['mksysmap', 'file-size.sh', 'link-vmlinux.sh']:
        p = K/'scripts'/name;p.parent.mkdir(parents=True,exist_ok=True);p.symlink_to(CLEAN/'scripts'/name)
    # Local Android SCM header extension, already part of the frozen cache.
    header = K/'drivers/firmware/qcom/qcom_scm.h'
    data = header.read_bytes()
    if b'qcom_scm_qseecom_android_call' not in data:
        data += b'\nint qcom_scm_qseecom_android_call(const struct qcom_scm_desc *desc, struct qcom_scm_res *res);\n'
    write(header, data)
    write(K/'drivers/firmware/qcom/qcom_scm.c', (OLDK/'drivers/firmware/qcom/qcom_scm.c').read_bytes())
    for p in (HERE.parent/'qseecom/driver').iterdir():
        if p.is_file(): write(K/'drivers/firmware/qcom'/p.name, p.read_bytes())
    OBJ.mkdir()
    # File symlinks are immutable inputs. Detach every native output before use.
    skip = {'vmlinux', 'vmlinux.unstripped', 'vmlinux.o', 'vmlinux.a', 'vmlinux.symvers',
            '.vmlinux.export.c', '.vmlinux.export.o', 'System.map', '.vmlinux.objs', 'source'}
    for p in OLD.rglob('*'):
        if p.is_dir(): continue
        rel = p.relative_to(OLD)
        if p.name in skip or p.name.startswith('.tmp_') or p.suffix == '.d': continue
        if not p.is_file(): continue
        dest = OBJ/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.symlink_to(p)
    config = TARGET_CONFIG or (BASE/'kernel.config').read_bytes()
    write(OBJ/'.config', config)
    for name in ['include/generated/autoconf.h', 'include/config/auto.conf']:
        data = (OLD/name).read_bytes().replace(b'CONFIG_QCOM_STATS_MODULE 1', b'CONFIG_QCOM_STATS 1').replace(b'CONFIG_QCOM_STATS=m', b'CONFIG_QCOM_STATS=y')
        if TARGET_CONFIG:
            data = data.replace(b'CONFIG_QCOM_TZMEM_MODE_GENERIC 1', b'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE 1').replace(b'CONFIG_QCOM_TZMEM_MODE_GENERIC=y', b'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y')
        write(OBJ/name, data)
    write(OBJ/'kernel/config_data.gz', gzip.compress(config, mtime=0))
    run(['patch','--batch','--fuzz=0','-p1','-d',K,'-i',HERE.parent/'qseecom/0002-a9-qseecom-resource-votes.patch'], 'scm-resource-patch')
    write(K/'drivers/firmware/qcom/qcom_scm-smc.c', (K/'drivers/firmware/qcom/qcom_scm-smc.c').read_bytes())
    run(['patch','--batch','--fuzz=0','-p1','-d',K,'-i',HERE.parent/'qseecom/0003-rpmb-register-diagnostics.patch'], 'rpmb-register-diag-patch')


def extra_headers():
    for subtree in ['include', 'arch/arm64/include', 'drivers/firmware/qcom', 'drivers/soc/qcom', 'kernel']:
        for p in (OLDK/subtree).rglob('*'):
            if not p.is_file(): continue
            dest = K/p.relative_to(OLDK)
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.symlink_to(p)


def compile_one(rel):
    dest = OBJ/rel;dest.unlink(missing_ok=True)
    template = str(Path(rel).parent / ('.'+Path(rel).name+'.cmd'))
    args = command(template)
    # Native cached compiler flags; source and generated output stay private.
    run(args, 'compile-'+Path(rel).stem)
    dep = dest.parent/('.'+dest.name+'.d')
    if not dep.exists(): dep = Path(str(dest)+'.d')
    cmdpath = dest.parent/('.'+dest.name+'.cmd')
    text = 'savedcmd_'+rel+' := '+shlex.join(args)+'\n'
    # Native symbol CRC generation, exactly Makefile.build's gen_symversions_c.
    symbols = sp.check_output(['aarch64-unknown-linux-gnu-nm', dest], env=ENV, text=True)
    if ' __export_symbol_' in symbols:
        pp = args.copy();i=pp.index('-o');del pp[i:i+2]
        pp.remove('-c');pp = [a for a in pp if not a.startswith('-Wp,-MMD,')]
        pp[1:1] = ['-E', '-D__GENKSYMS__']
        run(pp, 'preprocess-'+Path(rel).stem)
        preprocessed = (OUT/('preprocess-'+Path(rel).stem+'.log')).read_bytes()
        run([OLD/'scripts/genksyms/genksyms'], 'genksyms-'+Path(rel).stem, data=preprocessed)
        text += (OUT/('genksyms-'+Path(rel).stem+'.log')).read_text()
    write(cmdpath, text.encode())


def exports(path):
    return {a[1]:a[0] for l in path.read_text().splitlines() if len(a:=l.split()) >= 4}


def link():
    members = sp.check_output(['aarch64-unknown-linux-gnu-ar','t',OLD/'vmlinux.a'],env=ENV,text=True).splitlines()
    relative = [str(Path(p).relative_to(OLD)) for p in members]
    assert 'drivers/soc/qcom/qcom_stats.o' not in relative
    relative.insert(relative.index('drivers/soc/qcom/kryo-l2-accessors.o'), 'drivers/soc/qcom/qcom_stats.o')
    for rel in ['drivers/firmware/qcom/qcom_scm.o', 'drivers/firmware/qcom/qcom_scm-smc.o', 'drivers/firmware/qcom/a9-qseecom.o', 'drivers/soc/qcom/qcom_stats.o', 'kernel/configs.o', *EXTRA_OBJECTS]:
        assert rel in relative
        compile_one(rel)
    # All unchanged objects are read through immutable private symlinks.
    (OBJ/'vmlinux.a').unlink(missing_ok=True)
    run(['aarch64-unknown-linux-gnu-ar','cDPrST','vmlinux.a',*[str(OBJ/p) for p in relative]],'archive')
    write(OBJ/'.vmlinux.objs', ('\n'.join(relative)+'\n').encode())
    run(command('.vmlinux.o.cmd'), 'native-vmlinux-object')
    run([OLD/'scripts/mod/modpost','-M','-m','-b','-o','vmlinux.symvers','-n','vmlinux.o'], 'native-modpost')
    actual, expected = exports(OBJ/'vmlinux.symvers'), exports(BASE/'vmlinux.symvers')
    changed={n:[v,actual.get(n)] for n,v in expected.items() if actual.get(n)!=v}
    assert set(changed) <= ALLOWED_CHANGED_EXPORTS, changed
    importers=[]
    for module in (ROOT/'integ-20261023-fp1/module-audit').rglob('*.ko'):
        imports=sp.check_output(['modprobe','--dump-modversions',module],text=True)
        if any(name in imports for name in changed): importers.append(str(module))
    assert not importers, importers
    added=set(actual)-set(expected)
    assert added <= ALLOWED_NEW_EXPORTS, added
    (OUT/'abi.json').write_text(json.dumps(dict(native_genksyms_modpost=True, exports=len(actual), dictionary_identical=actual==expected, existing_exports_match=not changed, changed_exports=changed, changed_export_module_importers=importers, added_exports=sorted(added), module_imports_match=not importers),indent=2)+'\n')
    compile_one('.vmlinux.export.o')
    env = dict(ENV, srctree=str(K), objtree='.', SRCARCH='arm64', MAKE='true',
               CC='aarch64-unknown-linux-gnu-gcc', NM='aarch64-unknown-linux-gnu-nm',
               CONFIG_SHELL='/bin/sh', KBUILD_LDS='arch/arm64/kernel/vmlinux.lds',
               KBUILD_VMLINUX_LIBS='drivers/firmware/efi/libstub/lib.a',
               NOSTDINC_FLAGS='-nostdinc', LINUXINCLUDE='-I'+str(K/'arch/arm64/include')+' -Iarch/arm64/include/generated -I'+str(K/'include')+' -Iinclude -I'+str(K/'arch/arm64/include/uapi')+' -Iarch/arm64/include/generated/uapi -I'+str(K/'include/uapi')+' -Iinclude/generated/uapi',
               KBUILD_CPPFLAGS='-D__KERNEL__', KBUILD_AFLAGS='-D__ASSEMBLY__ -fno-PIE', KBUILD_AFLAGS_KERNEL='')
    # Reuse the unchanged init/version-timestamp.o; the native linker regenerates
    # all symbol addresses, kallsyms, exception-table sorting, and System.map.
    args = command('.vmlinux.unstripped.cmd')
    assert args[-1] == 'true' and args[-2] == ';'
    run(args[:-2], 'native-link',env)
    args = command('.vmlinux.cmd');i=args.index(';')
    run(args[:i], 'native-objcopy-1');run(args[i+1:], 'native-objcopy-2')
    image = OBJ/'arch/arm64/boot/Image';image.unlink(missing_ok=True)
    run(command('arch/arm64/boot/.Image.cmd'),'native-image')
    raw = image.read_bytes();assert b'qcom_stats_probe\n' in (OBJ/'System.map').read_bytes()
    config = raw.split(b'IKCFG_ST',1)[1].split(b'IKCFG_ED',1)[0]
    assert gzip.decompress(config) == (TARGET_CONFIG or (BASE/'kernel.config').read_bytes())
    (OUT/'Image.gz').write_bytes(gzip.compress(raw,mtime=0))
    for name in ['vmlinux.symvers','System.map']:
        shutil.copyfile(OBJ/name,OUT/name)
    full=(BASE/'Module.symvers').read_text().splitlines(True)
    modules=[line for line in full if len(line.split())>=4 and line.split()[2]!='vmlinux']
    (OUT/'Module.symvers').write_text((OBJ/'vmlinux.symvers').read_text()+''.join(modules))
    (OUT/'kernel.config').write_bytes(TARGET_CONFIG or (BASE/'kernel.config').read_bytes())
    (OUT/'kernel-commands.json').write_text(json.dumps(COMMANDS,indent=2)+'\n')
    print('FP4_KERNEL PASS: native CRC table matches integ5',flush=True)


def main():
    os.umask(0o077);OUT.mkdir(parents=True,exist_ok=True)
    if not OBJ.exists(): prepare()
    else: assert not (OUT/'Image.gz').exists(), 'kernel already built'
    extra_headers();link()


if __name__ == '__main__': main()
