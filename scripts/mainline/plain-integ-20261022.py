#!/usr/bin/env python3
"""Plain integration: proven R22c/QSEE source + row27's normal stock-timed WDT.
Host build/package only. Uses this worker's existing private incremental cache.
"""
from pathlib import Path
import hashlib, importlib.util, json, os, shutil, struct, subprocess as sp, sys
R=Path(__file__).resolve().parents[2]; ROOT=Path('/home/user/A9-build/mainline')
O=ROOT/'plain-integ-20261022'; B=ROOT/'plain-qseecom-r2-20261022'
CACHE=ROOT/'boot-reliability-20261022/cache/scratch'; K=CACHE/'kernel'; OBJ=CACHE/'obj'
ENV=dict(os.environ,PATH='/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin:'+':'.join(json.loads((ROOT/'demo-integrated-20261019/source.json').read_text())['tools'])+':'+os.environ['PATH'])
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def room(budget=0):
 free=shutil.disk_usage(O).free
 if free<2*1024**3+budget:raise RuntimeError(f'disk: {free} free, preserve 2GiB plus {budget} phase budget')
def run(cmd,name):
 room();print(name,flush=True)
 with (O/(name+'.log')).open('w') as log: rc=sp.call(list(map(str,cmd)),env=ENV,stdout=log,stderr=sp.STDOUT)
 (O/(name+'.rc')).write_text(str(rc)+'\n')
 if rc:raise RuntimeError(f'{name} rc={rc}')
def debug(image,cmd):return sp.check_output(['debugfs','-R',cmd,str(image)],stderr=sp.DEVNULL)
def kernel():
 room(192*1024**2)
 assert sha(OBJ/'arch/arm64/boot/Image.gz')==sha(B/'Image.gz'),'incremental cache is not proven plain QSEE source'
 assert (OBJ/'.config').read_bytes()==(B/'kernel.config').read_bytes()
 assert b'A9_BOOTLOG' not in (K/'init/main.c').read_bytes()
 original=ROOT/'suspend-pmtest-kernel-20261020/kernel/drivers/watchdog/qcom-wdt.c'
 w=K/'drivers/watchdog/qcom-wdt.c'
 if w.is_symlink():w.unlink()
 w.write_bytes(original.read_bytes())
 run(['patch','--batch','--fuzz=0',w,R/'scripts/mainline/suspend/qcom-wdt-stock-a9.patch'],'stock-watchdog-patch')
 assert w.read_bytes()==(ROOT/'row27-user-r22c/qcom-wdt-user.c').read_bytes()
 make=['make','-C',K,'O='+str(OBJ),'ARCH=arm64','CROSS_COMPILE=aarch64-unknown-linux-gnu-',
       'HOSTCC=gcc','HOSTCFLAGS=-I/nix/store/4rw03gwh97laxj6sgf630xdbnynkj43z-openssl-3.5.1-dev/include',
       'HOSTLDFLAGS=-L/nix/store/j09xkbypljdhxwspfiqd8sf64zgxhp52-openssl-3.5.1/lib','LOCALVERSION=+','DTC_FLAGS=-@','-j4','Image.gz']
 run(make,'kernel-build')
 for src,dest in [('arch/arm64/boot/Image.gz','Image.gz'),('.config','kernel.config'),('vmlinux.symvers','vmlinux.symvers')]:shutil.copyfile(OBJ/src,O/dest)
 shutil.copyfile(w,O/'qcom-wdt.c')
 # Config and all existing exported CRCs are unchanged: current modules remain usable.
 assert (O/'kernel.config').read_bytes()==(B/'kernel.config').read_bytes()
 def exports(p):return {r[1]:r[0] for line in p.read_text().splitlines() if len(r:=line.split())>=4}
 old,new=exports(B/'vmlinux.symvers'),exports(O/'vmlinux.symvers')
 bad={n:[v,new.get(n)] for n,v in old.items() if new.get(n)!=v}
 (O/'abi.json').write_text(json.dumps(dict(existing_exports=len(old),mismatches=bad),indent=2)+'\n')
 assert not bad,bad
 # R22c nonkernel boot payloads and both cmdline fields are copied exactly.
 raw=(B/'boot.img').read_bytes();page=struct.unpack_from('<I',raw,36)[0];assert page==4096
 pos=page;blobs=[];fields=(8,16,24,1632,1648)
 for f in fields:
  n=struct.unpack_from('<I',raw,f)[0];blobs.append(raw[pos:pos+n]);pos+=(n+page-1)//page*page
 blobs[0]=(O/'Image.gz').read_bytes();header=bytearray(raw[:page]);body=bytearray();digest=hashlib.sha1()
 for f,data in zip(fields,blobs):
  if f==1632:struct.pack_into('<Q',header,1636,page+len(body))
  struct.pack_into('<I',header,f,len(data));digest.update(data);digest.update(struct.pack('<I',len(data)))
  body+=data;body+=bytes((-len(body))%page)
 header[576:608]=digest.digest()+bytes(12)
 (O/'boot.img').write_bytes(header+body);(O/'recovery_dtbo.img').write_bytes(blobs[3])
 assert header[64:576]==raw[64:576] and header[608:1632]==raw[608:1632]
 (O/'boot-payloads.json').write_text(json.dumps(dict(nonkernel_payloads_identical=True,cmdline_identical=True,config_identical=True,reliability_package=False),indent=2)+'\n')
