#!/usr/bin/env python3
"""Host-only FIX9: frozen FIX4-WDT crypto lineage plus legacy fscrypt ioctl fix.

Never writes the shared source/object cache. Build with its Image dependencies
only; omitted module objects are not needed for Image.gz. Public/private structs
and the exact parent configuration remain unchanged.
"""
from pathlib import Path
import argparse
import gzip
import importlib.util
import json
import os
import shutil
import subprocess as sp
import sys
import build as b


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, b.HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_bytes(data)


def prepare(out):
    src = b.CACHE
    dst = out / 'scratch'
    assert not dst.exists()
    assert b.sha(src / 'obj/arch/arm64/boot/Image.gz') == '790823f576badda8e99094f5d3af82efb0d020428e39f4fe535de881fc90198e'
    ar = shutil.which('aarch64-unknown-linux-gnu-ar', path=b.ENV['PATH'])
    keep = {Path(p) for p in sp.check_output([ar, 't', str(src / 'obj/vmlinux.a')], text=True).splitlines()}
    dst.mkdir()
    (dst / 'base').symlink_to(src / 'kernel', target_is_directory=True)
    def source_link(source, target):
        # Short relative links fit in ext4 inodes; absolute links waste a block
        # per source file. The base alias remains strictly read-only.
        via = dst / 'base' / Path(source).relative_to(src / 'kernel')
        Path(target).symlink_to(os.path.relpath(via, Path(target).parent))
        return target
    shutil.copytree(src / 'kernel', dst / 'kernel', copy_function=source_link)
    def ignore(directory, names):
        result = []
        for name in names:
            p = Path(directory) / name
            rel = p.relative_to(src / 'obj')
            if (p.suffix == '.ko' or
                (p.suffix == '.o' and p not in keep and rel.parts[0] not in ('scripts', 'tools')) or
                (len(rel.parts) == 1 and name in ('vmlinux', 'vmlinux.o', 'vmlinux.unstripped'))):
                result.append(name)
        return result
    shutil.copytree(src / 'obj', dst / 'obj', symlinks=True, ignore=ignore)
    b.K, b.OBJ = dst / 'kernel', dst / 'obj'
    for p in b.OBJ.rglob('*.cmd'):
        p.write_text(p.read_text().replace(str(src), str(dst)))
    # Thin archives retain absolute member paths; rebuild them from our objects.
    for p in b.OBJ.rglob('*.a'):
        p.unlink()
    p = b.OBJ / 'source'
    p.unlink(missing_ok=True)
    p.symlink_to(b.K)
    (b.OBJ / 'Makefile').write_text('export KBUILD_OUTPUT = ' + str(b.OBJ) + '\ninclude ' + str(b.K / 'Makefile') + '\n')
    patches = [b.ROOT / 'stage2-ice-wrapped-20261021/patches/0001-a9-ice31-legacy-wrapped.patch',
               b.ROOT / 'stage2-ice-wrapped-20261021/patches/dm-default-key-7.2.patch']
    clean = b.ROOT / 'linux-7.2.10-gpu'
    before = {}
    for patch in patches:
        for line in patch.read_text().splitlines():
            if not line.startswith('+++ b/'):
                continue
            rel = line[6:].split('\t')[0]
            path = b.K / rel
            before.setdefault(rel, b.sha(path) if path.exists() else None)
            if rel == 'drivers/firmware/qcom/qcom_scm.c':
                data = (b.ROOT / 'plain-deadline-logfix1-wdt-20261022/source' / rel).read_bytes()
            else:
                r = sp.run(['git', '-C', str(clean), 'show', 'HEAD:' + rel], capture_output=True)
                data = r.stdout if r.returncode == 0 else None
            if data is not None:
                write(path, data)
            elif path.exists() or path.is_symlink():
                path.unlink()
        b.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', b.K, '-i', patch], patch.stem + '-restore')
    parent = b.ROOT / 'plain-wrapped-fix4-wdt-20261022'
    frozen = []
    for p in (parent / 'source').rglob('*'):
        rel = p.relative_to(parent / 'source')
        if p.is_file() and rel.parts[0] in ('init', 'include', 'drivers', 'block'):
            write(b.K / rel, p.read_bytes())
            frozen.append(str(rel))
    # The shared cache moved its configuration; restore the exact published one.
    shutil.copyfile(parent / 'kernel.config', b.OBJ / '.config')
    patch = b.HERE.parent / 'kernel/patches/0062-fscrypt-accept-android-wrappedkey-v0.patch'
    write(b.K / 'fs/crypto/keyring.c', (b.K / 'fs/crypto/keyring.c').read_bytes())
    b.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', b.K, '-i', patch], 'fscrypt-compat-patch')
    headers = ['include/uapi/linux/fscrypt.h', 'fs/crypto/fscrypt_private.h']
    assert all((b.K / p).read_bytes() == (src / 'kernel' / p).read_bytes() for p in headers)
    provenance = dict(shared_cache_read_only=str(src), normalized_before=before,
                      frozen_parent_files={p: b.sha(b.K / p) for p in frozen},
                      unchanged_fscrypt_struct_headers={p: b.sha(b.K / p) for p in headers})
    (out / 'source-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    for rel in set(frozen) | set(before) | set(headers) | {'fs/crypto/keyring.c'}:
        p = out / 'source' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(b.K / rel, p)
    shutil.copyfile(patch, out / patch.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--resume', action='store_true', help='resume this private cache after a recorded build failure')
    args = parser.parse_args()
    out = args.output.absolute()
    out.mkdir(parents=True, exist_ok=True)
    assert not (out / 'Image.gz').exists()
    b.OUT = out
    if args.resume:
        b.K, b.OBJ = out / 'scratch/kernel', out / 'scratch/obj'
        assert (b.K / 'fs/crypto/keyring.c').read_bytes() == (out / 'source/fs/crypto/keyring.c').read_bytes()
        provenance = json.loads((out / 'source-provenance.json').read_text())
        for p, expected in provenance['frozen_parent_files'].items():
            assert b.sha(b.K / p) == expected, p
    else:
        prepare(out)
    wdt = load('wdt', 'build-wdt.py')
    b.run(wdt.make_command() + ['olddefconfig'], 'olddefconfig')
    parent = b.ROOT / 'plain-wrapped-fix4-wdt-20261022'
    assert (b.OBJ / '.config').read_bytes() == (parent / 'kernel.config').read_bytes()
    b.run(wdt.make_command() + ['Image.gz'], 'kernel-build')
    for src, dst in [('arch/arm64/boot/Image.gz', 'Image.gz'), ('.config', 'kernel.config'),
                     ('vmlinux.symvers', 'vmlinux.symvers'), ('Module.symvers', 'Module.symvers')]:
        shutil.copyfile(b.OBJ / src, out / dst)
    assert b'fscrypt: Android wrappedkey_v0 add raw_size=' in gzip.decompress((out / 'Image.gz').read_bytes())
    def exports(path):
        return {r[1]: r[0] for line in path.read_text().splitlines() if len(r := line.split()) >= 4}
    old, new = exports(parent / 'vmlinux.symvers'), exports(out / 'vmlinux.symvers')
    bad = {n: [v, new.get(n)] for n, v in old.items() if new.get(n) != v}
    (out / 'abi.json').write_text(json.dumps(dict(existing_exports=len(old), mismatches=bad,
        dictionary_identical=old == new), indent=2) + '\n')
    assert old == new, bad
    # Image.gz generates vmlinux.symvers, not the complete module-build table.
    # Keep existing module-export rows; use the actual generated kernel rows.
    full = (out / 'Module.symvers').read_text().splitlines(True)
    kernel_rows = {r[1]: r[0] for line in full if len(r := line.split()) >= 4 and r[2] == 'vmlinux'}
    assert all(new.get(n) == crc for n, crc in kernel_rows.items())
    (out / 'cached-Module.symvers').write_text(''.join(full))
    (out / 'Module.symvers').write_bytes((out / 'vmlinux.symvers').read_bytes() + ''.join(
        line for line in full if len(r := line.split()) >= 4 and r[2] != 'vmlinux').encode())
    usb = load('usb', 'build-usb-disarm.py')
    usb.repack(parent, out, 'boot.img')
    shutil.copyfile(parent / 'recovery_dtbo.img', out / 'recovery_dtbo.img')
    b.run(['python3', '-P', b.REPO / 'scripts/abl-check.py', out / 'boot.img', 'boot',
           '--dtbo', out / 'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((out / 'abl-check.log').read_text())['result'] == 'PASS'
    (out / 'kernel-result.json').write_text(json.dumps(dict(sha256={p: b.sha(out / p)
        for p in ('boot.img', 'Image.gz', 'kernel.config', 'vmlinux.symvers')},
        config_unchanged=True, export_dictionary_identical=True, structs_unchanged=True,
        nonkernel_payloads_and_cmdline_unchanged=True, hardware_tested=False, phone_access=False), indent=2) + '\n')
    print('FIX9 kernel built, ABI and ABL PASS', flush=True)


if __name__ == '__main__':
    main()
