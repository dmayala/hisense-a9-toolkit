#!/usr/bin/env python3
"""INTEG-P: clean proven suspend fixes on the frozen O3-FBE composition.

Host only. Private archived N objects/source, fresh kernel + FastRPC/battery,
then replace only those module bytes and the two kernel-hash files in O3-FBE.
"""
import argparse,hashlib,importlib.util,json,os,shutil,stat,struct,subprocess as sp,tempfile
from pathlib import Path
R=Path(__file__).resolve().parents[2];ROOT=Path('/home/user/A9-build/mainline')
O=ROOT/'integrated-20261021p';BASE=ROOT/'integrated-20261021o3-fbe';N=ROOT/'integrated-20261021n'
SEED=ROOT/'suspend-pmtest-kernel-20261020'
ARCHIVE=Path('/home/user/a9-assets/mainline-archive/integrated-20261021n-kernel-scratch.tar')
K=O/'scratch/kernel';J=O/'scratch/obj'
ENV=dict(os.environ,PATH=':'.join(json.loads((ROOT/'demo-integrated-20261019/source.json').read_text())['tools'])+':'+os.environ['PATH'])
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(name,v):(O/name).write_text(json.dumps(v,indent=2)+'\n')
def room(budget=0):
    if shutil.disk_usage(O).free<2*1024**3+budget:raise RuntimeError('preserve 2GiB reserve plus declared phase budget')
def run(args,name,cwd=None):
    room()
    with (O/(name+'.log')).open('w') as f:p=sp.run(list(map(str,args)),cwd=cwd,env=ENV,stdout=f,stderr=sp.STDOUT)
    (O/(name+'.rc')).write_text(str(p.returncode)+'\n');assert p.returncode==0,name

def make(*targets):
    cmd=json.loads((SEED/'kernel-command.json').read_text())[:-1]
    return [s.replace(str(SEED/'kernel'),str(K)).replace(str(SEED/'obj'),str(J)) for s in cmd]+list(targets)
def debug(image,cmd):return sp.check_output(['debugfs','-R',cmd,str(image)],stderr=sp.DEVNULL)
def inventory(image):
    queue=[''];files={}
    while queue:
        directory=queue.pop()
        for line in debug(image,'ls -p /'+directory).decode().splitlines():
            if not line.strip():continue
            row=line.split('/');assert len(row)>=8,line
            if row[1]=='0' or row[5] in ('.','..'):continue
            name=row[5];assert name and not any(c.isspace() for c in name)
            path=(directory+'/' if directory else '')+name;mode=int(row[2],8)
            if stat.S_ISDIR(mode):queue.append(path)
            elif stat.S_ISREG(mode):files[path]=int(row[6])
    return files
def exports(p):return {r[1]:(r[0],*r[3:]) for l in p.read_text().splitlines() if len(r:=l.split())>=4}
def prepare():
    room(ARCHIVE.stat().st_size+256*1024**2)
    assert not (O/'scratch').exists()
    assert sha(BASE/'boot.img')=='09bb30c4cdf350707873d541de58f153290d47b3be87f01e936ef2b3ebf4abcb'
    assert sha(BASE/'vendor.img')=='5576d9052d14c10f2fcc4533351a40584fe0d5c7f44a7624ccd1791c87dfe6d4'
    assert sha(ARCHIVE)=='8aa2d4a513f4198eb7d883009a453d9af90fce2eee938558526e7c2b68360538'
    run(['tar','-xf',ARCHIVE,'-C',O],'restore-private-scratch')
    assert sha(J/'.config')==sha(BASE/'kernel.config')==sha(N/'kernel.config')
    assert sha(J/'arch/arm64/boot/Image.gz')==sha(N/'Image.gz')
    cfg=(J/'.config').read_text();assert '# CONFIG_VT_CONSOLE is not set' in cfg and 'CONFIG_VT_CONSOLE_SLEEP=y' not in cfg
    for p in J.rglob('*.cmd'):
        text=p.read_text()
        if str(N/'scratch') in text:p.write_text(text.replace(str(N/'scratch'),str(O/'scratch')))
    (J/'source').unlink();(J/'source').symlink_to(K)
    (J/'Makefile').write_text('export KBUILD_OUTPUT = '+str(J)+'\ninclude '+str(K/'Makefile')+'\n')
    usb=K/'drivers/usb/dwc3/dwc3-qcom-legacy.c'
    assert sha(usb)==sha(SEED/'kernel/drivers/usb/dwc3/dwc3-qcom-legacy.c')
    data=usb.read_bytes();usb.unlink();usb.write_bytes(data)
    patch=R/'scripts/mainline/usb/dwc3-sm6115-restore-qscratch.patch'
    run(['git','apply','--unidiff-zero',patch],'clean-usb-patch',cwd=K)
    assert sha(usb)=='ceb7b7dde307974b9fce7da15b25646ae6e405b68c4679e03eb885eab11d38dd'
    save('inputs.json',dict(base_boot=sha(BASE/'boot.img'),base_vendor=sha(BASE/'vendor.img'),archive_sha256=sha(ARCHIVE),config_sha256=sha(J/'.config'),usb_source_sha256=sha(usb),usb_patch_sha256=sha(patch)))
