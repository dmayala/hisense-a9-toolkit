#!/usr/bin/env python3
"""Native actual sandbox/broker/thread/RPC integration; simulated ioctl only."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

p=argparse.ArgumentParser()
p.add_argument('--cc',required=True)
p.add_argument('--root-dir',type=Path,default=Path('/dev/shm'))
p.add_argument('--strace',type=Path)
a=p.parse_args()
root=Path(__file__).resolve().parents[2]
src=root/'scripts/mainline/sensors'
with tempfile.TemporaryDirectory(prefix='s1-listener-integration-',dir='/tmp') as build:
    binary=Path(build)/'integration'
    subprocess.run([a.cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-pthread',
        str(root/'tests/mainline-sensors/test-s1-listener-integration.c'),
        str(src/'s1-registry-fs.c'),str(src/'s1-registry-sandbox.c'),'-o',str(binary)],check=True)
    for mode in ['noack','ack']:
        with tempfile.TemporaryDirectory(prefix='s1-listener-ram-',dir=a.root_dir) as fixture:
            cmd=['unshare','-Ur',str(binary),fixture,mode]
            if a.strace:
                cmd=[str(a.strace),'-f','-qq','-s','100','-o','/tmp/s1-listener-integration.strace']+cmd
            env=os.environ.copy()
            # Target runtime is static musl, which uses neither clone3 nor rseq.
            # Keep production seccomp's rseq refusal while testing with glibc.
            env['GLIBC_TUNABLES']='glibc.pthread.rseq=0'
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=30,env=env)
            print(r.stdout,end='');print(r.stderr,end='')
            assert not r.returncode,r.returncode
            assert 'actual-RAM actual-confinement actual-thread simulated-ioctl no-device' in r.stdout
            marker='S1-REGISTRY-DELIVERED reads=1 writes=0'
            assert (marker in r.stdout)==(mode=='ack'),r.stdout
