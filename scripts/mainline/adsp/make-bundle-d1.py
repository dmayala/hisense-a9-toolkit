#!/usr/bin/env python3
"""Create a hash-bound private D1 sidecar (derived from make-bundle.py), never install it or admit a phone."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess as sp
from importlib.machinery import SourceFileLoader

HERE=Path(__file__).resolve().parent
build=SourceFileLoader('build_d1',str(HERE/'build-d1.py')).load_module()
dt=SourceFileLoader('dt_d0',str(HERE/'check-d0-dt.py')).load_module()  # read_fdt only
sha=build.sha

def make(out,services):
    assert out.resolve().parent == build.W/'adsp'
    s=json.loads(services.read_text())
    assert s['status']=='REVIEW-REQUIRED' and not s['failures']
    admission=out/'service-admission.json'
    author=json.loads(admission.read_text())
    assert author['status']=='PASS-AUTHOR-STATIC'
    assert author['audit_sha256']==sha(services)
    packed=json.loads((out/'packaged-readback.json').read_text())
    assert packed['status']=='PASS'
    assert packed['boot_sha256']==sha(out/'android/out/a9-android-eink-boot-gpu.img')
    assert packed['vendor_sha256']==sha(out/'android/out/vendor_b-eink-gpu-bt-health-thermal.img')
    audited_vendor={name.removeprefix('vendor/'):({'symlink':rec['symlink']} if 'symlink' in rec else {'sha256':rec['sha256']})
                    for name,rec in s['files'].items() if name.startswith('vendor/') and '!payload/' not in name}
    assert audited_vendor==packed['vendor_inventory'], 'audited vendor != actual packed vendor'
    b=out/'bundle'; b.mkdir()
    snap=out/'snapshot'
    shutil.copytree(snap/'d1/modules',b/'modules')
    shutil.copy2(snap/'d1/modules.d1',b/'modules.d1')
    shutil.copy2(snap/'kernel.release',b/'kernel.release')
    shutil.copy2(HERE/'d1-target.sh',b/'d1-target.sh')
    shutil.copytree(out/'firmware',b/'firmware')
    (b/'config.sha256').write_text(sha(snap/'config')+'\n')
    boot=out/'android/out/a9-android-eink-boot-gpu.img'
    (b/'boot.bytes').write_text(str(boot.stat().st_size)+'\n')
    (b/'boot.sha256').write_text(sha(boot)+'\n')
    baseline=set()
    for name in ('modules.load','modules.bluetooth'):
        p=snap/'modules'/name
        if p.exists(): baseline.update(x.removesuffix('.ko').replace('-','_') for x in p.read_text().splitlines() if x)
    (b/'baseline.modules').write_text(''.join(x+'\n' for x in sorted(baseline)))
    # Require all installed regular files in all admitted namespaces. Symlinks
    # are checked separately, including root/system_ext/product/odm aliases.
    # D0 attempt-3 corrections (review 63f), carried into D1 (review 72 finding 1):
    # v7 rw-system.sh bind-mounts an EMPTY file over these three at boot (expectation = empty
    # file hash, still fail-closed); the ABL rewrites memory@80000000/reg (RAM size).
    EMPTY=hashlib.sha256(b'').hexdigest(); seen=set()
    MASKED={'/system/etc/permissions/android.hardware.bluetooth.xml','/system/etc/permissions/android.hardware.bluetooth_le.xml','/system/etc/permissions/android.hardware.fingerprint.xml'}
    ABL_FIXED={'/sys/firmware/devicetree/base/memory@80000000/reg'}
    live=[]; links=[]
    for name,rec in sorted(s['files'].items()):
        if '!payload/' in name: continue # packaged APEX bytes bind its payload
        prefix,rel=name.split('/',1)
        if prefix=='system-image': path='/'+rel
        elif prefix=='vendor': path='/vendor/'+rel
        else: continue # first-stage ramdisk bound by boot bytes + kernel notes
        if 'symlink' in rec: links.append(path+' '+rec['symlink']+'\n')
        elif path in MASKED: live.append(EMPTY+'  '+path+'\n'); seen.add(path)
        else: live.append(rec['sha256']+'  '+path+'\n')
    nodes,_=dt.read_fdt(snap/'sm6115-hisense-hlte556n-android-gpu.dtb')
    for path,props in sorted(nodes.items()):
        if path.startswith(('/chosen','/__symbols__')): continue
        for name,data in sorted(props.items()):
            p2='/sys/firmware/devicetree/base'+path.rstrip('/')+'/'+name
            if p2 in ABL_FIXED: seen.add(p2); continue
            live.append(hashlib.sha256(data).hexdigest()+'  '+p2+'\n')
    assert seen==MASKED|ABL_FIXED,(seen,'D0 live-manifest corrections not applicable: review')
    for p in sorted((out/'firmware').rglob('*')):
        if p.is_file() and p.name!='firmware.json':
            live.append(sha(p)+'  /data/local/tmp/a9fw/'+str(p.relative_to(out/'firmware'))+'\n')
    notes=out/'kernel.notes'
    sp.run(['aarch64-unknown-linux-gnu-objcopy','--dump-section','.notes='+str(notes),
            '--only-section','.notes',str(out/'build/vmlinux'),str(out/'kernel-notes.elf')],check=True)
    live.append(sha(notes)+'  /sys/kernel/notes\n')
    (b/'live.sha256').write_text(''.join(live))
    (b/'live.symlinks').write_text(''.join(links))
    shutil.copy2(services,b/'service-audit.json')
    shutil.copy2(admission,b/'service-admission.json')
    (b/'receipts').mkdir()
    for name in ('build.json','module-crcs.json','images.json','freeze-inputs.json','packaged-readback.json'):
        shutil.copy2(out/name,b/'receipts'/name)
    shutil.copy2(snap/'SHA256SUMS',b/'receipts/snapshot.SHA256SUMS')
    # Bind the final committed preparation tools as well as the older kernel
    # source anchor recorded while the long build was running.
    sp.run(['git','-C',str(build.REPO),'diff','--quiet'],check=True)
    (b/'receipts/repo.json').write_text(json.dumps({
        'prep_commit':sp.check_output(['git','-C',str(build.REPO),'rev-parse','HEAD'],text=True).strip(),
        'main_base':'db8bad50a35dd63ffe4538eeb025e987e7d381f3',
        'kernel_base':build.KHEAD},indent=2)+'\n')
    (b/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(b))+'\n' for p in sorted(b.rglob('*')) if p.is_file()))
    t=sha(b/'SHA256SUMS'); (b/'tuple').write_text(t+'\n')
    # This is intentionally not executable admission. An independent reviewer
    # must accept findings, fallback ownership and this exact manifest first.
    (out/'admission.pending.json').write_text(json.dumps({'tuple':t,
        'frozen_manifest_sha256':t,'stage':'D1','d0_pass_evidence':'PENDING',
        'independent_review':'PENDING',
        'service_admission':'PENDING','fallback_owner':'PENDING',
        'root_transport':'loopback',
        'fallback':'attended PON2/Power then golden restore','rehearsal':False},indent=2)+'\n')
    print('PRIVATE BUNDLE',t)

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('output',type=Path); ap.add_argument('--services',required=True,type=Path)
    a=ap.parse_args(); make(a.output,a.services)
