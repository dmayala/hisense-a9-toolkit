#!/usr/bin/env python3
"""Private offline D1 build (D0 + APR/Q6/ASoC front end), derived from build-d0.py.
Run inside kernel/shell.nix, see README-d1.md.

No source download, device transport, or writes to the golden dump. Every
invocation requires a new output directory; shared kernel inputs are read only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess as sp

REPO = Path(__file__).resolve().parents[3]
W = Path.home() / 'A9-build/mainline'
BASE = W / 'linux-7.2.10-gpu'
KHEAD = 'ab3d710164c746028ff2045e01e2e7b9d610f5ab'
BOARD = 'sm6115-hisense-hlte556n-android-gpu-thermal'

def run(*args, **kw):
    print('+', *map(str, args), flush=True)
    return sp.run(list(map(str, args)), check=True, **kw)

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()

def inventory(root):
    return {str(p.relative_to(root)): {'bytes': p.stat().st_size, 'sha256': sha(p)}
            for p in sorted(root.rglob('*')) if p.is_file() and '.git' not in p.parts}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('output', type=Path)
    ap.add_argument('--jobs', type=int, default=8)
    a = ap.parse_args()
    out = a.output.expanduser().resolve()
    assert out.parent == (W/'adsp').resolve() and not out.exists(), 'new direct child of ~/A9-build/mainline/adsp required'
    assert shutil.disk_usage('/').free >= 12_000_000_000, 'need >=12 GB free on /'
    assert sp.check_output(['git','-C',str(BASE),'rev-parse','HEAD'], text=True).strip() == KHEAD
    out.mkdir()
    src, obj = out/'linux', out/'build'
    # Local object database only. No clone URLs and no changes to shared source.
    run('git','clone','--shared','--no-hardlinks',BASE,src)
    run('patch','--fuzz=0','-p1','-i',REPO/'scripts/mainline/adsp/patches/0001-remoteproc-qcom-a9-adsp-manual-start.patch',cwd=src)
    d = src/'arch/arm64/boot/dts/qcom'
    for rel in ('docs/mainline/sm6115-hisense-hlte556n.dts',
                'scripts/mainline/android/sm6115-hisense-hlte556n-android.dts',
                'scripts/mainline/android/sm6115-hisense-hlte556n-android-gpu.dts',
                'scripts/mainline/pmic/a9-pmi632.dtsi',
                'scripts/mainline/thermal/a9-thermal.dtsi',
                'scripts/mainline/bluetooth/a9-bluetooth.dtsi',
                'scripts/mainline/adsp/a9-adsp-d1.dtsi'):
        shutil.copy2(REPO/rel, d/Path(rel).name)
    shutil.copy2(W/'sol-bt/runtime/address.dtsi',d/'a9-bt-private-address.dtsi')
    with (d/'sm6115-hisense-hlte556n-android-gpu.dts').open('a') as f:
        f.write('\n#include "a9-bluetooth.dtsi"\n#include "a9-bt-private-address.dtsi"\n')
    (d/(BOARD+'.dts')).write_text('#include "sm6115-hisense-hlte556n-android-gpu.dts"\n#include "a9-thermal.dtsi"\n')
    (d/(BOARD+'-adsp.dts')).write_text('#include "'+BOARD+'.dts"\n#include "a9-adsp-d1.dtsi"\n')
    with (d/'Makefile').open('a') as f:
        for b in (BOARD, BOARD+'-adsp'): f.write('dtb-$(CONFIG_ARCH_QCOM) += '+b+'.dtb\n')
    obj.mkdir()
    def mk(*args):
        run('make','-C',src,'O='+str(obj),'ARCH=arm64',
            'CROSS_COMPILE=aarch64-unknown-linux-gnu-', '-j'+str(a.jobs), *args)
    mk('defconfig')
    frags = [REPO/'scripts/mainline'/p for p in ('kernel/a9.config','kernel/a9-android.config',
        'kernel/a9-gpu.config','thermal/a9-thermal.config','bluetooth/a9-bluetooth.config','adsp/d1.config')]
    run('bash',src/'scripts/kconfig/merge_config.sh','-m','-O',obj,obj/'.config',*frags,
        cwd=src,env={**os.environ,'ARCH':'arm64'})
    mk('olddefconfig')
    config = (obj/'.config').read_text()
    for line in (REPO/'scripts/mainline/adsp/d1.config').read_text().splitlines():
        if line.startswith(('CONFIG_', '# CONFIG_')): assert line in config, line
    mk('Image.gz','modules')
    mk('DTC_FLAGS=-@','qcom/'+BOARD+'.dtb','qcom/'+BOARD+'-adsp.dtb')
    run('python3','-I',REPO/'scripts/mainline/adsp/check-d1-dt.py',
        obj/'arch/arm64/boot/dts/qcom'/(BOARD+'.dtb'),obj/'arch/arm64/boot/dts/qcom'/(BOARD+'-adsp.dtb'))
    run('python3','-I',REPO/'scripts/mainline/android/gpu/dt-reserved-audit.py',
        obj/'arch/arm64/boot/dts/qcom'/(BOARD+'-adsp.dtb'),W/'sol-pmic/evidence/base_0.dts',
        '--baseline',obj/'arch/arm64/boot/dts/qcom'/(BOARD+'.dtb'),'--gpu-okay')
    receipt = {'repo':sp.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
        'kernel_base':KHEAD,'toolchain':sp.check_output(['aarch64-unknown-linux-gnu-gcc','--version'],text=True),
        'source':inventory(src), 'config':sha(obj/'.config'),'symvers':sha(obj/'Module.symvers'),
        'Image.gz':sha(obj/'arch/arm64/boot/Image.gz'),
        'modules':{str(p.relative_to(obj)):sha(p) for p in sorted(obj.rglob('*.ko'))}}
    (out/'build.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('ADSP-D1-KERNEL PASS',out)

if __name__ == '__main__': main()
