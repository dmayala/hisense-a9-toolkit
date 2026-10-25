#!/usr/bin/env python3
"""Create a hash-bound private D2 sidecar (derived from make-bundle.py), never install it or admit a phone."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess as sp
from importlib.machinery import SourceFileLoader

HERE=Path(__file__).resolve().parent
build=SourceFileLoader('build_d2',str(HERE/'build-d2.py')).load_module()
dt=SourceFileLoader('dt_d0',str(HERE/'check-d0-dt.py')).load_module()  # read_fdt only
sha=build.sha
closure=SourceFileLoader('d2_bundle_module_closure',str(HERE/'check-d2-modules.py')).load_module()

def system_binding(out,audit):
    """Bind the audit's actual system bytes to one verified raw system image."""
    receipt=json.loads((out/'system-image.json').read_text())
    assert receipt['status']=='PASS-READONLY-SYSTEM-BINDING'
    assert sha(out/'system-files.json')==receipt['files_sha256'], 'system inventory changed'
    assert sha(out/'system-image.manifest.tsv')==receipt['manifest_sha256'], 'system manifest changed'
    image=Path(receipt['image']).resolve(strict=True)
    assert image.is_relative_to(build.W.parent), 'system image must stay local under A9-build'
    assert image.stat().st_size==receipt['image_bytes'] and sha(image)==receipt['image_sha256'], 'system image changed'
    expected=json.loads((out/'system-files.json').read_text())
    actual={'/'+name.removeprefix('system-image/'):rec for name,rec in audit['files'].items()
            if name.startswith('system-image/') and '!payload/' not in name}
    assert actual==expected, 'audited system differs from image-bound inventory'
    return receipt

def make(out,services):
    build.disk_gate(reserve=100_000_000)
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
    system=system_binding(out,s)
    snap=out/'snapshot'
    closure.check(snap)
    b=out/'bundle'; b.mkdir()
    tone=json.loads((out/'d2-tone-build.json').read_text())
    assert tone['binary_sha256']==sha(out/'d2-tone'), 'helper differs from its build receipt'
    assert tone['source_sha256']==sha(HERE/'d2-tone.c'), 'helper source changed'
    assert tone['test_sha256']==sha(build.REPO/'tests/mainline-adsp/test-d2-tone.c'), 'helper tests changed'
    shutil.copytree(snap/'d2/modules',b/'modules')
    shutil.copy2(snap/'d2/modules.d2',b/'modules.d2')
    shutil.copy2(snap/'kernel.release',b/'kernel.release')
    shutil.copy2(HERE/'d2-target.sh',b/'d2-target.sh')
    shutil.copytree(out/'firmware',b/'firmware')
    shutil.copy2(out/'d2-tone',b/'d2-tone')
    shutil.copy2(out/'d2-tone-build.json',b/'receipts-tone.json')
    # Controller/link numbers can depend on hardware/probe ordering. The target
    # also binds each canonical static part/instance to its exact DT OF node.
    (b/'soundwire.devices').write_text('0217:010a:00:3\n0217:010a:00:4\n')
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
    # D0 attempt-3 corrections (review 63f), carried into D2 (review 72 finding 1):
    # v7/v8 rw-system.sh bind-mounts an EMPTY file over these three at boot (expectation = empty
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
    for name in ('system-image.json','system-files.json','system-image.manifest.tsv'):
        shutil.copy2(out/name,b/name)
    (b/'receipts').mkdir()
    for name in ('build.json','module-crcs.json','images.json','freeze-inputs.json','packaged-readback.json'):
        shutil.copy2(out/name,b/'receipts'/name)
    shutil.copy2(snap/'SHA256SUMS',b/'receipts/snapshot.SHA256SUMS')
    # Bind the final committed preparation tools as well as the older kernel
    # source anchor recorded while the long build was running.
    sp.run(['git','-C',str(build.REPO),'diff','--quiet'],check=True)
    (b/'receipts/repo.json').write_text(json.dumps({
        'prep_commit':sp.check_output(['git','-C',str(build.REPO),'rev-parse','HEAD'],text=True).strip(),
        'd1_prep_commit':'1cf88a9381e4e1728c49ab78eee8ca0a91a6176e',
        'kernel_base':build.KHEAD},indent=2)+'\n')
    (b/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(b))+'\n' for p in sorted(b.rglob('*')) if p.is_file()))
    t=sha(b/'SHA256SUMS'); (b/'tuple').write_text(t+'\n')
    # This is intentionally not executable admission. An independent reviewer
    # must accept findings, fallback ownership and this exact manifest first.
    (out/'admission.pending.json').write_text(json.dumps({'tuple':t,
        'frozen_manifest_sha256':t,'stage':'D2',
        'system_b_sha256':system['image_sha256'],
        'd1_pass_evidence':'PASS-ATTENDED-D1; run.log sha256 07ea498492e5278fad7c5f2a3e5740a52ba90195fbd8027fd92a473b8e8ba538',
        'independent_review':'PENDING','operator_present':False,
        'output_path':'internal earpiece; headphone jack empty',
        'tone':{'rate':48000,'channels':1,'format':'S16_LE','hz':1000,'frames':48000,'peak':1036},
        'listening_result':'PENDING',
        'service_admission':'PENDING','fallback_owner':'PENDING',
        'root_transport':'loopback',
        'fallback':'attended PON2/Power then golden restore','rehearsal':False},indent=2)+'\n')
    print('PRIVATE BUNDLE',t)

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('output',type=Path); ap.add_argument('--services',required=True,type=Path)
    a=ap.parse_args(); make(a.output,a.services)