def package():
 assert not (O/'vendor.img').exists(),'never overwrite a delivered vendor'
 room(500*1024**2)
 changes={}
 # Current source owns the audio closure; never inherit the silent P payload.
 spec=importlib.util.spec_from_file_location('audio_current',R/'scripts/mainline/audio/output/stage-current.py')
 audio=importlib.util.module_from_spec(spec);spec.loader.exec_module(audio)
 audio_overlay=audio.stage(O/'audio',B/'vendor.img')
 changes.update({str(p.relative_to(audio_overlay)):p.read_bytes()
                 for p in audio_overlay.rglob('*') if p.is_file()})
 # Keep P composition plus all phone-proven plain R22d/e modules. No broad rebuild.
 helper=debug(B/'vendor.img','cat /bin/a9ml-usb.sh').decode()
 anchor='# ACM root shell\n'
 assert helper.count(anchor)==1
 helper=helper.replace(anchor,'# Same unconditional HAL handoff as row27-user-r22c-usbfix.\nsetprop sys.usb.configfs 2\n\n'+anchor)
 changes['bin/a9ml-usb.sh']=helper.encode()
 # Normal watchdogd owns the WDT; no test dead-man, host ACK, keeper or lease.
 rc=debug(B/'vendor.img','cat /etc/init/hw/init.hlte556n.rc').decode()
 blocks=re_split(rc)
 rc=''.join(block for block in blocks if not block.startswith('service a9ml_deadman '))
 rc=''.join(line for line in rc.splitlines(True) if 'start a9ml_deadman' not in line and 'dead-man started' not in line)
 rc=rc.replace('PON beacon and original dead-man remain active.','PON beacon is unchanged; watchdogd owns the stock-timed watchdog.')
 rc=rc.replace('Original keeper freshness/timers remain.','No test keeper/dead-man is started by this pair.')
 changes['etc/init/hw/init.hlte556n.rc']=rc.encode()
 changes['bin/a9ml-deadman.sh']=b'#!/system/bin/sh\n# Plain integration: normal watchdogd, no test lease.\nexit 0\n'
 # This is the same GSI watchdogd service, with row27 9+5 arguments. Vendor rc
 # is parsed after system rc; override preserves one owner on either GSI variant.
 changes['etc/init/init.a9-watchdog.rc']=(R/'scripts/mainline/suspend/init.a9-watchdog.rc').read_bytes()
 for p in ['lib/modules/kernel-image.sha256','etc/thermal-a9.kernel-image.sha256']:changes[p]=(sha(O/'Image.gz')+'\n').encode()
 spec=importlib.util.spec_from_file_location('metadata',R/'scripts/mainline/android/refresh-integrated-vendor.py')
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 assert not (O/'vendor.img').exists()
 run(['cp','--sparse=always',B/'vendor.img',O/'vendor.img'],'vendor-copy')
 files=O/'payload';files.mkdir();batch=[];meta={}
 for i,(p,data) in enumerate(changes.items()):
  f=files/str(i);f.write_bytes(data)
  exists=p!='etc/init/init.a9-watchdog.rc'
  mt=m.metadata(B/'vendor.img',p if exists else 'etc/init/hw/init.hlte556n.rc');meta[p]=mt
  lab=files/(str(i)+'.label');lab.write_bytes(mt['label'].encode()+b'\0')
  if exists:batch.append('rm /'+p)
  batch += [f'write {f} /{p}',f'sif /{p} mode 010{mt["mode"]}',f'sif /{p} uid {mt["uid"]}',f'sif /{p} gid {mt["gid"]}',f'ea_set -f {lab} /{p} security.selinux']
  for t in ['atime','mtime','ctime','crtime']:batch.append(f'sif /{p} {t} @{int(mt[t],16)}')
 (O/'install.debugfs').write_text('\n'.join(batch)+'\n');run(['debugfs','-w','-f',O/'install.debugfs',O/'vendor.img'],'vendor-install')
 for p,data in changes.items():
  assert debug(O/'vendor.img','cat /'+p)==data,p
  assert m.metadata(O/'vendor.img',p)==meta[p],p
 run(['e2fsck','-fn',O/'vendor.img'],'vendor-fsck')
 run([R/'scripts/abl-check.sh',O/'boot.img','boot','--dtbo',O/'recovery_dtbo.img','--json'],'abl-boot')
 assert json.loads((O/'abl-boot.log').read_text())['result']=='PASS'
 hashes={n:sha(O/n) for n in ['boot.img','vendor.img','Image.gz']}
 for n in ['boot.img','vendor.img']:(O/(n+'.sha256')).write_text(hashes[n]+'  '+n+'\n')
 (O/'SHA256SUMS').write_text(''.join(v+'  '+n+'\n' for n,v in hashes.items()))
 (O/'result.json').write_text(json.dumps(dict(kernel_rc=0,package_rc=0,base=str(B),changed_vendor_files=list(changes),sha256=hashes,hardware_tested=False,reliability_package=False,free_after=shutil.disk_usage(O).free),indent=2)+'\n')
 print(json.dumps(hashes,indent=2))
def re_split(rc):
 import re
 return re.split(r'(?=^(?:service |on |import ))',rc,flags=re.M)
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=['kernel','package'])
 p.add_argument('--out',type=Path,default=O);p.add_argument('--reuse-boot',type=Path)
 a=p.parse_args();O=a.out.resolve();O.mkdir(exist_ok=True)
 if a.phase=='kernel':kernel()
 else:
  if a.reuse_boot:
   assert not (O/'vendor.img').exists(),'never overwrite a delivered vendor'
   for n in ['boot.img','Image.gz','kernel.config','vmlinux.symvers','recovery_dtbo.img']:
    shutil.copyfile(a.reuse_boot/n,O/n)
  package()
