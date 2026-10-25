#!/usr/bin/env python3
"""Build S1 kernel/DT/gated FastRPC offline without mutating the measured D1.

Run in scripts/mainline/kernel/shell.nix with cached inputs only. Reuses the D1
source read-only, builds into fresh O= and a private external module directory.
The in-tree FastRPC module is not built or staged: only its exact source copy
plus the audited default-off S1 gate is compiled as an external module.
"""
import argparse
import hashlib
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import shutil
import subprocess as sp

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
CHECK=SourceFileLoader('s1_kernel_check',str(HERE/'s1-kernel-check.py')).load_module()
FLOOR=15_000_000_000
RESERVE=3_000_000_000
FAST_SHA='453952957b8a6b93d2c3a8685e391004361e3a730b2213042dec216b62157c4c'
def sha(p): return hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
def run(*args,**kw):
    print('+',*map(str,args),flush=True)
    sp.run(list(map(str,args)),check=True,**kw)
def guard():
    free=shutil.disk_usage('/').free
    if free<FLOOR: raise RuntimeError(f'disk floor breached: {free} < {FLOOR}')
    return free

def dtb(d1,out):
    src=d1/'linux'; obj=d1/'build'; dtd=src/'arch/arm64/boot/dts/qcom'
    board='sm6115-hisense-hlte556n-android-gpu-thermal-adsp'
    (out/'s1.dts').write_text(f'#include "{board}.dts"\n#include "a9-sensors-s1.dtsi"\n')
    shutil.copy2(HERE/'a9-sensors-s1.dtsi',out/'a9-sensors-s1.dtsi')
    run('cpp','-nostdinc','-undef','-D__DTS__','-x','assembler-with-cpp',
        '-I',out,'-I',dtd,'-I',src/'include','-I',src/'scripts/dtc/include-prefixes',
        '-o',out/'s1.pre.dts',out/'s1.dts')
    run(obj/'scripts/dtc/dtc','-@','-q','-I','dts','-O','dtb','-b','0',
        '-o',out/'s1.dtb',out/'s1.pre.dts')
    old=obj/'arch/arm64/boot/dts/qcom'/f'{board}.dtb'
    CHECK.check_dt(CHECK.D0.read_fdt(old),CHECK.D0.read_fdt(out/'s1.dtb'))
    return old

def module_only(d1,out,jobs):
    """Compile the real gated driver using a 5MB private prepared interface.

    This verifies compilation and D1 ABI only. It is never represented as an
    S1 kernel/image build, because the copied config still describes D1.
    """
    d1=d1.resolve(); out=out.resolve()
    if out.exists() or 'a9-assets' in out.parts or not out.is_relative_to(Path.home()/'A9-build'):
        raise ValueError('fresh private ~/A9-build output required')
    if guard()<FLOOR+64_000_000: raise RuntimeError('need 64MB above the disk floor')
    old=d1/'build'; src=d1/'linux'
    if sha(src/'drivers/misc/fastrpc.c')!=FAST_SHA: raise ValueError('FastRPC source changed')
    out.mkdir(parents=True,mode=0o700); obj=out/'build'; obj.mkdir()
    for directory in ('include','scripts','arch/arm64/include/generated'):
        shutil.copytree(old/directory,obj/directory,symlinks=True)
    for name in ('Makefile','.config','Module.symvers'):
        shutil.copy2(old/name,obj/name)
    ext=out/'fastrpc-module'; (ext/'drivers/misc').mkdir(parents=True)
    shutil.copy2(src/'drivers/misc/fastrpc.c',ext/'drivers/misc/fastrpc.c')
    run('patch','--fuzz=0','-p1','-i',HERE/'s1-kernel-fastrpc-gate.patch',cwd=ext)
    run('git','apply','--unidiff-zero',HERE/'fastrpc-restore-context.patch',cwd=ext)
    shutil.copy2(ext/'drivers/misc/fastrpc.c',ext/'fastrpc.c')
    (ext/'Makefile').write_text('obj-m += fastrpc.o\n')
    run('make','-C',src,'O='+str(obj),'ARCH=arm64',
        'CROSS_COMPILE=aarch64-unknown-linux-gnu-',f'-j{jobs}','M='+str(ext),'modules')
    imports=sp.check_output(['modprobe','--show-modversions',str(ext/'fastrpc.ko')],text=True)
    oldsym={f[1]:f[0].lower() for f in (line.split() for line in (old/'Module.symvers').read_text().splitlines())}
    for line in imports.splitlines():
        crc,name=line.split()
        if oldsym.get(name)!=crc.lower(): raise ValueError(('module CRC mismatch',name))
    (out/'module-receipt.json').write_text(json.dumps({'status':'PASS-GATED-MODULE-COMPILE-ONLY',
        'source_sha256':FAST_SHA,'gate_patch_sha256':sha(HERE/'s1-kernel-fastrpc-gate.patch'),'restore_patch_sha256':sha(HERE/'fastrpc-restore-context.patch'),
        'fastrpc.ko':sha(ext/'fastrpc.ko'),'imports':imports,
        'vermagic':sp.check_output(['modinfo','-F','vermagic',str(ext/'fastrpc.ko')],text=True).strip(),
        'gate_parameter':sp.check_output(['modinfo','-F','parm',str(ext/'fastrpc.ko')],text=True).strip(),
        'free_bytes':guard()},indent=2)+'\n')
    print('S1 GATED MODULE COMPILE ONLY PASS',out)

