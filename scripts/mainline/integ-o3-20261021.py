#!/usr/bin/env python3
"""Host-only O3: one sensor-script overlay, unchanged proven O2 boot/modules."""
from pathlib import Path
import hashlib,importlib.util,json,os,shutil,subprocess as sp
R=Path(__file__).resolve().parents[2]
ROOT=Path('/home/user/A9-build/mainline');O=ROOT/'integrated-20261021o3';BASE=ROOT/'integrated-20261021o2'
IMAGE=O/'vendor.img'
spec=importlib.util.spec_from_file_location('refresh',R/'scripts/mainline/android/refresh-integrated-vendor.py');REF=importlib.util.module_from_spec(spec);spec.loader.exec_module(REF)
def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(name,value):(O/name).write_text(json.dumps(value,indent=2)+'\n')
def run(args,name):
    with (O/(name+'.log')).open('w') as f:p=sp.run(list(map(str,args)),stdout=f,stderr=sp.STDOUT)
    (O/(name+'.rc')).write_text(str(p.returncode)+'\n');assert p.returncode==0,name
def main():
    assert not IMAGE.exists()
    assert sha(BASE/'boot.img')=='09bb30c4cdf350707873d541de58f153290d47b3be87f01e936ef2b3ebf4abcb'
    assert sha(BASE/'vendor.img')=='29343d7596ed82cb2695377c97003fa608537447d1c39d8c404042cd6da6e1c3'
    assert shutil.disk_usage(O).free>2*1024**3+2*(BASE/'vendor.img').stat().st_size
    run(['cp','--sparse=always','--reflink=auto',BASE/'vendor.img',IMAGE],'copy-vendor')
    path='bin/a9-sensors.sh';source=R/'scripts/mainline/android/vendor/bin/a9-sensors.sh'
    meta=REF.metadata(IMAGE,path);label=O/'label';label.write_bytes(meta['label'].encode()+b'\0')
    batch=['rm /'+path,f'write {source} /{path}',f'sif /{path} mode 010{meta["mode"]}',f'sif /{path} uid {meta["uid"]}',f'sif /{path} gid {meta["gid"]}',f'ea_set -f {label} /{path} security.selinux']
    for field in ('atime','mtime','ctime','crtime'):batch.append(f'sif /{path} {field} @{int(meta[field],16)}')
    (O/'install.debugfs').write_text('\n'.join(batch)+'\n');run(['debugfs','-w','-f',O/'install.debugfs',IMAGE],'install')
    REF.debug(IMAGE,'dump /'+path+' '+str(O/'readback-a9-sensors.sh'))
    assert sha(O/'readback-a9-sensors.sh')==sha(source) and REF.metadata(IMAGE,path)==meta
    for name in ('boot.img','recovery_dtbo.img','kernel.config','vmlinux.symvers','Module.symvers'):shutil.copyfile(BASE/name,O/name)
    run(['e2fsck','-fn',IMAGE],'vendor-fsck')
    extracted=O/'final';extracted.mkdir();run(['debugfs','-R','rdump / '+str(extracted),IMAGE],'extract-final')
    baseline=BASE/'audit/final-3'
    def inventory(root):return {str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
    before=inventory(baseline);after=inventory(extracted)
    changed={p:{'before':before.get(p),'after':after.get(p)} for p in before.keys()|after.keys() if before.get(p)!=after.get(p)}
    assert set(changed)=={path},changed
    save('vendor-delta.json',changed);save('final-file-hashes.json',after)
    abi=json.loads((BASE/'module-abi-audit.json').read_text())
    for name,v in abi.items():assert sha(extracted/'lib/modules'/name)==v['sha256']
    assert len(abi)==100 and sum(v['checked'] for v in abi.values())==6200
    shutil.copyfile(BASE/'module-abi-audit.json',O/'module-abi-audit.json')
    manifest=json.loads((extracted/'etc/a9-wifi/session/manifest.json').read_text())
    for path,digest in manifest['files'].items():assert sha(extracted/path)==digest,path
    # Reuse exact-boot ABL receipts, explicitly verifying their image and DTBO.
    for target in ('boot','recovery'):
        for suffix in ('json','err','rc'):shutil.copyfile(BASE/f'abl-{target}.{suffix}',O/f'abl-{target}.{suffix}')
        receipt=json.loads((O/f'abl-{target}.json').read_text())
        assert receipt['image_sha256']==sha(O/'boot.img') and all(s['pass'] for s in receipt['stages'].values())
    assert sha(O/'recovery_dtbo.img')==sha(BASE/'recovery_dtbo.img')
    save('validation.json',dict(regular_files=len(after),changed_paths=['bin/a9-sensors.sh'],all_other_regular_files_identical_to_O2=True,all_100_modules_identical=True,inherited_CRCs_checked=6200,inherited_vermagic='7.2.10-a9+ SMP preempt mod_unload modversions aarch64',boot_identical=True,policy_identical=True,ABL_boot='PASS (exact O2 boot receipt)',ABL_recovery='PASS (exact O2 boot receipt)',metadata=meta,free_bytes=shutil.disk_usage(O).free,phone_access=False))
    for name in ('boot.img','vendor.img','recovery_dtbo.img'):(O/(name+'.sha256')).write_text(sha(O/name)+'  '+str(O/name)+'\n')
    (O/'SHA256SUMS').write_text(''.join(sha(O/n)+'  '+n+'\n' for n in ('boot.img','vendor.img','recovery_dtbo.img')))
if __name__=='__main__':os.umask(0o077);main()
