#!/usr/bin/env python3
"""Host-only 6r2 delta: pre-start firmware admission, same 6r safety/payloads."""
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


def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, default=ROOT/'integ-20261023-6r')
    ap.add_argument('--out', type=Path, default=ROOT/'integ-20261023-6r2')
    args = ap.parse_args(); base = args.base.resolve(); out = args.out.resolve()
    assert not out.exists(), 'new output required; inspect partial/completed candidates'
    assert sha(base/'boot.img') == '853710225e508e6514ad48137449e0836916ee0ed0060e980bc9f2daad4c9c16'
    assert sha(base/'vendor.img') == '7a31401e4dab3f333fd4ee2d840c3838b77dc7c338c7df1400137d6a77d592cf'
    floor = 15*1024**3
    assert shutil.disk_usage(base).free >= floor + 900*1024**2, '15GiB floor +900MiB candidate budget'
    os.umask(0o077); out.mkdir()
    def run(argv, label):
        assert shutil.disk_usage(out).free >= floor
        with (out/(label+'.log')).open('w') as f:
            sp.run(list(map(str, argv)), check=True, stdout=f, stderr=sp.STDOUT)
        assert shutil.disk_usage(out).free >= floor
    def member(image, name):
        return sp.check_output(['debugfs', '-R', 'cat /'+name, image], stderr=sp.DEVNULL)
    spec = importlib.util.spec_from_file_location('meta', MAIN/'android/refresh-integrated-vendor.py')
    meta = importlib.util.module_from_spec(spec); spec.loader.exec_module(meta)
    src = base/'vendor.img'; image = out/'vendor.img'
    controller_name = 'etc/a9-wifi/session/tools/coordinator-target.sh'
    controller = MAIN/'wifi/coordinator-target.sh'
    # A delta against exactly the tested 6r controller. All previous safety code
    # remains byte-identical after removing this added admission function/call.
    new = controller.read_text(); old = member(src, controller_name).decode()
    first = new.index('firmware_gate() ('); last = new.index('\nadmission_file() {', first)
    stripped = new[:first]+new[last+1:]
    stripped = stripped.replace("        firmware_gate || die 'firmware bridge changed or modem firmware missing before sole MPSS start'\n", '')
    assert stripped == old, 'only additional firmware admission is permitted'
    overlay = out/'overlay'; overlay.mkdir()
    sums_name = 'etc/a9-wifi/session/tools/SHA256SUMS'
    sums = member(src, sums_name).decode()
    old_sha = hashlib.sha256(old.encode()).hexdigest()
    assert sums.count(old_sha+'  coordinator-target.sh\n') == 1
    sums = sums.replace(old_sha+'  coordinator-target.sh\n', sha(controller)+'  coordinator-target.sh\n')
    sums_path = overlay/'SHA256SUMS'; sums_path.write_text(sums)
    manifest_name = 'etc/a9-wifi/session/manifest.json'
    manifest = json.loads(member(src, manifest_name))
    manifest['files'][controller_name] = sha(controller)
    manifest['files'][sums_name] = sha(sums_path)
    manifest_path = overlay/'manifest.json'; manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    payload = {controller_name:controller, sums_name:sums_path, manifest_name:manifest_path}
    commands = []
    for i, (name, source) in enumerate(payload.items()):
        info = meta.metadata(src, name); label = out/('label-'+str(i)); label.write_bytes(info['label'].encode()+b'\0')
        commands += ['rm /'+name, f'write {source} /{name}', f'sif /{name} mode 010{info["mode"]}',
                     f'sif /{name} uid {info["uid"]}', f'sif /{name} gid {info["gid"]}',
                     f'ea_set -f {label} /{name} security.selinux']
        commands += [f'sif /{name} {field} @{int(info[field],16)}' for field in ('atime','mtime','ctime','crtime')]
    batch = out/'install.debugfs'; batch.write_text('\n'.join(commands)+'\n')
    run(['cp','--reflink=auto','--sparse=always',src,image], 'vendor-copy')
    run(['debugfs','-w','-f',batch,image], 'vendor-install')
    for name, source in payload.items():
        assert member(image,name) == source.read_bytes(), name
        assert meta.metadata(image,name) == meta.metadata(src,name), name
    run(['e2fsck','-fn',image], 'vendor-fsck')
    unchanged = 0; modules = 0
    for name in meta.vendor_files(src):
        if name in payload: continue
        assert member(src,name) == member(image,name), name
        unchanged += 1; modules += int(name.startswith('lib/modules/') and name.endswith('.ko'))
    assert modules == 100
    for name in ('boot.img','Image.gz','integ.dtb','kernel.config','Module.symvers','vmlinux.symvers','recovery_dtbo.img'):
        shutil.copyfile(base/name,out/name); assert sha(base/name) == sha(out/name)
    # Verify installed tool hashes, not just the rewritten source receipt.
    for line in sums.splitlines():
        digest, name = line.split('  ')
        assert hashlib.sha256(member(image,'etc/a9-wifi/session/tools/'+name)).hexdigest() == digest
    result = dict(host_only=True,hardware_qualified=False,base=str(base),base_vendor_sha256=sha(src),
                  changed={n:sha(p) for n,p in payload.items()},unchanged_regular_files=unchanged,
                  byte_identical_modules=modules,boot_kernel_dt_unchanged=True,fsck='PASS',
                  inherited_6r_checks='Native ABI and init verifier preserved: all modules, kernel, rc and policy byte-identical',
                  old_safety_code_byte_identical=True,no_added_persist_metadata_or_storage_change=True,
                  source_commit=sp.check_output(['git','-C',MAIN,'rev-parse','HEAD'],text=True).strip(),
                  source_sha256={str(p.relative_to(MAIN)):sha(p) for p in (Path(__file__),controller,MAIN/'play/onb.py',MAIN/'acceptance/onb.py')},
                  sha256={n:sha(out/n) for n in ('boot.img','vendor.img','Image.gz','integ.dtb','kernel.config')},
                  free_bytes=shutil.disk_usage(out).free)
    (out/'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in result['sha256'].items()))
    print('6r2 HOST BUILD PASS',out)


if __name__ == '__main__': main()
