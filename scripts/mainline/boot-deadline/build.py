#!/usr/bin/env python3
"""Host-only incremental build on the worker-owned proven plain-integ cache.

Produces boot.img, boot-hang.img (60s, PID1 deliberately held), vendor.img.
No kernel UMH, synchronous early storage hook, finite hardware-WDT lease or ACK.
"""
from pathlib import Path
import gzip, hashlib, importlib.util, json, os, shutil, struct, subprocess as sp

HERE=Path(__file__).resolve().parent
ROOT=Path('/home/user/A9-build/mainline')
OUT=ROOT/'plain-deadline-20261022'
BASE=ROOT/'plain-integ-20261022'
CACHE=ROOT/'boot-reliability-20261022/cache/scratch'
K=CACHE/'kernel'; OBJ=CACHE/'obj'
REPO=Path('/home/user/A9')
ENV=dict(os.environ,PATH='/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin:'+':'.join(json.loads((ROOT/'demo-integrated-20261019/source.json').read_text())['tools'])+':'+os.environ['PATH'])

def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def room(budget=0):
    free=shutil.disk_usage(ROOT).free
    if free<2*1024**3+budget:raise RuntimeError(f'need 2GiB floor plus {budget}, have {free}')

def run(cmd,name):
    print(name,flush=True)
    with (OUT/(name+'.log')).open('w') as log:
        rc=sp.call(list(map(str,cmd)),env=ENV,stdout=log,stderr=sp.STDOUT)
    (OUT/(name+'.rc')).write_text(str(rc)+'\n')
    if rc:raise RuntimeError(f'{name}: {rc}')

def materialize(p,data):
    if p.is_symlink():p.unlink()
    p.write_bytes(data)

def kernel():
    room(160*1024**2)
    assert sha(OBJ/'arch/arm64/boot/Image.gz')==sha(BASE/'Image.gz'), 'cache changed; do not overwrite another worker'
    assert (OBJ/'.config').read_bytes()==(BASE/'kernel.config').read_bytes()
    assert sha(K/'drivers/watchdog/qcom-wdt.c')==sha(BASE/'qcom-wdt.c')
    assert 'a9_bootlog_prepare' not in (K/'init/main.c').read_text()
    materialize(K/'init/a9_boot_deadline.c',(HERE/'a9_boot_deadline.c').read_bytes())
    p=K/'init/Makefile';text=p.read_text()
    assert 'a9_boot_deadline' not in text
    materialize(p,(text+'\nobj-y += a9_boot_deadline.o\n').encode())
    p=K/'drivers/firmware/qcom/qcom_scm.c';text=p.read_text();before=text
    anchor='\tdownload_mode = ret;\n'
    assert text.count(anchor)==1
    text=text.replace(anchor,'\t/* A9 test boots must never route a panic/reset into EDL. */\n\tif (ret && of_machine_is_compatible("hisense,hlte556n"))\n\t\treturn -EPERM;\n\n'+anchor)
    anchor='\tqcom_scm_set_download_mode(download_mode);\n'
    # The unindented probe call, not the nested setter call.
    i=text.index(anchor,text.index('static int qcom_scm_probe('))
    text=text[:i]+'\tif (of_machine_is_compatible("hisense,hlte556n"))\n\t\tdownload_mode = QCOM_DLOAD_NODUMP;\n'+text[i:]
    materialize(p,text.encode())
    (OUT/'qcom_scm.before.c').write_text(before)
    shutil.copyfile(p,OUT/'qcom_scm.c')
    run(['make','-C',K,'O='+str(OBJ),'ARCH=arm64','CROSS_COMPILE=aarch64-unknown-linux-gnu-',
         'HOSTCC=gcc','HOSTCFLAGS=-I/nix/store/4rw03gwh97laxj6sgf630xdbnynkj43z-openssl-3.5.1-dev/include',
         'HOSTLDFLAGS=-L/nix/store/j09xkbypljdhxwspfiqd8sf64zgxhp52-openssl-3.5.1/lib',
         'LOCALVERSION=+','DTC_FLAGS=-@','-j4','Image.gz'],'kernel-build')
    for a,b in [('arch/arm64/boot/Image.gz','Image.gz'),('.config','kernel.config'),('vmlinux.symvers','vmlinux.symvers')]:
        shutil.copyfile(OBJ/a,OUT/b)
    assert (OUT/'kernel.config').read_bytes()==(BASE/'kernel.config').read_bytes()
    def exports(p):return {r[1]:r[0] for l in p.read_text().splitlines() if len(r:=l.split())>=4}
    old,new=exports(BASE/'vmlinux.symvers'),exports(OUT/'vmlinux.symvers')
    bad={n:[v,new.get(n)] for n,v in old.items() if new.get(n)!=v}
    assert not bad,bad
    (OUT/'abi.json').write_text(json.dumps(dict(existing_exports=len(old),mismatches=bad))+'\n')

