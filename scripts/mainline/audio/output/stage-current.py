#!/usr/bin/env python3
"""Stage current audio, including shared endpoint quiesce and direct PCM tool."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def load(name,path):
    s=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def stage(out,base):
    out=out.resolve();base=base.resolve()
    if base.name!='vendor.img':raise ValueError('base must be an existing vendor.img')
    with base.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    for name in ['build-fix2.py','build-fix3.py']:
        m=load(name.replace('.','_'),HERE/name)
        m.OUT=out;m.BASE=base.parent;m.BASE_SHA=digest;m.OVERLAY=out/'vendor-overlay'
        if name=='build-fix2.py':m.prepare()
        else:m.modules();m.tinyalsa()
    overlay=out/'vendor-overlay'
    hashes={str(p.relative_to(overlay)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(overlay.rglob('*')) if p.is_file()}
    (out/'overlay-hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
    return overlay


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--base-vendor',type=Path,required=True);a=p.parse_args();print(stage(a.out,a.base_vendor))
