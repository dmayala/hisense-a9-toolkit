#!/usr/bin/env python3
"""One integ11 image: integ10 + FP10/WLAN policy + rebuilt MTP HAL/current sweep.

Host only. Explicit parent and staged native HAL are build-time inputs. All
image-specific run-time inputs, hooks and the policy compiler travel together.
"""
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


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def member(image, name):
    return sp.check_output(['debugfs','-R','cat /'+name,image],stderr=sp.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, default=ROOT/'integ-20261024-fp10')
    ap.add_argument('--usb', type=Path, default=ROOT/'integ11-host/usb')
    ap.add_argument('--out', type=Path, default=ROOT/'integ-20261024-11')
    args = ap.parse_args(); base, usb, out = [p.resolve() for p in (args.base,args.usb,args.out)]
    assert not out.exists(), 'preserve earlier candidates'
    assert not sp.check_output(['git','-C',REPO,'status','--porcelain','--untracked-files=no'])
    for commit in ('78a81f3','501bb3e','067e68a','272cd0b','5197158'):
        sp.run(['git','-C',REPO,'merge-base','--is-ancestor',commit,'HEAD'],check=True)
    parent = json.loads((base/'integrated-build.json').read_text())
    for name, digest in parent['sha256'].items(): assert sha(base/name) == digest, name
    assert parent['policy_tests']['passed'] and parent['policy_tests']['permissive_domains'] == []
    for name, digest in parent['policy_tests']['payload'].items():
        assert hashlib.sha256(member(base/'vendor.img','etc/selinux/'+name)).hexdigest() == digest, name
    for name in ('etc/init/init.a9-fingerprint.rc','etc/vintf/manifest/fingerprint-a9.xml'):
        assert member(base/'vendor.img',name) == (MAIN/'fingerprint/vendor'/name).read_bytes()
    for path in (MAIN/'android/sepolicy/rootsh-enforcing.cil', MAIN/'fingerprint/policy/fingerprint.cil'):
        assert sha(path) == parent['policy_tests']['source_sha256'][path.name], path.name
    pin = json.loads((usb/'hal-build.json').read_text())
    for name, digest in pin['source_sha256'].items(): assert sha(MAIN/'usb'/name) == digest, name
    binary = usb/'android.hardware.usb.gadget-service.a9'; assert sha(binary) == pin['binary_sha256']
    assert shutil.disk_usage(ROOT).free >= 15*1024**3 + 700*1024**2
    os.umask(0o077); out.mkdir()

    def run(command, name):
        with (out/(name+'.log')).open('w') as log:
            sp.run(list(map(str,command)),check=True,stdout=log,stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= 15*1024**3

    # FP10 already contains both enforcing metadata observation and501bb3e's
    # WLAN cap socket grants. Replace only the5197158 userspace USB executable.
    meta = load('integ11_meta',MAIN/'android/refresh-integrated-vendor.py')
    name = 'bin/hw/android.hardware.usb.gadget-service.a9'
    info = meta.metadata(base/'vendor.img',name)
    label = out/'usb.label'; label.write_bytes(info['label'].encode()+b'\0')
    batch = [f'rm /{name}', f'write {binary} /{name}', f'sif /{name} mode 010{info["mode"]}',
             f'sif /{name} uid {info["uid"]}', f'sif /{name} gid {info["gid"]}',
             f'ea_set -f {label} /{name} security.selinux']
    batch += [f'sif /{name} {k} @{int(info[k],16)}' for k in ('atime','mtime','ctime','crtime')]
    script = out/'install.debugfs'; script.write_text('\n'.join(batch)+'\n')
    run(['cp','--reflink=auto','--sparse=always',base/'vendor.img',out/'vendor.img'],'vendor-copy')
    run(['debugfs','-w','-f',script,out/'vendor.img'],'vendor-install')
    assert member(out/'vendor.img',name) == binary.read_bytes()
    assert meta.metadata(out/'vendor.img',name) == info
    files = sorted(meta.vendor_files(base/'vendor.img'))
    assert sorted(meta.vendor_files(out/'vendor.img')) == files
    changed = [p for p in files if member(base/'vendor.img',p) != member(out/'vendor.img',p)]
    assert changed == [name], changed
    run(['e2fsck','-fn',out/'vendor.img'],'vendor-fsck')
    artifacts = ['boot.img','Image.gz','integ.dtb','recovery_dtbo.img','kernel.config','Module.symvers','vmlinux.symvers']
    for name in artifacts: shutil.copyfile(base/name,out/name)
    shutil.copyfile(base/'module-audit-integ6r3.json',out/'module-audit-integ6r3.json')
    run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3','-P',
         REPO/'scripts/abl-check.py',out/'boot.img','boot','--dtbo',out/'recovery_dtbo.img','--json'],'abl-check')
    assert json.loads((out/'abl-check.log').read_text())['result'] == 'PASS'
    check = out/'init-check'; (check/'etc').mkdir(parents=True)
    run(['debugfs','-R',f'rdump /etc/init {check}/etc',out/'vendor.img'],'init-extract')
    for name in ('passwd','selinux/vendor_property_contexts'):
        (check/Path(name).name).write_bytes(member(out/'vendor.img','etc/'+name))
    run([ROOT/'android/dl/cf_arm64_x/otatools/bin/host_init_verifier','-p',check/'passwd',
         '--property-contexts='+str(check/'vendor_property_contexts'),'--out_vendor='+str(check)],'init-verify')
    for directory in ('acceptance-assets','sleep-logger','runtime'):
        shutil.copytree(base/directory,out/directory,ignore=shutil.ignore_patterns('__pycache__'))
    # Refresh every frozen source that exists in main, including the current
    # MTP counters/waits, FP10 hook, cap error tags, and source-relative helpers.
    runtime = out/'runtime'
    for dest in sorted(runtime.rglob('*')):
        if dest.is_file() and (MAIN/dest.relative_to(runtime)).is_file():
            shutil.copyfile(MAIN/dest.relative_to(runtime),dest)
    compiler = runtime/'tools/bin/secilc'; compiler.parent.mkdir(parents=True)
    shutil.copyfile(ROOT/'android/dl/cf_arm64_x/otatools/bin/secilc',compiler); compiler.chmod(0o755)
    # This compiler links installed Nix host libraries, not archived image dirs.
    assets = json.loads((out/'acceptance-assets/assets.json').read_text())
    for name, digest in assets['assets'].items(): assert sha(out/'acceptance-assets'/name) == digest, name
    for name, digest in assets['sources'].items(): assert sha(MAIN/name) == digest, name
    for name in ('run.sh','run-fbe.sh','run-fingerprint.sh','onb-integ10.py'):
        shutil.copyfile(base/name,out/name); (out/name).chmod(0o755)
    # Both launchers select the same boot/vendor pair and bundled runtime.
    for name in ('run.sh','run-fbe.sh','run-fingerprint.sh'):
        p = out/name; text = p.read_text().replace('export A9_BTRY_SOUND=0',
            'export A9_SECILC="$image/runtime/tools/bin/secilc"\nexport A9_BTRY_SOUND=0')
        p.write_text(text); run(['bash','-n',p],name+'-syntax')
    hashes = {name:sha(out/name) for name in ['vendor.img',*artifacts]}
    sources = {str(p.relative_to(MAIN)):sha(p) for p in
               [Path(__file__),MAIN/'usb/build.py',*[MAIN/'usb'/n for n in pin['source_sha256']]]}
    sources.update({str(p.relative_to(runtime)):sha(p) for p in runtime.rglob('*')
                    if p.is_file() and (MAIN/p.relative_to(runtime)).is_file()})
    result = dict(host_only=True,phone_access=False,hardware_tested=False,source_commit=
        sp.check_output(['git','-C',REPO,'rev-parse','HEAD'],text=True).strip(),
        parent=str(base),parent_sha256=parent['sha256'],sha256=hashes,vendor_changes=changed,
        other_vendor_files_identical=len(files)-1,kernel_dt_ramdisk_modules_unchanged=True,
        stock_boot_identity_preserved=True,hardware_wrapped_fbe_preserved=True,fbe_fixed=False,
        fp10_and_wifi_policy_preserved=True,policy_tests=parent['policy_tests'],
        usb_build=pin,source_sha256=sources,runtime_sha256={str(p.relative_to(out)):sha(p) for p in
            [*runtime.rglob('*'),*list((out/'acceptance-assets').rglob('*')),*list((out/'sleep-logger').rglob('*')),
             *[out/n for n in ('run.sh','run-fbe.sh','run-fingerprint.sh','onb-integ10.py')]] if p.is_file()},
        abi_audit=json.loads((out/'module-audit-integ6r3.json').read_text()),
        fbe_evidence='${A9_BTRY_LOGS}/fbe-evidence.log',fingerprint_evidence='${A9_BTRY_LOGS}/fingerprint-result.json',
        run=str(out/'run.sh'),fingerprint_run=str(out/'run-fingerprint.sh'),free_bytes=shutil.disk_usage(out).free)
    (out/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in hashes.items()))
    (out/'vendor.img.sha256').write_text(hashes['vendor.img']+'  vendor.img\n')
    (out/'READY.txt').write_text('INTEG11 host PASS; hardware qualification pending.\nSweep: '+result['run']+
                               '\nFingerprint: '+result['fingerprint_run']+'\n')
    print('INTEG11 assembled; one boot/vendor pair with both launchers',flush=True)


if __name__ == '__main__': main()