def records(raw):
    p=0
    while True:
        assert raw[p:p+6] in (b'070701',b'070702')
        fields=[int(raw[p+6+i*8:p+14+i*8],16) for i in range(13)]
        name=raw[p+110:p+110+fields[11]-1].decode()
        q=(p+110+fields[11]+3)//4*4
        data=raw[q:q+fields[6]];p=(q+fields[6]+3)//4*4
        if name=='TRAILER!!!':break
        yield name,fields,data

def cpio(raw,shim):
    items=list(records(raw));ino=max(f[0] for n,f,d in items)+1
    added=[]
    for i,(n,f,d) in enumerate(items):
        if n=='init':
            assert f[1]&0o170000==0o100000
            items[i]=(n,f,shim)
            added.append(('init.android',f.copy(),d))
    assert len(added)==1
    added.append(('a9-mainline-logger',[ino,0o100700,0,0,1,0,0,0,0,0,0,0,0],(OUT/'logger').read_bytes()))
    added.append(('TRAILER!!!',[ino+1,0,0,0,1,0,0,0,0,0,0,0,0],b''))
    buf=bytearray()
    for n,f,d in items+added:
        f=f.copy();f[6]=len(d);f[11]=len(n)+1
        buf+=b'070701'+b''.join(f'{v:08x}'.encode() for v in f)+n.encode()+b'\0'
        buf+=bytes(-len(buf)%4);buf+=d;buf+=bytes(-len(buf)%4)
    buf+=bytes(-len(buf)%512)
    return bytes(buf)

def package_boot():
    for source,dest,flags in [('logger.c','logger',[]),('ramdisk-init.c','init-shim',[]),('ramdisk-init.c','init-hang',['-DA9_DELIBERATE_HANG'])]:
        run(['/nix/store/ik128k4xm49sbm27i1nzxlr06aflixma-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc','-static','-O2','-Wall','-Wextra','-Wno-unused-parameter',*flags,HERE/source,'-o',OUT/dest],dest+'-build')
    raw=(BASE/'boot.img').read_bytes();page=struct.unpack_from('<I',raw,36)[0];assert page==4096
    pos=page;fields=(8,16,24,1632,1648);original=[]
    for f in fields:
        n=struct.unpack_from('<I',raw,f)[0];original.append(raw[pos:pos+n]);pos+=(n+page-1)//page*page
    cmd=(raw[64:576].split(b'\0')[0]+b' '+raw[608:1632].split(b'\0')[0]).decode().split()
    cmd=[x for x in cmd if not x.startswith(('panic=','a9_deadline=','qcom_scm.download_mode=','pstore.kmsg_bytes='))]
    for name,seconds,shim in [('boot.img',300,'init-shim'),('boot-hang.img',60,'init-hang')]:
        blobs=original.copy();blobs[0]=(OUT/'Image.gz').read_bytes()
        blobs[1]=gzip.compress(cpio(gzip.decompress(blobs[1]),(OUT/shim).read_bytes()),mtime=0)
        header=bytearray(raw[:page]);body=bytearray();digest=hashlib.sha1()
        # This ABL has paths that ignore extra_cmdline. Keep deadline, panic,
        # routing and wrapped-key opt-in in the functional primary field.
        critical=[f'a9_deadline={seconds}','panic=5','qcom_scm.download_mode=0','pstore.kmsg_bytes=262144']
        critical += [x for x in cmd if x.startswith(('qcom_pon.beacon=','qcom_ice.use_wrapped_keys='))]
        primary=[];extra=[]
        for token in critical+[x for x in cmd if x not in critical]:
            if len(' '.join(primary+[token]).encode())<=511:primary.append(token)
            else:extra.append(token)
        assert all(x in primary for x in critical)
        assert len(' '.join(extra).encode())<1024
        header[64:576]=' '.join(primary).encode().ljust(512,b'\0')
        header[608:1632]=' '.join(extra).encode().ljust(1024,b'\0')
        for f,d in zip(fields,blobs):
            if f==1632:struct.pack_into('<Q',header,1636,page+len(body))
            struct.pack_into('<I',header,f,len(d));digest.update(d);digest.update(struct.pack('<I',len(d)))
            body+=d;body+=bytes(-len(body)%page)
        header[576:608]=digest.digest()+bytes(12)
        (OUT/name).write_bytes(header+body)
        (OUT/(name+'.sha256')).write_text(sha(OUT/name)+'  '+name+'\n')
        assert blobs[2:]==original[2:]
    (OUT/'recovery_dtbo.img').write_bytes(original[3])

