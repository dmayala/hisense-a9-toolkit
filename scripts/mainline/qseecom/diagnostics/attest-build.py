#!/usr/bin/env python3
"""Build the passive qseecomd preload, no proprietary input or phone access."""
import importlib.util
import json
from pathlib import Path
import hashlib
import subprocess as sp
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('diag_build', HERE / 'build.py')
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)

def build(out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    binary = out / 'liba9-attest-fs.so'
    command = [b.CC, '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-shared', '-Wl,-z,defs',
               '-Wl,-soname,liba9-attest-fs.so', '-Wl,--version-script='+str(HERE/'attest-fs.map'),
               HERE/'attest-fs.c', HERE/'backend-fs.c', '-ldl', '-llog', '-o', binary]
    sp.run(list(map(str, command)), check=True)
    result = dict(command=list(map(str, command)), sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                  sources={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in (HERE/'attest-fs.c', HERE/'backend-fs.c', HERE/'attest-fs.map', Path(__file__))})
    (out / 'build.json').write_text(json.dumps(result, indent=2)+'\n')
    return binary

if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('--out',type=Path,required=True)
    print(build(p.parse_args().out))
