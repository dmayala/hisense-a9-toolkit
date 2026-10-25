#!/usr/bin/env python3
"""Host fault injection using an executable fake adb. Never invokes real adb."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HOST=Path(__file__).resolve().parents[2]/'scripts/mainline/adsp/d0-host.py'
with tempfile.TemporaryDirectory(prefix='adsp-host-') as td:
    root=Path(td)
    adb=root/'fake-adb'
    adb.write_text('''#!/usr/bin/env python3
import os,sys,time,re,shlex
from pathlib import Path
assert sys.argv[1:3]==['-t','4']
case=os.environ['CASE']; root=Path(os.environ['STATE'])
with (root/'transport-calls').open('a') as f: f.write(repr(sys.argv[1:])+'\\n')
if sys.argv[3:]==['get-serialno']:
    if case=='transport-lost' and (root/'started').exists(): sys.exit(1)
    wrong=case=='wrong-serial' or (case=='serial-replaced' and (root/'started').exists())
    print('wrong' if wrong else 'fixture'); sys.exit(0)
assert sys.argv[3:5]==['shell','-T'] and sys.argv[5] in ('nc 127.0.0.1 2324','/system/bin/sh -s')
script=sys.stdin.read()
nonce=re.search(r'D0-TRANSPORT-[a-f0-9]+',script)[0]
args=shlex.split(next(l for l in script.splitlines() if l.startswith('/system/bin/sh /data/')))
mode=args[2]
uid='2000' if case=='not-root' else '0'
boot='22222222-2222-2222-2222-222222222222' if case=='wrong-boot' or (case=='reset' and (root/'started').exists()) else '11111111-1111-1111-1111-111111111111'
print(nonce+' ID '+uid+' '+boot,flush=True)
if uid!='0' or boot!='11111111-1111-1111-1111-111111111111': sys.exit(0)
rc=0
with (root/'calls').open('a') as f: f.write(mode+'\\n')
if mode=='boot-id':
    print('11111111-1111-1111-1111-111111111111' if case!='reset' or not (root/'started').exists() else '22222222-2222-2222-2222-222222222222')
elif mode=='run':
    assert args[4]=='11111111-1111-1111-1111-111111111111'
    (root/'started').touch()
    (root/'child-pid').write_text(str(os.getpid()))
    if case in ('hang','signal'): time.sleep(20)
    elif case in ('pass','reset','target-nonzero','no-completion','malformed-completion','serial-replaced'):
        print('ADSP-D0-11111111-1111-1111-1111-111111111111-'+args[3])
        print('D0-PASS-ONE-START-180S-OFFLINE')
    else:
        print('D0-FAIL '+case)
        print('CONTAINMENT-UNVERIFIED' if case in ('pending-start','dead-executor','group-hup','no-completion','malformed-completion','target-nonzero') else 'D0-FAILED-OFFLINE')
        rc=1
elif mode=='contain':
    if case in ('disconnect','contain-hang','reset'): sys.exit(1)
    print('D0-CONTAINED-OFFLINE')
if case=='no-completion' and mode=='run': sys.exit(0)
if case=='malformed-completion' and mode=='run': print(nonce+' RC bogus'); sys.exit(0)
if case=='target-nonzero' and mode=='run': rc=7
print('\\n'+nonce+' RC '+str(rc))
''')
    adb.chmod(0o755)
    for case in ('pass','gate-missing','gate-false','wrong-node','duplicate','stale-tuple',
                 'autoload','firmware-missing','load-fail','start-fail','stop-fail',
                 'disconnect','crash','marker-lost','reset','hang','contain-hang','signal','pending-start','dead-executor','group-hup','no-completion','malformed-completion','target-nonzero','serial-replaced','transport-lost'):
        r=root/case; r.mkdir()
        receipt=r/'admission.json'
        manifest=r/'SHA256SUMS'; manifest.write_text('offline fixture\n')
        t=hashlib.sha256(manifest.read_bytes()).hexdigest()
        receipt.write_text(json.dumps({'tuple':t,'frozen_manifest_sha256':t,
            'independent_review':'ADMITTED-ATTENDED-D0','fallback_owner':'offline fixture',
            'fallback':'attended PON2/Power then golden restore','service_admission':'PASS','rehearsal':True,'root_transport':'loopback'}))
        args=[sys.executable,'-I',str(HOST),'--adb',str(adb),'--serial','fixture',
            '--transport-id','4','--root-transport','loopback','--boot-id','11111111-1111-1111-1111-111111111111',
            '--admission',str(receipt),'--manifest',str(manifest),'--output',str(r/'out'),'--ledger',str(r/'ledger'),'--rehearse']
        env={**os.environ,'CASE':case,'STATE':str(r)}
        if case=='signal':
            child=subprocess.Popen(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            for _ in range(100):
                if (r/'child-pid').exists(): break
                time.sleep(.02)
            assert (r/'child-pid').exists()
            child.terminate(); stdout,stderr=child.communicate(timeout=10)
            p=subprocess.CompletedProcess(args,child.returncode,stdout,stderr)
            try: os.kill(int((r/'child-pid').read_text()),0)
            except ProcessLookupError: pass
            else: raise AssertionError('owned transport child survived host signal')
        else: p=subprocess.run(args,env=env,capture_output=True,timeout=12)
        assert (p.returncode==0)==(case=='pass'), (case,p.stderr)
        calls=(r/'calls').read_text().splitlines()
        assert calls.count('run')==1 and calls.count('contain')==(case not in ('pass','reset','serial-replaced','transport-lost')),(case,calls)
        assert len(list((r/'ledger').iterdir()))==1
        if case!='pass':
            result=(r/'out/result').read_text()
            assert result==('CONTAINMENT-UNVERIFIED\n' if case in ('disconnect','contain-hang','reset','hang','signal','pending-start','dead-executor','group-hup','no-completion','malformed-completion','target-nonzero','serial-replaced','transport-lost') else 'FAILED-OFFLINE\n'),(case,result)
        if case in ('pending-start','dead-executor','group-hup'):
            assert 'offline fixture owns attended PON2/Power' in (r/'out/fallback').read_text()
        # Durable boot ledger rejects even a fresh tuple on this same boot.
        args[args.index('--output')+1]=str(r/'again')
        p=subprocess.run(args,env={**env,'CASE':'pass'},capture_output=True,timeout=8)
        assert p.returncode!=0 and (r/'calls').read_text().splitlines().count('run')==1
        print('PASS host',case,'and ledger reentry')
    receipt.write_text(json.dumps({'tuple':t,'independent_review':'PENDING'}))
    args[args.index('--output')+1]=str(root/'refused-admission')
    previous=(r/'calls').read_text()
    p=subprocess.run(args,env=env,capture_output=True,timeout=8)
    assert p.returncode and (r/'calls').read_text()==previous
    print('PASS host pending admission refused before transport')
    receipt.write_text(json.dumps({'tuple':t,'frozen_manifest_sha256':t,
        'independent_review':'ADMITTED-ATTENDED-D0','fallback_owner':'PENDING',
        'fallback':'attended PON2/Power then golden restore','service_admission':'PASS','rehearsal':True,'root_transport':'loopback'}))
    p=subprocess.run(args,env=env,capture_output=True,timeout=8)
    assert p.returncode and (r/'calls').read_text()==previous
    print('PASS host unspecified fallback owner refused before transport')
    for case in ('not-root','wrong-boot','wrong-serial','not-admitted','legacy-receipt'):
        r=root/case; r.mkdir()
        receipt=r/'admission.json'
        rec={'tuple':t,'frozen_manifest_sha256':t,
            'independent_review':'ADMITTED-ATTENDED-D0','fallback_owner':'offline fixture',
            'fallback':'attended PON2/Power then golden restore','service_admission':'PASS',
            'rehearsal':True,'root_transport':'adbd' if case=='not-admitted' else 'loopback'}
        if case=='legacy-receipt': del rec['root_transport']
        receipt.write_text(json.dumps(rec))
        args[args.index('--admission')+1]=str(receipt)
        args[args.index('--output')+1]=str(r/'out')
        args[args.index('--ledger')+1]=str(r/'ledger')
        p=subprocess.run(args,env={**os.environ,'CASE':case,'STATE':str(r)},capture_output=True,timeout=8)
        assert p.returncode and not (r/'ledger').exists() and not (r/'calls').exists(),(case,p.stderr)
        if case in ('not-admitted','legacy-receipt'):
            assert not (r/'transport-calls').exists() and not (r/'out').exists()
        print('PASS host',case,'refused before ledger/target')
print('ADSP-D0-HOST TEST PASS')
