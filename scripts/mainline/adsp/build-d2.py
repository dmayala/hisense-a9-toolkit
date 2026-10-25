#!/usr/bin/env python3
"""Private offline D2 earpiece build over the measured D1, derived from build-d1.py.
Run inside kernel/shell.nix, see README-d2.md.

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
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from offline_d2 import run, disk_gate

REPO = Path(__file__).resolve().parents[3]
W = Path.home() / 'A9-build/mainline'
BASE = W / 'linux-7.2.10-gpu'
KHEAD = 'ab3d710164c746028ff2045e01e2e7b9d610f5ab'
BOARD = 'sm6115-hisense-hlte556n-android-gpu-thermal'

def shared_source_state():
    makefile='arch/arm64/boot/dts/qcom/Makefile'
    changed=sp.check_output(['git','-C',str(BASE),'diff','--name-only','HEAD'],text=True).splitlines()
    assert changed in ([],[makefile]), ('shared tracked source changed',changed)
    if changed:
        original=sp.check_output(['git','-C',str(BASE),'show','HEAD:'+makefile])
        assert (BASE/makefile).read_bytes()==original+b'dtb-$(CONFIG_ARCH_QCOM)\t+= '+BOARD.encode()+b'.dtb\n'
    return sha(BASE/makefile)

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()

def inventory(root):
    return {str(p.relative_to(root)): {'bytes': p.stat().st_size, 'sha256': sha(p)}
            for p in sorted(root.rglob('*')) if p.is_file() and '.git' not in p.parts}

def private_file(path):
    """Detach an input before any write; shared kernel input inodes stay read-only."""
    if path.is_symlink():
        raise ValueError('refusing to modify source symlink: '+str(path))
    if path.exists():
        tmp = path.with_name(path.name+'.d2-private')
        shutil.copy2(path, tmp)
        tmp.replace(path)

def local_source(src):
    # A separate Git index/object reference, with read-only hardlinked input
    # files. Kernel outputs all live in O=. Detach EVERY patched/copied/written
    # source first. This saves ~2 GB while another offline job shares the disk.
    run('git','clone','--shared','--no-checkout',BASE,src)
    for root, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d != '.git']
        rel = Path(root).relative_to(BASE)
        dest = src/rel
        dest.mkdir(exist_ok=True)
        for name in list(dirs):
            p = Path(root)/name
            if p.is_symlink():
                (dest/name).symlink_to(os.readlink(p)); dirs.remove(name)
        for name in files:
            p, q = Path(root)/name, dest/name
            if p.is_symlink(): q.symlink_to(os.readlink(p))
            else: os.link(p, q)
    run('git','-C',src,'read-tree','HEAD')

def apply_patch(src, patch):
    for line in patch.read_text().splitlines():
        if line.startswith('+++ b/'):
            rel = Path(line[6:].split('\t',1)[0])
            assert not rel.is_absolute() and '..' not in rel.parts
            private_file(src/rel)
    run('patch','--fuzz=0','-p1','-i',patch,cwd=src)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('output', type=Path)
    ap.add_argument('--jobs', type=int, default=8)
    a = ap.parse_args()
    out = a.output.expanduser().resolve()
    assert out.parent == (W/'adsp').resolve() and not out.exists(), 'new direct child of ~/A9-build/mainline/adsp required'
    # Read-only hardlinked sources save ~2 GB; allow four GB for private
    # objects, images and audit outputs, in addition to the untouched floor.
    disk_gate(reserve=4_000_000_000)
    assert sp.check_output(['git','-C',str(BASE),'rev-parse','HEAD'], text=True).strip() == KHEAD
    base_makefile=shared_source_state()
    assert sp.check_output(['git','-C',str(REPO),'branch','--show-current'],text=True).strip() == 'adsp'
    # Freeze every kernel recipe/input by content. Unrelated harness work may
    # proceed concurrently; it cannot change the committed kernel input tuple.
    inputs = [Path(__file__).resolve(), REPO/'scripts/mainline/adsp/offline_d2.py',
              REPO/'scripts/mainline/adsp/a9-adsp-d1.dtsi',
              REPO/'scripts/mainline/adsp/a9-adsp-d2.dtsi',
              REPO/'scripts/mainline/adsp/d1.config', REPO/'scripts/mainline/adsp/d2.config',
              REPO/'scripts/mainline/adsp/check-d2-dt.py']
    inputs += sorted((REPO/'scripts/mainline/adsp/d2-kernel').glob('*.patch'))
    inputs += [REPO/p for p in (
        'docs/mainline/sm6115-hisense-hlte556n.dts',
        'scripts/mainline/android/sm6115-hisense-hlte556n-android.dts',
        'scripts/mainline/android/sm6115-hisense-hlte556n-android-gpu.dts',
        'scripts/mainline/pmic/a9-pmi632.dtsi',
        'scripts/mainline/thermal/a9-thermal.dtsi',
        'scripts/mainline/bluetooth/a9-bluetooth.dtsi',
        'scripts/mainline/adsp/patches/0001-remoteproc-qcom-a9-adsp-manual-start.patch',
        'scripts/mainline/adsp/check-d1-dt.py','scripts/mainline/adsp/check-d0-dt.py',
        'scripts/mainline/android/gpu/dt-reserved-audit.py',
        'scripts/mainline/kernel/a9.config','scripts/mainline/kernel/a9-android.config',
        'scripts/mainline/kernel/a9-gpu.config','scripts/mainline/thermal/a9-thermal.config',
        'scripts/mainline/bluetooth/a9-bluetooth.config')]
    prepared_commit = sp.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()
    input_hashes = {}
    for p in inputs:
        raw = p.read_bytes()
        assert sp.check_output(['git','-C',str(REPO),'show',prepared_commit+':'+str(p.relative_to(REPO))]) == raw, ('unfrozen kernel input',p)
        input_hashes[str(p.relative_to(REPO))] = hashlib.sha256(raw).hexdigest()
    out.mkdir()
    src, obj = out/'linux', out/'build'
    # Local object database only. No clone URLs and no changes to shared source.
    local_source(src)
    apply_patch(src,REPO/'scripts/mainline/adsp/patches/0001-remoteproc-qcom-a9-adsp-manual-start.patch')
    patches = sorted((REPO/'scripts/mainline/adsp/d2-kernel').glob('*.patch'))
    assert patches, 'D2 kernel deltas missing'
    for patch in patches:
        apply_patch(src,patch)
    d = src/'arch/arm64/boot/dts/qcom'
    for rel in ('docs/mainline/sm6115-hisense-hlte556n.dts',
                'scripts/mainline/android/sm6115-hisense-hlte556n-android.dts',
                'scripts/mainline/android/sm6115-hisense-hlte556n-android-gpu.dts',
                'scripts/mainline/pmic/a9-pmi632.dtsi',
                'scripts/mainline/thermal/a9-thermal.dtsi',
                'scripts/mainline/bluetooth/a9-bluetooth.dtsi',
                'scripts/mainline/adsp/a9-adsp-d1.dtsi',
                'scripts/mainline/adsp/a9-adsp-d2.dtsi'):
        private_file(d/Path(rel).name)
        shutil.copy2(REPO/rel, d/Path(rel).name)
    private_file(d/'a9-bt-private-address.dtsi')
    shutil.copy2(W/'sol-bt/runtime/address.dtsi',d/'a9-bt-private-address.dtsi')
    with (d/'sm6115-hisense-hlte556n-android-gpu.dts').open('a') as f:
        f.write('\n#include "a9-bluetooth.dtsi"\n#include "a9-bt-private-address.dtsi"\n')
    for name in (BOARD+'.dts', BOARD+'-adsp.dts', BOARD+'-adsp-d2.dts', 'Makefile'):
        private_file(d/name)
    (d/(BOARD+'.dts')).write_text('#include "sm6115-hisense-hlte556n-android-gpu.dts"\n#include "a9-thermal.dtsi"\n')
    (d/(BOARD+'-adsp.dts')).write_text('#include "'+BOARD+'.dts"\n#include "a9-adsp-d1.dtsi"\n')
    (d/(BOARD+'-adsp-d2.dts')).write_text('#include "'+BOARD+'-adsp.dts"\n#include "a9-adsp-d2.dtsi"\n')
    with (d/'Makefile').open('a') as f:
        old=(d/'Makefile').read_text()
        for b in (BOARD, BOARD+'-adsp', BOARD+'-adsp-d2'):
            if b+'.dtb' not in old: f.write('dtb-$(CONFIG_ARCH_QCOM) += '+b+'.dtb\n')
    obj.mkdir()
    def mk(*args):
        run('make','-C',src,'O='+str(obj),'ARCH=arm64',
            'CROSS_COMPILE=aarch64-unknown-linux-gnu-', '-j'+str(a.jobs), *args)
    mk('defconfig')
    frags = [REPO/'scripts/mainline'/p for p in ('kernel/a9.config','kernel/a9-android.config',
        'kernel/a9-gpu.config','thermal/a9-thermal.config','bluetooth/a9-bluetooth.config','adsp/d1.config','adsp/d2.config')]
    run('bash',src/'scripts/kconfig/merge_config.sh','-m','-O',obj,obj/'.config',*frags,
        cwd=src,env={**os.environ,'ARCH':'arm64'})
    mk('olddefconfig')
    config = (obj/'.config').read_text()
    for line in (REPO/'scripts/mainline/adsp/d2.config').read_text().splitlines():
        if line.startswith(('CONFIG_', '# CONFIG_')): assert line in config, line
    mk('Image.gz','modules')
    mk('DTC_FLAGS=-@','qcom/'+BOARD+'.dtb','qcom/'+BOARD+'-adsp.dtb','qcom/'+BOARD+'-adsp-d2.dtb')
    run('python3','-I',REPO/'scripts/mainline/adsp/check-d1-dt.py',
        obj/'arch/arm64/boot/dts/qcom'/(BOARD+'.dtb'),obj/'arch/arm64/boot/dts/qcom'/(BOARD+'-adsp.dtb'))
    run('python3','-I',REPO/'scripts/mainline/adsp/check-d2-dt.py',
        obj/'arch/arm64/boot/dts/qcom'/(BOARD+'-adsp.dtb'),obj/'arch/arm64/boot/dts/qcom'/(BOARD+'-adsp-d2.dtb'))
    run('python3','-I',REPO/'scripts/mainline/android/gpu/dt-reserved-audit.py',
        obj/'arch/arm64/boot/dts/qcom'/(BOARD+'-adsp-d2.dtb'),W/'sol-pmic/evidence/base_0.dts',
        '--baseline',obj/'arch/arm64/boot/dts/qcom'/(BOARD+'.dtb'),'--gpu-okay')
    assert shared_source_state()==base_makefile, 'shared source changed during build'
    assert all(sha(REPO/name)==h for name,h in input_hashes.items()), 'kernel inputs changed during build'
    receipt = {'repo':prepared_commit,
        'repo_at_completion':sp.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
        'kernel_inputs': input_hashes,
        'kernel_base':KHEAD,'toolchain':sp.check_output(['aarch64-unknown-linux-gnu-gcc','--version'],text=True),
        'source':inventory(src), 'config':sha(obj/'.config'),'symvers':sha(obj/'Module.symvers'),
        'Image.gz':sha(obj/'arch/arm64/boot/Image.gz'),
        'modules':{str(p.relative_to(obj)):sha(p) for p in sorted(obj.rglob('*.ko'))}}
    (out/'build.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('ADSP-D2-KERNEL PASS',out)

if __name__ == '__main__': main()
