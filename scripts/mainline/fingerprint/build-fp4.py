#!/usr/bin/env python3
"""FP4 host package: native QSEE resource fix, CE1 DT vote, unchanged HAL/module."""
from pathlib import Path
import difflib
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess as sp

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
ROOT=Path('/home/user/A9-build/mainline')
OUT=ROOT/'integ-20261023-fp4'
PARENT=ROOT/'integ-20261023-fp3'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def main():
    os.umask(0o077);OUT.mkdir(parents=True,exist_ok=True)
    if not (OUT/'Image.gz').exists(): sp.run(['python3','-I',HERE/'build-fp4-kernel.py'],check=True)
    b=load('fp_build',HERE/'build.py');b.OUT=OUT
    assert not (OUT/'boot.img').exists(), 'FP4 already packaged'
    assert json.loads((OUT/'abi.json').read_text()).get('module_imports_match',json.loads((OUT/'abi.json').read_text())['dictionary_identical'])
    for p in (HERE.parent/'qseecom/driver').iterdir():
        if p.is_file(): assert p.read_bytes()==(OUT/'kernel-source/drivers/firmware/qcom'/p.name).read_bytes(),p
    cached=ROOT/'plain-wrapped-unicode-fix11-20261023/scratch/kernel/drivers/firmware/qcom/qcom_scm.c'
    patch=''.join(difflib.unified_diff(cached.read_text().splitlines(True),(OUT/'kernel-source/drivers/firmware/qcom/qcom_scm.c').read_text().splitlines(True),fromfile='a/drivers/firmware/qcom/qcom_scm.c',tofile='b/drivers/firmware/qcom/qcom_scm.c'))
    assert patch==(HERE.parent/'qseecom/0002-a9-qseecom-resource-votes.patch').read_text()
    parent=json.loads((PARENT/'integrated-build.json').read_text())
    for name,digest in parent['sha256'].items(): assert b.sha(PARENT/name)==digest,name
    # The Sunwave control driver and stock reset/power/IRQ sequence are retained.
    assert b.sha(HERE/'driver/a9-sunwave.c')==parent['source_sha256']['scripts/mainline/fingerprint/driver/a9-sunwave.c']
    (OUT/'module').mkdir(exist_ok=True);(OUT/'module/a9-sunwave.ko').hardlink_to(PARENT/'module/a9-sunwave.ko')
    raw=(PARENT/'boot.img').read_bytes();pg=struct.unpack_from('<I',raw,36)[0];pos=pg;blobs=[];fields=(8,16,24,1632,1648)
    for f in fields:
        size=struct.unpack_from('<I',raw,f)[0];blobs.append(raw[pos:pos+size]);pos+=(size+pg-1)//pg*pg
    original=blobs.copy();(OUT/'parent.dtb').write_bytes(blobs[4])
    (OUT/'qsee-clock.dts').write_text('/dts-v1/;\n/plugin/;\n'+(HERE.parent/'qseecom/a9-qseecom-clock.dtsi').read_text())
    b.run([b.DTC/'dtc','-@','-I','dts','-O','dtb','-o',OUT/'qsee-clock.dtbo',OUT/'qsee-clock.dts'],'dt-clock')
    b.run([b.DTC/'fdtoverlay','-i',OUT/'parent.dtb','-o',OUT/'integ.dtb',OUT/'qsee-clock.dtbo'],'dt-apply')
    def prop(path,node,key,kind='s'):
        return sp.check_output([b.DTC/'fdtget','-t',kind,path,node,key],text=True).strip()
    dt=OUT/'integ.dtb';scm=prop(dt,'/__symbols__','scm');rpm=prop(dt,'/__symbols__','rpmcc');ph=int(prop(dt,rpm,'phandle','i'))
    assert prop(dt,scm,'clock-names')=='qsee'
    assert list(map(int,prop(dt,scm,'clocks','i').split()))==[ph,70]
    assert prop(dt,scm,'assigned-clocks','i')==prop(dt,scm,'clocks','i')
    assert prop(dt,scm,'assigned-clock-rates','i')=='192000000'
    b.check_fingerprint_dtb(dt)
    normalized=OUT/'dt-without-clock.dtb';shutil.copyfile(dt,normalized)
    for key in ['clocks','clock-names','assigned-clocks','assigned-clock-rates']:
        sp.run([b.DTC/'fdtput','-d',normalized,scm,key],check=True)
    def canonical(path):return sp.check_output([b.DTC/'dtc','-I','dtb','-O','dts',path],stderr=sp.DEVNULL)
    assert canonical(normalized)==canonical(OUT/'parent.dtb'), 'unexpected DT change'
    (OUT/'integ.dts').write_bytes(canonical(dt))
    blobs[0]=(OUT/'Image.gz').read_bytes();blobs[4]=dt.read_bytes();header=bytearray(raw[:pg]);body=bytearray();digest=hashlib.sha1()
    for f,blob in zip(fields,blobs):
        if f==1632:struct.pack_into('<Q',header,1636,pg+len(body))
        struct.pack_into('<I',header,f,len(blob));digest.update(blob);digest.update(struct.pack('<I',len(blob)));body+=blob;body+=bytes(-len(body)%pg)
    header[576:608]=digest.digest()+bytes(12);(OUT/'boot.img').write_bytes(header+body)
    assert blobs[1:4]==original[1:4] and header[64:576]==raw[64:576] and header[608:1632]==raw[608:1632]
    (OUT/'recovery_dtbo.img').write_bytes(original[3])
    b.run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3','-P',REPO/'scripts/abl-check.py',OUT/'boot.img','boot','--dtbo',OUT/'recovery_dtbo.img','--json'],'abl-check')
    assert json.loads((OUT/'abl-check.log').read_text())['result']=='PASS'
    # Change only the existing kernel hash marker; preserve inode metadata/label.
    base=PARENT/'vendor.img';image=OUT/'vendor.img';assert not image.exists()
    b.room(base.stat().st_blocks*512+32*1024**2)
    b.run(['cp','--reflink=auto','--sparse=always',base,image],'vendor-copy')
    meta=load('fp_meta',HERE.parent/'android/refresh-integrated-vendor.py')
    name='lib/modules/kernel-image.sha256';info=meta.metadata(base,name)
    marker=OUT/'kernel-image.sha256';marker.write_text(b.sha(OUT/'Image.gz')+'\n')
    label=OUT/'module-marker-label';label.write_bytes((info['label']+'\0').encode())
    batch=[f'rm /{name}',f'write {marker} /{name}',f'sif /{name} mode 010{info["mode"]}',f'sif /{name} uid {info["uid"]}',f'sif /{name} gid {info["gid"]}',f'ea_set -f {label} /{name} security.selinux']
    for field in ('atime','mtime','ctime','crtime'):batch.append(f'sif /{name} {field} @{int(info[field],16)}')
    script=OUT/'install.debugfs';script.write_text('\n'.join(batch)+'\n')
    b.run(['debugfs','-w','-f',script,image],'vendor-install')
    assert b.read(image,name)==marker.read_bytes() and meta.metadata(image,name)==info
    for path in ['lib/modules/a9-sunwave.ko','etc/selinux/precompiled_sepolicy','etc/init/init.a9-fingerprint.rc','lib64/hw/fingerprint.default.so']:
        assert b.read(image,path)==b.read(base,path),path
    b.run(['e2fsck','-fn',image],'vendor-fsck')
    hashes={n:b.sha(OUT/n) for n in ['boot.img','vendor.img','Image.gz','integ.dtb','module/a9-sunwave.ko']}
    (OUT/'SHA256SUMS').write_text(''.join(v+'  '+n+'\n' for n,v in hashes.items()))
    paths=[p for root in [HERE,HERE.parent/'qseecom'] for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths.append(REPO/'scripts/mainline/android/sm6115-hisense-hlte556n-android.dts')
    result=dict(parent=str(PARENT),base=str(b.BASE),sha256=hashes,source_commit=sp.check_output(['git','-C',REPO,'rev-parse','HEAD'],text=True).strip(),source_sha256={str(p.relative_to(REPO)):b.sha(p) for p in paths},host_only=True,hardware_tested=False,native_export_audit=str(OUT/'abi.json'),vendor_changes=[name],vendor_metadata_preserved=True,dt_changes='SCM CE1 clock vote only; secure SPI reservations/control sequence unchanged',reused_module_policy_audit=str(ROOT/'integ-20261023-fp2/integrated-build.json'))
    (OUT/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    print('FP4 host image ready: '+str(OUT),flush=True)


if __name__=='__main__':main()
