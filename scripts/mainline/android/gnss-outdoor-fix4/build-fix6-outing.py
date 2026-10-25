#!/usr/bin/env python3
"""Offline FIX6 outing addendum: UART wake polarity and deadman allowance."""
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
BASE=Path('/home/user/A9-build/mainline/integ-20261023-6')
OUT=BASE/'outing-fix'
DTC='/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin/'
NODE='/soc@0/geniqup@4ac0000/serial@4a8c000'


def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--min-free-gib',type=int,default=15)
    args=ap.parse_args()
    assert args.min_free_gib>0
    os.umask(0o077)
    assert not OUT.exists(),'candidate already exists; preserve its evidence'
    assert sha(BASE/'boot.img')=='c1545932af49fe0753c0cad65e8c2e883b7a63c4d0483255500083d52bd4fdd9'
    assert sha(BASE/'vendor.img')=='9f0a37849cecd1e790026d4c7afa007085ec9d22c6d3552b16bc1309feb9c5ce'
    assert not sp.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=REPO),'freeze source first'
    reserve=args.min_free_gib*1024**3
    assert shutil.disk_usage(BASE).free>=reserve+(BASE/'vendor.img').stat().st_blocks*512+64*1024**2,'disk reserve'
    OUT.mkdir()
    def run(cmd,name):
        with (OUT/(name+'.log')).open('wb') as log:
            sp.run(list(map(str,cmd)),check=True,stdout=log,stderr=sp.STDOUT,cwd=REPO)
        assert shutil.disk_usage(OUT).free>=reserve,'disk reserve'
    raw=(BASE/'boot.img').read_bytes();page=struct.unpack_from('<I',raw,36)[0]
    fields=(8,16,24,1632,1648);pos=page;blobs=[]
    for field in fields:
        size=struct.unpack_from('<I',raw,field)[0]
        blobs.append(raw[pos:pos+size]);pos+=(size+page-1)//page*page
    cmdline=raw[64:576].split(b'\0')[0]+b' '+raw[608:1632].split(b'\0')[0]
    assert not any(x.startswith(b'a9ml.hold=') for x in cmdline.split()),'boot overrides vendor hold'
    dt=OUT/'integ.dtb';dt.write_bytes(blobs[4])
    old=sp.check_output([DTC+'fdtget','-tx',str(dt),NODE,'interrupts-extended'],text=True).split()
    assert old==['1','0','14a','4','27','b','4']
    run([DTC+'fdtput','-tx',dt,NODE,'interrupts-extended',*old[:-1],'2'],'dt-wake')
    patched=dt.read_bytes()
    # Exactly one byte in the whole DT changes. This also preserves private
    # properties without exposing them in a decompiled DT or a build log.
    assert len(patched)==len(blobs[4])
    assert [(a,b) for a,b in zip(blobs[4],patched) if a!=b]==[(4,2)]
    blobs[4]=patched
    header=bytearray(raw[:page]);body=bytearray();digest=hashlib.sha1()
    for field,blob in zip(fields,blobs):
        if field==1632:struct.pack_into('<Q',header,1636,page+len(body))
        struct.pack_into('<I',header,field,len(blob))
        digest.update(blob);digest.update(struct.pack('<I',len(blob)))
        body+=blob;body+=bytes(-len(body)%page)
    header[576:608]=digest.digest()+bytes(12)
    (OUT/'boot.img').write_bytes(header+body)
    for field in [8,16,24,1632]:
        assert struct.unpack_from('<I',raw,field)[0]==struct.unpack_from('<I',header,field)[0]
    (OUT/'recovery_dtbo.img').write_bytes(blobs[3])
    run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3','-P',REPO/'scripts/abl-check.py',OUT/'boot.img','boot','--dtbo',OUT/'recovery_dtbo.img','--json'],'abl-check')
    assert json.loads((OUT/'abl-check.log').read_text())['result']=='PASS'
    spec=importlib.util.spec_from_file_location('meta_outing',REPO/'scripts/mainline/android/refresh-integrated-vendor.py')
    meta=importlib.util.module_from_spec(spec);spec.loader.exec_module(meta)
    def member(image,path):return sp.check_output(['debugfs','-R','cat /'+path,str(image)],stderr=sp.DEVNULL)
    path='bin/a9ml-deadman.sh';before=member(BASE/'vendor.img',path)
    assert before.count(b'HOLD=$(flag a9ml.hold 2700)')==1
    after=before.replace(b'HOLD=$(flag a9ml.hold 2700)',b'HOLD=$(flag a9ml.hold 5400)')
    assert b'MAXSTAY=$(flag a9ml.maxstay 7200)' in after
    script=OUT/'a9ml-deadman.sh';script.write_bytes(after)
    # Syntax check only: never execute the deadman on the host.
    run(['sh','-n',script],'deadman-syntax')
    run(['cp','--reflink=auto','--sparse=always',BASE/'vendor.img',OUT/'vendor.img'],'vendor-copy')
    info=meta.metadata(BASE/'vendor.img',path)
    label=OUT/'label';label.write_bytes(info['label'].encode()+b'\0')
    batch=[f'rm /{path}',f'write {script} /{path}',f'sif /{path} mode 010{info["mode"]}',f'sif /{path} uid {info["uid"]}',f'sif /{path} gid {info["gid"]}',f'ea_set -f {label} /{path} security.selinux']
    batch.extend(f'sif /{path} {key} @{int(info[key],16)}' for key in ['atime','mtime','ctime','crtime'])
    (OUT/'install.debugfs').write_text('\n'.join(batch)+'\n')
    run(['debugfs','-w','-f',OUT/'install.debugfs',OUT/'vendor.img'],'vendor-install')
    assert member(OUT/'vendor.img',path)==after
    assert meta.metadata(OUT/'vendor.img',path)==info
    unchanged=0
    for name in meta.vendor_files(BASE/'vendor.img'):
        if name==path:continue
        assert member(BASE/'vendor.img',name)==member(OUT/'vendor.img',name),name
        unchanged+=1
    run(['e2fsck','-fn',OUT/'vendor.img'],'vendor-fsck')
    prior=json.loads((BASE/'integrated-build.json').read_text())
    result=dict(host_only=True,phone_access=False,hardware_tested=False,
                source_commit=sp.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
                base_boot_sha256=sha(BASE/'boot.img'),base_vendor_sha256=sha(BASE/'vendor.img'),
                dt_changed='UART RX GPIO11 IRQ_TYPE_LEVEL_HIGH -> IRQ_TYPE_EDGE_FALLING; one DT byte',
                stock_source='https://android.googlesource.com/kernel/msm/+/f31097e7182e763ee7debc98b40e9dc09f432785/drivers/tty/serial/msm_geni_serial.c',
                kernel_rebuilt=False,kernel_unchanged=True,ramdisk_unchanged=True,recovery_dtbo_unchanged=True,
                modules_unchanged=100,abi=prior['abi'],boot_radio_init_unchanged=True,
                changed_vendor_files=[path],unchanged_regular_files=unchanged,deadman_hold_seconds=5400,
                deadman_maxstay_seconds=7200,host_hold_seconds=4200,replug_seconds=1200,quiet_seconds=1800,
                abl='PASS',fsck='PASS',minimum_free_gib=args.min_free_gib,free_bytes=shutil.disk_usage(OUT).free,
                sha256={n:sha(OUT/n) for n in ['boot.img','vendor.img','integ.dtb','recovery_dtbo.img']})
    (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS FIX6 outing images: one DT byte, one deadman default, ABL/fsck; all other payloads retained')


if __name__=='__main__':main()
