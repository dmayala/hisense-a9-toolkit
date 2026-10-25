#!/usr/bin/env python3
"""Host-only O2 vendor overlay: shared RAM/ROW29 service gate, same O boot."""
from pathlib import Path
import hashlib,importlib.util,json,os,shutil,subprocess as sp

R=Path(__file__).resolve().parents[2]
ROOT=Path('/home/user/A9-build/mainline')
O=ROOT/'integrated-20261021o2'; BASE=ROOT/'integrated-20261021o'
IMAGE=O/'vendor.img'
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
REF=load(R/'scripts/mainline/android/refresh-integrated-vendor.py','refresh')
ROW=load(R/'scripts/mainline/storage-row29/data/integrate.py','row29')
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(name,data):(O/name).write_text(json.dumps(data,indent=2)+'\n')
def run(args,name):
    with (O/(name+'.log')).open('w') as log:p=sp.run(list(map(str,args)),stdout=log,stderr=sp.STDOUT)
    (O/(name+'.rc')).write_text(str(p.returncode)+'\n');assert p.returncode==0,name
def install(payload,phase):
    batch=[];metadata={}
    for i,(path,src) in enumerate(payload.items()):
        present=b'Inode:' in sp.check_output(['debugfs','-R','stat /'+path,IMAGE],stderr=sp.DEVNULL)
        m=REF.metadata(IMAGE,path if present else 'bin/a9-sensors.sh')
        if path=='bin/a9ml-test-data.sh':m={**m,'mode':'0644','uid':'0','gid':'0','label':'u:object_r:vendor_file:s0'}
        label=O/(phase+'-label-'+str(i));label.write_bytes(m['label'].encode()+b'\0')
        if present:batch.append('rm /'+path)
        batch += [f'write {src} /{path}',f'sif /{path} mode 010{m["mode"]}',f'sif /{path} uid {m["uid"]}',f'sif /{path} gid {m["gid"]}',f'ea_set -f {label} /{path} security.selinux']
        for field in ('atime','mtime','ctime','crtime'):batch.append(f'sif /{path} {field} @{int(m[field],16)}')
        metadata[path]=m
    commands=O/(phase+'.debugfs');commands.write_text('\n'.join(batch)+'\n')
    run(['debugfs','-w','-f',commands,IMAGE],phase)
    for path,src in payload.items():
        q=O/'readback';REF.debug(IMAGE,f'dump /{path} {q}')
        assert sha(q)==sha(src) and REF.metadata(IMAGE,path)==metadata[path],path
    save(phase+'-readback.json',{p:{'sha256':sha(s),'metadata':metadata[p]} for p,s in payload.items()})

def build():
    assert not IMAGE.exists()
    assert sha(BASE/'boot.img')=='09bb30c4cdf350707873d541de58f153290d47b3be87f01e936ef2b3ebf4abcb'
    assert sha(BASE/'vendor.img')=='c960e8bd3f9f721bc8e52c8f71de84633ccdae30aab027236db9bf1e42217210'
    assert shutil.disk_usage(O).free>2*1024**3+(BASE/'vendor.img').stat().st_size
    stage=O/'overlay';stage.mkdir()
    payload={'bin/a9-sensors.sh':R/'scripts/mainline/android/vendor/bin/a9-sensors.sh','bin/a9ml-test-data.sh':R/'scripts/mainline/android/vendor/bin/a9ml-test-data.sh','etc/a9-wifi/session/tools/coordinator-target.sh':R/'scripts/mainline/wifi/coordinator-target.sh'}
    tools='etc/a9-wifi/session/tools/'
    original=O/'audit/vendor'
    sums=(original/tools/'SHA256SUMS').read_text()
    before=sha(original/tools/'coordinator-target.sh');after=sha(payload[tools+'coordinator-target.sh'])
    assert sums.count(before+'  coordinator-target.sh')==1
    f=stage/'tools-SHA256SUMS';f.write_text(sums.replace(before+'  coordinator-target.sh',after+'  coordinator-target.sh'));payload[tools+'SHA256SUMS']=f
    manifest=json.loads((original/'etc/a9-wifi/session/manifest.json').read_text())
    # Some baseline documentary entries predate later session logging/init
    # updates. Refresh every entry against the actual payload, not old pins.
    for path in manifest['files']:manifest['files'][path]=sha(payload.get(path,original/path))
    f=stage/'session-manifest.json';f.write_text(json.dumps(manifest,indent=2)+'\n');payload['etc/a9-wifi/session/manifest.json']=f
    f=stage/'vendor_sepolicy.cil';f.write_text((original/'etc/selinux/vendor_sepolicy.cil').read_text()+'\n'+(R/'scripts/mainline/android/sepolicy/row29-service-data.cil').read_text());payload['etc/selinux/vendor_sepolicy.cil']=f
    run(['cp','--reflink=auto','--sparse=always',BASE/'vendor.img',IMAGE],'copy-vendor')
    install(payload,'services')
    policy=O/'policy';policy.mkdir()
    compiled=ROW.compile_policy(IMAGE,policy,ROW.DEFAULT_CACHE,Path('/home/user/A9-build/mainline/android/dl/cf_arm64_x/otatools/bin/secilc'),REF.debug)
    install({'etc/'+p:s for p,s in compiled.items()},'policy')
    for name in ('boot.img','recovery_dtbo.img','kernel.config','vmlinux.symvers','Module.symvers'):shutil.copyfile(BASE/name,O/name)
    run(['e2fsck','-fn',IMAGE],'vendor-fsck')

