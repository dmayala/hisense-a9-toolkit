#!/usr/bin/env python3
"""Build the battery module against exact image exports in private host output."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess as sp

HERE=Path(__file__).resolve().parent
MAIN=HERE.parents[1]
ROOT=Path('/home/user/A9-build/mainline')
NAMES=('Makefile','pmi632-battery.c','policy.h','profile.h','input-policy.h',
       'status.h','drain-qg.h','pmi632-drain.h')


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sources():
    return {str(p.relative_to(MAIN)):sha(p) for p in
            [*(HERE/n for n in NAMES),HERE.parent/'pmi632-policy.h',Path(__file__)]}


def imports(p):
    data=p.read_bytes()
    assert data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==183
    offset=struct.unpack_from('<Q',data,40)[0]
    size,count,names=struct.unpack_from('<HHH',data,58)
    sections=[struct.unpack_from('<IIQQQQIIQQ',data,offset+i*size) for i in range(count)]
    strings=data[sections[names][4]:sections[names][4]+sections[names][5]]
    section=next(s for s in sections if strings[s[0]:].split(b'\0',1)[0]==b'__versions')
    rows=data[section[4]:section[4]+section[5]]
    assert len(rows)%64==0
    return {rows[i+8:i+64].split(b'\0',1)[0].decode():struct.unpack_from('<Q',rows,i)[0]
            for i in range(0,len(rows),64)}


def verify(out,kernel):
    out,kernel=Path(out),Path(kernel)
    record=json.loads((out/'battery-build.json').read_text())
    module=out/'pmi632-battery.ko'
    assert record['source_sha256']==sources(), 'rebuild stale battery stage'
    assert record['module_sha256']==sha(module)
    assert record['config_sha256']==sha(kernel/'kernel.config')
    expected={s[1]:int(s[0],16) for line in (kernel/'Module.symvers').read_text().splitlines()
              if len(s:=line.split())>=4}
    imported=imports(module)
    bad=[name for name,crc in imported.items() if expected.get(name)!=crc]
    assert imported and not bad, 'battery import CRC mismatch: '+', '.join(bad)
    release=re.search(rb'Linux version ([^ ]+)',gzip.decompress((kernel/'Image.gz').read_bytes())).group(1)
    assert b'vermagic='+release+b' ' in module.read_bytes(), 'battery vermagic mismatch'
    assert all(name in module.read_bytes() for name in (b'drain_snapshot',b'input_suspend',b'drain_capacity'))
    return {'lib/modules/pmi632-battery.ko':module},record


def build(out,kernel,prepared=None,source=None):
    out,kernel=Path(out),Path(kernel)
    prepared=Path(prepared or ROOT/'6r3-host/q6/obj')
    source=Path(source or ROOT/'linux-7.2.10')
    assert not out.exists(), 'preserve previous module stage'
    assert shutil.disk_usage(out.parent).free>15*1024**3+50*1024**2
    source_digests=sources()
    out.mkdir();obj,mod=out/'obj',out/'module';obj.mkdir();mod.mkdir()
    for name in ('include','scripts','arch/arm64/include'):
        shutil.copytree(prepared/name,obj/name,symlinks=True)
    for name in ('.config','Makefile'):shutil.copyfile(prepared/name,obj/name)
    assert (obj/'.config').read_bytes()==(kernel/'kernel.config').read_bytes()
    shutil.copyfile(kernel/'Module.symvers',obj/'Module.symvers')
    (obj/'source').symlink_to(source)
    for name in NAMES:shutil.copyfile(HERE/name,mod/name)
    shutil.copyfile(HERE.parent/'pmi632-policy.h',mod/'pmi632-policy.h')
    paths=[]
    for pattern in ('*gcc-wrapper-14.3.0/bin','*binutils*/bin','*gnumake*/bin',
                    '*coreutils*/bin','*gnused*/bin','*gnugrep*/bin','*gawk*/bin','*findutils*/bin'):
        paths.extend(str(p) for p in Path('/nix/store').glob(pattern))
    env=dict(os.environ,PATH=':'.join(paths)+':'+os.environ['PATH'])
    command=['make','-C',str(source),'O='+str(obj),'M='+str(mod),'ARCH=arm64',
             'CROSS_COMPILE=aarch64-unknown-linux-gnu-','HOSTCC=gcc','LOCALVERSION=+','-j3','W=1','modules']
    (out/'command.json').write_text(json.dumps(command,indent=2)+'\n')
    with (out/'build.log').open('w') as log:
        result=sp.run(command,env=env,stdout=log,stderr=sp.STDOUT)
    (out/'build.rc').write_text(str(result.returncode)+'\n');result.check_returncode()
    assert sources()==source_digests, 'battery sources changed during compile'
    for name in NAMES:
        assert sha(mod/name)==sha(HERE/name), 'staged source changed: '+name
    assert sha(mod/'pmi632-policy.h')==sha(HERE.parent/'pmi632-policy.h')
    shutil.copyfile(mod/'pmi632-battery.ko',out/'pmi632-battery.ko')
    record=dict(source_sha256=source_digests,module_sha256=sha(out/'pmi632-battery.ko'),
                config_sha256=sha(kernel/'kernel.config'),exports_sha256=sha(kernel/'Module.symvers'),
                imports_checked=len(imports(out/'pmi632-battery.ko')),host_only=True,hardware_tested=False,
                default_drain=False,measurement='relative-QG-hardware-batches-with-coverage')
    (out/'battery-build.json').write_text(json.dumps(record,indent=2)+'\n')
    payload,record=verify(out,kernel)
    print('BATTERY_PAYLOAD PASS: '+str(record['imports_checked'])+' import CRCs',flush=True)
    return payload,record


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--kernel',type=Path,required=True)
    args=ap.parse_args();build(args.out.resolve(),args.kernel.resolve())