def kernel():
    room(256*1024**2);run(make('Image.gz'),'kernel-build')
    assert sha(J/'.config')==sha(BASE/'kernel.config')
    assert exports(J/'vmlinux.symvers')==exports(BASE/'vmlinux.symvers')
    rows=[l for l in (BASE/'Module.symvers').read_text().splitlines() if l.split()[2]!='vmlinux']
    (J/'Module.symvers').write_text((J/'vmlinux.symvers').read_text()+'\n'.join(rows)+'\n')
    for a,b in [('arch/arm64/boot/Image.gz','Image.gz'),('.config','kernel.config'),('Module.symvers','Module.symvers'),('vmlinux.symvers','vmlinux.symvers')]:shutil.copyfile(J/a,O/b)
    save('kernel-result.json',dict(config_unchanged=True,builtin_exports_unchanged=len(exports(J/'vmlinux.symvers')),kernel_sha256=sha(O/'Image.gz'),kernel_release=(J/'include/config/kernel.release').read_text().strip()))
def modules():
    room(64*1024**2)
    m=O/'fastrpc-module';(m/'drivers/misc').mkdir(parents=True)
    p=m/'drivers/misc/fastrpc.c';shutil.copyfile(SEED/'kernel/drivers/misc/fastrpc.c',p)
    assert sha(p)=='f6b63cace02af489df6e0597ea90a3c78a3b2164e599a615dadd921b24b219d0'
    run(['git','apply','--unidiff-zero',R/'scripts/mainline/sensors/fastrpc-restore-context.patch'],'fastrpc-restore',cwd=m)
    assert sha(p)=='dafab838d60439d1c2807620359be412588bfabc7c340c1c1874783f02045e24'
    shutil.copyfile(p,m/'fastrpc.c');(m/'Makefile').write_text('obj-m += fastrpc.o\n')
    run(make('M='+str(m),'W=1','modules'),'fastrpc-build')
    b=O/'battery-module';b.mkdir()
    for p in [R/'scripts/mainline/pmic/battery/Makefile',R/'scripts/mainline/pmic/battery/pmi632-battery.c',*sorted((R/'scripts/mainline/pmic/battery').glob('*.h'))]:shutil.copyfile(p,b/p.name)
    shutil.copyfile(R/'scripts/mainline/pmic/pmi632-policy.h',b/'pmi632-policy.h')
    run(make('M='+str(b),'W=1','modules'),'battery-build')
    for d,n in [(m,'fastrpc.ko'),(b,'pmi632-battery.ko')]:
        shutil.copyfile(d/n,O/n);run(['aarch64-unknown-linux-gnu-strip','--strip-debug',O/n],'strip-'+n)
    assert sp.check_output(['modinfo','-F','version',O/'pmi632-battery.ko'],env=ENV,text=True).strip()=='r21g'
    save('module-sources.json',{str(p.relative_to(R)):sha(p) for p in [R/'scripts/mainline/sensors/fastrpc-restore-context.patch',R/'scripts/mainline/usb/dwc3-sm6115-restore-qscratch.patch',R/'scripts/mainline/pmic/battery/pmi632-battery.c']})