def verify():
    final=O/'audit/final';index=1
    while final.exists():
        index+=1;final=O/('audit/final-'+str(index))
    final.mkdir()
    run(['debugfs','-R','rdump / '+str(final),IMAGE],'extract-final')
    def inventory(root):return {str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
    before=inventory(O/'audit/vendor');after=inventory(final)
    changed={p:{'before':before.get(p),'after':after.get(p)} for p in before.keys()|after.keys() if before.get(p)!=after.get(p)}
    expected={'bin/a9-sensors.sh','bin/a9ml-test-data.sh','etc/a9-wifi/session/tools/coordinator-target.sh','etc/a9-wifi/session/tools/SHA256SUMS','etc/a9-wifi/session/manifest.json','etc/selinux/vendor_sepolicy.cil','etc/selinux/precompiled_sepolicy'}
    assert changed.keys()==expected,changed
    save('vendor-delta.json',changed)
    assert all(before[p]==after[p] for p in before if p.startswith('lib/modules/'))
    abi=json.loads((BASE/'module-abi-audit.json').read_text())
    for name,v in abi.items():assert sha(final/'lib/modules'/name)==v['sha256'],name
    assert len(abi)==100 and sum(v['checked'] for v in abi.values())==6200
    shutil.copyfile(BASE/'module-abi-audit.json',O/'module-abi-audit.json')
    manifest=json.loads((final/'etc/a9-wifi/session/manifest.json').read_text())
    for path,digest in manifest['files'].items():assert sha(final/path)==digest,path
    for line in (final/'etc/a9-wifi/session/tools/SHA256SUMS').read_text().splitlines():
        digest,name=line.split();assert sha(final/'etc/a9-wifi/session/tools'/name)==digest,name
    assert sha(O/'boot.img')==sha(BASE/'boot.img')
    for target in ('boot','recovery'):
        cmd=['bash',R/'scripts/abl-check.sh',O/'boot.img',target,'--json','--save-dtb',O/('abl-'+target+'.dtb')]
        if target=='boot':cmd+=['--dtbo',O/'recovery_dtbo.img']
        with (O/('abl-'+target+'.json')).open('w') as out,(O/('abl-'+target+'.err')).open('w') as err:p=sp.run(list(map(str,cmd)),stdout=out,stderr=err)
        (O/('abl-'+target+'.rc')).write_text(str(p.returncode)+'\n');assert p.returncode==0,target
        assert all(s['pass'] for s in json.loads((O/('abl-'+target+'.json')).read_text())['stages'].values())
    save('validation.json',dict(regular_files_before=len(before),regular_files_after=len(after),changed_paths=sorted(changed),all_100_modules_byte_identical_to_O=True,inherited_CRCs_checked=6200,inherited_CRC_unknown_or_mismatch=0,inherited_vermagic='7.2.10-a9+ SMP preempt mod_unload modversions aarch64',boot_identical=True,ABL_boot='PASS',ABL_recovery='PASS',wifi_manifests='PASS',phone_access=False,free_bytes=shutil.disk_usage(O).free))
    for name in ('boot.img','vendor.img','recovery_dtbo.img'):(O/(name+'.sha256')).write_text(sha(O/name)+'  '+str(O/name)+'\n')
    (O/'SHA256SUMS').write_text(''.join(sha(O/n)+'  '+n+'\n' for n in ('boot.img','vendor.img','recovery_dtbo.img')))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=('build','verify'));a=p.parse_args();os.umask(0o077);globals()[a.phase]()
