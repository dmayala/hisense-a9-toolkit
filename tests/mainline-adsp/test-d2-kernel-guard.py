#!/usr/bin/env python3
"""Compile actual patched kernel guard functions against hardware stubs."""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
BASE=Path.home()/'A9-build/mainline/linux-7.2.10-gpu'
PATCH=ROOT/'scripts/mainline/adsp/d2-kernel/0001-a9-d2-earpiece.patch'
p=argparse.ArgumentParser();p.add_argument('--source',type=Path);p.add_argument('--cc',type=Path)
a=p.parse_args()
assert shutil.disk_usage('/').free>=15_000_000_000
cc=a.cc or Path(shutil.which('cc') or sorted(Path('/nix/store').glob('*-gcc-wrapper-14.3.0/bin/cc'))[0])
def function(source,name):
    pattern=r'static [^\n]+ '+re.escape(name)+r'\([^;]*?\n\{.*?\n}\n'
    match=re.search(pattern,source,re.S)
    assert match is not None, ('missing production function',name)
    return match[0]
with tempfile.TemporaryDirectory(prefix='d2-kernel-guard-') as td:
    out=Path(td);src=a.source
    if src is None:
        src=out/'source';src.mkdir()
        for line in PATCH.read_text().splitlines():
            if line.startswith('--- a/'):
                rel=Path(line[6:].split('\t',1)[0]);dest=src/rel
                assert not rel.is_absolute() and '..' not in rel.parts
                dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BASE/rel,dest)
        subprocess.run(['patch','--fuzz=0','-p1','-i',str(PATCH)],cwd=src,check=True,stdout=subprocess.DEVNULL)
    h=(src/'sound/soc/codecs/wcd937x-a9-d2.h').read_text()
    c=(src/'sound/soc/codecs/wcd937x.c').read_text()
    names=('wcd937x_d2_contain','wcd937x_d2_release','wcd937x_d2_check_route',
           'wcd937x_update_bits','wcd937x_d2_read','wcd937x_d2_write_verify','wcd937x_d2_timeout','wcd937x_d2_fault_irq',
           'd2_fault_show','d2_ready_show','d2_stop_store','d2_state_show','wcd937x_d2_setup')
    actual='\n'.join(function(h,n) for n in names)
    actual+='\n'+function(c,'wcd937x_a9_d2_ear_pa')+'\n'+function(c,'wcd937x_d2_mux_put')
    (out/'production.inc').write_text(actual)
    binary=out/'test'
    subprocess.run([str(cc),'-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-parameter',
        '-Wno-unused-function','-Wno-sign-compare','-O2','-I',str(out),
        str(Path(__file__).with_suffix('.c')),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True,timeout=10)
    print('Production functions sha256',hashlib.sha256(actual.encode()).hexdigest())
    print('Patch sha256',hashlib.sha256(PATCH.read_bytes()).hexdigest())
