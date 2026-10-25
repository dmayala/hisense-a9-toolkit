#!/usr/bin/env python3
"""Inventory the entire composed userspace, including every packaged APEX.

Conservative static evidence, not a semantic proof of arbitrary ELF behavior.
Every finding requires a hash-bound disposition before admission. A missing
root, APEX extraction error, unresolved import or unreviewed finding is NO-GO.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess as sp
import tempfile
import zipfile

RISK=re.compile(rb'remoteproc|rproc_boot|pd[-_]mapper|protection.domain|adsprpc|fastrpc|qcom_q6v5|/sys/.{0,80}(?:recovery|coredump)|\b(?:insmod|modprobe|load_modules)\b',re.I)
TOKENS=(b'remoteproc',b'rproc_boot',b'pd-mapper',b'pd_mapper',b'protection',
        b'adsprpc',b'fastrpc',b'qcom_q6v5',b'/sys/',b'insmod',b'modprobe',b'load_modules')

def sha(data): return hashlib.sha256(data).hexdigest()

def risk_matches(data):
    # Literal searches run in C. Running a case-insensitive alternation over
    # gigabytes of mostly empty APEX filesystem data needlessly takes minutes.
    lower=data.lower(); candidates=set()
    for token in TOKENS:
        start=0
        while True:
            pos=lower.find(token,start)
            if pos<0: break
            candidates.add(pos); start=pos+len(token)
    found=set()
    for pos in sorted(candidates):
        left=max(0,pos-1)
        for m in RISK.finditer(data[left:pos+160]):
            found.add((left+m.start(),left+m.end()))
    return sorted(found)

def audit(roots,out):
    assert not out.exists()
    out.mkdir(parents=True)
    records={}; findings=[]; imports=[]; apexes=[]; failures=[]
    def scan(root,label,apex=True):
        for p in sorted(root.rglob('*')):
            rel=label+'/'+str(p.relative_to(root))
            if p.is_symlink():
                records[rel]={'symlink':str(p.readlink())}; continue
            if not p.is_file(): continue
            data=p.read_bytes()
            records[rel]={'sha256':sha(data),'bytes':len(data)}
            offsets=risk_matches(data)
            if offsets:
                matches=[]
                for start,end in offsets:
                    lo=max(0,start-100); hi=min(len(data),end+140)
                    matches.append(data[lo:hi].replace(b'\0',b' ').decode(errors='replace'))
                findings.append({'file':rel,'sha256':sha(data),'matches':matches})
            if p.suffix=='.rc':
                for line in data.decode(errors='replace').splitlines():
                    if line.strip().startswith('import '): imports.append({'file':rel,'import':line.strip()})
            if apex and p.suffix in ('.apex','.capex'):
                with tempfile.TemporaryDirectory(prefix='apex-',dir=out) as td:
                    t=Path(td)
                    try:
                        with zipfile.ZipFile(p) as z:
                            if 'original_apex' in z.namelist():
                                z.extract('original_apex',t)
                                z=zipfile.ZipFile(t/'original_apex')
                            with z.open('apex_payload.img') as f, (t/'payload.img').open('wb') as g:
                                shutil.copyfileobj(f,g)
                        (t/'root').mkdir()
                        proc=sp.run(['debugfs','-R','rdump / '+str(t/'root'),str(t/'payload.img')],capture_output=True,text=True)
                        # debugfs often exits zero on errors. Require actual
                        # APEX metadata and capture all diagnostics for review.
                        assert (t/'root/apex_manifest.pb').is_file(),proc.stderr
                        apexes.append({'file':rel,'sha256':sha(data),'payload_sha256':sha((t/'payload.img').read_bytes()),'diagnostics':proc.stderr})
                        scan(t/'root',rel+'!payload',False)
                        print('APEX',rel,'PASS',flush=True)
                    except Exception as e: failures.append({'file':rel,'error':str(e)})
    for label,root in roots.items():
        assert root.is_dir(),label
        scan(root,label)
        print('ROOT',label,'PASS',flush=True)
    report={'roots':{k:str(v) for k,v in roots.items()},'files':records,
        'findings':findings,'imports':imports,'apexes':apexes,'failures':failures,
        'status':'REVIEW-REQUIRED' if not failures else 'NO-GO-EXTRACTION'}
    (out/'services.json').write_text(json.dumps(report,indent=2)+'\n')
    print(len(records),'files,',len(apexes),'APEXes,',len(findings),'finding files,',len(failures),'extraction failures')
    assert not failures, failures

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--system',required=True,type=Path)
    p.add_argument('--vendor',required=True,type=Path); p.add_argument('--ramdisk',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path); a=p.parse_args()
    audit({'system-image':a.system,'vendor':a.vendor,'ramdisk':a.ramdisk},a.output)
