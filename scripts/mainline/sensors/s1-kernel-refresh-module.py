#!/usr/bin/env python3
"""Compile revised private FastRPC module and refresh receipts, never images.

Uses the completed private S1 object tree. D1 source/objects/images and both S1
image files are read-only inputs. Preserves pre-refresh module/receipt evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess as sp

HERE=Path(__file__).resolve().parent
FLOOR=15_000_000_000
def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
def run(*args,**kw):
    print('+',*map(str,args),flush=True)
    return sp.run(list(map(str,args)),check=True,**kw)
def write_json(path,value):
    temporary=path.with_name(path.name+'.refresh.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n'); temporary.replace(path)
def refresh(kernel,images):
    kernel=kernel.resolve(); images=images.resolve()
    root=Path.home()/'A9-build/mainline/sensors-s1'
    if not kernel.is_relative_to(root) or not images.is_relative_to(root): raise ValueError('private S1 inputs required')
    if shutil.disk_usage('/').free<FLOOR+64_000_000: raise RuntimeError('need 64MB above disk floor')
    kp=kernel/'kernel-receipt.json'; ip=images/'image-receipt.json'
    k=json.loads(kp.read_text()); image=json.loads(ip.read_text())
    if k['status']!='PASS-OFFLINE-KERNEL' or image['kernel_receipt']!=sha(kp): raise ValueError('completed matching receipts required')
    frozen={kernel/'build/arch/arm64/boot/Image.gz':k['Image.gz'],kernel/'build/.config':k['config'],
            kernel/'s1.dtb':k['dtb'],kernel/'kernel.notes':k['kernel_notes'],
            images/'a9-android-eink-boot-gpu.img':image['boot'],
            images/'vendor_b-eink-gpu-bt-health-thermal.img':image['vendor']}
    for path,digest in frozen.items():
        if sha(path)!=digest: raise ValueError(('frozen image/kernel changed',path))
    index=1
    while (kernel/('fastrpc-refresh-drain'+('' if index==1 else '-'+str(index)))).exists(): index+=1
    suffix='' if index==1 else '-'+str(index)
    ext=kernel/('fastrpc-refresh-drain'+suffix); ext.mkdir(mode=0o700)
    (ext/'drivers/misc').mkdir(parents=True)
    d1=Path(k['d1']); src=d1/'linux'; obj=kernel/'build'
    upstream=src/'drivers/misc/fastrpc.c'
    if sha(upstream)!=k['source_anchors'][str(upstream)]: raise ValueError('D1 source changed')
    shutil.copy2(upstream,ext/'drivers/misc/fastrpc.c')
    run('patch','--fuzz=0','-p1','-i',HERE/'s1-kernel-fastrpc-gate.patch',cwd=ext)
    run('git','apply','--unidiff-zero',HERE/'fastrpc-restore-context.patch',cwd=ext)
    shutil.copy2(ext/'drivers/misc/fastrpc.c',ext/'fastrpc.c')
    (ext/'Makefile').write_text('obj-m += fastrpc.o\n')
    run('make','-C',src,'O='+str(obj),'ARCH=arm64','CROSS_COMPILE=aarch64-unknown-linux-gnu-',
        '-j8','M='+str(ext),'modules')
    symbols={f[1]:f[0].lower() for f in (line.split() for line in (obj/'Module.symvers').read_text().splitlines())}
    imports=sp.check_output(['modprobe','--show-modversions',str(ext/'fastrpc.ko')],text=True)
    for line in imports.splitlines():
        crc,name=line.split()
        if symbols.get(name)!=crc.lower(): raise ValueError(('new module import mismatch',name))
    parameters=sp.check_output(['modinfo','-F','parm',str(ext/'fastrpc.ko')],text=True)
    if 'a9_sensor_fastrpc:' not in parameters or 'a9_sensor_drain_pending:' not in parameters:
        raise ValueError('default-off and read-only drain observation parameters required')
    for path,digest in frozen.items():
        if sha(path)!=digest: raise ValueError(('module compile changed frozen bytes',path))
    # Preserve old evidence. No stale component hash is silently discarded.
    previous_kernel=kernel/('kernel-receipt.pre-drain'+suffix+'.json')
    previous_image=images/('image-receipt.pre-drain'+suffix+'.json')
    shutil.copy2(kp,previous_kernel)
    shutil.copy2(ip,previous_image)
    old_module=kernel/'fastrpc-module/fastrpc.ko'
    shutil.copy2(old_module,kernel/('fastrpc-module/fastrpc.pre-drain'+suffix+'.ko'))
    shutil.copy2(ext/'fastrpc.ko',old_module)
    shutil.copy2(ext/'fastrpc.c',kernel/'fastrpc-module/fastrpc.c')
    k.update({'fastrpc.ko':sha(old_module),'fastrpc_source':sha(ext/'fastrpc.c'),
              'gate_patch':sha(HERE/'s1-kernel-fastrpc-gate.patch'),'restore_patch':sha(HERE/'fastrpc-restore-context.patch'),'fastrpc_imports':imports,
              'module_refresh':{'status':'PASS-COMPILED-DRAIN-GATED-MODULE',
                'previous_kernel_receipt':sha(previous_kernel),
                'producer':sha(Path(__file__).resolve()),'parameters':parameters,
                'ioctls':'INIT_ATTACH_SNS and INVOKE with all fd=-1 only',
                'module_lifetime':'gate=1 pins module until reboot',
                'drain':'Y until all counted DMA buffers freed and deferred CB depopulate returned',
                'image_bytes':'UNCHANGED','hardware_result':'NOT_RUN'}})
    write_json(kp,k)
    image.update({'kernel_receipt':sha(kp),'fastrpc_module':k['fastrpc.ko'],
                  'execution_admission':'NO_GO_INDEPENDENT_REVIEW_REQUIRED',
                  'module_refresh':{'previous_image_receipt':sha(previous_image),
                                    'image_bytes':'UNCHANGED','gate_patch':k['gate_patch']}})
    write_json(ip,image)
    if shutil.disk_usage('/').free<FLOOR: raise RuntimeError('disk floor breached')
    print('S1 MODULE REFRESH PASS; image bytes unchanged; independent review required')
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('kernel',type=Path); ap.add_argument('images',type=Path); a=ap.parse_args()
    refresh(a.kernel,a.images)
