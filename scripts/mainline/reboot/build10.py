#!/usr/bin/env python3
"""Host-only integ10: stock boot identity, FBE tracing, FP9, enforcing policy.

Build-time inputs may be explicit older images. Every run-time input is copied
under this output or belongs to the persistent repository/tool installation.
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
REPO = MAIN.parents[1]
ROOT = Path('/home/user/A9-build/mainline')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def member(image, name):
    return sp.check_output(['debugfs', '-R', 'cat /'+name, image], stderr=sp.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, default=ROOT/'integ-20261024-9')
    ap.add_argument('--stage', type=Path, default=ROOT/'integ10-host')
    ap.add_argument('--out', type=Path, default=ROOT/'integ-20261024-10')
    args = ap.parse_args(); base, stage, out = [p.resolve() for p in (args.base, args.stage, args.out)]
    assert not out.exists(), 'preserve existing candidate'
    assert not sp.check_output(['git', '-C', REPO, 'status', '--porcelain', '--untracked-files=no'])
    for commit in ('e1f6df0', '77b60ba'):
        sp.run(['git', '-C', REPO, 'merge-base', '--is-ancestor', commit, 'HEAD'], check=True)
    parent = json.loads((base/'integrated-build.json').read_text())
    for name, digest in parent['sha256'].items(): assert sha(base/name) == digest, name
    assert member(base/'vendor.img', 'etc/init/init.a9-fingerprint.rc') == (MAIN/'fingerprint/vendor/etc/init/init.a9-fingerprint.rc').read_bytes()
    tests = json.loads((stage/'tests.json').read_text())
    assert tests['actual_android_arm64_qemu'] and all(t['passed'] for t in tests['groups'])
    for name, digest in tests['source_sha256'].items(): assert sha(HERE/name) == digest, name
    assert sha(stage/'liba9-fbe-trace.so') == tests['library_sha256']
    assert shutil.disk_usage(ROOT).free > 15*1024**3 + 700*1024**2
    os.umask(0o077); out.mkdir()

    def run(command, name):
        with (out/(name+'.log')).open('w') as log:
            sp.run(list(map(str, command)), check=True, stdout=log, stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= 15*1024**3

    identity = load('integ10_identity', HERE/'stock-boot-identity.py').patch(base/'boot.img', out/'boot.img')
    old, new = (base/'boot.img').read_bytes(), (out/'boot.img').read_bytes()
    assert old[:44] == new[:44] and old[48:] == new[48:]
    # Include77b60ba's actual linked enforcing policy, not merely its source.
    prep = load('integ10_prepare', MAIN/'boot-deadline/prepare-vendor.py')
    prep.prepare(out/'boot.img', base/'vendor.img', out/'vendor.img', out/'vendor-policy')
    meta = load('integ10_meta', MAIN/'android/refresh-integrated-vendor.py')
    payload = {'bin/a9ml-wrapped-start.sh': HERE/'vendor/bin/a9ml-wrapped-start.sh',
               'lib64/a9-wrapped-stock/liba9-fbe-trace.so': stage/'liba9-fbe-trace.so'}
    batch = []; metadata = {}
    for i, (name, source) in enumerate(payload.items()):
        info = meta.metadata(base/'vendor.img', name); metadata[name] = info
        label = out/f'payload-{i}.label'; label.write_bytes(info['label'].encode()+b'\0')
        batch += [f'rm /{name}', f'write {source} /{name}', f'sif /{name} mode 010{info["mode"]}',
                  f'sif /{name} uid {info["uid"]}', f'sif /{name} gid {info["gid"]}',
                  f'ea_set -f {label} /{name} security.selinux']
        batch += [f'sif /{name} {k} @{int(info[k],16)}' for k in ('atime','mtime','ctime','crtime')]
    install = out/'install.debugfs'; install.write_text('\n'.join(batch)+'\n')
    run(['debugfs','-w','-f',install,out/'vendor.img'], 'vendor-install')
    for name, source in payload.items():
        assert member(out/'vendor.img', name) == source.read_bytes(), name
        assert meta.metadata(out/'vendor.img', name) == metadata[name], name
    assert b'(typepermissive ' not in member(out/'vendor.img','etc/selinux/vendor_sepolicy.cil')
    run(['e2fsck','-fn',out/'vendor.img'], 'vendor-fsck')
    for name in ('Image.gz','integ.dtb','recovery_dtbo.img','kernel.config','Module.symvers','vmlinux.symvers'):
        shutil.copyfile(base/name, out/name)
    shutil.copyfile(base/'inherited-module-audit.json',out/'module-audit-integ6r3.json')
    run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3', '-P',
         REPO/'scripts/abl-check.py', out/'boot.img', 'boot', '--dtbo', out/'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((out/'abl-check.log').read_text())['result'] == 'PASS'
    check = out/'init-check'; (check/'etc').mkdir(parents=True)
    run(['debugfs','-R',f'rdump /etc/init {check}/etc',out/'vendor.img'], 'init-extract')
    for name in ('passwd','selinux/vendor_property_contexts'):
        (check/Path(name).name).write_bytes(member(out/'vendor.img','etc/'+name))
    run([ROOT/'android/dl/cf_arm64_x/otatools/bin/host_init_verifier','-p',check/'passwd',
         '--property-contexts='+str(check/'vendor_property_contexts'),'--out_vendor='+str(check)], 'init-verify')
    # Matching executable/APK assets are reused only when all source pins match.
    shutil.copytree(base/'acceptance-assets', out/'acceptance-assets')
    assets = json.loads((out/'acceptance-assets/assets.json').read_text())
    for name, digest in assets['assets'].items(): assert sha(out/'acceptance-assets'/name) == digest
    for name, digest in assets['sources'].items(): assert sha(MAIN/name) == digest, name
    firmware = out/'acceptance-assets/firmware'; firmware.mkdir()
    for name in ('a9-waveform.bin','a9-ctrl-template.bin'):
        shutil.copyfile(ROOT/'firmware/hisense'/name,firmware/name)
    shutil.copytree(base/'sleep-logger', out/'sleep-logger')
    # Freeze the runtime source graph and the policy inputs it loads. Generic
    # a9-env/registry/USB locking stay in the persistent repository as before.
    runtime = out/'runtime'
    names = ['acceptance/onb.py','acceptance/run.sh','play/onb.py',
             'boot-deadline/b-try.sh','boot-deadline/prepare-vendor.py','boot-deadline/onb-root.py',
             'boot-deadline/init.a9-boot-deadline.rc','android/refresh-integrated-vendor.py',
             'android/sepolicy/rootsh-enforcing.cil','reboot/fbe-onb.py','fingerprint/onb.py']
    names += list(assets['sources'])
    for name in names:
        dest = runtime/name; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(MAIN/name,dest)
        if name.endswith(('.py','.sh')): dest.chmod(0o755)
    shutil.copytree(MAIN/'boot-deadline/policy-platform', runtime/'boot-deadline/policy-platform')
    # Prefix only the fixed FBE collector; acceptance keeps its own existing
    # outcome, cleanup, mute and credential semantics.
    hook = out/'onb-integ10.py'
    hook.write_text('#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\nimport subprocess as sp\nimport sys\n'
                    'p=Path(__file__).resolve().parent\n'
                    'if os.environ.get("A9_BTRY_IN_HOOK")!="1": sys.exit(2)\n'
                    'sp.run([sys.executable,"-B",str(p/"runtime/reboot/fbe-onb.py")],check=False)\n'
                    'sys.exit(sp.call([sys.executable,"-B",str(p/"runtime/acceptance/onb.py")]))\n')
    hook.chmod(0o755)
    for name, onb, hold in [('run.sh','onb-integ10.py',3600),('run-fbe.sh','runtime/reboot/fbe-onb.py',120)]:
        runner = out/name
        runner.write_text('#!/usr/bin/env bash\nset -euo pipefail\nimage=$(cd -- "$(dirname -- "$0")" && pwd)\n'
                          'export A9_SWEEP_IMAGE="$image" A9_SWEEP_ASSETS="$image/acceptance-assets"\n'
                          f'export A9_BTRY_SOUND=0 A9_BTRY_HOLD=${{A9_BTRY_HOLD:-{hold}}}\n'
                          f'export A9_BTRY_ONB="$image/{onb}"\n'
                          'exec "$image/runtime/boot-deadline/b-try.sh" "$image/boot.img" "$image/vendor.img"\n')
        runner.chmod(0o755); run(['bash','-n',runner], name+'-syntax')
    changed = [name for name in meta.vendor_files(base/'vendor.img')
               if member(base/'vendor.img',name) != member(out/'vendor.img',name)]
    allowed = set(payload) | set(json.loads((out/'vendor-policy/result.json').read_text())['changed'])
    assert set(changed) <= allowed, changed
    hashes = {name:sha(out/name) for name in ('boot.img','vendor.img','Image.gz','integ.dtb','kernel.config')}
    result = dict(host_only=True, hardware_tested=False, real_data_mount_verified=False,
                  base=str(base), source_commit=sp.check_output(['git','-C',REPO,'rev-parse','HEAD'],text=True).strip(),
                  sha256=hashes, boot_identity=identity, kernel_dt_ramdisk_modules_unchanged=True,
                  vendor_changes=changed, fp9_preserved=True, enforcing_root_console=True,
                  no_added_format_key_deletion_or_crypto_fallback=True,
                  fbe_evidence='${A9_BTRY_LOGS}/fbe-evidence.log',
                  abi_audit=json.loads((out/'module-audit-integ6r3.json').read_text()),
                  source_sha256={str(p.relative_to(MAIN)):sha(p) for p in
                     [HERE/'build10.py', HERE/'stock-boot-identity.py', HERE/'fbe-trace.c',HERE/'fbe-trace-abi.h',*map(lambda n:MAIN/n,names)]},
                  runtime_sha256={str(p.relative_to(out)):sha(p) for p in
                     [*runtime.rglob('*'), hook,out/'run.sh',out/'run-fbe.sh'] if p.is_file() and '__pycache__' not in p.parts},
                  free_bytes=shutil.disk_usage(out).free)
    (out/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    # prepare() wrote a sidecar before the trace overlay; finalize it too.
    (out/'vendor.img.sha256').write_text(hashes['vendor.img']+'  vendor.img\n')
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in hashes.items()))
    (out/'READY.txt').write_text('HOST PASS. Stock boot identity restored; real FBE reopen requires owner run.\n'
                               'Sweep: '+str(out/'run.sh')+'\nFBE only: '+str(out/'run-fbe.sh')+'\n')
    print('INTEG10 host assembly PASS', out)


if __name__ == '__main__': main()
