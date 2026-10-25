#!/usr/bin/env python3
"""Build/run serialization tests; never open a phone or FastRPC device."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

p=argparse.ArgumentParser()
p.add_argument('--cc',required=True)
p.add_argument('--qemu')
a=p.parse_args()
root=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='s1-rpc-wire-',dir='/tmp') as temp:
    out=Path(temp)/'wire-test'
    cmd=[a.cc,'-std=c11','-O2','-Wall','-Wextra','-Werror']
    if a.qemu:
        cmd+=['-static']
    subprocess.run(cmd+[str(root/'tests/mainline-sensors/s1-rpc-wire-test.c'),'-o',str(out)],check=True,cwd=root)
    subprocess.run(([a.qemu] if a.qemu else [])+[str(out)],check=True,timeout=20,cwd=root)
