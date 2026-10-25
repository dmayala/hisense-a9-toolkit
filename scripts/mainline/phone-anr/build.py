#!/usr/bin/env python3
"""Patch the pinned v8 framework, optionally in a private raw system image copy."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess as sp
import zipfile

HERE=Path(__file__).resolve().parent
BUILD=Path.home()/'A9-build'
JAVA=Path('/nix/store/pkhdb9invwiad9c8qvbzhkyvl4yblwd6-openjdk-17.0.16+8/bin/java')
GHIDRA=BUILD/'tools/ghidra-12.1.4-x/ghidra_12.1.4_PUBLIC/Ghidra'
CLASSPATH=':'.join(str(p) for p in (GHIDRA/'Features/FileFormats/lib/dexlib2-2.5.2.jar',GHIDRA/'Framework/Generic/lib/guava-32.1.3-jre.jar'))
PIN='a8865bcc540f12176d7ec54b8ea40e40999392e48cf0945b6e52b3cd1c0f581b'
MEMBER='system/framework/services.jar'

def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def run(args,log):
    with log.open('w') as f:sp.run(list(map(str,args)),stdout=f,stderr=sp.STDOUT,check=True,timeout=180)

def metadata(image):
    text=sp.check_output(['debugfs','-R','stat /'+MEMBER,str(image)],stderr=sp.DEVNULL,text=True)
    if 'Type: regular' not in text or 'security.capability' in text:
        raise ValueError('expected existing regular framework jar without capabilities')
    result={k:re.search(pattern,text)[1] for k,pattern in dict(mode=r'Mode:\s+([0-7]+)',
        uid=r'User:\s+(\d+)',gid=r'Group:\s+(\d+)',label=r'security.selinux \(\d+\) = "([^"\\]+)\\000"').items()}
    for key in ('atime','mtime','ctime','crtime'):
        m=re.search(r'\b'+key+r':\s+0x([0-9a-f]+):([0-9a-f]+)',text)
        result[key]=[int(m[1],16),int(m[2],16)]
    return result

def install_system(image,target,work):
    before=metadata(image)
    batch=[f'rm /{MEMBER}',f'write {target.resolve()} /{MEMBER}',
           f'sif /{MEMBER} mode 010{before["mode"]}']
    for key in ('uid','gid'):batch.append(f'sif /{MEMBER} {key} {before[key]}')
    label=work/'services.label';label.write_bytes(before['label'].encode()+b'\0')
    batch.append(f'ea_set -f {label.resolve()} /{MEMBER} security.selinux')
    # The GSI carries nonzero nanoseconds, unlike the vendor staging fixtures.
    for key in ('atime','mtime','ctime','crtime'):
        seconds,extra=before[key]
        batch.extend([f'sif /{MEMBER} {key} @{seconds}',f'sif /{MEMBER} {key}_extra {extra}'])
    for arch in ('arm','arm64'):
        for suffix in ('art','odex','vdex'):
            batch.append(f'rm /system/framework/oat/{arch}/services.{suffix}')
    script=work/'install.debugfs';script.write_text('\n'.join(batch)+'\n')
    run(['debugfs','-w','-f',script,image],work/'install.log')
    check=sp.check_output(['debugfs','-R','cat /'+MEMBER,str(image)],stderr=sp.DEVNULL)
    if hashlib.sha256(check).hexdigest()!=sha(target):raise ValueError('system jar readback differs')
    if metadata(image)!=before:raise ValueError('system jar metadata differs')
    run(['e2fsck','-fn',image],work/'fsck.log')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    source=ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--jar',type=Path)
    source.add_argument('--system',type=Path,help='raw ext4 system-as-root image; copied, never edited in place')
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    if a.out.exists():ap.error('preserve earlier outputs; select a fresh --out')
    if a.system and shutil.disk_usage(a.out.parent).free < a.system.stat().st_size+15*1024**3:
        ap.error('system image copy would breach the 15 GiB free-space floor')
    a.out.mkdir(parents=True)
    original=a.out/'original-services.jar'
    if a.system:
        with original.open('wb') as f:sp.run(['debugfs','-R','cat /'+MEMBER,str(a.system)],stdout=f,stderr=sp.DEVNULL,check=True)
    else:shutil.copyfile(a.jar,original)
    if sha(original)!=PIN:raise ValueError('unsupported services.jar; inspect the new framework before porting the patch')
    dex=a.out/'classes.dex'
    run([JAVA,'-cp',CLASSPATH,HERE/'PatchServices.java',original,dex],a.out/'patch.log')
    target=a.out/'services.jar'
    with zipfile.ZipFile(original) as src,zipfile.ZipFile(target,'w') as dst:
        for item in src.infolist():dst.writestr(item,dex.read_bytes() if item.filename=='classes.dex' else src.read(item.filename))
    record=dict(input_jar_sha256=PIN,output_jar_sha256=sha(target),host_only=True,
                phone_tested=False,cleared_messages=[82,83],preserved_provider_timeout=57,
                sources={p.name:sha(p) for p in (HERE/'build.py',HERE/'PatchServices.java')})
    if a.system:
        record['input_system_sha256']=sha(a.system)
        image=a.out/'system.img';shutil.copyfile(a.system,image)
        install_system(image,target,a.out)
        record['system_sha256']=sha(image)
    (a.out/'result.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))

if __name__=='__main__':main()
