#!/usr/bin/env python3
"""Service scanner must cover nested roots and refuse opaque APEX payloads."""
import json
import random
from pathlib import Path
import subprocess
import sys
import tempfile
import importlib.util
import copy

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/mainline/adsp/audit-services.py'
spec=importlib.util.spec_from_file_location('audit',SCRIPT)
audit=importlib.util.module_from_spec(spec); spec.loader.exec_module(audit)
rng=random.Random(0)
tokens=(b'remoteproc',b'RPROC_BOOT',b'pd-mapper',b'protection_domain',b'fastrpc',
        b'qcom_q6v5',b'insmod',b'modprobe',b'load_modules',b'/sys/'+b'x'*70+b'/recovery')
for i in range(1000):
    data=rng.randbytes(rng.randrange(200))+rng.choice(tokens)+rng.randbytes(rng.randrange(200))
    expected={(m.start(),m.end()) for m in audit.RISK.finditer(data)}
    assert expected <= set(audit.risk_matches(data)), 'optimized scanner dropped a risk match'
with tempfile.TemporaryDirectory(prefix='adsp-services-') as td:
    root=Path(td)
    for name in ('system','vendor','ramdisk'): (root/name).mkdir()
    p=root/'system/system_ext/etc/init'; p.mkdir(parents=True)
    (p/'bad.rc').write_text('on boot\n    write /sys/class/remoteproc/remoteproc0/state start\n')
    (root/'vendor/mapper').write_bytes(b'\x7fELF\0PD-MAPPER\0')
    (root/'ramdisk/init.rc').write_text('import /vendor/etc/init/hw/init.${ro.hardware}.rc\n')
    def run(out):
        return subprocess.run([sys.executable,'-I',str(SCRIPT),'--system',str(root/'system'),
            '--vendor',str(root/'vendor'),'--ramdisk',str(root/'ramdisk'),'--output',str(out)],capture_output=True,timeout=20)
    assert run(root/'ok').returncode==0
    r=json.loads((root/'ok/services.json').read_text())
    assert len(r['files'])==3 and len(r['findings'])==2 and len(r['imports'])==1
    assert r['status']=='REVIEW-REQUIRED'
    (root/'system/broken.apex').write_bytes(b'uninspectable APEX')
    assert run(root/'bad').returncode!=0
    r=json.loads((root/'bad/services.json').read_text())
    assert r['status']=='NO-GO-EXTRACTION' and len(r['failures'])==1
spec=importlib.util.spec_from_file_location('admit',SCRIPT.with_name('admit-services.py'))
admit=importlib.util.module_from_spec(spec); spec.loader.exec_module(admit)
with tempfile.TemporaryDirectory(prefix='adsp-admit-') as td:
    root=Path(td); snap=root/'snapshot'; md=snap/'modules'; md.mkdir(parents=True)
    for name in ('modules.load','modules.bluetooth','modules.gpu'): (md/name).write_text('msm.ko\n')
    (md/'msm.ko').write_bytes(b'offline module fixture')
    for name in ('modules.alias','modules.softdep'): (md/name).write_text('')
    (md/'modules.options').write_text('msm.ko separate_gpu_kms=1\n')
    (snap/'config').write_text('CONFIG_QCOM_Q6V5_PAS=m\nCONFIG_QCOM_PD_MAPPER=m\n')
    (snap/'d0').mkdir(); (snap/'d0/modules.d0').write_text('qcom_pd_mapper.ko\nqcom_q6v5_pas.ko\n')
    r={'status':'REVIEW-REQUIRED','failures':[],'files':{'vendor/bin/toybox':{'sha256':'a'*64}},
       'findings':[{'file':'vendor/bin/toybox','sha256':'a'*64,'matches':['generic modprobe']}],'imports':[]}
    d={'vendor/bin/toybox':{'sha256':'a'*64,'reason':'generic utility; no packaged PAS asset'}}
    def check(report,disp,out):
        (root/'audit.json').write_text(json.dumps(report)); (root/'d.json').write_text(json.dumps(disp))
        admit.admit(root/'audit.json',root/'d.json',snap,root/out)
    check(r,d,'pass.json')
    assert json.loads((root/'pass.json').read_text())['status']=='PASS-AUTHOR-STATIC'
    for mutation in ('unknown','stale','pas-asset','alias','builtin','order','writer'):
        rr=copy.deepcopy(r); dd=copy.deepcopy(d)
        if mutation=='unknown': dd={}
        if mutation=='stale': dd['vendor/bin/toybox']['sha256']='b'*64
        if mutation=='pas-asset': rr['files']['vendor/lib/modules/qcom_q6v5_pas.ko']={}
        if mutation=='alias': (md/'modules.alias').write_text('alias of:* qcom_q6v5_pas\n')
        if mutation=='builtin': (snap/'config').write_text('CONFIG_QCOM_Q6V5_PAS=y\nCONFIG_QCOM_PD_MAPPER=m\n')
        if mutation=='order': (snap/'d0/modules.d0').write_text('qcom_q6v5_pas.ko\nqcom_pd_mapper.ko\n')
        if mutation=='writer':
            rr['findings'][0]['file']='vendor/etc/init/bad.rc'
            rr['findings'][0]['matches']=['write /sys/class/remoteproc/remoteproc0/state start']
            dd={'vendor/etc/init/bad.rc':dd.pop('vendor/bin/toybox')}
        try: check(rr,dd,mutation+'.json')
        except AssertionError: pass
        else: raise AssertionError('unsafe service admission accepted: '+mutation)
        (md/'modules.alias').write_text('')
        (snap/'config').write_text('CONFIG_QCOM_Q6V5_PAS=m\nCONFIG_QCOM_PD_MAPPER=m\n')
        (snap/'d0/modules.d0').write_text('qcom_pd_mapper.ko\nqcom_q6v5_pas.ko\n')
print('ADSP-D0-SERVICES TEST PASS')
