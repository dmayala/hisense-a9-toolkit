#!/usr/bin/env python3
"""Build deadline on the existing wrapped-fix1 source lineage, host only."""
from pathlib import Path
import importlib.util, json, shutil, subprocess as sp
import build as b

OUT=b.ROOT/'plain-wrapped-deadline-20261022'
BASE=b.ROOT/'plain-wrapped-20261022'
FIX=b.ROOT/'plain-wrapped-real-fix1-20261022'
PATCHES=Path('/home/user/A9-wt/stage2-ice-wrapped-20261021/scripts/mainline')

def kernel():
    b.room(250*1024**2)
    assert b.sha(b.OBJ/'arch/arm64/boot/Image.gz')==b.sha(b.ROOT/'plain-deadline-20261022/Image.gz')
    # Return to wrapped-fix1's original WDT, keeping its cmdline guard0.
    original=b.ROOT/'suspend-pmtest-kernel-20261020/kernel/drivers/watchdog/qcom-wdt.c'
    b.materialize(b.K/'drivers/watchdog/qcom-wdt.c',original.read_bytes())
    for patch in [PATCHES/'ice-wrapped/0001-a9-ice31-legacy-wrapped.patch',PATCHES/'metadata/dm-default-key-7.2.patch']:
        for line in patch.read_text().splitlines():
            if line.startswith('+++ b/'):
                p=b.K/line[6:].split('\t')[0]
                if p.is_symlink():b.materialize(p,p.read_bytes())
        b.run(['patch','--batch','--fuzz=0','-p1','-d',b.K,'-i',patch],patch.stem+'-apply')
    shutil.copyfile(BASE/'kernel.config',b.OBJ/'.config')
    make=['make','-C',b.K,'O='+str(b.OBJ),'ARCH=arm64','CROSS_COMPILE=aarch64-unknown-linux-gnu-',
          'HOSTCC=gcc','HOSTCFLAGS=-I/nix/store/4rw03gwh97laxj6sgf630xdbnynkj43z-openssl-3.5.1-dev/include',
          'HOSTLDFLAGS=-L/nix/store/j09xkbypljdhxwspfiqd8sf64zgxhp52-openssl-3.5.1/lib',
          'LOCALVERSION=+','DTC_FLAGS=-@','-j4']
    b.run(make+['olddefconfig'],'olddefconfig')
    assert (b.OBJ/'.config').read_bytes()==(BASE/'kernel.config').read_bytes()
    b.run(make+['Image.gz'],'kernel-build')
    for a,c in [('arch/arm64/boot/Image.gz','Image.gz'),('.config','kernel.config'),('vmlinux.symvers','vmlinux.symvers')]:shutil.copyfile(b.OBJ/a,OUT/c)
    def exports(p):return {r[1]:r[0] for l in p.read_text().splitlines() if len(r:=l.split())>=4}
    old,new=exports(BASE/'vmlinux.symvers'),exports(OUT/'vmlinux.symvers')
    bad={n:[v,new.get(n)] for n,v in old.items() if new.get(n)!=v}
    (OUT/'abi.json').write_text(json.dumps(dict(existing_exports=len(old),mismatches=bad))+'\n')
    assert not bad,bad

if __name__=='__main__':
    import sys
    OUT.mkdir(exist_ok=True);b.OUT=OUT;b.BASE=FIX
    if sys.argv[1]=='kernel':kernel()
    elif sys.argv[1]=='package':b.package_boot()
    else:raise SystemExit('kernel or package')
