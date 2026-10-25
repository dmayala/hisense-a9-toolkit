#!/usr/bin/env python3
"""Build/verify the stock-PG E Ink module in private host output, no image I/O."""
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess as sp
from pathlib import Path

MAIN = Path(__file__).resolve().parents[2]
ROOT = Path('/home/user/A9-build/mainline')
PATCHES = ['0057-regulator-fp9929a-read-only-VCOM-readback.patch',
           '0059-regulator-fp9929a-starvation-recovery.patch',
           '0065-regulator-fp9929a-stock-pg-telemetry.patch']


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sources():
    paths = [p for p in (MAIN/'eink/driver').rglob('*') if p.is_file()]
    paths += [MAIN/'tcon'/n for n in ('a9tcon.c', 'a9tcon.h')]
    paths += [MAIN/'kernel/patches'/n for n in PATCHES]
    paths += [Path(__file__)]
    return {str(p.relative_to(MAIN)): sha(p) for p in paths}


def imports(module):
    b = module.read_bytes()
    assert b[:6] == b'\x7fELF\x02\x01'
    off = struct.unpack_from('<Q', b, 40)[0]
    size, count, names = struct.unpack_from('<HHH', b, 58)
    sections = [struct.unpack_from('<IIQQQQIIQQ', b, off+i*size) for i in range(count)]
    strings = b[sections[names][4]:sections[names][4]+sections[names][5]]
    sec = next(s for s in sections if strings[s[0]:].split(b'\0', 1)[0] == b'__versions')
    data = b[sec[4]:sec[4]+sec[5]]
    assert len(data) % 64 == 0
    return {data[i+8:i+64].split(b'\0', 1)[0].decode(): struct.unpack_from('<Q', data, i)[0]
            for i in range(0, len(data), 64)}


def verify(stage, symvers):
    stage, symvers = Path(stage), Path(symvers)
    record = json.loads((stage/'pg-build.json').read_text())
    assert record['source_sha256'] == sources(), 'rebuild stale E Ink PG stage'
    module = stage/'a9_eink.ko'
    assert sha(module) == record['module_sha256']
    b = module.read_bytes()
    assert b'pg_dip_policy' in b and b'pg-observe:' in b
    assert b'PG still low %llu' not in b and b'pg_settle_budget' not in b
    expected = {s[1]: int(s[0], 16) for l in symvers.read_text().splitlines()
                if len(s := l.split()) >= 4}
    imported = imports(module)
    assert imported and all(expected.get(n) == crc for n, crc in imported.items()), 'E Ink module CRC mismatch'
    return {'lib/modules/a9_eink.ko': module}, record


def build(out, kernel):
    assert not out.exists(), 'preserve previous stage'
    assert shutil.disk_usage(out.parent).free > 15*1024**3 + 100*1024**2
    out.mkdir()
    src, base = ROOT/'linux-7.2.10-gpu', ROOT/'6r3-host/q6/obj'
    pmic, obj, mod = out/'pmic', out/'obj', out/'module'
    for rel in ('drivers/regulator/fp9929a.c', 'include/linux/regulator/fp9929a.h'):
        target = pmic/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src/rel, target)
    env = dict(os.environ)
    paths = []
    for pattern in ('*gcc-wrapper-14.3.0/bin', '*gnumake*/bin', '*coreutils*/bin',
                    '*gnused*/bin', '*gnugrep*/bin', '*gawk*/bin', '*findutils*/bin', '*binutils*/bin'):
        paths += [str(p) for p in Path('/nix/store').glob(pattern)]
    env['PATH'] = ':'.join(paths)+':'+env['PATH']
    def run(args, name):
        with (out/(name+'.log')).open('w') as log:
            sp.run(list(map(str, args)), env=env, stdout=log, stderr=sp.STDOUT, check=True)
        print(name+' PASS', flush=True)
    for patch in PATCHES:
        run(['patch', '--batch', '--fuzz=0', '-p1', '-d', pmic,
             '-i', MAIN/'kernel/patches'/patch], patch)
    obj.mkdir()
    for name in ('include', 'scripts', 'arch/arm64/include'):
        shutil.copytree(base/name, obj/name, symlinks=True)
    for name in ('.config', 'Makefile'):
        shutil.copyfile(base/name, obj/name)
    assert (obj/'.config').read_bytes() == (kernel/'kernel.config').read_bytes()
    shutil.copyfile(kernel/'Module.symvers', obj/'Module.symvers')
    (obj/'source').symlink_to(src)
    shutil.copytree(MAIN/'eink/driver', mod)
    for name in ('a9tcon.c', 'a9tcon.h'):
        shutil.copyfile(MAIN/'tcon'/name, mod/name)
    shutil.copyfile(pmic/'include/linux/regulator/fp9929a.h', mod/'fp9929a-published.h')
    for name in ('a9_eink_drv.c', 'a9_eink_transport.c'):
        p = mod/name
        p.write_text('#ifndef KBUILD_MODFILE\n#define KBUILD_MODFILE "drivers/regulator/fp9929a"\n#endif\n'+p.read_text().replace('<linux/regulator/fp9929a.h>', '"fp9929a-published.h"'))
    command = ['make', '-C', src, 'O='+str(obj), 'ARCH=arm64',
               'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc', 'LOCALVERSION=+', '-j4']
    run(command+['M='+str(mod), 'modules'], 'eink-build')
    shutil.copyfile(mod/'a9_eink.ko', out/'a9_eink.ko')
    run(['aarch64-unknown-linux-gnu-strip', '--strip-debug', out/'a9_eink.ko'], 'strip')
    # Compile the built-in PMIC translation unit, without linking a second kernel.
    pmod = out/'pmic-object'
    pmod.mkdir()
    shutil.copyfile(pmic/'drivers/regulator/fp9929a.c', pmod/'fp9929a.c')
    shutil.copyfile(mod/'fp9929a-published.h', pmod/'fp9929a-published.h')
    p = pmod/'fp9929a.c'
    p.write_text('#ifndef KBUILD_MODFILE\n#define KBUILD_MODFILE "drivers/regulator/fp9929a"\n#endif\n'+p.read_text().replace('<linux/regulator/fp9929a.h>', '"fp9929a-published.h"'))
    (pmod/'Makefile').write_text('obj-m += fp9929a.o\n')
    run(command+['M='+str(pmod), 'CFLAGS_MODULE=-UMODULE', 'fp9929a.o'], 'pmic-object-build')
    record = dict(source_sha256=sources(), module_sha256=sha(out/'a9_eink.ko'),
                  pmic_object_sha256=sha(pmod/'fp9929a.o'), host_only=True,
                  policy='stock-observe-only-v1', hardware_tested=False)
    (out/'pg-build.json').write_text(json.dumps(record, indent=2)+'\n')
    verify(out, kernel/'Module.symvers')
    print('PG_PAYLOAD PASS', flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--kernel', type=Path, default=ROOT/'integ-20261024-25')
    args = ap.parse_args()
    build(args.out.resolve(), args.kernel.resolve())