def package():
    room(900*1024**2);old=(BASE/'boot.img').read_bytes();page=struct.unpack_from('<I',old,36)[0]
    assert page==4096 and struct.unpack_from('<I',old,40)[0]==2
    pad=lambda n:(n+page-1)//page*page
    blobs=[];pos=page
    for off in (8,16,24,1632,1648):
        n=struct.unpack_from('<I',old,off)[0];blobs.append(old[pos:pos+n]);pos+=pad(n)
    before=list(blobs);blobs[0]=(O/'Image.gz').read_bytes();h=bytearray(old[:page])
    struct.pack_into('<I',h,8,len(blobs[0]));struct.pack_into('<Q',h,1636,page+sum(pad(len(b)) for b in blobs[:3]))
    digest=hashlib.sha1()
    for b in blobs:digest.update(b);digest.update(struct.pack('<I',len(b)))
    h[576:608]=digest.digest()+b'\0'*12
    allowed=set(range(8,12))|set(range(576,608))|set(range(1636,1644))
    assert all(a==b or i in allowed for i,(a,b) in enumerate(zip(old[:page],h)))
    primary=h[64:576].split(b'\0')[0].decode().split()
    assert primary.count('qcom_wdt.a9_guard=1')==1 and not any(t.startswith('qcom_wdt.a9_guard=') and t!='qcom_wdt.a9_guard=1' for t in primary)
    assert before[1:]==blobs[1:]
    (O/'boot.img').write_bytes(h+b''.join(b.ljust(pad(len(b)),b'\0') for b in blobs));(O/'recovery_dtbo.img').write_bytes(blobs[3])
    save('boot-payload-check.json',dict(non_kernel_payloads_identical=True,primary_cmdline_identical=h[64:576]==old[64:576],extra_cmdline_identical=h[608:1632]==old[608:1632],a9_guard=1,boot_sha256=sha(O/'boot.img'),dtbo_sha256=sha(O/'recovery_dtbo.img')))
    image=O/'vendor.img';assert not image.exists();run(['cp','--reflink=auto','--sparse=always',BASE/'vendor.img',image],'copy-vendor')
    spec=importlib.util.spec_from_file_location('refresh',R/'scripts/mainline/android/refresh-integrated-vendor.py');ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref)
    (O/'kernel-image.sha256').write_text(sha(O/'Image.gz')+'\n')
    changed={'lib/modules/fastrpc.ko':O/'fastrpc.ko','lib/modules/pmi632-battery.ko':O/'pmi632-battery.ko','lib/modules/kernel-image.sha256':O/'kernel-image.sha256','etc/thermal-a9.kernel-image.sha256':O/'kernel-image.sha256'}
    batch=[];metadata={}
    for i,(path,p) in enumerate(changed.items()):
        m=ref.metadata(image,path);metadata[path]=m;label=O/('label-'+str(i));label.write_bytes(m['label'].encode()+b'\0')
        batch += ['rm /'+path,f'write {p} /{path}',f'sif /{path} mode 010{m["mode"]}',f'sif /{path} uid {m["uid"]}',f'sif /{path} gid {m["gid"]}',f'ea_set -f {label} /{path} security.selinux']
        for field in ('atime','mtime','ctime','crtime'):batch.append(f'sif /{path} {field} @{int(m[field],16)}')
    script=O/'install.debugfs';script.write_text('\n'.join(batch)+'\n');run(['debugfs','-w','-f',script,image],'install')
    for path,p in changed.items():
        q=O/'readback';debug(image,f'dump /{path} {q}');assert sha(q)==sha(p) and ref.metadata(image,path)==metadata[path]
    save('changed-payloads.json',{p:sha(v) for p,v in changed.items()})
    assert image.stat().st_size==BASE.joinpath('vendor.img').stat().st_size==847532032

