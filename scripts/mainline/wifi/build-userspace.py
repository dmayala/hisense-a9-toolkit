#!/usr/bin/env python3
"""Offline reproducible source/patch validation and static radio userspace build.

Never executes a built daemon, fetches dependencies or touches a device.
Upstream checkouts remain unchanged. Source archive hashes are mandatory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import io
import tempfile

HERE = Path(__file__).resolve().parent
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973')

def static_elf(path, machine=183):
    b = path.read_bytes()
    if b[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', b, 18)[0] != machine:
        raise ValueError('unexpected ELF identity: ' + path.name)
    off = struct.unpack_from('<Q', b, 32)[0]
    size, count = struct.unpack_from('<HH', b, 54)
    if any(struct.unpack_from('<I', b, off + i*size)[0] == 3 for i in range(count)):
        raise ValueError('dynamic ELF interpreter forbidden: ' + path.name)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources', type=Path, default=Path.home()/'A9-build/mainline/sol-wifi/src')
    p.add_argument('--out', type=Path, default=Path.home()/'A9-build/mainline/sol-wifi/build/android')
    p.add_argument('--ndk', type=Path, default=Path(os.environ.get('ANDROID_NDK_HOME', NDK)))
    p.add_argument('--host', action='store_true', help='build host ABI static binaries for compile tests only')
    a = p.parse_args()
    if any(n.is_symlink() for n in (a.out.absolute(),*a.out.absolute().parents)):
        p.error('build output must not traverse symlinks')
    out = a.out.resolve()
    repo=HERE.parents[2]
    forbidden=[repo,Path('/home/user/A9'),Path('/home/user/a9-assets').resolve(),Path('/srv/nas/A9')]
    if any(out==n or n in out.parents for n in forbidden):
        p.error('build output must be outside source and golden storage')
    out.mkdir(parents=True, exist_ok=True)
    # Never follow preplanted leaf links through the compiler or manifest
    # writer, even when the build directory itself is a valid local path.
    for name in ('rmtfs','tqftpserv','pd-mapper','diag-router','qrtr-lookup','a9-wifi-sandbox','build-manifest.json'):
        existing=out/name
        if existing.is_symlink() or (existing.exists() and (not existing.is_file() or existing.stat().st_nlink!=1)):
            p.error('unsafe existing build output: '+name)
    lock = json.loads((HERE/'sources.lock.json').read_text())
    records = []
    for row in lock:
        name, pin = row['name'], row['commit']
        src = a.sources / name
        actual = subprocess.check_output(['git', '-C', str(src), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != pin:
            raise ValueError('source pin mismatch: ' + name)
        if subprocess.check_output(['git','-C',str(src),'status','--porcelain','--untracked-files=no']):
            raise ValueError('modified upstream source: ' + name)
        archive = subprocess.check_output(['git', '-C', str(src), 'archive', '--format=tar', pin])
        if hashlib.sha256(archive).hexdigest() != row['archive_sha256']:
            raise ValueError('source archive digest mismatch: ' + name)
        dest = out/'source'/name
        if (out/'source').is_symlink() or dest.is_symlink():
            raise ValueError('build source directory symlink rejected')
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(dest, filter='data')
        patch = HERE/'patches'/(name+'.patch')
        record = dict(row)
        if patch.exists():
            subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(patch)],cwd=dest,check=True,stdout=subprocess.DEVNULL)
            record['patch_sha256'] = hashlib.sha256(patch.read_bytes()).hexdigest()
        records.append(record)
    tool = a.ndk/'toolchains/llvm/prebuilt/linux-x86_64/bin'
    abi = 'x86_64' if a.host else 'aarch64'
    cc = tool/(abi+'-linux-android30-clang')
    if not cc.is_file():
        p.error('cached NDK compiler missing; this script never downloads')
    qrtr = out/'source/qrtr'
    lib = [qrtr/'lib'/n for n in ('qrtr.c','qmi.c','logging.c')]
    includes = ['-I'+str(qrtr/n) for n in ('include','lib','src')]
    common = [str(cc),'-static','-O2','-D_GNU_SOURCE','-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-Wall','-Wextra']
    programs = {
        'rmtfs': ('rmtfs', ['rmtfs.c','qmi_rmtfs.c','a9-storage.c','a9-sharedmem.c','util.c'], ['-DA9_RMTFS_SAFE']),
        'tqftpserv': ('tqftpserv', ['tqftpserv.c','translate.c'], ['-DA9_TQFTP_SANDBOX']),
        'pd-mapper': ('pd-mapper', ['pd-mapper.c','servreg_loc.c'], ['-DA9_PD_SANDBOX']),
        'diag-router': ('diag', ['router/a9-router.c'], []),
        'qrtr-lookup': ('qrtr', ['src/lookup.c'], []),
    }
    hashes = {}
    for binary, (project, names, flags) in programs.items():
        src = out/'source'/project
        with tempfile.TemporaryDirectory(prefix='.compile-',dir=out) as temporary:
            target=Path(temporary)/binary
            cmd = common + includes + ['-I'+str(src)] + flags + [str(src/n) for n in names] + [str(f) for f in lib] + ([] if binary=='qrtr-lookup' else [str(HERE/'postexec.c')]) + ['-o',str(target)]
            subprocess.run(cmd, check=True)
            os.replace(target,out/binary)
        static_elf(out/binary,62 if a.host else 183)
        hashes[binary] = hashlib.sha256((out/binary).read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='.compile-',dir=out) as temporary:
        target=Path(temporary)/'a9-wifi-sandbox'
        subprocess.run(common+[str(HERE/'sandbox.c'),'-o',str(target)],check=True)
        os.replace(target,out/'a9-wifi-sandbox')
    static_elf(out/'a9-wifi-sandbox',62 if a.host else 183)
    hashes['a9-wifi-sandbox'] = hashlib.sha256((out/'a9-wifi-sandbox').read_bytes()).hexdigest()
    manifest={'abi':abi,'static':True,'sources':records,'binaries':hashes,
              'local_sources':{n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ['sandbox.c','sandbox-policy.h','sha256.h','postexec.c']}}
    with tempfile.TemporaryDirectory(prefix='.manifest-',dir=out) as temporary:
        target=Path(temporary)/'build-manifest.json'
        target.write_text(json.dumps(manifest,indent=2)+'\n')
        os.replace(target,out/'build-manifest.json')
    print('Built six static '+abi+' binaries; no daemon executed.')

if __name__ == '__main__':
    main()
