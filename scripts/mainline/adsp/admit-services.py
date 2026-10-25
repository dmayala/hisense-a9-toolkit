#!/usr/bin/env python3
"""Close a complete static service audit with explicit hash-bound dispositions.

This is author evidence for independent review, not a deployment authorization.
No automatic allowlist of unknown binaries or future finding hashes is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

FORBIDDEN=re.compile(r'^(?:qcom_q6v5_(?:pas|adsp|mss|wcss)|qcom_wcnss.*|apr|fastrpc|snd.*|.*lpass.*)\.ko$',re.I)

def admit(audit, dispositions, snapshot, output):
    r=json.loads(audit.read_text()); d=json.loads(dispositions.read_text())
    assert r['status']=='REVIEW-REQUIRED' and not r['failures']
    expected={x['file']:x['sha256'] for x in r['findings']}
    assert set(d)==set(expected), 'every finding needs exactly one disposition'
    for name,h in expected.items():
        assert d[name]['sha256']==h and len(d[name]['reason'])>=20,(name,'stale/incomplete disposition')
    for name in r['files']:
        assert not FORBIDDEN.match(Path(name).name),(name,'forbidden packaged module')
    md=snapshot/'modules'
    for name in ('modules.alias','modules.softdep'): assert (md/name).read_text()==''
    lists={}
    for name in ('modules.load','modules.bluetooth','modules.gpu'):
        rows=(md/name).read_text().splitlines()
        assert len(rows)==len(set(rows))
        for row in rows: assert (md/row).is_file() and not FORBIDDEN.match(row),row
        lists[name]=rows
    assert (md/'modules.options').read_text()=='msm.ko separate_gpu_kms=1\n'
    cfg=(snapshot/'config').read_text().splitlines()
    for symbol in ('QCOM_APR','QCOM_FASTRPC','SOUND','PINCTRL_LPASS_LPI','REMOTEPROC_CDEV',
                   'QCOM_Q6V5_ADSP','QCOM_Q6V5_MSS','QCOM_Q6V5_WCSS','QCOM_WCNSS_PIL'):
        assert not any(x.startswith('CONFIG_'+symbol+'=') for x in cfg),symbol
    assert 'CONFIG_QCOM_Q6V5_PAS=m' in cfg and 'CONFIG_QCOM_PD_MAPPER=m' in cfg
    optional=(snapshot/'d0/modules.d0').read_text().splitlines()
    assert optional[-1]=='qcom_q6v5_pas.ko' and len(optional)==len(set(optional))
    assert 'qcom_pd_mapper.ko' in optional
    for x in r['findings']:
        if x['file'].endswith(('.rc','.sh')):
            assert not any(re.search(r'remoteproc|qcom_q6v5|pd[-_]mapper',m,re.I) for m in x['matches']),x['file']
    # Every possible packaged init fragment was audited, including fragments
    # not selected by property expansion. Missing optional imports add no code.
    props={'ro.hardware':'hlte556n','ro.zygote':'zygote64','ro.bootmode':'normal'}
    imports=[]
    for x in r['imports']:
        path=x['import'].split()[1]
        expanded=re.sub(r'\$\{([^}]+)\}',lambda m:props[m[1]],path)
        assert expanded.startswith('/') and '..' not in Path(expanded).parts
        if expanded.startswith('/vendor/'): key='vendor/'+expanded[8:]
        elif expanded.startswith('/odm/'): key='vendor/odm/'+expanded[5:]
        else: key='system-image'+expanded
        present=key in r['files'] or 'ramdisk'+expanded in r['files']
        imports.append({**x,'expanded':expanded,'present':present,
                        'disposition':'inventoried' if present else 'missing optional import; no code added'})
    output.write_text(json.dumps({'status':'PASS-AUTHOR-STATIC',
        'independent_admission':'PENDING','audit_sha256':hashlib.sha256(audit.read_bytes()).hexdigest(),
        'dispositions':d,'imports':imports,'autoload_lists':lists,
        'mapper_owner':'kernel qcom_pd_mapper; no userspace owner; Wi-Fi absent',
        'module_composition':'PAS/providers sidecar only; not present in boot/system/vendor',
        'scope':'exact frozen images, RAM /data, no updated APEX or Magisk modules; trusted packaged startup actors'},indent=2)+'\n')
    print('ADSP-D0-SERVICE AUTHOR STATIC PASS;',len(expected),'findings dispositioned;',len(imports),'imports')

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--audit',type=Path,required=True)
    p.add_argument('--dispositions',type=Path,required=True); p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    admit(a.audit,a.dispositions,a.snapshot,a.output)