def verify():
    image=O/'vendor.img';run(['e2fsck','-fn',image],'vendor-fsck')
    before=json.loads((BASE/'final-file-hashes.json').read_text());expected=dict(before);expected.update(json.loads((O/'changed-payloads.json').read_text()))
    files=inventory(image);assert files.keys()==expected.keys()
    after={}
    for path,size in files.items():
        data=debug(image,'cat /'+path);assert len(data)==size,path
        after[path]=hashlib.sha256(data).hexdigest()
    assert after==expected
    # ABI tools need actual ELF files; extract only the modules (not full vendor).
    with tempfile.TemporaryDirectory(prefix='module-readback-',dir=O) as temp:
        root=Path(temp);mods=[]
        for path in sorted(files):
            if path.startswith('lib/modules/') and path.endswith('.ko'):
                p=root/Path(path).name;p.write_bytes(debug(image,'cat /'+path));mods.append(p)
        known={n:int(v[0],16) for n,v in exports(O/'vmlinux.symvers').items()}
        for p in mods:
            q=sp.run(['modprobe','--show-exports',p],capture_output=True,text=True,env=ENV)
            if q.returncode:assert not q.stdout and 'could not get symbols' in q.stderr
            for line in q.stdout.splitlines():
                crc,n=line.split();crc=int(crc,16);assert n not in known or known[n]==crc;known[n]=crc
        audit={};baseaudit=json.loads((BASE/'module-abi-audit.json').read_text())
        for p in mods:
            imports={r[1]:int(r[0],16) for l in sp.check_output(['modprobe','--show-modversions',p],text=True,env=ENV).splitlines() if len(r:=l.split())==2}
            assert not imports.keys()-known.keys(),p.name
            assert all(known[n]==v for n,v in imports.items()),p.name
            assert not imports.keys()&{'pm_vt_switch_required','pm_vt_switch_unregister'}
            vm=sp.check_output(['modinfo','-F','vermagic',p],text=True,env=ENV).strip();assert vm==baseaudit[p.name]['vermagic']
            if p.name not in ('fastrpc.ko','pmi632-battery.ko'):assert sha(p)==baseaudit[p.name]['sha256']
            audit[p.name]=dict(sha256=sha(p),imports=len(imports),checked=len(imports),vermagic=vm)
        assert len(audit)==100;save('module-abi-audit.json',audit)
        session=json.loads(debug(image,'cat /etc/a9-wifi/session/manifest.json'))
        for p,h in session['files'].items():assert after[p]==h,p
        assert b'write /proc/sys/kernel/printk 1' in debug(image,'cat /etc/init/init.a9ml-debug.rc')
        assert b'/dev/a9-row29-hold/stay' in debug(image,'cat /bin/a9ml-deadman.sh')
        assert b'fileencryption=aes-256-xts:aes-256-cts:v2' in debug(image,'cat /etc/fstab.hlte556n')
    save('final-file-hashes.json',after);save('vendor-delta.json',{p:dict(before=before.get(p),after=after.get(p)) for p in before.keys()|after.keys() if before.get(p)!=after.get(p)})
    for role in ('boot','recovery'):
        cmd=['bash',R/'scripts/abl-check.sh',O/'boot.img',role,'--json','--save-dtb',O/('abl-'+role+'.dtb')]
        if role=='boot':cmd+=['--dtbo',O/'recovery_dtbo.img']
        with (O/('abl-'+role+'.json')).open('w') as f,(O/('abl-'+role+'.err')).open('w') as err:p=sp.run(list(map(str,cmd)),env=ENV,stdout=f,stderr=err)
        (O/('abl-'+role+'.rc')).write_text(str(p.returncode)+'\n');assert p.returncode==0
        receipt=json.loads((O/('abl-'+role+'.json')).read_text());assert receipt['image_sha256']==sha(O/'boot.img') and all(s['pass'] for s in receipt['stages'].values())
    lp=json.loads((O/'lp-fit-input.json').read_text());assert lp['no_resize_fits'] and lp['vendor_b_bytes']>=image.stat().st_size==847532032
    save('lp-size-check.json',dict(image_bytes=image.stat().st_size,vendor_b_bytes=lp['vendor_b_bytes'],input_sha256=sha(O/'lp-fit-input.json'),no_resize_fits=True,source='cached host LP-write readback, not a new phone query'))
    shutil.copyfile(BASE/'keeper-fbe.sh',O/'keeper-fbe.sh');(O/'phone').mkdir(exist_ok=True)
    text=(BASE/'direct-b-fbe.sh').read_text();assert str(BASE/'phone') in text and str(BASE/'keeper-fbe.sh') in text
    (O/'direct-b-fbe.sh').write_text(text.replace(str(BASE/'phone'),str(O/'phone')).replace(str(BASE/'keeper-fbe.sh'),str(O/'keeper-fbe.sh')))
    run(['bash','-n',O/'direct-b-fbe.sh'],'launcher-syntax');run(['bash','-n',O/'keeper-fbe.sh'],'keeper-syntax')
    code=hashlib.sha256((sha(image)+' vendor_b 0').encode()).hexdigest()[:8]
    (O/'boot-command.txt').write_text('bash '+str(O/'direct-b-fbe.sh')+' '+str(O/'boot.img')+' '+str(image)+' '+code+'\n')
    save('validation.json',dict(module_count=len(audit),import_CRCs_checked=sum(v['checked'] for v in audit.values()),inherited_modules_exact=98,regular_files=len(after),only_module_and_kernel_hash_payloads_changed=True,config_identical_to_O3_FBE=True,nonkernel_boot_payloads_identical=True,a9_guard=1,quiet_console_retained=True,FBE_and_SELinux_and_sensor_fix_identical=True,vendor_bytes=image.stat().st_size,same_size_vendor_b_no_resize=True,ABL_boot='PASS',ABL_recovery='PASS',phone_access=False))
    for n in ('boot.img','vendor.img','recovery_dtbo.img'):(O/(n+'.sha256')).write_text(sha(O/n)+'  '+str(O/n)+'\n')
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('phase',choices=('prepare','kernel','modules','package','verify'));a=ap.parse_args();os.umask(0o077);O.mkdir(exist_ok=True);globals()[a.phase]()
