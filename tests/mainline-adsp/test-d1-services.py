#!/usr/bin/env python3
"""D1 service admission: audio stack allowed ONLY as the modular sidecar."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/mainline/adsp/audit-services.py'
CFG=('CONFIG_QCOM_Q6V5_PAS=m\nCONFIG_QCOM_PD_MAPPER=m\nCONFIG_QCOM_APR=m\nCONFIG_SND=m\nCONFIG_SOUND=y\n'
     'CONFIG_SND_SOC_QDSP6=m\nCONFIG_SND_SOC_SM8250=m\n')
D1=('apr.ko\nq6core.ko\nq6afe.ko\nq6adm.ko\nq6routing.ko\nq6asm.ko\nsnd-soc-sm8250.ko\n'
    'qcom_pd_mapper.ko\nqcom_q6v5_pas.ko\n')
spec=importlib.util.spec_from_file_location('admit',SCRIPT.with_name('admit-services-d1.py'))
admit=importlib.util.module_from_spec(spec); spec.loader.exec_module(admit)
with tempfile.TemporaryDirectory(prefix='adsp-admit-') as td:
    root=Path(td); snap=root/'snapshot'; md=snap/'modules'; md.mkdir(parents=True)
    for name in ('modules.load','modules.bluetooth','modules.gpu'): (md/name).write_text('msm.ko\n')
    (md/'msm.ko').write_bytes(b'offline module fixture')
    for name in ('modules.alias','modules.softdep'): (md/name).write_text('')
    (md/'modules.options').write_text('msm.ko separate_gpu_kms=1\n')
    (snap/'config').write_text(CFG)
    (snap/'d1').mkdir(); (snap/'d1/modules.d1').write_text(D1)
    r={'status':'REVIEW-REQUIRED','failures':[],'files':{'vendor/bin/toybox':{'sha256':'a'*64}},
       'findings':[{'file':'vendor/bin/toybox','sha256':'a'*64,'matches':['generic modprobe']}],'imports':[]}
    d={'vendor/bin/toybox':{'sha256':'a'*64,'reason':'generic utility; no packaged PAS asset'}}
    def check(report,disp,out):
        (root/'audit.json').write_text(json.dumps(report)); (root/'d.json').write_text(json.dumps(disp))
        admit.admit(root/'audit.json',root/'d.json',snap,root/out)
    check(r,d,'pass.json')
    assert json.loads((root/'pass.json').read_text())['status']=='PASS-AUTHOR-STATIC'
    for mutation in ('unknown','stale','pas-asset','alias','builtin','order','writer','audio-packaged','audio-autoload','apr-builtin','no-apr',
                     'fastrpc-on','sdw-controller','missing-sidecar-module','sound-off'):
        rr=copy.deepcopy(r); dd=copy.deepcopy(d)
        if mutation=='unknown': dd={}
        if mutation=='stale': dd['vendor/bin/toybox']['sha256']='b'*64
        if mutation=='pas-asset': rr['files']['vendor/lib/modules/qcom_q6v5_pas.ko']={}
        if mutation=='alias': (md/'modules.alias').write_text('alias of:* qcom_q6v5_pas\n')
        if mutation=='builtin': (snap/'config').write_text(CFG.replace('CONFIG_QCOM_Q6V5_PAS=m','CONFIG_QCOM_Q6V5_PAS=y'))
        if mutation=='audio-packaged': rr['files']['vendor/lib/modules/q6afe.ko']={}
        if mutation=='audio-autoload': (md/'modules.load').write_text('msm.ko\nq6core.ko\n'); (md/'q6core.ko').write_bytes(b'x')
        if mutation=='apr-builtin': (snap/'config').write_text(CFG.replace('CONFIG_QCOM_APR=m','CONFIG_QCOM_APR=y'))
        if mutation=='no-apr': (snap/'config').write_text(CFG.replace('CONFIG_QCOM_APR=m\n',''))
        if mutation=='fastrpc-on': (snap/'config').write_text(CFG+'CONFIG_QCOM_FASTRPC=m\n')
        if mutation=='sdw-controller': (snap/'config').write_text(CFG+'CONFIG_SOUNDWIRE_QCOM=m\n')
        if mutation=='sound-off': (snap/'config').write_text(CFG.replace('CONFIG_SOUND=y\n',''))
        if mutation=='missing-sidecar-module': (snap/'d1/modules.d1').write_text(D1.replace('q6asm.ko\n',''))
        if mutation=='order': (snap/'d1/modules.d1').write_text(D1.replace('qcom_pd_mapper.ko\nqcom_q6v5_pas.ko\n','qcom_q6v5_pas.ko\nqcom_pd_mapper.ko\n'))
        if mutation=='writer':
            rr['findings'][0]['file']='vendor/etc/init/bad.rc'
            rr['findings'][0]['matches']=['write /sys/class/remoteproc/remoteproc0/state start']
            dd={'vendor/etc/init/bad.rc':dd.pop('vendor/bin/toybox')}
        try: check(rr,dd,mutation+'.json')
        except AssertionError: pass
        else: raise AssertionError('unsafe service admission accepted: '+mutation)
        (md/'modules.alias').write_text('')
        (md/'modules.load').write_text('msm.ko\n')
        (snap/'config').write_text(CFG)
        (snap/'d1/modules.d1').write_text(D1)
print('ADSP-D1-SERVICES TEST PASS')