def package_vendor():
    room(430*1024**2)
    def debug(img,cmd):return sp.check_output(['debugfs','-R',cmd,str(img)],stderr=sp.DEVNULL)
    spec=importlib.util.spec_from_file_location('meta',REPO/'scripts/mainline/android/refresh-integrated-vendor.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    rc=debug(BASE/'vendor.img','cat /etc/init/hw/init.hlte556n.rc')
    rc+=b'\n'+(HERE/'init.a9-boot-deadline.rc').read_bytes()
    changes={'etc/init/hw/init.hlte556n.rc':rc}
    for p in ['lib/modules/kernel-image.sha256','etc/thermal-a9.kernel-image.sha256']:
        changes[p]=(sha(OUT/'Image.gz')+'\n').encode()
    assert not (OUT/'vendor.img').exists()
    run(['cp','--sparse=always',BASE/'vendor.img',OUT/'vendor.img'],'vendor-copy')
    payload=OUT/'payload';payload.mkdir();batch=[]
    for i,(p,d) in enumerate(changes.items()):
        f=payload/str(i);f.write_bytes(d);mt=m.metadata(BASE/'vendor.img',p)
        label=payload/(str(i)+'.label');label.write_bytes(mt['label'].encode()+b'\0')
        batch += [f'rm /{p}',f'write {f} /{p}',f'sif /{p} mode 010{mt["mode"]}',f'sif /{p} uid {mt["uid"]}',f'sif /{p} gid {mt["gid"]}',f'ea_set -f {label} /{p} security.selinux']
        for t in ['atime','mtime','ctime','crtime']:batch.append(f'sif /{p} {t} @{int(mt[t],16)}')
    (OUT/'install.debugfs').write_text('\n'.join(batch)+'\n')
    run(['debugfs','-w','-f',OUT/'install.debugfs',OUT/'vendor.img'],'vendor-install')
    for p,d in changes.items():
        assert debug(OUT/'vendor.img','cat /'+p)==d
        assert m.metadata(OUT/'vendor.img',p)==m.metadata(BASE/'vendor.img',p)
    run(['e2fsck','-fn',OUT/'vendor.img'],'vendor-fsck')
    (OUT/'vendor.img.sha256').write_text(sha(OUT/'vendor.img')+'  vendor.img\n')

if __name__=='__main__':
    import sys
    OUT.mkdir(exist_ok=True)
    if sys.argv[1]=='kernel':kernel()
    elif sys.argv[1]=='package':
        package_boot();package_vendor()
        spec=importlib.util.spec_from_file_location('prepare',HERE/'prepare-vendor.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.prepare(OUT/'boot.img',OUT/'vendor.img',OUT/'vendor.img',OUT/'policy-overlay')
    elif sys.argv[1]=='boots':package_boot()
    else:raise SystemExit('kernel or package')
