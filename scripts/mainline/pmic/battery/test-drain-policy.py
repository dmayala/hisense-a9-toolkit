#!/usr/bin/env python3
"""Compile/query the actual drain policy with a read-only vendor baseline."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess as sp
HERE=Path(__file__).resolve().parent
MAIN=HERE.parents[1]
ROOT=Path('/home/user/A9-build/mainline')
def load(name,p):
    spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--vendor',type=Path,default=ROOT/'integ-20261024-25/vendor.img')
    a=ap.parse_args();out=a.out.resolve();assert not out.exists();out.mkdir()
    prep=load('drain_policy_prep',MAIN/'boot-deadline/prepare-vendor.py')
    def read(p):return sp.check_output(['debugfs','-R','cat /etc/selinux/'+p,str(a.vendor)],stderr=sp.DEVNULL)
    base=read('vendor_sepolicy.cil').replace(b'(typepermissive a9ml_rootsh)',b'')
    additions=[l for l in prep.RULES.splitlines() if l.startswith(b'(') and l not in base.splitlines()]
    vendor=out/'vendor.cil';vendor.write_bytes(base+b'\n'+b'\n'.join(additions)+b'\n')
    public=out/'plat_pub_versioned.cil';public.write_bytes(read('plat_pub_versioned.cil'))
    inputs=[];platform=MAIN/'boot-deadline/policy-platform'
    for name in json.loads((platform/'inputs.json').read_text())['files']:
        p=out/name;p.write_bytes(gzip.decompress((platform/(name+'.gz')).read_bytes()));inputs.append(p)
    policy=out/'production-policy.bin'
    with (out/'compile.log').open('w') as log:
        sp.run(list(map(str,[prep.SECILC,'-D','-m','-M','true','-G','-N','-c','30',*inputs,public,vendor,'-o',policy,'-f','/dev/null'])),stdout=log,stderr=sp.STDOUT,check=True)
    onb=load('drain_policy_onb',MAIN/'acceptance/onb.py')
    replay=out/'host-query.policy';replay.write_bytes(onb.policy_for_host(policy.read_bytes())[0])
    # Query-only old-libsepol adapter above never changes the production policy.
    cc='/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin/gcc'
    dev='/nix/store/blq676h0hx4dq5hlrn6gbxa7zygkvqvz-libsepol-3.8.1-dev/include'
    lib='/nix/store/q97pqkiy6bc27zh80167c8w478wbvg42-libsepol-3.8.1/lib/libsepol.a'
    tool=out/'policy-check'
    sp.run(list(map(str,[cc,'-O2','-Wall','-Wextra','-Werror','-I'+dev,MAIN/'fingerprint/policy-check.c',lib,'-o',tool])),check=True)
    queries=[('a9ml_rootsh','sysfs_batteryinfo','dir','search',1),
             ('a9ml_rootsh','sysfs_batteryinfo','file','read',1),
             ('a9ml_rootsh','sysfs_batteryinfo','file','write',0),
             ('a9ml_rootsh','a9ml_sysfs_drain_control','file','write',1),
             ('a9ml_rootsh','debugfs_wakeup_sources','file','read',1),
             ('shell','a9ml_sysfs_drain_control','file','write',0)]
    with (out/'query.log').open('w') as log:
        for subject,target,cls,perm,expected in queries:
            sp.run(list(map(str,[tool,replay,'u:r:'+subject+':s0','u:object_r:'+target+':s0',cls,perm,expected])),stdout=log,stderr=sp.STDOUT,check=True)
    print('DRAIN_POLICY PASS: telemetry/wakeup reads and exact USER control allowed; generic battery/shell writes refused')

if __name__=='__main__':main()
