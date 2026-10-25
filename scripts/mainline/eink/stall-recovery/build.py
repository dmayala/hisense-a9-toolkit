#!/usr/bin/env python3
"""Host-only built-in PMIC + E Ink module rebuild; stops on every rc."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import struct

REPO=Path(__file__).resolve().parents[4]
BASE=Path('/home/user/A9-build/mainline/suspend-pmtest-kernel-20261020')

def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def run(cmd,log,env=None):
    with log.open('w') as f: sp.run(list(map(str,cmd)),stdout=f,stderr=sp.STDOUT,check=True,env=env)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--vendor',type=Path,required=True); ap.add_argument('--scratch',type=Path,required=True); ap.add_argument('--resume',action='store_true'); args=ap.parse_args()
    out=args.out.resolve(); out.mkdir(exist_ok=True,parents=True)
    if args.resume and (out/'vendor.img').exists(): raise RuntimeError('resume only an un-packaged failed build')
    if sp.check_output(['git','-C',str(REPO),'status','--porcelain'],text=True).strip():
        raise RuntimeError('commit/freeze the worktree before building')
    stage=out/'modules'; stage.mkdir(exist_ok=args.resume) # fresh output only; never mutate shared sources/objects
    tools=json.loads(Path('/home/user/A9-build/mainline/demo-integrated-20261019/source.json').read_text())['tools']
    env=dict(os.environ); env['PATH']=':'.join(tools)+':'+env['PATH']
    obj=BASE/'obj'; src=BASE/'kernel'
    before={str(p):sha(p) for p in (obj/'.config',obj/'Module.symvers',obj/'include/config/kernel.release')}
    scratch=args.scratch.resolve(); private_src=scratch/'kernel'; private_obj=scratch/'obj'
    command=json.loads((BASE/'kernel-command.json').read_text())
    command=[x.replace(str(src),str(private_src)).replace(str(obj),str(private_obj)) for x in command]
    if not args.resume:
        scratch.mkdir()
        for name,rel in [('fp9929a.c','drivers/regulator/fp9929a.c'),('fp9929a.h','include/linux/regulator/fp9929a.h')]:
            dst=out/'patch'/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src/rel,dst)
        # PMIC is BUILT-IN in this config. Build a new kernel in private RAM
        # scratch; do not attempt to replace it with a module or mutate shared obj.
        run(['cp','-as',src,private_src],out/'source-links.log')
        run(['cp','-a',obj,private_obj],out/'objects-copy.log')
        (private_obj/'source').unlink(); (private_obj/'source').symlink_to(private_src)
        (private_obj/'Makefile').write_text('export KBUILD_OUTPUT = '+str(private_obj)+'\ninclude '+str(private_src/'Makefile')+'\n')
        for cmd in private_obj.rglob('*.cmd'):
            data=cmd.read_text()
            updated=data.replace(str(src),str(private_src)).replace(str(obj),str(private_obj))
            if updated != data: cmd.write_text(updated)
        patch=REPO/'scripts/mainline/kernel/patches/0059-regulator-fp9929a-starvation-recovery.patch'
        run(['patch','--batch','--fuzz=0','-d',out/'patch','-p1','-i',patch],out/'patch.log')
        run(['python3',REPO/'scripts/mainline/eink/stall-recovery/test-recovery.py',out/'patch/drivers/regulator/fp9929a.c'],out/'recovery-tests.log',env)
        msm_files=('drivers/gpu/drm/msm/msm_atomic.c','drivers/gpu/drm/msm/msm_kms.h',
                   'drivers/gpu/drm/msm/disp/dpu1/dpu_kms.c')
        for rel in msm_files:
            target=private_src/rel; data=target.read_bytes(); target.unlink(); target.write_bytes(data)
        run(['patch','--batch','--fuzz=0','-d',private_src,'-p1','-i',
             REPO/'scripts/mainline/kernel/patches/0060-drm-msm-latch-DPU-completion-errors.patch'],out/'msm-patch.log')
        run(['python3',REPO/'scripts/mainline/eink/stall-recovery/test-msm-latch.py',private_src],out/'msm-latch-tests.log',env)
        for rel in ('drivers/regulator/fp9929a.c','include/linux/regulator/fp9929a.h'):
            dst=private_src/rel; dst.unlink(); shutil.copyfile(out/'patch'/rel,dst)
        assert 'CONFIG_REGULATOR_FP9929A=y' in (obj/'.config').read_text()
        assert 'CONFIG_DRM_A9_EINK=m' in (obj/'.config').read_text()
        run(command,out/'kernel-build.log',env)
    else:
        assert (private_obj/'vmlinux.symvers').is_file()
        assert 'msm_a9_commit_faulted' in (private_src/'drivers/gpu/drm/msm/msm_atomic.c').read_text()
    # New exports may be added; CRCs of existing built-in APIs must match.
    def exports(path):
        return {row[1]: (row[0],*row[3:]) for line in path.read_text().splitlines()
                if len(row:=line.split())>=4 and row[2]=='vmlinux'}
    old=exports(obj/'Module.symvers'); new=exports(private_obj/'vmlinux.symvers')
    assert old and all(new.get(k)==v for k,v in old.items()),'existing built-in ABI changed'
    # Regenerate the full table from vmlinux.o + modules.order. Replacing an
    # existing table with built-in-only exports can fool incremental modpost
    # into considering that incomplete table up to date on a resumed build.
    (private_obj/'Module.symvers').unlink(missing_ok=True)
    run([*command[:-1], 'modules'],out/'msm-build.log',env)
    def module_exports(path):
        return {row[1]:(row[0],*row[3:]) for line in path.read_text().splitlines()
                if len(row:=line.split())>=4 and row[2].endswith('/msm')}
    old_msm=module_exports(obj/'Module.symvers'); new_msm=module_exports(private_obj/'Module.symvers')
    assert all(new_msm.get(k)==v for k,v in old_msm.items()),'existing MSM export ABI changed'
    assert new_msm.keys()-old_msm.keys()=={'msm_a9_commit_faulted'},'unexpected new MSM exports'
    # Merge fresh built-in + MSM exports for the dependent E Ink module.
    rows={}
    for path in (obj/'Module.symvers',private_obj/'vmlinux.symvers',private_obj/'Module.symvers'):
        for line in path.read_text().splitlines():
            row=line.split()
            if len(row)>=4: rows[row[1]]=line
    assert 'msm_a9_commit_faulted' in rows, 'new MSM health export absent'
    (private_obj/'Module.symvers').write_text('\n'.join(rows.values())+'\n')
    shutil.copyfile(private_obj/'drivers/gpu/drm/msm/msm.ko',out/'msm.ko')
    assert (obj/'.config').read_bytes()==(private_obj/'.config').read_bytes()
    assert (obj/'include/config/kernel.release').read_bytes()==(private_obj/'include/config/kernel.release').read_bytes()
    image=out/'Image.gz'; shutil.copyfile(private_obj/'arch/arm64/boot/Image.gz',image)
    boot_base=Path('/home/user/A9-build/mainline/integrated-20261020m/boot.img')
    assert sha(boot_base)=='8544e0fcc80fe7d4023bb88c2e7a646869e8735b1e0cb14a12987034506817ce'
    data=boot_base.read_bytes(); page=struct.unpack_from('<I',data,36)[0]
    assert page==4096 and struct.unpack_from('<I',data,40)[0]==2
    pad=lambda n: (n+page-1)//page*page
    offsets=(8,16,24,1632,1648); pos=page; blobs=[]
    for off in offsets:
        size=struct.unpack_from('<I',data,off)[0]; blobs.append(data[pos:pos+size]); pos+=pad(size)
    preserved=[sha(boot_base),*[hashlib.sha256(b).hexdigest() for b in blobs[1:]]]
    blobs[0]=image.read_bytes(); header=bytearray(data[:page]); digest=hashlib.sha1()
    for off,blob in zip(offsets,blobs):
        struct.pack_into('<I',header,off,len(blob)); digest.update(blob); digest.update(struct.pack('<I',len(blob)))
    struct.pack_into('<Q',header,1636,page+sum(pad(len(b)) for b in blobs[:3]))
    header[576:608]=digest.digest()+b'\0'*12
    (out/'boot.img').write_bytes(header+b''.join(b.ljust(pad(len(b)),b'\0') for b in blobs))
    (out/'boot-preservation.json').write_text(json.dumps({'base_boot_and_non_kernel_blobs':preserved,'cmdline_unchanged':header[64:576]==data[64:576] and header[608:1632]==data[608:1632]},indent=2)+'\n')
    shutil.copyfile(out/'patch/include/linux/regulator/fp9929a.h',stage/'fp9929a-recovery.h')
    shutil.copytree(REPO/'scripts/mainline/eink/driver',stage/'eink')
    for f in ('a9tcon.c','a9tcon.h'): shutil.copyfile(REPO/'scripts/mainline/tcon'/f,stage/'eink'/f)
    # Explicit header selection without touching the shared kernel include tree.
    for f in (stage/'eink/a9_eink_drv.c',stage/'eink/a9_eink_transport.c'):
        f.write_text(f.read_text().replace('<linux/regulator/fp9929a.h>','"fp9929a-recovery.h"'))
    (stage/'Makefile').write_text('ccflags-y += -I$(src) -I$(src)/eink -I$(src)/eink/kcompat\nobj-m += a9_eink.o\na9_eink-y := '+ ' '.join('eink/'+n+'.o' for n in ('a9_eink_drv','a9_eink_transport','a9_eink_guard','a9_eink_ring','a9_eink_engine','a9_eink_damage','a9tcon'))+'\n')
    run(['make','-C',private_src,'O='+str(private_obj),'ARCH=arm64','CROSS_COMPILE=aarch64-unknown-linux-gnu-','HOSTCC=gcc','LOCALVERSION=+','M='+str(stage),'-j4','modules'],out/'modules-build.log',env)
    assert all(sha(p)==h for p,h in before.items()),'shared config/export/release changed'
    # Build rc and nonempty artifacts are mandatory before copying the vendor.
    for name in ('a9_eink.ko',): assert (stage/name).stat().st_size>0
    free=shutil.disk_usage(out).free
    # No unrelated deletion or new full kernel build. This narrow derivative is <1 GiB.
    if free < args.vendor.stat().st_size+512*1024**2: raise RuntimeError('insufficient derivative image budget')
    vendor=out/'vendor.img'; shutil.copyfile(args.vendor,vendor)
    label=out/'label'; label.write_bytes(b'u:object_r:vendor_file:s0\0')
    replacements={'lib/modules/a9_eink.ko':stage/'a9_eink.ko','lib/modules/msm.ko':out/'msm.ko','bin/a9ml-eink.sh':REPO/'scripts/mainline/android/vendor/bin/a9ml-eink.sh'}
    kernel_hash=out/'kernel-image.sha256'; kernel_hash.write_text(sha(image)+'\n')
    replacements['lib/modules/kernel-image.sha256']=kernel_hash
    replacements['etc/thermal-a9.kernel-image.sha256']=kernel_hash
    lines=[]
    for dest,source in replacements.items():
        lines += ['rm /'+dest,'write '+str(source)+' /'+dest,'set_inode_field /'+dest+' mode 0100755','ea_set -f '+str(label)+' /'+dest+' security.selinux']
    batch=out/'vendor-debugfs.txt'; batch.write_text('\n'.join(lines)+'\n')
    run(['debugfs','-w','-f',batch,vendor],out/'vendor-install.log')
    for i,(dest,source) in enumerate(replacements.items()):
        readback=out/('readback-'+str(i)); run(['debugfs','-R','dump /'+dest+' '+str(readback),vendor],out/('readback-'+str(i)+'.log'))
        assert sha(readback)==sha(source),dest+' readback mismatch'
    run(['e2fsck','-fn',vendor],out/'vendor-fsck.log')
    for name in ('glass-proof.sh',): shutil.copyfile(REPO/'scripts/mainline/android/sepolicy'/name,out/name)
    shutil.copyfile(REPO/'scripts/mainline/eink/stall-recovery/dev-reopen.sh',out/'dev-reopen.sh')
    result={'source_commit':sp.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
      'base_vendor':str(args.vendor),'base_vendor_sha256':sha(args.vendor),'kernel_base':str(BASE),
      'boot_base':str(boot_base),'kernel_rebuilt':True,'existing_msm_exports_unchanged':len(old_msm),'new_exports':sorted(new.keys()-old.keys()),
      'sha256':{str(p.relative_to(out)):sha(p) for p in (vendor,out/'boot.img',image,out/'msm.ko',stage/'a9_eink.ko',out/'glass-proof.sh',out/'dev-reopen.sh')},
      'replacements':{d:sha(s) for d,s in replacements.items()},'shared_inputs_unchanged':before}
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in result['sha256'].items()))
    for name in ('boot.img','vendor.img'):
        (out/(name+'.sha256')).write_text(sha(out/name)+'  '+str(out/name)+'\n')
    shutil.copyfile(Path('/home/user/A9-build/mainline/integrated-20261020m/snapshot/recovery_dtbo.img'),out/'recovery_dtbo.img')
    with (out/'SHA256SUMS').open('a') as f: f.write(sha(out/'recovery_dtbo.img')+'  recovery_dtbo.img\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__': main()
