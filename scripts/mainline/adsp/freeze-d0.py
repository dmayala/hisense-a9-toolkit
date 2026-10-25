#!/usr/bin/env python3
"""Finish a private build: linked-module closure/CRCs, snapshot, boot, vendor.

Uses cached local Android/Cuttlefish/Mesa inputs. NIX_CONFIG disables downloads
and builders. Run in the kernel shell; no adb/fastboot, mount or phone access.
"""
import argparse
import json
import gzip
import os
from pathlib import Path
import shutil
import struct
import subprocess as sp
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from importlib.machinery import SourceFileLoader
b = SourceFileLoader('build_d0',str(Path(__file__).with_name('build-d0.py'))).load_module()
run, sha, inventory, REPO, W, BOARD = b.run, b.sha, b.inventory, b.REPO, b.W, b.BOARD

def freeze(out):
    out = out.expanduser().resolve()
    assert out.parent == W/'adsp' and (out/'build.json').is_file()
    obj, src, snap, aw = out/'build',out/'linux',out/'snapshot',out/'android'
    assert not snap.exists() and not aw.exists(), 'fresh image outputs required'
    actual=set((obj/'.config').read_text().splitlines())
    wanted={}
    for rel in ('kernel/a9.config','kernel/a9-android.config','kernel/a9-gpu.config',
                'thermal/a9-thermal.config','bluetooth/a9-bluetooth.config','adsp/d0.config'):
        for line in (REPO/'scripts/mainline'/rel).read_text().splitlines():
            if line.startswith('CONFIG_'): wanted[line.split('=')[0]]=line
            elif line.startswith('# CONFIG_'): wanted[line.split()[1]]=line
    for name,line in wanted.items():
        assert line in actual or (line.startswith('# ') and not any(x.startswith(name+'=') for x in actual)),line
    image=gzip.decompress((obj/'arch/arm64/boot/Image.gz').read_bytes())
    assert image[56:60]==b'ARMd' and struct.unpack_from('<Q',image,16)[0]<=36024320
    aw.mkdir()
    (aw/'dl').symlink_to(W/'android/dl',target_is_directory=True)
    # All optional builders see private outputs and already cached inputs.
    for rel in ('aosp','gsi-libs'):
        shutil.copytree(W/'sensors'/rel,out/'sensors'/rel)
    shutil.copytree(W/'gpu/out/mesa',out/'gpu/out/mesa')
    (out/'gpu/out/probe').mkdir()
    shutil.copytree(W/'gpu/fw/stage',out/'gpu/fw/stage')
    shutil.copy2(W/'gpu/fw/stage.SHA256SUMS',out/'gpu/fw/stage.SHA256SUMS')
    run('podman','image','exists','localhost/a9-android-tools')
    env = {**os.environ,'NIX_CONFIG':'substitute = false\nsubstituters =\nbuilders =\n',
        'A9_ANDROID':str(aw),'A9_KSRC':str(src),'A9_GPU':'1','A9_THERMAL':'1',
        'A9_BLUETOOTH':'1','A9_SNAP_DTB':BOARD+'-adsp.dtb',
        'A9_SENSORS_OUT':str(out/'sensors'),'A9_HEALTH':'real','A9_THERMAL_HAL':'1',
        'A9_GPU_WORK':str(out/'gpu'),
        'A9_BT_HAL_BUNDLE':str(W/'sol-bt/r2/hal-bundle'),
        'A9_ANDROID_WORK':str(aw)}
    run('bash',REPO/'scripts/mainline/android/build-frontlight-module.sh',obj,env=env)
    env['A9_FRONTLIGHT_KO'] = str(aw/'work/frontlight-mod/a9-frontlight.ko')
    run(obj/'scripts/dtc/dtc','-@','-q','-I','dts','-O','dtb','-o',out/'null-overlay.dtbo',
        REPO/'docs/mainline/hlte556n-null-overlay.dts')
    run('python3','-I',REPO/'scripts/mainline/mkdtbo.py',out/'recovery_dtbo.img',out/'null-overlay.dtbo')
    env['A9_DTBO']=str(out/'recovery_dtbo.img')
    run('bash',REPO/'scripts/mainline/android/snapshot-kernel.sh',obj,snap,env=env)
    # Full ELF dependency closure, plus runtime providers that are not ELF deps.
    mods = {p.stem.replace('-','_'):p for p in obj.rglob('*.ko')}
    mods['a9_frontlight'] = Path(env['A9_FRONTLIGHT_KO'])
    order = []
    def field(p,k): return sp.check_output(['modinfo','-F',k,str(p)],text=True).strip()
    def visit(n):
        n=n.replace('-','_')
        if n in order: return
        for d in field(mods[n],'depends').split(','):
            if d: visit(d)
        order.append(n)
    for n in ('qrtr','qrtr_smd','qcom_pdr_msg','qcom_pd_mapper','qcom_q6v5_pas'): visit(n)
    d0=snap/'d0'; (d0/'modules').mkdir(parents=True)
    sym = {x[1]:x[0].lower() for x in (l.split() for l in (obj/'Module.symvers').read_text().splitlines())}
    crc = {}
    for n,p in mods.items():
        if n not in order and not (snap/'modules'/p.name).exists(): continue
        imported = sp.check_output(['modprobe','--show-modversions',str(p)],text=True)
        for line in imported.splitlines():
            value,name=line.split()
            assert sym.get(name) == value.lower(), (p,name,value,sym.get(name))
        crc[p.name]={'sha256':sha(p),'imports':imported,'vermagic':field(p,'vermagic')}
        if n in order: shutil.copy2(p,d0/'modules'/p.name)
    assert len({v['vermagic'] for v in crc.values()}) == 1
    # No autoload alias/softdep. PAS has exactly one permitted explicit loader.
    (d0/'modules.d0').write_text(''.join(mods[n].name+'\n' for n in order))
    with (d0/'modules/modules.dep').open('w') as f:
        for n in order:
            f.write(mods[n].name+': '+' '.join(mods[x.replace('-','_')].name for x in field(mods[n],'depends').split(',') if x)+'\n')
    for x in ('modules.alias','modules.softdep'): (snap/'modules'/x).write_text('')
    (snap/'modules/modules.options').write_text('msm.ko separate_gpu_kms=1\n')
    (d0/'modules/modules.options').write_text('qcom_q6v5_pas.ko a9_adsp_manual_start=1\n')
    shutil.copy2(obj/'Module.symvers',snap/'Module.symvers')
    (out/'module-crcs.json').write_text(json.dumps(crc,indent=2)+'\n')
    sums = lambda root: ''.join(v['sha256']+'  ./'+k+'\n' for k,v in inventory(root).items() if k!='SHA256SUMS')
    (snap/'SHA256SUMS').write_text(sums(snap))
    # mkramdisk uses its own boolean A9_ANDROID; keep workspace explicit.
    renv = {**env,'A9_MAINLINE':str(out),'A9_ANDROID_KERNEL':str(snap)}
    run('bash',REPO/'scripts/mainline/android/mkramdisk.sh','--eink','--gpu','--check',env=renv)
    env['A9_THERMAL_SNAP']=str(snap)
    run('bash',REPO/'scripts/mainline/android/build-vendor.sh','--eink','--gpu',snap,env=env)
    # Firmware stays in a private sidecar: a future coordinator stages it only
    # after tuple admission. No mapper JSN daemon or auto-loader is installed.
    (out/'images.json').write_text(json.dumps({
        'snapshot':inventory(snap),'android_out':inventory(aw/'out'),
        'vendor_stage':inventory(aw/'work/vendor-stage-bt'),
        'ramdisk':inventory(aw/'work/ramdisk/root')},indent=2)+'\n')
    tools={}
    for name in ('aarch64-unknown-linux-gnu-gcc','aarch64-unknown-linux-gnu-ld','make','modinfo','modprobe','mkbootimg','debugfs'):
        p=Path(shutil.which(name)).resolve(); tools[name]={'path':str(p),'sha256':sha(p)}
    (out/'freeze-inputs.json').write_text(json.dumps({'tools':tools,
        'nixpkgs':str(Path(sp.check_output(['nix-instantiate','--find-file','nixpkgs'],text=True).strip()).resolve()),
        'cross_compiler_closure':sp.check_output(['nix-store','-qR',tools['aarch64-unknown-linux-gnu-gcc']['path']],text=True).splitlines(),
        'repo':sp.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
        'recipes':{str(p.relative_to(REPO)):sha(p) for directory in ('scripts/mainline/adsp','scripts/mainline/android','scripts/mainline/kernel')
                   for p in (REPO/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'build' not in p.parts},
        'config':sha(obj/'.config'),'symvers':sha(obj/'Module.symvers')},indent=2)+'\n')
    print('ADSP-D0-FREEZE PASS',out)

if __name__ == '__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('output',type=Path)
    freeze(ap.parse_args().output)
