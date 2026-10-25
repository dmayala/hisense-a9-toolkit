#!/usr/bin/env python3
"""Host-only integ6 repack: correct existing stats access and boot radio startup.

The integ5 kernel/DT already contain the correct RPM stats implementation.
Repack them unchanged; do not invent a kernel change to fix an unmounted FS.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess as sp

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
ROOT=Path('/home/user/A9-build/mainline')
OLD=ROOT/'integ-20261023-5'
OUT=ROOT/'integ-20261023-6'


def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--min-free-gib',type=int,default=15)
    args=parser.parse_args()
    os.umask(0o077);OUT.mkdir(exist_ok=True)
    assert not (OUT/'integrated-build.json').exists(),'completed candidate exists'
    base=Path(json.loads((ROOT/'gnss-outdoor-fix3-20261023/payload.json').read_text())['vendor']).resolve()
    assert sha(base)=='aac75180250669715c35fda353c2d8bd1ec851b7873452a4fd13c99a96f351aa'
    assert shutil.disk_usage(OUT).free>=args.min_free_gib*1024**3+base.stat().st_blocks*512+64*1024**2
    def run(command,label):
        with (OUT/(label+'.log')).open('wb') as log:
            sp.run(list(map(str,command)),check=True,stdout=log,stderr=sp.STDOUT,cwd=REPO)
        assert shutil.disk_usage(OUT).free>=args.min_free_gib*1024**3
    # Rebuild the complete boot container from the five original payloads,
    # preserving both command lines, DT, deadlines and recovery_dtbo.
    raw=(OLD/'boot.img').read_bytes();page=struct.unpack_from('<I',raw,36)[0]
    fields=(8,16,24,1632,1648);pos=page;blobs=[]
    for field in fields:
        size=struct.unpack_from('<I',raw,field)[0];blobs.append(raw[pos:pos+size]);pos+=(size+page-1)//page*page
    header=bytearray(raw[:page]);body=bytearray();digest=hashlib.sha1()
    for field,blob in zip(fields,blobs):
        if field==1632:struct.pack_into('<Q',header,1636,page+len(body))
        struct.pack_into('<I',header,field,len(blob));digest.update(blob);digest.update(struct.pack('<I',len(blob)))
        body+=blob;body+=bytes(-len(body)%page)
    header[576:608]=digest.digest()+bytes(12)
    boot=bytes(header+body);assert boot==raw
    (OUT/'boot.img').write_bytes(boot)
    (OUT/'Image.gz').write_bytes(blobs[0]);(OUT/'integ.dtb').write_bytes(blobs[4]);(OUT/'recovery_dtbo.img').write_bytes(blobs[3])
    for name in ['kernel.config','Module.symvers','vmlinux.symvers']:shutil.copyfile(OLD/name,OUT/name)
    config=(OUT/'kernel.config').read_text()
    assert all(k+'=y\n' in config for k in ['CONFIG_QCOM_STATS','CONFIG_DEBUG_FS','CONFIG_QCOM_SMEM'])
    assert sha(OUT/'Image.gz')==sha(OLD/'Image.gz')
    run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3','-P',REPO/'scripts/abl-check.py',OUT/'boot.img','boot','--dtbo',OUT/'recovery_dtbo.img','--json'],'abl-check')
    assert json.loads((OUT/'abl-check.log').read_text())['result']=='PASS'
    spec=importlib.util.spec_from_file_location('meta6',REPO/'scripts/mainline/android/refresh-integrated-vendor.py')
    meta=importlib.util.module_from_spec(spec);spec.loader.exec_module(meta)
    run(['cp','--reflink=auto','--sparse=always',base,OUT/'vendor.img'],'vendor-copy')
    path='etc/init/init.a9ml-stock-radio.rc'
    source=REPO/'scripts/mainline/cellular/stock/vendor'/path
    info=meta.metadata(base,path);label=OUT/'label';label.write_bytes(info['label'].encode()+b'\0')
    batch=[f'rm /{path}',f'write {source} /{path}',f'sif /{path} mode 010{info["mode"]}',f'sif /{path} uid {info["uid"]}',f'sif /{path} gid {info["gid"]}',f'ea_set -f {label} /{path} security.selinux']
    batch.extend(f'sif /{path} {k} @{int(info[k],16)}' for k in ['atime','mtime','ctime','crtime'])
    (OUT/'install.debugfs').write_text('\n'.join(batch)+'\n')
    run(['debugfs','-w','-f',OUT/'install.debugfs',OUT/'vendor.img'],'vendor-install')
    def member(image,p):return sp.check_output(['debugfs','-R','cat /'+p,str(image)],stderr=sp.DEVNULL)
    assert member(OUT/'vendor.img',path)==source.read_bytes()
    assert meta.metadata(OUT/'vendor.img',path)==info
    # Every other regular file, including firmware, seeds, policy, modules,
    # helper/APK-facing assets and E Ink scripts, must remain byte-identical.
    unchanged=0
    for p in meta.vendor_files(base):
        if p==path:continue
        assert member(OUT/'vendor.img',p)==member(base,p),p
        unchanged+=1
    policy=member(OUT/'vendor.img','etc/selinux/vendor_sepolicy.cil').decode()
    assert '(allow vendor_init_202504 a9ml_prop (property_service (set)))' in policy
    run(['e2fsck','-fn',OUT/'vendor.img'],'vendor-fsck')
    # Verify the installed full init tree against the same platform cache.
    init=OUT/'init-check';(init/'etc').mkdir(parents=True,exist_ok=True)
    run(['debugfs','-R',f'rdump /etc/init {init}/etc',OUT/'vendor.img'],'init-extract')
    (init/'vendor.passwd').write_bytes(member(OUT/'vendor.img','etc/passwd'))
    (init/'vendor_property_contexts').write_bytes(member(OUT/'vendor.img','etc/selinux/vendor_property_contexts'))
    verifier=ROOT/'android/dl/cf_arm64_x/otatools/bin/host_init_verifier'
    run([verifier,'-p',init/'vendor.passwd','--property-contexts='+str(init/'vendor_property_contexts'),'--out_vendor='+str(init)],'init-verify')
    result=dict(host_only=True,hardware_tested=False,boot_repacked=True,boot_byte_identical_to_integ5=True,kernel_rebuilt=False,
                qcom_stats_builtin=True,rpm_dt_unchanged=True,stats_fix='mount debugfs in sleep logger namespace, then read upstream qcom_stats/vlow,vmin',
                radio_start='vendor init queues existing owner at boot; normal owner+prepare-ready gates retained',
                base_vendor=str(base),changed=[path],unchanged_regular_files=unchanged,modules_unchanged=100,
                abi='integ5 kernel unchanged; FIX4 100 modules/6202 imports zero mismatches reused',abl='PASS',fsck='PASS',
                minimum_free_gib=args.min_free_gib,free_bytes=shutil.disk_usage(OUT).free,
                source_commit=sp.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                sha256={n:sha(OUT/n) for n in ['boot.img','vendor.img','Image.gz','integ.dtb','kernel.config']})
    (OUT/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    (OUT/'SHA256SUMS').write_text(''.join(d+'  '+n+'\n' for n,d in result['sha256'].items()))
    print('PASS integ6 boot repack, ABL, one-file vendor change, init verifier and fsck')


if __name__=='__main__':main()
