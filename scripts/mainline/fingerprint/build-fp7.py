#!/usr/bin/env python3
"""FP7: replace only the fingerprint TA signing variant in the integrated image."""
from pathlib import Path
import importlib.util,json,os,shutil,subprocess as sp

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
ROOT=Path('/home/user/A9-build/mainline')
BASE=ROOT/'integ-20261023-7'
OUT=ROOT/'integ-20261023-fp7'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def main():
    os.umask(0o077);OUT.mkdir(parents=True,exist_ok=True)
    assert (BASE/'READY.txt').is_file(),'wait for the integrated baseline to be published'
    assert not (OUT/'boot.img').exists() and not (OUT/'vendor.img').exists(),'preserve existing candidate'
    b=load('fp_build',HERE/'build.py');b.OUT=OUT
    meta=load('fp_meta',HERE.parent/'android/refresh-integrated-vendor.py')
    parent=json.loads((BASE/'integrated-build.json').read_text())
    for name,digest in parent['sha256'].items():assert b.sha(BASE/name)==digest,name
    # Never drop the integrated GPIO11 wake fix, media, sensors, radio or GNSS files.
    assert parent['sha256']['Image.gz']==b.sha(ROOT/'integ-20261023-fp6/Image.gz')
    for p in (HERE.parent/'qseecom/driver').iterdir():
        if p.is_file():assert b.sha(p)==parent['source_sha256'][str(p.relative_to(REPO))],p
    signing=load('fp_signing',HERE/'audit-signed-ta.py')
    selected=signing.audit(OUT/'signed-ta-audit')
    overlay=OUT/'overlay/firmware';overlay.mkdir(parents=True,exist_ok=True)
    for p in (OUT/'signed-ta-audit/golden-a').glob('sw_fp.*'):shutil.copyfile(p,overlay/p.name)
    (OUT/'stock-assets.json').write_text(json.dumps(selected,indent=2)+'\n')
    b.run(['python3','-I',HERE/'audit-load-image.py','--out',OUT,'--assets-root',OUT],'load-layout')
    original=BASE/'vendor.img';image=OUT/'vendor.img'
    b.room(original.stat().st_blocks*512+32*1024**2)
    b.run(['cp','--reflink=auto','--sparse=always',original,image],'vendor-copy')
    batch=[];changed=[]
    for p in sorted(overlay.iterdir()):
        name='firmware/'+p.name
        if b.read(original,name)==p.read_bytes():continue
        changed.append(name);info=meta.metadata(original,name)
        label=OUT/('label-'+p.name);label.write_bytes((info['label']+'\0').encode())
        batch += [f'rm /{name}',f'write {p} /{name}',f'sif /{name} mode 010{info["mode"]}',
                  f'sif /{name} uid {info["uid"]}',f'sif /{name} gid {info["gid"]}',
                  f'ea_set -f {label} /{name} security.selinux']
        for field in ('atime','mtime','ctime','crtime'):batch.append(f'sif /{name} {field} @{int(info[field],16)}')
    assert changed==['firmware/sw_fp.b01','firmware/sw_fp.mdt'],changed
    script=OUT/'install.debugfs';script.write_text('\n'.join(batch)+'\n')
    b.run(['debugfs','-w','-f',script,image],'vendor-install')
    files=sorted(meta.vendor_files(original));assert files==sorted(meta.vendor_files(image)),'vendor file inventory'
    actual=[]
    for name in files:
        old,new=b.read(original,name),b.read(image,name)
        if old!=new:actual.append(name)
    assert sorted(actual)==changed,actual
    for p in overlay.iterdir():
        name='firmware/'+p.name
        assert b.read(image,name)==p.read_bytes(),name
        assert meta.metadata(image,name)==meta.metadata(original,name),name
    b.run(['e2fsck','-fn',image],'vendor-fsck')
    for name in ['boot.img','Image.gz','integ.dtb','recovery_dtbo.img','kernel.config','Module.symvers','vmlinux.symvers']:
        (OUT/name).hardlink_to(BASE/name)
    b.run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3','-P',
           REPO/'scripts/abl-check.py',OUT/'boot.img','boot','--dtbo',OUT/'recovery_dtbo.img','--json'],'abl-check')
    assert json.loads((OUT/'abl-check.log').read_text())['result']=='PASS'
    audit=json.loads((BASE/'module-audit-integ7.json').read_text())
    for row in audit['vendors'].values():assert row['modules']==101 and row['imports_checked']==6243 and not row['failing_modules']
    hashes={name:b.sha(OUT/name) for name in ['boot.img','vendor.img','Image.gz','integ.dtb']}
    sources={str(p.relative_to(REPO)):b.sha(p) for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    receipt=dict(parent=str(BASE),parent_sha256=parent['sha256'],sha256=hashes,source_commit=sp.check_output(['git','-C',REPO,'rev-parse','HEAD'],text=True).strip(),source_sha256=sources,
                 host_only=True,hardware_tested=False,vendor_changes=changed,other_vendor_files_identical=len(files)-len(changed),vendor_metadata_preserved=True,
                 inherited_module_audit=str(BASE/'module-audit-integ7.json'),kernel_and_boot_unchanged=True,signing_audit=str(OUT/'signed-ta-audit/stock-assets.json'))
    (OUT/'integrated-build.json').write_text(json.dumps(receipt,indent=2)+'\n')
    (OUT/'SHA256SUMS').write_text(''.join(v+'  '+n+'\n' for n,v in hashes.items()))
    print('FP7 integrated image host PASS; only the two TA signing-envelope files changed',flush=True)

if __name__=='__main__':main()
