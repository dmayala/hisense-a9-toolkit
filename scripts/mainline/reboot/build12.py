#!/usr/bin/env python3
"""Host-only INTEG12: current main policy, stability evidence, FP12 and FBE12.

Use integrated11 only as a build-time binary baseline. Both launchers use
the adjacent images, frozen runtime, policy inputs, compiler and probe assets.
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
REQUIRED = ('5206c15', '6a1e4b9', '1cf138f', '7663ba9', '0680a12', 'a028120', 'f98cb9f')


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
    ap.add_argument('--base', type=Path, default=ROOT/'integ-20261024-11')
    ap.add_argument('--fbe', type=Path, default=ROOT/'fbe12-host')
    ap.add_argument('--out', type=Path, default=ROOT/'integ-20261024-12')
    ap.add_argument('--reopen-stage', type=Path, default=ROOT/'reopen-host')
    args = ap.parse_args(); base, fbe, out = [p.resolve() for p in (args.base, args.fbe, args.out)]
    assert not out.exists(), 'preserve earlier candidates'
    assert not sp.check_output(['git', '-C', REPO, 'status', '--porcelain', '--untracked-files=no'])
    commit = sp.check_output(['git', '-C', REPO, 'rev-parse', 'HEAD'], text=True).strip()
    for required in REQUIRED:
        sp.run(['git', '-C', REPO, 'merge-base', '--is-ancestor', required, commit], check=True)
    parent = json.loads((base/'integrated-build.json').read_text())
    for name, digest in parent['sha256'].items(): assert sha(base/name) == digest, name
    trace = json.loads((fbe/'tests.json').read_text())
    assert trace['actual_android_arm64_qemu'] and len(trace['groups']) == 5
    assert all(row['passed'] for row in trace['groups'])
    for name, digest in trace['source_sha256'].items(): assert sha(HERE/name) == digest, name
    assert sha(fbe/'liba9-fbe-trace.so') == trace['library_sha256']
    assert shutil.disk_usage(ROOT).free >= 15*1024**3 + 1024**3
    os.umask(0o077); out.mkdir()

    def run(command, name):
        with (out/(name+'.log')).open('w') as log:
            sp.run(list(map(str, command)), check=True, stdout=log, stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= 15*1024**3

    artifacts = ['boot.img', 'Image.gz', 'integ.dtb', 'recovery_dtbo.img',
                 'kernel.config', 'Module.symvers', 'vmlinux.symvers']
    for name in artifacts: shutil.copyfile(base/name, out/name)
    shutil.copyfile(base/'module-audit-integ6r3.json', out/'module-audit-integ6r3.json')
    prep = load('integ12_prepare', MAIN/'boot-deadline/prepare-vendor.py')
    prep.prepare(out/'boot.img', base/'vendor.img', out/'vendor.img', out/'vendor-policy')
    meta = load('integ12_metadata', MAIN/'android/refresh-integrated-vendor.py')
    tools = 'etc/a9-wifi/session/tools/'
    controller = MAIN/'wifi/coordinator-target.sh'
    old = hashlib.sha256(member(base/'vendor.img', tools+'coordinator-target.sh')).hexdigest()
    sums = member(base/'vendor.img', tools+'SHA256SUMS').decode()
    assert sums.count(old+'  coordinator-target.sh\n') == 1
    sums = sums.replace(old+'  coordinator-target.sh\n', sha(controller)+'  coordinator-target.sh\n')
    staged_sums = out/'coordinator-SHA256SUMS'; staged_sums.write_text(sums)
    payload = {tools+'coordinator-target.sh': controller, tools+'SHA256SUMS': staged_sums,
               'bin/a9ml-wrapped-start.sh': HERE/'vendor/bin/a9ml-wrapped-start.sh',
               'lib64/a9-wrapped-stock/liba9-fbe-trace.so': fbe/'liba9-fbe-trace.so'}
    batch = []; metadata = {}
    for i, (name, source) in enumerate(payload.items()):
        info = meta.metadata(base/'vendor.img', name); metadata[name] = info
        label = out/f'payload-{i}.label'; label.write_bytes(info['label'].encode()+b'\0')
        batch += [f'rm /{name}', f'write {source} /{name}', f'sif /{name} mode 010{info["mode"]}',
                  f'sif /{name} uid {info["uid"]}', f'sif /{name} gid {info["gid"]}',
                  f'ea_set -f {label} /{name} security.selinux']
        batch += [f'sif /{name} {k} @{int(info[k],16)}' for k in ('atime','mtime','ctime','crtime')]
    script = out/'install.debugfs'; script.write_text('\n'.join(batch)+'\n')
    run(['debugfs', '-w', '-f', script, out/'vendor.img'], 'vendor-install')
    for name, source in payload.items():
        assert member(out/'vendor.img', name) == source.read_bytes(), name
        assert meta.metadata(out/'vendor.img', name) == metadata[name], name
    files = sorted(meta.vendor_files(base/'vendor.img'))
    assert sorted(meta.vendor_files(out/'vendor.img')) == files
    changed = [name for name in files if member(base/'vendor.img', name) != member(out/'vendor.img', name)]
    allowed = set(payload) | set(json.loads((out/'vendor-policy/result.json').read_text())['changed'])
    assert set(changed) <= allowed, changed
    run(['e2fsck', '-fn', out/'vendor.img'], 'vendor-fsck')
    run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3', '-P',
         REPO/'scripts/abl-check.py', out/'boot.img', 'boot', '--dtbo', out/'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((out/'abl-check.log').read_text())['result'] == 'PASS'
    check = out/'init-check'; (check/'etc').mkdir(parents=True)
    run(['debugfs', '-R', f'rdump /etc/init {check}/etc', out/'vendor.img'], 'init-extract')
    for name in ('passwd', 'selinux/vendor_property_contexts'):
        (check/Path(name).name).write_bytes(member(out/'vendor.img', 'etc/'+name))
    run([ROOT/'android/dl/cf_arm64_x/otatools/bin/host_init_verifier', '-p', check/'passwd',
         '--property-contexts='+str(check/'vendor_property_contexts'), '--out_vendor='+str(check)], 'init-verify')
    for directory in ('acceptance-assets', 'sleep-logger', 'runtime'):
        shutil.copytree(base/directory, out/directory, ignore=shutil.ignore_patterns('__pycache__'))
    runtime = out/'runtime'
    for dest in runtime.rglob('*'):
        source = MAIN/dest.relative_to(runtime)
        if dest.is_file() and source.is_file(): shutil.copyfile(source, dest)
    for name in ('reboot/fbe-public-auth.py', 'reboot/rpmb-evidence.py'):
        shutil.copyfile(MAIN/name, runtime/name)
    compiler = runtime/'tools/bin/secilc'
    shutil.copyfile(ROOT/'android/dl/cf_arm64_x/otatools/bin/secilc', compiler); compiler.chmod(0o755)
    assets = json.loads((out/'acceptance-assets/assets.json').read_text())
    for name, digest in assets['assets'].items(): assert sha(out/'acceptance-assets'/name) == digest, name
    for name, digest in assets['sources'].items(): assert sha(MAIN/name) == digest, name
    hook = out/'onb-integ12.py'
    hook.write_text('#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\nimport subprocess as sp\nimport sys\n'
                    'p=Path(__file__).resolve().parent\n'
                    'if os.environ.get("A9_BTRY_IN_HOOK")!="1": sys.exit(2)\n'
                    'sp.run([sys.executable,"-B",str(p/"runtime/reboot/fbe-onb.py")],check=False)\n'
                    'sys.exit(sp.call([sys.executable,"-B",str(p/"runtime/acceptance/onb.py")]))\n')
    hook.chmod(0o755)
    for name, target, hold in [('run.sh', 'onb-integ12.py', '${A9_BTRY_HOLD:-3600}'),
                               ('run-fingerprint.sh', 'runtime/fingerprint/onb.py', '240')]:
        runner = out/name
        runner.write_text('#!/usr/bin/env bash\nset -euo pipefail\nimage=$(cd -- "$(dirname -- "$0")" && pwd)\n'
                          'export A9_SWEEP_IMAGE="$image" A9_SWEEP_ASSETS="$image/acceptance-assets"\n'
                          'export A9_SECILC="$image/runtime/tools/bin/secilc"\n'
                          f'export A9_BTRY_SOUND=0 A9_BTRY_HOLD={hold}\n'
                          f'export A9_BTRY_ONB="$image/{target}"\n'
                          'exec "$image/runtime/boot-deadline/b-try.sh" "$image/boot.img" "$image/vendor.img"\n')
        runner.chmod(0o755); run(['bash', '-n', runner], name+'-syntax')
    reopen = load('integ_reopen_prepare', HERE/'reopen/prepare.py')
    reopen.install(out, args.reopen_stage.resolve())
    assert not any(p.is_symlink() for p in runtime.rglob('*'))
    hashes = {name:sha(out/name) for name in ['vendor.img', *artifacts]}
    sources = {str(p.relative_to(MAIN)):sha(p) for p in
               [Path(__file__), controller, MAIN/'wifi/audit-opens.awk', HERE/'fbe-trace.c', HERE/'fbe-trace-abi.h']}
    sources.update({str((HERE/name).relative_to(MAIN)):digest for name,digest in trace['source_sha256'].items()})
    sources.update({str(p.relative_to(runtime)):sha(p) for p in runtime.rglob('*')
                    if p.is_file() and (MAIN/p.relative_to(runtime)).is_file()})
    bundled = [*runtime.rglob('*'), *(out/'acceptance-assets').rglob('*'), *(out/'sleep-logger').rglob('*'),
               *(out/'reopen-client').rglob('*'), out/'reopen-manifest.json',
               out/'run-reopen.sh', out/'reopen-before-b.sh', out/'reopen-on-b.sh',
               hook, out/'run.sh', out/'run-fingerprint.sh']
    result = dict(host_only=True, phone_access=False, hardware_tested=False, source_commit=commit,
                  included_commits=list(REQUIRED), parent=str(base), parent_sha256=parent['sha256'], sha256=hashes,
                  vendor_changes=changed, other_vendor_files_identical=len(files)-len(changed),
                  kernel_dt_ramdisk_modules_unchanged=True, stock_boot_identity_preserved=True, fbe_fixed=False,
                  fbe_trace=trace, fp12_hook_included=True, source_sha256=sources,
                  runtime_sha256={str(p.relative_to(out)):sha(p) for p in bundled if p.is_file()},
                  abi_audit=parent['abi_audit'], fbe_evidence='${A9_BTRY_LOGS}/fbe-evidence.log',
                  fbe_saved_evidence='${A9_BTRY_LOGS}/fbe-evidence-saved.log',
                  stability_evidence=['stability-evidence.json', 'stability-events.jsonl'],
                  run=str(out/'run.sh'), fingerprint_run=str(out/'run-fingerprint.sh'),
                  reopen_run=str(out/'run-reopen.sh'),
                  free_bytes=shutil.disk_usage(out).free)
    (out/'integrated-build.json').write_text(json.dumps(result, indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in hashes.items()))
    (out/'vendor.img.sha256').write_text(hashes['vendor.img']+'  vendor.img\n')
    print('INTEG12 assembled; final host checks pending', flush=True)


if __name__ == '__main__': main()
