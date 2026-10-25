#!/usr/bin/env python3
"""Pack/read back actual S1 boot + paired vendor, offline and gates closed.

Reuses measured D1 ramdisk and vendor modules. Only boot kernel/DT and the two
vendor kernel-pairing hashes change. FastRPC remains a private gated sidecar.
This tool never invokes a device transport or android-boot.sh.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess as sp

PAIR=('/etc/thermal-a9.kernel-image.sha256','/lib/modules/kernel-image.sha256')
FLOOR=15_000_000_000
def sha(p): return hashlib.file_digest(Path(p).open('rb'),'sha256').hexdigest()
def run(*args,**kw):
    print('+',*map(str,args),flush=True)
    return sp.run(list(map(str,args)),check=True,**kw)
def read_vendor(image,name):
    return run('debugfs','-R','cat '+name,image,capture_output=True).stdout
def pack(kernel,d1,out):
    kernel=kernel.resolve(); d1=d1.resolve(); out=out.resolve()
    if out.exists() or 'a9-assets' in out.parts or not out.is_relative_to(Path.home()/'A9-build'):
        raise ValueError('fresh private ~/A9-build output required')
    rec=json.loads((kernel/'kernel-receipt.json').read_text())
    if rec['status']!='PASS-OFFLINE-KERNEL': raise ValueError('kernel compilation not complete')
    image=kernel/'build/arch/arm64/boot/Image.gz'; dtb=kernel/'s1.dtb'
    if sha(image)!=rec['Image.gz'] or sha(dtb)!=rec['dtb']: raise ValueError('kernel receipt mismatch')
    oldboot=d1/'android/out/a9-android-eink-boot-gpu.img'
    oldvendor=d1/'android/out/vendor_b-eink-gpu-bt-health-thermal.img'
    required=oldvendor.stat().st_size*2+oldboot.stat().st_size*3+32_000_000
    if shutil.disk_usage('/').free<FLOOR+required: raise RuntimeError('insufficient space above 15GB floor for image outputs')
    header=oldboot.read_bytes()[:4096]
    fields=struct.unpack_from('<10I',header,8)
    if header[:8]!=b'ANDROID!' or fields[1]!=0x8000 or fields[3]!=0x1000000 or fields[4]!=0 or fields[6]!=0x100 or fields[7]!=4096 or fields[8]!=2:
        raise ValueError('unexpected D1 boot layout; no guessed repack')
    if struct.unpack_from('<Q',header,1652)[0]!=0x1f00000: raise ValueError('unexpected D1 DTB address')
    cmdline=header[64:576].split(b'\0',1)[0].decode('ascii')
    if header[608:1632].strip(b'\0') or len(cmdline)>511: raise ValueError('unexpected extra command line')
    version=fields[9]
    if version!=((16<<25)|(1<<18)|((2099-2000)<<4)|12): raise ValueError('unexpected D1 OS version/patch level')
    out.mkdir(parents=True,mode=0o700); unpack=out/'d1-readback'; unpack.mkdir()
    run('unpack_bootimg','--boot_img',oldboot,'--out',unpack,capture_output=True)
    oldkernel=sha(unpack/'kernel'); newkernel=sha(image)
    for name in PAIR:
        if read_vendor(oldvendor,name).decode().strip()!=oldkernel: raise ValueError(('D1 vendor kernel pairing mismatch',name))
    boot=out/'a9-android-eink-boot-gpu.img'
    run('mkbootimg','--header_version','2','--pagesize','4096','--base','0',
        '--kernel_offset','0x8000','--ramdisk_offset','0x1000000','--tags_offset','0x100',
        '--second_offset','0','--dtb_offset','0x1f00000','--os_version','16.1.0','--os_patch_level','2099-12',
        '--kernel',image,'--ramdisk',unpack/'ramdisk','--dtb',dtb,
        '--recovery_dtbo',unpack/'recovery_dtbo','--cmdline',cmdline,'-o',boot)
    vendor=out/oldvendor.name; shutil.copy2(oldvendor,vendor)
    pairfile=out/'kernel-image.sha256'; pairfile.write_text(newkernel+'\n')
    # Writes target only the newly created private vendor image, never a block
    # device, mounted filesystem, golden image or source D1 image.
    for name in PAIR:
        run('debugfs','-w','-R','rm '+name,vendor,capture_output=True)
        run('debugfs','-w','-R','write '+str(pairfile)+' '+name,vendor,capture_output=True)
        if read_vendor(vendor,name).decode().strip()!=newkernel: raise ValueError(('new vendor pairing mismatch',name))
    packed=out/'s1-readback'; packed.mkdir()
    run('unpack_bootimg','--boot_img',boot,'--out',packed,capture_output=True)
    expected={'kernel':image,'dtb':dtb,'ramdisk':unpack/'ramdisk','recovery_dtbo':unpack/'recovery_dtbo'}
    for name,source in expected.items():
        if sha(packed/name)!=sha(source): raise ValueError(('packed section mismatch',name))
    # Full packed vendor inventory, not a claim inferred from debugfs commands.
    expected_vendor=json.loads((d1/'packaged-readback.json').read_text())['vendor_inventory']
    for name in PAIR: expected_vendor[name.removeprefix('/')]={'sha256':sha(pairfile)}
    vendor_root=out/'vendor-readback'; vendor_root.mkdir()
    run('debugfs','-R','rdump / '+str(vendor_root),vendor,capture_output=True)
    actual_vendor={str(p.relative_to(vendor_root)):({'symlink':str(p.readlink())} if p.is_symlink() else {'sha256':sha(p)})
                   for p in sorted(vendor_root.rglob('*')) if p.is_symlink() or p.is_file()}
    actual_vendor={name:value for name,value in actual_vendor.items() if name!='lost+found' and not name.startswith('lost+found/')}
    if actual_vendor!=expected_vendor: raise ValueError('packed vendor differs beyond the two kernel-pairing files')
    if boot.stat().st_size>96*1024*1024: raise ValueError('boot image exceeds boot_b')
    if shutil.disk_usage('/').free<FLOOR: raise RuntimeError('disk floor breached')
    for p in (boot,vendor): (out/(p.name+'.sha256')).write_text(sha(p)+'  '+p.name+'\n')
    (out/'image-receipt.json').write_text(json.dumps({'status':'PASS-OFFLINE-IMAGE-READBACK',
        'kernel_receipt':sha(kernel/'kernel-receipt.json'),'d1_boot':sha(oldboot),'d1_vendor':sha(oldvendor),
        'boot':sha(boot),'vendor':sha(vendor),'sections':{name:sha(p) for name,p in expected.items()},
        'vendor_changed_files':list(PAIR),'kernel_config':rec['config'],'fastrpc_module':rec['fastrpc.ko'],
        'vendor_inventory':actual_vendor,
        'cmdline':cmdline,'device_contact':False,'gates':'CLOSED','execution_admission':'NO_GO_PENDING_LISTENER'},indent=2)+'\n')
    print('S1 ACTUAL IMAGES READBACK PASS',out)
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('kernel',type=Path); ap.add_argument('output',type=Path)
    ap.add_argument('--d1',type=Path,default=Path.home()/'A9-build/mainline/adsp/d1-20261017')
    a=ap.parse_args(); pack(a.kernel,a.d1,a.output)
if __name__=='__main__': main()
