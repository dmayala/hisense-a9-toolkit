#!/usr/bin/env python3
"""Host-only integrated9: image8 + FP9 + tested capture/radio/policy fixes."""
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
REPO = MAIN.parents[1]
ROOT = Path('/home/user/A9-build/mainline')
BASE = ROOT/'integ-20261024-fp9'
IMAGE8 = ROOT/'integ-20261024-8'
STAGE = ROOT/'integ9-host'


def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def member(image, name):
    return sp.check_output(['debugfs','-R','cat /'+name,image],stderr=sp.DEVNULL)


def load(name, path):
    s = importlib.util.spec_from_file_location(name,path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,default=ROOT/'integ-20261024-9')
    ap.add_argument('--stage-only',action='store_true')
    a = ap.parse_args(); out = a.out.resolve()
    parent = json.loads((BASE/'integrated-build.json').read_text())
    assert parent['parent'] == str(IMAGE8)
    assert parent['vendor_changes'] == ['etc/init/init.a9-fingerprint.rc']
    for n,h in parent['sha256'].items(): assert sha(BASE/n) == h,n
    for p in (IMAGE8, BASE):
        handoff = json.loads((p/'source-handoff.json').read_text())
        assert handoff['source_pins_match_main']
        sp.run(['git','-C',REPO,'merge-base','--is-ancestor',handoff['main_commit'],'HEAD'],check=True)
    fp = MAIN/'fingerprint/vendor/etc/init/init.a9-fingerprint.rc'
    assert member(BASE/'vendor.img','etc/init/init.a9-fingerprint.rc') == fp.read_bytes()
    audio = STAGE/'audio-final'; audio_pin = json.loads((audio/'capture-build.json').read_text())
    assert audio_pin['route_groups'] == 5 and audio_pin['base_vendor_sha256'] == sha(IMAGE8/'vendor.img')
    for n,h in audio_pin['source_sha256'].items(): assert sha(REPO/n) == h,n
    adapter = audio/'vendor-overlay/lib64/a9-audio/liba9audioroute.so'
    assert sha(adapter) == audio_pin['library_sha256']
    policy = STAGE/'policy'; policy_pin = json.loads((policy/'build.json').read_text())
    policy_test = json.loads((policy/'tests.json').read_text())
    assert policy_test['passed'] and policy_test['permissive_after'] == []
    assert policy_test['positive_permissions'] == 67 and policy_test['negative_decisions'] == 2
    assert policy_pin['rules_sha256'] == sha(MAIN/'android/sepolicy/rootsh-enforcing.cil')
    assert policy_pin['base_sha256'] == sha(IMAGE8/'vendor.img')
    assert member(BASE/'vendor.img','etc/selinux/vendor_sepolicy.cil') == member(IMAGE8/'vendor.img','etc/selinux/vendor_sepolicy.cil')
    payload = {'lib64/a9-audio/liba9audioroute.so':adapter,
               'etc/init/init.a9ml-stock-radio.rc':MAIN/'cellular/stock/vendor/etc/init/init.a9ml-stock-radio.rc'}
    for n,h in policy_pin['payload'].items():
        source = policy/'selinux'/n; assert sha(source) == h,n
        payload['etc/selinux/'+n] = source
    assets = json.loads((BASE/'acceptance-assets/assets.json').read_text())
    for n,h in assets['assets'].items(): assert sha(BASE/'acceptance-assets'/n) == h,n
    for n,h in assets['sources'].items(): assert sha(MAIN/n) == h,n
    assert not sp.check_output(['git','-C',REPO,'status','--porcelain','--untracked-files=no']), 'freeze sources first'
    if a.stage_only:
        print('INTEG9 parent/source/assets/policy/capture pins PASS; no image written'); return
    assert not out.exists(), 'preserve handed-off and partial candidates'
    floor = 15*1024**3
    assert shutil.disk_usage(ROOT).free >= floor+(BASE/'vendor.img').stat().st_blocks*512+64*1024**2
    os.umask(0o077); out.mkdir()
    def run(command, label):
        with (out/(label+'.log')).open('w') as f:
            sp.run(list(map(str,command)),check=True,stdout=f,stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= floor
    meta = load('integ9_meta',MAIN/'android/refresh-integrated-vendor.py')
    overlay = out/'overlay'; overlay.mkdir(); commands = []; metadata = {}
    for i,(name,source) in enumerate(payload.items()):
        info = meta.metadata(BASE/'vendor.img',name); metadata[name] = info
        dest = overlay/name; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,dest)
        label = out/f'label-{i}'; label.write_bytes(info['label'].encode()+b'\0')
        commands += ['rm /'+name,f'write {dest} /{name}',f'sif /{name} mode 010{info["mode"]}',
                     f'sif /{name} uid {info["uid"]}',f'sif /{name} gid {info["gid"]}',
                     f'ea_set -f {label} /{name} security.selinux']
        commands += [f'sif /{name} {k} @{int(info[k],16)}' for k in ('atime','mtime','ctime','crtime')]
    batch = out/'install.debugfs'; batch.write_text('\n'.join(commands)+'\n')
    image = out/'vendor.img'
    run(['cp','--reflink=auto','--sparse=always',BASE/'vendor.img',image],'vendor-copy')
    run(['debugfs','-w','-f',batch,image],'vendor-install')
    for n,p in payload.items():
        assert member(image,n) == p.read_bytes(),n
        assert meta.metadata(image,n) == metadata[n],n
    files = sorted(meta.vendor_files(BASE/'vendor.img'))
    assert sorted(meta.vendor_files(image)) == files
    unchanged = 0
    for n in files:
        if n in payload: continue
        assert member(image,n) == member(BASE/'vendor.img',n),n
        unchanged += 1
    artifacts = ('boot.img','Image.gz','integ.dtb','kernel.config','Module.symvers','vmlinux.symvers','recovery_dtbo.img')
    for n in artifacts:
        shutil.copyfile(BASE/n,out/n); assert sha(out/n) == sha(BASE/n) == sha(IMAGE8/n),n
    run(['e2fsck','-fn',image],'vendor-fsck')
    check = out/'init-check'; (check/'etc').mkdir(parents=True)
    run(['debugfs','-R',f'rdump /etc/init {check}/etc',image],'init-extract')
    for n in ('passwd','selinux/vendor_property_contexts'):
        (check/Path(n).name).write_bytes(member(image,'etc/'+n))
    run([ROOT/'android/dl/cf_arm64_x/otatools/bin/host_init_verifier','-p',check/'passwd',
         '--property-contexts='+str(check/'vendor_property_contexts'),'--out_vendor='+str(check)],'init-verifier')
    run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3','-P',
         REPO/'scripts/abl-check.py',out/'boot.img','boot','--dtbo',out/'recovery_dtbo.img','--json'],'abl-check')
    assert json.loads((out/'abl-check.log').read_text())['result'] == 'PASS'
    # Every module and kernel/export artifact is byte-identical to image8.
    # Retain its verified 101-module / 6243-import audit rather than relabel a new build.
    audit = json.loads((IMAGE8/'module-audit-integ6r3.json').read_text())
    abi = audit['vendors']['integ-20261023-6r3']
    assert abi['modules'] == 101 and abi['imports_checked'] == 6243
    assert not any(abi[k] for k in ('failing_modules','crc_mismatches','vermagic_mismatches'))
    shutil.copyfile(IMAGE8/'module-audit-integ6r3.json',out/'inherited-module-audit.json')
    for directory in ('acceptance-assets','sleep-logger','hooks'):
        shutil.copytree(BASE/directory,out/directory)
    # Keep all tools for this integrated candidate together, including source
    # files used by the runtime APK identity check. No execution during build.
    runtime = ['acceptance/onb.py','play/onb.py','boot-deadline/onb-root.py',
               'fingerprint/onb.py','reboot/fbe-onb.py',*assets['sources']]
    for n in runtime:
        dest = out/'hooks'/n; dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(MAIN/n,dest)
        if n.endswith('onb.py'): dest.chmod(0o755)
    for name,hook,hold in [('sweep','acceptance/onb.py',3600),('fingerprint','fingerprint/onb.py',240),('fbe','reboot/fbe-onb.py',120)]:
        runner = out/f'run-{name}.sh'
        runner.write_text('#!/usr/bin/env bash\nset -euo pipefail\nimage='+str(out)+'\n'
                          'export A9_BTRY_SOUND=0\nexport A9_BTRY_HOLD=${A9_BTRY_HOLD:-'+str(hold)+'}\n'
                          'export A9_SWEEP_IMAGE="$image" A9_SWEEP_ASSETS="$image/acceptance-assets"\n'
                          'export A9_BTRY_ONB="$image/hooks/'+hook+'"\n'
                          'exec /home/user/A9/scripts/mainline/boot-deadline/b-try.sh "$image/boot.img" "$image/vendor.img"\n')
        runner.chmod(0o755); run(['bash','-n',runner],name+'-launcher-syntax')
    sources = [Path(__file__),MAIN/'android/sepolicy/enforce-rootsh.py',MAIN/'android/sepolicy/rootsh-enforcing.cil',
               MAIN/'acceptance/selftest.py',MAIN/'cameras/STOCK-CAPABILITIES.md',MAIN/'reboot/README9.md',
               *[MAIN/n for n in runtime],*[REPO/n for n in audio_pin['source_sha256']],
               MAIN/'cellular/stock/vendor/etc/init/init.a9ml-stock-radio.rc',fp]
    runtime_pins = {str(p.relative_to(out)):sha(p) for p in [*sorted((out/'hooks').rglob('*')),*sorted(out.glob('run-*.sh'))] if p.is_file()}
    result = dict(host_only=True,phone_access=False,hardware_tested=False,
                  base=str(BASE),integrated_parent=str(IMAGE8),base_sha256=parent['sha256'],
                  source_commit=sp.check_output(['git','-C',REPO,'rev-parse','HEAD'],text=True).strip(),
                  source_sha256={str(p.resolve().relative_to(REPO)):sha(p) for p in sources},
                  runtime_sha256=runtime_pins,changed={n:sha(p) for n,p in payload.items()},
                  unchanged_regular_files=unchanged,boot_kernel_dt_unchanged=True,
                  inherited_module_abi={k:v for k,v in abi.items() if k!='details'},
                  fsck='PASS',init_verifier='PASS',abl='PASS',policy_tests=policy_test,
                  capture_tests='five Android arm64 fixture groups PASS',acceptance_assets=assets,
                  fbe_owner='agent',fbe_fixed=False,
                  unresolved=['real wrapped FBE remains assigned to agent',
                              'old signed Venus firmware has decode-only mainline compatibility',
                              'rear AF and flash require driver/DT/libcamera implementation',
                              'WiFi, AudioRecord, MTP and phone ANR recovery require a new owned sweep'],
                  sha256={n:sha(out/n) for n in ('vendor.img',*artifacts)},free_bytes=shutil.disk_usage(out).free)
    (out/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in result['sha256'].items()))
    (out/'STATUS.txt').write_text('HOST CANDIDATE ONLY; zero FAILs not yet demonstrated.\nSee scripts/mainline/reboot/README9.md and integrated-build.json.\n')
    print('INTEG9 host build PASS',out)


if __name__ == '__main__': main()
