#!/usr/bin/env python3
"""Host-only 6r3: retain integ7 and restore the merged FIX3 audio closure."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent
ROOT = Path('/home/user/A9-build/mainline')


def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def member(image, name):
    return sp.check_output(['debugfs', '-R', 'cat /'+name, image], stderr=sp.DEVNULL)


def stage_q6(stage, base):
    """Rebuild FIX3 for integ7's Unicode-enabled kernel, never reuse old CRCs."""
    helper = load('r3tools', HERE/'build.py')
    env = dict(os.environ, PATH=':'.join(helper.TOOLS)+':'+os.environ['PATH'])
    source = ROOT/'linux-7.2.10'; prepared = ROOT/'integ-20261023-fp6/obj'
    inputs = {str(p):sha(p) for p in [MAIN/'audio/output/q6asm-reprepare.patch', base/'kernel.config',
                base/'Module.symvers', *[(source/'sound/soc/qcom/qdsp6')/n for n in ['q6asm.c','q6asm-dai.c']]]}
    receipt = stage/'build.json'
    if receipt.exists():
        r = json.loads(receipt.read_text()); assert r['inputs'] == inputs
        for n,h in r['modules'].items(): assert sha(stage/n) == h
        return stage
    assert not stage.exists(), 'inspect partial Q6 build before retry'
    assert shutil.disk_usage(ROOT).free >= 15*1024**3+100*1024**2
    stage.mkdir(); obj = stage/'obj'; obj.mkdir(); ext = stage/'source'; ext.mkdir()
    for n in ('include','scripts','arch/arm64/include/generated'):
        shutil.copytree(prepared/n,obj/n,symlinks=True)
    for n in ('.config','Module.symvers'): shutil.copyfile(prepared/n,obj/n)
    assert sha(obj/'.config') == sha(base/'kernel.config')
    # Use the final kernel export CRCs and exclude the two modules rebuilt here.
    rows = [l for l in (base/'Module.symvers').read_text().splitlines()
            if Path(l.split()[2]).name not in ('q6asm','q6asm-dai')]
    (obj/'Module.symvers').write_text('\n'.join(rows)+'\n')
    (obj/'Makefile').write_text('export KBUILD_OUTPUT = '+str(obj)+'\ninclude '+str(source/'Makefile')+'\n')
    os.utime(obj/'include/config/auto.conf',None)
    for p in (source/'sound/soc/qcom/qdsp6').glob('*.h'): shutil.copyfile(p,ext/p.name)
    for n in ('q6asm.c','q6asm-dai.c'): shutil.copyfile(source/'sound/soc/qcom/qdsp6'/n,ext/n)
    with (stage/'compile.log').open('w') as log:
        sp.run(['patch','--batch','--fuzz=0','-p1','-d',ext,'-i',MAIN/'audio/output/q6asm-reprepare.patch'],check=True,stdout=log,stderr=sp.STDOUT)
        (ext/'Makefile').write_text('obj-m += q6asm.o q6asm-dai.o\n')
        sp.run(['make','-C',source,'O='+str(obj),'M='+str(ext),'ARCH=arm64',
                'CROSS_COMPILE=aarch64-unknown-linux-gnu-','HOSTCC=gcc','LOCALVERSION=+','-j2','modules'],
               check=True,env=env,stdout=log,stderr=sp.STDOUT)
    modules = {}
    for n in ('q6asm.ko','q6asm-dai.ko'):
        shutil.copyfile(ext/n,stage/n)
        sp.run(['aarch64-unknown-linux-gnu-strip','--strip-debug',stage/n],check=True,env=env)
        modules[n] = sha(stage/n)
    receipt.write_text(json.dumps(dict(inputs=inputs,modules=modules),indent=2)+'\n')
    return stage


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, default=ROOT/'integ-20261023-fp7')
    ap.add_argument('--out', type=Path, default=ROOT/'integ-20261023-6r3')
    ap.add_argument('--stage', type=Path, default=ROOT/'6r3-host/q6')
    ap.add_argument('--stage-only', action='store_true')
    a = ap.parse_args(); base = a.base.resolve(); out = a.out.resolve()
    if a.stage_only:
        stage_q6(a.stage.resolve(),ROOT/'integ-20261023-7'); print('6r3 Q6 source build PASS'); return
    assert not out.exists(), 'preserve any partial or delivered candidate'
    assert not sp.check_output(['git','-C',MAIN,'status','--porcelain','--untracked-files=no']), 'merge tested source first'
    assert sha(base/'boot.img') == '8650aa7fd12d8b8ea48d681ccfd41f819b0fd3615b927d686f67b6d8f04d7b24'
    assert sha(base/'vendor.img') == '6cf5c7de7568596ce48658974c32c4e2ab5187f24380ca55dfd911b6f2c91b4d'
    integrated = ROOT/'integ-20261023-7'
    assert sha(base/'kernel.config') == sha(integrated/'kernel.config')
    floor = 15*1024**3
    assert shutil.disk_usage(base).free >= floor+(base/'vendor.img').stat().st_blocks*512+100*1024**2
    os.umask(0o077); out.mkdir()
    def run(argv, label):
        with (out/(label+'.log')).open('w') as f:
            sp.run(list(map(str,argv)),check=True,stdout=f,stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= floor
    meta = load('r3meta', MAIN/'android/refresh-integrated-vendor.py')
    fix = load('r3fix', MAIN/'audio/output/build-fix3.py')
    overlay = out/'overlay'; overlay.mkdir()
    tiny = 'lib64/a9-audio/libtinyalsav2.so'
    p = overlay/tiny; p.parent.mkdir(parents=True); p.write_bytes(fix.fix_tinyalsa(member(base/'vendor.img',tiny)))
    payload = {tiny:p}
    fixed = stage_q6(a.stage.resolve(),integrated)
    pins = json.loads((fixed/'build.json').read_text())['modules']
    for name, digest in pins.items():
        assert sha(fixed/name) == digest
        p = overlay/'lib/modules'/name; p.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(fixed/name,p); payload['lib/modules/'+name] = p
    commands = []
    for i,(name,source) in enumerate(payload.items()):
        info = meta.metadata(base/'vendor.img',name); label = out/('label-'+str(i)); label.write_bytes(info['label'].encode()+b'\0')
        commands += ['rm /'+name,f'write {source} /{name}',f'sif /{name} mode 010{info["mode"]}',
                     f'sif /{name} uid {info["uid"]}',f'sif /{name} gid {info["gid"]}',f'ea_set -f {label} /{name} security.selinux']
        commands += [f'sif /{name} {k} @{int(info[k],16)}' for k in ('atime','mtime','ctime','crtime')]
    batch = out/'install.debugfs'; batch.write_text('\n'.join(commands)+'\n')
    image = out/'vendor.img'
    run(['cp','--reflink=auto','--sparse=always',base/'vendor.img',image],'vendor-copy')
    run(['debugfs','-w','-f',batch,image],'vendor-install')
    for name,p in payload.items():
        assert member(image,name) == p.read_bytes()
        assert meta.metadata(image,name) == meta.metadata(base/'vendor.img',name)
    unchanged = 0
    for name in meta.vendor_files(base/'vendor.img'):
        if name in payload: continue
        assert member(image,name) == member(base/'vendor.img',name),name
        unchanged += 1
    for name in ('boot.img','Image.gz','integ.dtb','kernel.config','Module.symvers','vmlinux.symvers','recovery_dtbo.img'):
        shutil.copyfile(base/name,out/name); assert sha(base/name) == sha(out/name)
    run(['e2fsck','-fn',image],'vendor-fsck')
    audit = out/'audit-modules.py'; shutil.copyfile(integrated/'audit-modules.py',audit)
    run(['python3','-B',audit,out,'integ6r3',image],'module-audit')
    abi = json.loads((out/'module-audit-integ6r3.json').read_text())['vendors'][out.name]
    assert abi['modules'] == 101 and abi['failing_modules'] == 0
    assets = out/'acceptance-assets'
    shutil.copytree(integrated/'acceptance-assets',assets)
    asset_manifest = json.loads((assets/'assets.json').read_text())
    for name,h in asset_manifest['assets'].items(): assert sha(assets/name) == h,name
    for name,h in asset_manifest['sources'].items(): assert sha(MAIN/name) == h,name
    # No rc, policy, DT, radio or firmware changes: preserve integ7's verified
    # startup, fingerprint, safety and stock-radio closure byte-for-byte.
    result = dict(host_only=True,hardware_tested=False,base=str(base),
                  base_vendor_sha256=sha(base/'vendor.img'),changed={n:sha(p) for n,p in payload.items()},
                  unchanged_regular_files=unchanged,module_abi={k:v for k,v in abi.items() if k!='details'},
                  boot_kernel_dt_unchanged=True,fsck='PASS',
                  inherited_checks='integ7 ABL/init; all boot, rc, policy and firmware byte-identical',
                  source_commit=sp.check_output(['git','-C',MAIN,'rev-parse','HEAD'],text=True).strip(),
                  source_sha256={str(p.relative_to(MAIN)):sha(p) for p in [Path(__file__),MAIN/'audio/output/build-fix3.py',MAIN/'audio/output/q6asm-reprepare.patch',
                                  MAIN/'audio/output/test-route.c',MAIN/'acceptance/onb.py',MAIN/'acceptance/Probe.java',MAIN/'acceptance/run.sh',
                                  MAIN/'fingerprint/run.sh',MAIN/'android/gnss-outdoor-fix4/run.sh']},
                  acceptance_assets=asset_manifest,
                  sha256={n:sha(out/n) for n in ['boot.img','vendor.img','Image.gz','integ.dtb','kernel.config']},
                  free_bytes=shutil.disk_usage(out).free)
    (out/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(v+'  '+k+'\n' for k,v in result['sha256'].items()))
    print('6r3 INTEG7 AUDIO RESTORE HOST PASS',out)


if __name__ == '__main__': main()
