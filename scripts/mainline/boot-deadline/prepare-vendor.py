#!/usr/bin/env python3
"""Add deadline trigger, logger transition/kmsg access and kernel digest.

Keeps input image immutable unless caller explicitly supplies the same path.
Uses existing matching platform policy inputs and the existing root diagnostic
domain with explicit enforcing permissions. No phone operations.
"""
from pathlib import Path
import gzip, hashlib, importlib.util, json, os, re, shutil, struct, subprocess as sp, sys

ROOT=Path('/home/user/A9-build/mainline')
HERE=Path(__file__).resolve().parent
BUNDLED_SECILC=HERE.parent/'tools/bin/secilc'
SECILC=Path(os.environ.get('A9_SECILC', str(BUNDLED_SECILC if BUNDLED_SECILC.is_file() else ROOT/'android/dl/cf_arm64_x/otatools/bin/secilc')))
RULES=b'''
; A9 asynchronous early logger: adopt existing diagnostic domain after policy load.
(allow kernel_202504 self (process (setcurrent)))
(allow kernel_202504 a9ml_rootsh (process (dyntransition)))
; Both private /kmsg descriptors are reopened after adopting SID_KERNEL.
; Do not grant use of the original SID_INIT/unlabeled early descriptors.
(allow a9ml_rootsh kernel_202504 (fd (use)))
(allow a9ml_rootsh tmpfs_202504 (chr_file (getattr open read write)))
; INTEG20 boot119 audited read on rootfs /a9-log-kmsg (deleted), not tmpfs.
(allow a9ml_rootsh rootfs_202504 (chr_file (getattr open read write)))
; Policy may load before the next iteration adopts the diagnostic domain.
(allow kernel_202504 tmpfs_202504 (chr_file (getattr open read write)))
(allow kernel_202504 rootfs_202504 (chr_file (getattr open read write)))
; Root-only stock-compatible TA log, read by the private asynchronous logger.
(type a9ml_qsee_log)
(roletype object_r a9ml_qsee_log)
(typeattributeset fs_type (a9ml_qsee_log))
(typeattributeset debugfs_type (a9ml_qsee_log))
(genfscon debugfs "/tzdbg" (u object_r a9ml_qsee_log ((s0) (s0))))
(allow a9ml_rootsh debugfs_202504 (dir (search)))
(allow a9ml_rootsh a9ml_qsee_log (dir (getattr search)))
(allow a9ml_rootsh a9ml_qsee_log (file (getattr open read)))
; Use existing vendor_init boot-control write permission for this exact node.
(genfscon sysfs "/module/a9_boot_deadline/parameters/boot_completed" (u object_r a9ml_sysfs_beacon ((s0) (s0))))
'''
RULES += (HERE.parent/'android/sepolicy/rootsh-enforcing.cil').read_bytes()

def prepare(boot,source,target,work):
    boot,source,target,work=map(Path,(boot,source,target,work))
    work.mkdir(parents=True,exist_ok=True)
    def read(path):return sp.check_output(['debugfs','-R','cat /'+path,str(source)],stderr=sp.DEVNULL)
    spec=importlib.util.spec_from_file_location('meta',HERE.parent/'android/refresh-integrated-vendor.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    b=boot.read_bytes();page=struct.unpack_from('<I',b,36)[0];size=struct.unpack_from('<I',b,8)[0]
    kh=hashlib.sha256(b[page:page+size]).hexdigest().encode()+b'\n'
    path='etc/init/hw/init.hlte556n.rc';rc=read(path)
    if b'/sys/module/a9_boot_deadline/parameters/boot_completed' not in rc:
        rc+=b'\n'+(HERE/'init.a9-boot-deadline.rc').read_bytes()
    changes={path:rc,'lib/modules/kernel-image.sha256':kh,'etc/thermal-a9.kernel-image.sha256':kh}
    original=read('etc/selinux/vendor_sepolicy.cil')
    additions=[line for line in RULES.splitlines() if line.startswith(b'(') and line not in original.splitlines()]
    if additions or b'(typepermissive a9ml_rootsh)' in original:
        original=original.replace(b'(typepermissive a9ml_rootsh)',b'')
        vendor=work/'vendor_sepolicy.cil';vendor.write_bytes(original+b'\n; A9 deadline logging additions\n'+b'\n'.join(additions)+b'\n')
        public=work/'plat_pub_versioned.cil';public.write_bytes(read('etc/selinux/plat_pub_versioned.cil'))
        # Public, matching GSI policy inputs travel with the source. Expand in
        # this run's private work directory; never mutate an older build cache.
        bundled=HERE/'policy-platform'
        manifest=json.loads((bundled/'inputs.json').read_text())
        cached=work/'policy-platform';cached.mkdir(exist_ok=True)
        files=[]
        for name,digest in manifest['files'].items():
            data=gzip.decompress((bundled/(name+'.gz')).read_bytes())
            assert hashlib.sha256(data).hexdigest()==digest, name
            target_input=cached/name;target_input.write_bytes(data);files.append(target_input)
        command=[SECILC,'-D','-m','-M','true','-G','-N','-c','30',*files,public,vendor,'-o',work/'precompiled_sepolicy','-f','/dev/null']
        with (work/'compile.log').open('w') as log:sp.run(list(map(str,command)),stdout=log,stderr=sp.STDOUT,check=True)
        (work/'compile-command.json').write_text(json.dumps(list(map(str,command)),indent=2)+'\n')
        changes['etc/selinux/vendor_sepolicy.cil']=vendor.read_bytes()
        changes['etc/selinux/precompiled_sepolicy']=(work/'precompiled_sepolicy').read_bytes()
    meta={p:m.metadata(source,p) for p in changes}
    if source.resolve()!=target.resolve():
        assert not target.exists()
        if shutil.disk_usage(target.parent).free < 2*1024**3+420*1024**2:raise RuntimeError('need 2GiB plus vendor copy budget')
        sp.run(['cp','--sparse=always',str(source),str(target)],check=True)
    batch=[]
    for i,(p,data) in enumerate(changes.items()):
        f=work/str(i);f.write_bytes(data);mt=meta[p]
        lab=work/(str(i)+'.label');lab.write_bytes(mt['label'].encode()+b'\0')
        batch += [f'rm /{p}',f'write {f} /{p}',f'sif /{p} mode 010{mt["mode"]}',f'sif /{p} uid {mt["uid"]}',f'sif /{p} gid {mt["gid"]}',f'ea_set -f {lab} /{p} security.selinux']
        for t in ['atime','mtime','ctime','crtime']:batch.append(f'sif /{p} {t} @{int(mt[t],16)}')
    script=work/'install.debugfs';script.write_text('\n'.join(batch)+'\n')
    with (work/'install.log').open('w') as log:sp.run(['debugfs','-w','-f',str(script),str(target)],stdout=log,stderr=sp.STDOUT,check=True)
    for p,data in changes.items():
        assert sp.check_output(['debugfs','-R','cat /'+p,str(target)],stderr=sp.DEVNULL)==data,p
        assert m.metadata(target,p)==meta[p],p
    with (work/'fsck.log').open('w') as log:sp.run(['e2fsck','-fn',str(target)],stdout=log,stderr=sp.STDOUT,check=True)
    with target.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    Path(str(target)+'.sha256').write_text(digest+'  '+target.name+'\n')
    (work/'result.json').write_text(json.dumps(dict(source=str(source),target=str(target),changed=list(changes),sha256=digest),indent=2)+'\n')

if __name__=='__main__':prepare(*sys.argv[1:])
