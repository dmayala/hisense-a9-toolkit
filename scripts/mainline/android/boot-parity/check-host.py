#!/usr/bin/env python3
"""Prepare an isolated ARM64 compile and run four host checks. No phone access."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess as sp
import sys

HERE=Path(__file__).resolve().parent
MAIN=HERE.parents[1]
ROOT=Path('/home/user/A9-build/mainline')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--vendor',type=Path,default=ROOT/'integ-20261024-23/vendor.img')
    ap.add_argument('--stock-vendor',type=Path,default=ROOT/'android/ref/vendor_a.img')
    a=ap.parse_args();o=a.out.resolve();assert not o.exists(),'preserve prior evidence'
    assert shutil.disk_usage(o.parent).free>=15*1024**3+64*1024**2
    os.umask(0o077);o.mkdir();k=o/'kernel-source';obj=o/'obj';k.mkdir();obj.mkdir()
    old=ROOT/'plain-wrapped-unicode-fix11-20261023/scratch/obj';clean=ROOT/'linux-7.2.10-gpu'
    for folder in ('include','arch/arm64/include','kernel/cgroup','kernel/sched'):
        target=k/folder;target.parent.mkdir(parents=True,exist_ok=True)
        if folder=='kernel/cgroup':
            target.mkdir()
            for p in (clean/folder).iterdir():
                if p.suffix=='.h':(target/p.name).symlink_to(p)
        else:target.symlink_to(clean/folder)
    (k/'kernel/cgroup/cgroup-v1.c').write_bytes((clean/'kernel/cgroup/cgroup-v1.c').read_bytes())
    for folder in ('include','arch/arm64/include'):
        target=obj/folder;target.parent.mkdir(parents=True,exist_ok=True);target.symlink_to(old/folder)
    (obj/'kernel/cgroup').mkdir(parents=True)
    def run(args,name,cwd=obj):
        with (o/(name+'.log')).open('w') as f:
            p=sp.run(list(map(str,args)),cwd=cwd,stdout=f,stderr=sp.STDOUT,timeout=120)
        assert p.returncode==0,(name,p.returncode)
    patch=MAIN/'kernel/patches/0064-cgroup-v1-cap-sys-nice.patch'
    run(['patch','--batch','--fuzz=0','-p1','-d',k,'-i',patch],'kernel-patch')
    command=(old/'kernel/cgroup/.cgroup-v1.o.cmd').read_text().splitlines()[0].split(' := ',1)[1]
    args=[x.replace(str(old.parent/'kernel'),str(k)) for x in shlex.split(command)]
    args[0]=str(next(Path('/nix/store').glob('*gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-gnu-gcc')))
    run(args,'cgroup-v1-arm64-compile')
    cc='/nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin/cc'
    include='/nix/store/blq676h0hx4dq5hlrn6gbxa7zygkvqvz-libsepol-3.8.1-dev/include'
    lib='/nix/store/q97pqkiy6bc27zh80167c8w478wbvg42-libsepol-3.8.1/lib/libsepol.a'
    run([cc,'-O2','-Wall','-Wextra','-Werror','-I'+include,MAIN/'fingerprint/policy-check.c',lib,'-o',o/'policy-check'],'policy-query-compile')
    # Reuse the existing host-only policy-capability adapter. The shipped
    # policy is untouched; only unsupported functionfs bit12 is masked here.
    raw=sp.check_output(['debugfs','-R','cat /etc/selinux/precompiled_sepolicy',a.vendor],stderr=sp.DEVNULL)
    spec=importlib.util.spec_from_file_location('host_policy',MAIN/'acceptance/onb.py')
    acceptance=importlib.util.module_from_spec(spec);spec.loader.exec_module(acceptance)
    adapted,cleared=acceptance.policy_for_host(raw);(o/'host-query.policy').write_bytes(adapted)
    stock=sp.check_output(['debugfs','-R','cat /etc/selinux/precompiled_sepolicy',a.stock_vendor],stderr=sp.DEVNULL)
    (o/'stock.policy').write_bytes(stock)
    env=dict(os.environ,A9_BOOT_PARITY_EVIDENCE=str(o))
    with (o/'tests.log').open('w') as f:p=sp.run([sys.executable,'-B',str(HERE/'test-host.py')],env=env,stdout=f,stderr=sp.STDOUT,timeout=120)
    assert p.returncode==0,'host checks: see tests.log'
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    sources=[*HERE.glob('*.py'),*HERE.glob('*.rc'),*HERE.glob('*.json'),patch,MAIN/'reboot/build14.py',MAIN/'reboot/build14-kernel.py']
    record=dict(host_only=True,phone_access=False,hardware_tested=False,groups=4,passed=True,
        arm64_object_sha256=sha(obj/'kernel/cgroup/cgroup-v1.o'),policy_sha256=hashlib.sha256(raw).hexdigest(),
        stock_policy_sha256=hashlib.sha256(stock).hexdigest(),frp_read_only=False,
        functionfs_bit12_cleared_host_only=cleared,source_sha256={str(p.relative_to(MAIN)):sha(p) for p in sources})
    (o/'checks.json').write_text(json.dumps(record,indent=2)+'\n')
    print('FRP_CGROUP_HOST_PASS groups=4 hardware_tested=0')


if __name__=='__main__':main()