def build(d1,out,jobs,dt_only=False,reuse_objects=False):
    d1=d1.resolve(); out=out.resolve()
    if out.exists() or 'a9-assets' in out.parts or not out.is_relative_to(Path.home()/'A9-build'):
        raise ValueError('fresh private ~/A9-build output required')
    free=guard()
    if not dt_only and free<FLOOR+RESERVE:
        raise RuntimeError(f'need >=18GB free before isolated build (3GB bounded growth); currently {free}')
    src=d1/'linux'; old=d1/'build'
    if sha(src/'drivers/misc/fastrpc.c')!=FAST_SHA: raise ValueError('upstream FastRPC source changed')
    anchors={str(p):sha(p) for p in (src/'drivers/misc/fastrpc.c',old/'.config',old/'Module.symvers',old/'arch/arm64/boot/Image.gz')}
    out.mkdir(parents=True,mode=0o700)
    baseline=dtb(d1,out)
    if dt_only:
        (out/'dt-receipt.json').write_text(json.dumps({'baseline':str(baseline),'baseline_sha256':sha(baseline),
            's1_sha256':sha(out/'s1.dtb'),'status':'PASS-EXACT-DT-DELTA'},indent=2)+'\n')
        print('S1 DT ONLY PASS',out); return
    obj=out/'build'
    if reuse_objects:
        # Ordinary private copies only: never hardlink writable build outputs.
        # Saved Kbuild commands still name the same read-only D1 source path.
        shutil.copytree(old,obj,symlinks=True)
    else:
        obj.mkdir()
        shutil.copy2(old/'.config',obj/'.config')
    run(src/'scripts/config','--file',obj/'.config','--module','QCOM_FASTRPC')
    def make(*args): run('make','-C',src,'O='+str(obj),'ARCH=arm64',
                         'CROSS_COMPILE=aarch64-unknown-linux-gnu-',f'-j{jobs}',*args)
    make('olddefconfig')
    CHECK.check_config((old/'.config').read_text(),(obj/'.config').read_text())
    # Source is the immutable D1 source; no source patch or new default recipe.
    # Monitor with a 256MB early-stop margin, so the 15GB floor is never the
    # intended stop threshold. The child group is this offline build only.
    argv=['make','-C',str(src),'O='+str(obj),'ARCH=arm64',
          'CROSS_COMPILE=aarch64-unknown-linux-gnu-',f'-j{jobs}','Image.gz']
    process=sp.Popen(argv,start_new_session=True)
    (out/'build.pid').write_text(str(process.pid)+'\n')
    import signal,time
    while process.poll() is None:
        if shutil.disk_usage('/').free<FLOOR+256_000_000:
            os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=30)
            raise RuntimeError('disk early-stop before 15GB floor; no images admitted')
        time.sleep(.2)
    if process.returncode: raise sp.CalledProcessError(process.returncode,argv)
    guard()
    packed_config=sp.check_output([str(src/'scripts/extract-ikconfig'),str(obj/'arch/arm64/boot/Image.gz')])
    if packed_config!=(obj/'.config').read_bytes(): raise ValueError('Image embedded config differs from resolved S1 config')
    release=(obj/'include/config/kernel.release').read_text()
    if release!=(old/'include/config/kernel.release').read_text(): raise ValueError('kernel release changed; reused modules not admitted')
    run('aarch64-unknown-linux-gnu-objcopy','--dump-section','.notes='+str(out/'kernel.notes'),
        '--only-section','.notes',obj/'vmlinux',out/'kernel-notes.elf')
    # Every built-in export must remain identical to D1. The config delta only
    # adds a module; imports of every reused D1 sidecar are checked separately.
    def symvers(p): return {f[1]:(f[0],f[2],tuple(f[3:])) for f in (line.split() for line in p.read_text().splitlines())}
    before=symvers(old/'Module.symvers'); builtin=symvers(obj/'vmlinux.symvers')
    expected_builtin={name:value for name,value in before.items() if value[1]=='vmlinux'}
    if builtin!=expected_builtin:
        raise ValueError('built-in export inventory/CRC changed against measured D1')
    shutil.copy2(old/'Module.symvers',obj/'Module.symvers')
    ext=out/'fastrpc-module'; (ext/'drivers/misc').mkdir(parents=True)
    shutil.copy2(src/'drivers/misc/fastrpc.c',ext/'drivers/misc/fastrpc.c')
    run('patch','--fuzz=0','-p1','-i',HERE/'s1-kernel-fastrpc-gate.patch',cwd=ext)
    run('git','apply','--unidiff-zero',HERE/'fastrpc-restore-context.patch',cwd=ext)
    shutil.copy2(ext/'drivers/misc/fastrpc.c',ext/'fastrpc.c')
    (ext/'Makefile').write_text('obj-m += fastrpc.o\n')
    make('M='+str(ext),'modules')
    reused={}
    modules=set((d1/'snapshot/d1/modules').glob('*.ko')) | set((d1/'snapshot/modules').glob('*.ko'))
    for module in sorted(modules):
        imports=sp.check_output(['modprobe','--show-modversions',str(module)],text=True)
        for line in imports.splitlines():
            crc,name=line.split()
            if name not in before or before[name][0].lower()!=crc.lower(): raise ValueError(('module import mismatch',module.name,name))
        reused[str(module.relative_to(d1/'snapshot'))]={'sha256':sha(module),'imports':imports}
    imports=sp.check_output(['modprobe','--show-modversions',str(ext/'fastrpc.ko')],text=True)
    for line in imports.splitlines():
        crc,name=line.split()
        if name not in before or before[name][0].lower()!=crc.lower(): raise ValueError(('FastRPC import mismatch',name))
    for p,digest in anchors.items():
        if sha(Path(p))!=digest: raise ValueError(('read-only D1 anchor changed',p))
    (out/'kernel-receipt.json').write_text(json.dumps({'status':'PASS-OFFLINE-KERNEL',
        'd1':str(d1),'source_anchors':anchors,'Image.gz':sha(obj/'arch/arm64/boot/Image.gz'),
        'config':sha(obj/'.config'),'dtb':sha(out/'s1.dtb'),'fastrpc.ko':sha(ext/'fastrpc.ko'),
        'fastrpc_source':sha(ext/'fastrpc.c'),'gate_patch':sha(HERE/'s1-kernel-fastrpc-gate.patch'),'restore_patch':sha(HERE/'fastrpc-restore-context.patch'),
        'kernel_notes':sha(out/'kernel.notes'),'kernel_release':release.strip(),
        'fastrpc_imports':imports,'reused_modules':reused,'free_bytes':guard()},indent=2)+'\n')
    print('S1 KERNEL PASS',out)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output',type=Path)
    ap.add_argument('--d1',type=Path,default=Path.home()/'A9-build/mainline/adsp/d1-20261017')
    ap.add_argument('--jobs',type=int,default=8)
    mode=ap.add_mutually_exclusive_group()
    mode.add_argument('--dt-only',action='store_true'); mode.add_argument('--module-only',action='store_true')
    ap.add_argument('--reuse-d1-objects',action='store_true',help='private copy of D1 object cache, never hardlinks')
    a=ap.parse_args()
    if a.module_only: module_only(a.d1,a.output,a.jobs)
    else: build(a.d1,a.output,a.jobs,a.dt_only,a.reuse_d1_objects)
if __name__=='__main__': main()
