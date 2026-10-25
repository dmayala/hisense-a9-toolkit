#!/usr/bin/env python3
"""Host-only integ8 FBE diagnostic candidate on the immutable 6r3 image pair.

This does not repair an incompatible key or claim a successful wrapped mount.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent
ROOT = Path('/home/user/A9-build/mainline')
BASE = ROOT/'integ-20261023-6r3'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def member(image, name):
    return sp.check_output(['debugfs', '-R', 'cat /'+name, image], stderr=sp.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=ROOT/'integ-20261024-8')
    ap.add_argument('--stage', type=Path, default=ROOT/'integ8-fbe-host')
    ap.add_argument('--stage-only', action='store_true')
    # Any exception below the repository's reserve needs explicit user approval.
    ap.add_argument('--reserve-gib', type=int, choices=[14, 15], default=15)
    a = ap.parse_args(); stage = a.stage.resolve(); out = a.out.resolve()
    assert sha(BASE/'boot.img') == '8650aa7fd12d8b8ea48d681ccfd41f819b0fd3615b927d686f67b6d8f04d7b24'
    assert sha(BASE/'vendor.img') == '62fb305c349c8ea6a50bd6f475ac2a81f367a3c0332cd8d2781eb01e7ba1131d'
    # FP8 is a source-merged init-only change on this exact 6r3 baseline.
    fp_name = 'etc/init/init.a9-fingerprint.rc'
    fp_source = MAIN/'fingerprint/vendor'/fp_name
    fp_receipt = json.loads((ROOT/'integ-20261024-fp8/integrated-build.json').read_text())
    sp.run(['git', '-C', MAIN, 'merge-base', '--is-ancestor', fp_receipt['source_commit'], 'HEAD'], check=True)
    assert fp_receipt['parent'] == str(BASE) and fp_receipt['vendor_changes'] == [fp_name]
    assert fp_receipt['kernel_and_boot_unchanged']
    assert sha(fp_source) == fp_receipt['source_sha256']['scripts/mainline/fingerprint/vendor/'+fp_name]
    assert member(ROOT/'integ-20261024-fp8/vendor.img',fp_name) == fp_source.read_bytes()
    tests = json.loads((stage/'tests.json').read_text())
    assert len(tests['groups']) == 5 and all(t['passed'] for t in tests['groups'])
    assert tests['actual_android_arm64_qemu']
    assert json.loads((stage/'shell-trace-test.json').read_text())['passed']
    for name, digest in tests['source_sha256'].items(): assert sha(HERE/name) == digest, name
    assert sha(stage/'liba9-fbe-trace.so') == tests['library_sha256']
    pin = json.loads((ROOT/'soc-sleep-20261024/build.json').read_text())
    sleep_binary = ROOT/'soc-sleep-20261024/a9-resume-stats'
    # The source and binary are also pinned in the original resume logger receipt.
    assert sha(sleep_binary) == '54ab24f017a7892fe7926c9114c3339a7ef00a81f205db21a5d9f12310440abb'
    assert sha(MAIN/'android/gnss-outdoor-fix4/resume-stats.c') == '669c5a0372bfa314098b5ad6197688d8bb687c8992893ea9baa88ab142218ede'
    assert pin
    rc_name = 'etc/init/init.a9-wrapped-keymaster.rc'
    rc = member(BASE/'vendor.img', rc_name).decode()
    anchor = 'service vendor.keymaster-4-1 /vendor/bin/hw/android.hardware.keymaster@4.1-service-qti\n'
    assert rc.count(anchor) == 1 and 'LD_PRELOAD' not in rc
    rc = rc.replace(anchor, anchor+'    setenv LD_PRELOAD /vendor/lib64/a9-wrapped-stock/liba9-fbe-trace.so\n')
    prepared = stage/'keymaster.rc'; prepared.write_text(rc)
    if a.stage_only:
        print('INTEG8 source/tests/base/logger pins PASS; image not assembled; FBE unresolved')
        return
    assert not out.exists(), 'preserve partial and delivered candidates'
    assert not sp.check_output(['git', '-C', MAIN, 'status', '--porcelain', '--untracked-files=no']), 'freeze tested sources first'
    floor = a.reserve_gib*1024**3
    budget = (BASE/'vendor.img').stat().st_blocks*512 + 60*1024**2
    assert shutil.disk_usage(ROOT).free >= floor+budget, 'disk reserve plus declared image budget unavailable'
    os.umask(0o077); out.mkdir()
    def run(command, name):
        with (out/(name+'.log')).open('w') as log:
            sp.run(list(map(str, command)), check=True, stdout=log, stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= floor
    spec = importlib.util.spec_from_file_location('meta8', MAIN/'android/refresh-integrated-vendor.py')
    meta = importlib.util.module_from_spec(spec); spec.loader.exec_module(meta)
    overlay = out/'overlay'; overlay.mkdir()
    payload = {fp_name: fp_source, 'bin/a9ml-wrapped-start.sh': HERE/'vendor/bin/a9ml-wrapped-start.sh',
               rc_name: prepared, 'lib64/a9-wrapped-stock/liba9-fbe-trace.so': stage/'liba9-fbe-trace.so'}
    commands = []; metadata = {}
    for i, (name, source) in enumerate(payload.items()):
        old = name if name != 'lib64/a9-wrapped-stock/liba9-fbe-trace.so' else 'lib64/a9-wrapped-stock/libqtikeymaster4.so'
        info = meta.metadata(BASE/'vendor.img', old); metadata[name] = info
        dest = overlay/name; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, dest)
        label = out/f'label-{i}'; label.write_bytes(info['label'].encode()+b'\0')
        if old == name: commands += ['rm /'+name]
        commands += [f'write {dest} /{name}', f'sif /{name} mode 010{info["mode"]}',
                     f'sif /{name} uid {info["uid"]}', f'sif /{name} gid {info["gid"]}',
                     f'ea_set -f {label} /{name} security.selinux']
        commands += [f'sif /{name} {k} @{int(info[k],16)}' for k in ('atime','mtime','ctime','crtime')]
    batch = out/'install.debugfs'; batch.write_text('\n'.join(commands)+'\n')
    image = out/'vendor.img'
    run(['cp','--reflink=auto','--sparse=always',BASE/'vendor.img',image], 'vendor-copy')
    run(['debugfs','-w','-f',batch,image], 'vendor-install')
    for name, source in payload.items():
        assert member(image,name) == source.read_bytes(), name
        assert meta.metadata(image,name) == metadata[name], name
    unchanged = 0
    for name in meta.vendor_files(BASE/'vendor.img'):
        if name in payload: continue
        assert member(image,name) == member(BASE/'vendor.img',name), name
        unchanged += 1
    for name in ('boot.img','Image.gz','integ.dtb','kernel.config','Module.symvers','vmlinux.symvers','recovery_dtbo.img'):
        shutil.copyfile(BASE/name,out/name); assert sha(BASE/name) == sha(out/name)
    run(['e2fsck','-fn',image], 'vendor-fsck')
    check = out/'init-check'; (check/'etc').mkdir(parents=True)
    run(['debugfs','-R',f'rdump /etc/init {check}/etc',image], 'init-extract')
    for n in ('passwd','selinux/vendor_property_contexts'):
        (check/Path(n).name).write_bytes(member(image,'etc/'+n))
    run([ROOT/'android/dl/cf_arm64_x/otatools/bin/host_init_verifier', '-p',check/'passwd',
         '--property-contexts='+str(check/'vendor_property_contexts'), '--out_vendor='+str(check)], 'init-verify')
    shutil.copytree(BASE/'acceptance-assets',out/'acceptance-assets')
    assets = json.loads((out/'acceptance-assets/assets.json').read_text())
    for name, digest in assets['assets'].items(): assert sha(out/'acceptance-assets'/name) == digest
    for name, digest in assets['sources'].items(): assert sha(MAIN/name) == digest, name
    sleep = out/'sleep-logger'; sleep.mkdir()
    for name in ('onb.py','sleep.sh','extract.py','resume-stats.c'):
        shutil.copyfile(MAIN/'android/gnss-outdoor-fix4'/name,sleep/name)
    shutil.copyfile(sleep_binary,sleep/'a9-resume-stats')
    # Freeze the short collector and its existing native helper with this build.
    # Nothing is executed here; only the future phone owner runs run-fbe.sh.
    hooks = out/'hooks'; (hooks/'reboot').mkdir(parents=True); (hooks/'boot-deadline').mkdir()
    shutil.copyfile(HERE/'fbe-onb.py',hooks/'reboot/fbe-onb.py')
    (hooks/'reboot/fbe-onb.py').chmod(0o755)
    shutil.copyfile(MAIN/'boot-deadline/onb-root.py',hooks/'boot-deadline/onb-root.py')
    runner = out/'run-fbe.sh'
    runner.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
                      'image='+shlex.quote(str(out))+'\n'
                      'export A9_BTRY_SOUND=0 A9_BTRY_HOLD=120\n'
                      'export A9_BTRY_ONB="$image/hooks/reboot/fbe-onb.py"\n'
                      'exec /home/user/A9-build/mainline/b-try.sh "$image/boot.img" "$image/vendor.img"\n')
    runner.chmod(0o755)
    run(['bash','-n',runner], 'launcher-syntax')
    result = dict(host_only=True, hardware_tested=False, fbe_fixed=False,
                  unresolved='existing metadata key Begin -8; authorized modes not in retained sweeps',
                  base=str(BASE), source_commit=sp.check_output(['git','-C',MAIN,'rev-parse','HEAD'],text=True).strip(),
                  changed={n:sha(p) for n,p in payload.items()}, unchanged_regular_files=unchanged,
                  source_sha256={str(p.relative_to(MAIN)):sha(p) for p in
                                 [Path(__file__),HERE/'fbe-onb.py',HERE/'fbe-trace.c',HERE/'fbe-trace-abi.h',
                                  HERE/'vendor/bin/a9ml-wrapped-start.sh',fp_source]},
                  boot_kernel_dt_unchanged=True, fsck='PASS', init_verifier='PASS',
                  inherited_module_abi=json.loads((BASE/'integrated-build.json').read_text())['module_abi'],
                  inherited_abl='PASS byte-identical 6r3/integ7 boot',
                  fp8_init_sha256=sha(fp_source), fp8_source_commit=fp_receipt['source_commit'],
                  sleep_logger_commit='02d38b6f46075d7e58ac0f200da2444fe36a016a',
                  sleep_logger_sha256={p.name:sha(p) for p in sleep.iterdir()},
                  runtime_sha256={str(p.relative_to(out)):sha(p) for p in [runner,*hooks.rglob('*.py')]},
                  fbe_evidence='${A9_BTRY_LOGS}/fbe-evidence.log (also onb.log)',
                  no_added_format_key_deletion_or_crypto_fallback=True,
                  reserve_gib=a.reserve_gib, free_bytes=shutil.disk_usage(out).free,
                  sha256={n:sha(out/n) for n in ('boot.img','vendor.img','Image.gz','integ.dtb','kernel.config')})
    (out/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(v+'  '+n+'\n' for n,v in result['sha256'].items()))
    (out/'STATUS.txt').write_text('HOST DIAGNOSTIC CANDIDATE ONLY\nFBE fix and zero sweep FAILs remain unproven. No device access.\n')
    print('INTEG8 diagnostic image assembled; FBE remains unresolved', out)


if __name__ == '__main__': main()
