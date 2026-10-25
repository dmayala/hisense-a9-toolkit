#!/usr/bin/env python3
"""Host-only INTEG-O: N-e + proven USB/FastRPC/battery + plain ROW29.

Run prepare, kernel, modules, package, storage, verify in order. No phone tools.
The archived N build keeps its regulator/MSM fixes and exact noVT config.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess as sp

R = Path(__file__).resolve().parents[2]
ROOT = Path('/home/user/A9-build/mainline')
O = ROOT/'integrated-20261021o'
N = ROOT/'integrated-20261021n'
BASE = ROOT/'integrated-20261021n-e'
SEED = ROOT/'suspend-pmtest-kernel-20261020'
ARCHIVE = Path('/home/user/a9-assets/mainline-archive/integrated-20261021n-kernel-scratch.tar')
K = O/'scratch/kernel'
J = O/'scratch/obj'
ENV = dict(os.environ, PATH=':'.join(json.loads((ROOT/'demo-integrated-20261019/source.json').read_text())['tools'])+':'+os.environ['PATH'])

def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def room(budget=0):
    free = shutil.disk_usage(O).free
    if free < budget+2*1024**3:
        raise RuntimeError('preserve 2GiB reserve: '+str(free))

def run(args, name):
    room()
    with (O/(name+'.log')).open('w') as f:
        p = sp.run(list(map(str, args)), env=ENV, stdout=f, stderr=sp.STDOUT)
    (O/(name+'.rc')).write_text(str(p.returncode)+'\n')
    if p.returncode:
        raise RuntimeError(name+' rc='+str(p.returncode))

def make(*targets):
    cmd = json.loads((SEED/'kernel-command.json').read_text())[:-1]
    return [s.replace(str(SEED/'kernel'), str(K)).replace(str(SEED/'obj'), str(J)) for s in cmd]+list(targets)

def save(name, value):
    (O/name).write_text(json.dumps(value, indent=2)+'\n')

def debug(image, command):
    return sp.check_output(['debugfs', '-R', command, str(image)], stderr=sp.DEVNULL)

def prepare():
    O.mkdir(exist_ok=True)
    room(0 if (O/'scratch').exists() else 2*1024**3)
    assert sha(BASE/'boot.img') == 'b4936f2f73c35a4cbd49cdb7966aa840a7259d928e504d470df567947197e017'
    assert sha(BASE/'vendor.img') == '3640432d0fb384c9762c1546348f20677565dd1020994d10728391789efcbbb8'
    assert sha(ARCHIVE) == '8aa2d4a513f4198eb7d883009a453d9af90fce2eee938558526e7c2b68360538'
    if not (O/'scratch').exists():
        run(['tar', '-xf', ARCHIVE, '-C', O], 'restore-n-scratch')
    assert sha(J/'.config') == sha(N/'kernel.config')
    assert sha(J/'arch/arm64/boot/Image.gz') == sha(N/'Image.gz')
    assert '# CONFIG_VT_CONSOLE is not set' in (J/'.config').read_text()
    old = str(N/'scratch')
    for p in J.rglob('*.cmd'):
        text = p.read_text()
        if old in text:
            p.write_text(text.replace(old, str(O/'scratch')))
    (J/'source').unlink()
    (J/'source').symlink_to(K)
    (J/'Makefile').write_text('export KBUILD_OUTPUT = '+str(J)+'\ninclude '+str(K/'Makefile')+'\n')
    source = K/'drivers/usb/dwc3/dwc3-qcom-legacy.c'
    assert sha(source)==sha(SEED/'kernel/drivers/usb/dwc3/dwc3-qcom-legacy.c')
    if source.is_symlink():
        data = source.read_bytes()
        source.unlink()
        source.write_bytes(data)
    run(['patch','--batch','--fuzz=0','-d',K,'-p1','-i', R/'scripts/mainline/suspend/devices/dwc3-sm6115-restore-qscratch.patch'], 'qscratch-patch')
    assert sha(source) == sha(ROOT/'suspend-novtcon-20261021/usb-r20/dwc3-qcom-legacy.c')
    save('inputs.json', dict(base_boot_sha256=sha(BASE/'boot.img'), base_vendor_sha256=sha(BASE/'vendor.img'), archive_sha256=sha(ARCHIVE), source_commit=sp.check_output(['git','-C',R,'rev-parse','HEAD'],text=True).strip(), config_sha256=sha(J/'.config'), qscratch_source_sha256=sha(source)))

def exports(path):
    return {r[1]:(r[0],*r[3:]) for l in path.read_text().splitlines() if len(r:=l.split())>=4}

def kernel():
    run(make('Image.gz'), 'kernel-build')
    assert sha(J/'.config') == sha(N/'kernel.config')
    assert exports(J/'vmlinux.symvers') == exports(N/'vmlinux.symvers')
    # Keep all external providers, replacing builtin rows with this build.
    rows = [l for l in (N/'Module.symvers').read_text().splitlines() if l.split()[2]!='vmlinux']
    (J/'Module.symvers').write_text((J/'vmlinux.symvers').read_text()+'\n'.join(rows)+'\n')
    for src,dst in [('arch/arm64/boot/Image.gz','Image.gz'),('.config','kernel.config'),('Module.symvers','Module.symvers'),('vmlinux.symvers','vmlinux.symvers')]:
        shutil.copyfile(J/src,O/dst)
    save('kernel-result.json',dict(config_unchanged=True,builtin_exports_unchanged=len(exports(J/'vmlinux.symvers')),kernel_sha256=sha(O/'Image.gz'),kernel_release=(J/'include/config/kernel.release').read_text().strip()))

def modules():
    m = O/'fastrpc-module'
    (m/'drivers/misc').mkdir(parents=True,exist_ok=True)
    shutil.copyfile(SEED/'kernel/drivers/misc/fastrpc.c',m/'drivers/misc/fastrpc.c')
    # The retained N seed already contains the S1 gate/drain patch.
    assert sha(m/'drivers/misc/fastrpc.c') == 'f6b63cace02af489df6e0597ea90a3c78a3b2164e599a615dadd921b24b219d0'
    run(['patch','--batch','--fuzz=0','-d',m,'-p1','-i',R/'scripts/mainline/sensors/fastrpc-restore-context.patch'],'fastrpc-restore')
    assert sha(m/'drivers/misc/fastrpc.c') == 'dafab838d60439d1c2807620359be412588bfabc7c340c1c1874783f02045e24'
    shutil.copyfile(m/'drivers/misc/fastrpc.c',m/'fastrpc.c')
    (m/'Makefile').write_text('obj-m += fastrpc.o\n')
    run(make('M='+str(m),'W=1','modules'),'fastrpc-build')
    b = O/'battery-module'
    b.mkdir(exist_ok=True)
    for name in ('Makefile','pmi632-battery.c'):
        shutil.copyfile(R/'scripts/mainline/pmic/battery'/name,b/name)
    for header in (R/'scripts/mainline/pmic/battery').glob('*.h'):
        shutil.copyfile(header,b/header.name)
    shutil.copyfile(R/'scripts/mainline/pmic/pmi632-policy.h',b/'pmi632-policy.h')
    run(make('M='+str(b),'W=1','modules'),'battery-build')
    for module,name in [(m,'fastrpc.ko'),(b,'pmi632-battery.ko')]:
        shutil.copyfile(module/name,O/name)
        run(['aarch64-unknown-linux-gnu-strip','--strip-debug',O/name],'strip-'+name)

def package():
    room(900*1024**2)
    old = (BASE/'boot.img').read_bytes()
    page = struct.unpack_from('<I',old,36)[0]
    assert page==4096 and struct.unpack_from('<I',old,40)[0]==2
    pad = lambda n:(n+page-1)//page*page
    offsets = (8,16,24,1632,1648)
    pos=page; blobs=[]
    for offset in offsets:
        n=struct.unpack_from('<I',old,offset)[0];blobs.append(old[pos:pos+n]);pos+=pad(n)
    original=list(blobs);blobs[0]=(O/'Image.gz').read_bytes()
    h=bytearray(old[:page]);struct.pack_into('<I',h,8,len(blobs[0]))
    struct.pack_into('<Q',h,1636,page+sum(pad(len(b)) for b in blobs[:3]))
    digest=hashlib.sha1()
    for blob in blobs:
        digest.update(blob);digest.update(struct.pack('<I',len(blob)))
    h[576:608]=digest.digest()+b'\0'*12
    assert h[64:576]==old[64:576] and h[608:1632]==old[608:1632]
    allowed=set(range(8,12))|set(range(576,608))|set(range(1636,1644))
    assert all(a==b or i in allowed for i,(a,b) in enumerate(zip(old[:page],h)))
    (O/'boot.img').write_bytes(h+b''.join(b.ljust(pad(len(b)),b'\0') for b in blobs))
    (O/'recovery_dtbo.img').write_bytes(blobs[3])
    primary=h[64:576].split(b'\0')[0].decode()
    assert all(t in primary.split() for t in ('enforcing=1','androidboot.a9audit=1','cpuidle.off=1'))
    save('payload-check.json',dict(primary_cmdline_identical=True,extra_cmdline_identical=True,nonkernel_payloads_identical=original[1:]==blobs[1:],header_only_changed=['kernel_size','image_id','recovery_dtbo_offset'],primary_cmdline=primary))
    image=O/'modules-vendor.img'
    run(['cp','--reflink=auto','--sparse=always',BASE/'vendor.img',image],'copy-vendor')
    spec=importlib.util.spec_from_file_location('refresh',R/'scripts/mainline/android/refresh-integrated-vendor.py')
    refresh=importlib.util.module_from_spec(spec);spec.loader.exec_module(refresh)
    (O/'kernel-image.sha256').write_text(sha(O/'Image.gz')+'\n')
    changed={'lib/modules/fastrpc.ko':O/'fastrpc.ko','lib/modules/pmi632-battery.ko':O/'pmi632-battery.ko','lib/modules/kernel-image.sha256':O/'kernel-image.sha256','etc/thermal-a9.kernel-image.sha256':O/'kernel-image.sha256'}
    if b'Inode:' in debug(image,'stat /lib/modules/SHA256SUMS'):
        manifest=debug(image,'cat /lib/modules/SHA256SUMS').decode()
        for path,src in changed.items():
            if path.startswith('lib/modules/'):
                name=path.removeprefix('lib/modules/')
                manifest,count=re.subn(r'(?m)^[0-9a-f]{64}(\s+\*?(?:\./)?'+re.escape(name)+r')$',lambda m:sha(src)+m[1],manifest)
                if name.endswith('.ko'):assert count==1,(name,count)
        (O/'module-SHA256SUMS').write_text(manifest)
        changed['lib/modules/SHA256SUMS']=O/'module-SHA256SUMS'
    batch=[];metadata={}
    for i,(path,src) in enumerate(changed.items()):
        meta=refresh.metadata(image,path);metadata[path]=meta
        label=O/('module-label-'+str(i));label.write_bytes(meta['label'].encode()+b'\0')
        batch += ['rm /'+path,f'write {src} /{path}',f'sif /{path} mode 010{meta["mode"]}',f'sif /{path} uid {meta["uid"]}',f'sif /{path} gid {meta["gid"]}',f'ea_set -f {label} /{path} security.selinux']
        for field in ('atime','mtime','ctime','crtime'):
            batch.append(f'sif /{path} {field} @{int(meta[field],16)}')
    (O/'modules.debugfs').write_text('\n'.join(batch)+'\n')
    run(['debugfs','-w','-f',O/'modules.debugfs',image],'install-modules')
    for path,src in changed.items():
        q=O/'module-readback';debug(image,f'dump /{path} {q}')
        assert sha(q)==sha(src) and refresh.metadata(image,path)==metadata[path],path
    save('module-overlay.json',{p:sha(s) for p,s in changed.items()})

def storage():
    run(['python3','-I',R/'scripts/mainline/storage-row29/data/integrate.py','--base-vendor',O/'modules-vendor.img','--out',O/'storage','--data-mode','row29','--kernel-config',O/'kernel.config'],'storage')
    shutil.move(O/'storage/vendor.img',O/'vendor.img')

def verify():
    image=O/'vendor.img'
    run(['e2fsck','-fn',image],'vendor-fsck')
    extracted=O/'vendor-extract';extracted.mkdir(exist_ok=True)
    run(['debugfs','-R','rdump /lib/modules '+str(extracted),image],'extract-modules')
    known={s:int(v[0],16) for s,v in exports(O/'vmlinux.symvers').items()}
    # Resolve module-to-module imports from the ACTUAL shipped providers,
    # including audio/video updates newer than N's original SDK inventory.
    providers={}
    for module in sorted((extracted/'modules').glob('*.ko')):
        result=sp.run(['modprobe','--show-exports',module],env=ENV,text=True,capture_output=True)
        if result.returncode:
            assert not result.stdout and 'could not get symbols' in result.stderr,(module.name,result.stderr)
            continue
        for line in result.stdout.splitlines():
            crc,name=line.split();crc=int(crc,16)
            assert name not in known or known[name]==crc,(module.name,name)
            known[name]=crc;providers[name]=module.name
    save('module-export-providers.json',providers)
    audit={}
    baseaudit=json.loads((N/'module-abi-audit.json').read_text())
    for module in sorted((extracted/'modules').glob('*.ko')):
        imports={r[1]:int(r[0],16) for l in sp.check_output(['modprobe','--show-modversions',module],env=ENV,text=True).splitlines() if len(r:=l.split())==2}
        bad={s:[v,known[s]] for s,v in imports.items() if s in known and v!=known[s]}
        assert not bad,(module.name,bad)
        unknown=sorted(imports.keys()-known.keys())
        assert not set(imports)&{'pm_vt_switch_required','pm_vt_switch_unregister'}
        assert not unknown,(module.name,unknown)
        vm=sp.check_output(['modinfo','-F','vermagic',module],env=ENV,text=True).strip()
        assert vm==baseaudit[module.name]['vermagic'],(module.name,vm)
        audit[module.name]=dict(sha256=sha(module),imports=len(imports),checked=len(imports)-len(unknown),inherited_external_symbols=unknown,vermagic=vm)
    assert len(audit)==len(baseaudit),(len(audit),len(baseaudit))
    save('module-abi-audit.json',audit)
    # Final N-e E Ink pair and all other inherited module bytes must survive.
    retained={}
    for path in ('bin/a9ml-eink.sh','lib/modules/a9_eink.ko','etc/init/init.a9ml-debug.rc','lib/modules/modules.dep','lib/modules/modules.options','lib/modules/modules.d1'):
        a=O/'retained-base';b=O/'retained-final';debug(BASE/'vendor.img',f'dump /{path} {a}');debug(image,f'dump /{path} {b}')
        assert sha(a)==sha(b),path
        retained[path]=sha(b)
    for module in (extracted/'modules').glob('*.ko'):
        if module.name in ('fastrpc.ko','pmi632-battery.ko'):continue
        q=O/'retained-module';debug(BASE/'vendor.img',f'dump /lib/modules/{module.name} {q}')
        assert sha(q)==sha(module),module.name
    rows=debug(image,'cat /lib/modules/SHA256SUMS').decode().splitlines()
    for row in rows:
        digest,name=row.split();p=extracted/'modules'/name.lstrip('*').removeprefix('./')
        assert sha(p)==digest,name
    (O/'vendor-modules.SHA256SUMS').write_text(''.join(v['sha256']+'  '+k+'\n' for k,v in audit.items()))
    # Storage policy/mapper exactly match the phone-proven R2 package.
    r2=ROOT/'storage-row29-data-r2-20261021/vendor.img'
    storage_equal={}
    for path in ('bin/a9ml-row29-map','etc/selinux/vendor_sepolicy.cil','etc/selinux/precompiled_sepolicy','etc/selinux/vendor_file_contexts'):
        a=O/'row29-proven';b=O/'row29-final';debug(r2,f'dump /{path} {a}');debug(image,f'dump /{path} {b}')
        if path.endswith('vendor_file_contexts'):
            rules=lambda p:[l.split() for l in p.read_text().splitlines() if l.strip() and not l.lstrip().startswith('#')]
            assert rules(a)==rules(b),path
        else:assert sha(a)==sha(b),path
        storage_equal[path]=sha(b)
    rc=debug(image,'cat /etc/init/hw/init.hlte556n.rc').decode()
    assert rc.index('exec_start a9ml_row29_map')<rc.index('mount_all /vendor/etc/fstab.hlte556n --late')
    assert 'write /proc/sys/kernel/printk 1' in debug(image,'cat /etc/init/init.a9ml-debug.rc').decode()
    for target in ('boot','recovery'):
        cmd=['bash',R/'scripts/abl-check.sh',O/'boot.img',target,'--json','--save-dtb',O/('abl-'+target+'.dtb')]
        if target=='boot':cmd+=['--dtbo',O/'recovery_dtbo.img']
        with (O/('abl-'+target+'.json')).open('w') as out,(O/('abl-'+target+'.err')).open('w') as err:
            p=sp.run(list(map(str,cmd)),stdout=out,stderr=err,env=ENV)
        (O/('abl-'+target+'.rc')).write_text(str(p.returncode)+'\n')
        assert p.returncode==0,target
        assert all(s['pass'] for s in json.loads((O/('abl-'+target+'.json')).read_text())['stages'].values())
    save('validation.json',dict(module_count=len(audit),retained=retained,storage_matches_R2=storage_equal,all_other_modules_identical=True,vendor_module_manifest_entries=len(rows),host_module_manifest='vendor-modules.SHA256SUMS',ABL_boot='PASS',ABL_recovery='PASS',phone_access=False,free_bytes=shutil.disk_usage(O).free))
    for name in ('boot.img','vendor.img','recovery_dtbo.img'):
        (O/(name+'.sha256')).write_text(sha(O/name)+'  '+str(O/name)+'\n')
    (O/'SHA256SUMS').write_text(''.join(sha(O/n)+'  '+n+'\n' for n in ('boot.img','vendor.img','recovery_dtbo.img')))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=('prepare','kernel','modules','package','storage','verify'))
    a=p.parse_args()
    os.umask(0o077)
    globals()[a.phase]()
