#!/usr/bin/env python3
"""Prepare private, offline-only S1 diagnostic inputs. Never contacts a device.

Reuses the hardware-passed D1 images unchanged. Does NOT enable FastRPC or
provide a registry listener; the resulting admission is deliberately NO-GO.
Firmware and factory registry contents live only in the private build output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
WORK = Path.home() / 'A9-build/mainline'
FLOOR = 15_000_000_000
D1_TUPLE = 'e0378ea314279b6259d161909335ea2a584ca051d7f9cbb85ab643de4e667a63'
IMAGES = {
    'android/out/a9-android-eink-boot-gpu.img': 'ed2ccc8b5a824bcb2f302653d6b8d20bcd93be522c74a20acde5ea9ebd1d6840',
    'android/out/vendor_b-eink-gpu-bt-health-thermal.img': 'c993906a36fd7779db7455c4ecc2eae0da4414801bb7fe6b19a43db312662209',
    'snapshot/recovery_dtbo.img': 'cf2fe1f2b10d73f5f99d2972e9fecb87fd281c33d92cf305f5b4f41b55ec01d0',
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def static_observer(path):
    data = path.read_bytes()
    if (len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or
            data[18:20] != b'\xb7\x00'):
        raise ValueError('observer must be little-endian ELF64 AArch64')
    phoff = struct.unpack_from('<Q', data, 32)[0]
    entsize, count = struct.unpack_from('<HH', data, 54)
    if entsize != 56 or not 1 <= count <= 64 or phoff + entsize * count > len(data):
        raise ValueError('invalid observer ELF program headers')
    types = [struct.unpack_from('<I', data, phoff + n * entsize)[0] for n in range(count)]
    if 1 not in types or 3 in types:
        raise ValueError('observer needs static linkage without PT_INTERP')


def checked_tree(root, max_bytes=32 * 1024 * 1024):
    """Accept bounded ordinary files/directories; never follow copied links."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('ordinary source directory required')
    files = []
    total = 0
    for p in sorted(root.rglob('*')):
        mode = p.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('links and special files are forbidden in private seed')
        rel = p.relative_to(root).as_posix()
        if not re.fullmatch(r'[A-Za-z0-9_./+-]+', rel) or '..' in rel.split('/'):
            raise ValueError('unsafe seed name')
        if stat.S_ISREG(mode):
            total += p.stat().st_size
            files.append(p)
    if not files or len(files) > 4096 or total > max_bytes:
        raise ValueError('empty or oversized private seed')
    return files


def registry_archive(source, archive, manifest):
    files = checked_tree(source)
    if not (source / 'registry/registry').is_dir():
        raise ValueError('expected factory sensors registry layout')
    with tarfile.open(archive, 'w', format=tarfile.USTAR_FORMAT) as tar:
        for p in sorted(source.rglob('*')):
            name = 'sensors/' + p.relative_to(source).as_posix()
            info = tar.gettarinfo(str(p), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = 0
            info.mode = 0o700 if p.is_dir() else 0o600
            if p.is_file():
                with p.open('rb') as stream:
                    tar.addfile(info, stream)
            else:
                tar.addfile(info)
    archive.chmod(0o600)
    manifest.write_text(''.join(sha(p) + '  /mnt/vendor/persist/sensors/' +
                               p.relative_to(source).as_posix() + '\n' for p in files))
    manifest.chmod(0o600)
    return len(files)


def prepare(out, d1, seed, config, observer):
    out = out.expanduser().absolute()
    # Resolve the parent first: an existing symlink must not redirect private data.
    private_root = WORK / 'sensors-s1'
    if (private_root.resolve() != private_root or out.parent != private_root or
            out.exists() or out.is_symlink() or not re.fullmatch(r'[A-Za-z0-9_-]+', out.name)):
        raise ValueError('new direct child of ~/A9-build/mainline/sensors-s1 required')
    if not observer.is_file() or observer.is_symlink():
        raise ValueError('compiled ordinary observer required')
    static_observer(observer)
    observer_receipt = observer.with_name(observer.name + '.build.json')
    built = json.loads(observer_receipt.read_text())
    if (built.get('static_aarch64') is not True or built.get('output_sha256') != sha(observer) or
            built.get('source_sha256') != sha(HERE / 's1-observe.c')):
        raise ValueError('observer build does not match current source and executable')
    packed = json.loads((d1 / 'packaged-readback.json').read_text())
    if packed['status'] != 'PASS':
        raise ValueError('D1 packaged readback missing')
    for name, digest in IMAGES.items():
        if sha(d1 / name) != digest:
            raise ValueError('hardware-passed D1 image changed: ' + name)
    if sha(d1 / 'bundle/SHA256SUMS') != D1_TUPLE:
        raise ValueError('D1 bundle manifest changed')
    bundle_files = checked_tree(d1 / 'bundle', max_bytes=128 * 1024 * 1024)
    expected_names = {'SHA256SUMS', 'tuple'}
    for line in (d1 / 'bundle/SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if name.startswith('/') or '..' in Path(name).parts or sha(d1 / 'bundle' / name) != digest:
            raise ValueError('D1 sidecar changed')
        expected_names.add(name)
    if {p.relative_to(d1 / 'bundle').as_posix() for p in bundle_files} != expected_names:
        raise ValueError('unmanifested D1 sidecar file')
    # No module or DT enablement is smuggled into this passive diagnostic subset.
    if '# CONFIG_QCOM_FASTRPC is not set' not in (d1 / 'snapshot/config').read_text().splitlines():
        raise ValueError('D1 config unexpectedly enables FastRPC')
    checked_tree(seed)
    checked_tree(config)
    needed = sum((d1 / name).stat().st_size for name in IMAGES) + 256 * 1024 * 1024
    if shutil.disk_usage('/').free - needed < FLOOR:
        raise RuntimeError('would fall below 15 GB free on /; ask coordinator')
    os.umask(0o077)
    out.mkdir(parents=True, mode=0o700)
    for name in IMAGES:
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(d1 / name, dest)
        dest.chmod(0o400)
        dest.with_name(dest.name + '.sha256').write_text(IMAGES[name] + '  ' + dest.name + '\n')
        if sha(dest) != IMAGES[name]:
            raise RuntimeError('copied image mismatch')
    bundle = out / 'bundle'
    shutil.copytree(d1 / 'bundle', bundle)
    for name in ('d1-target.sh', 'SHA256SUMS', 'tuple'):
        (bundle / name).unlink()
    subprocess.run(['python3', '-I', str(HERE / 's1-derive.py'), '--output', str(bundle)], check=True)
    shutil.copyfile(observer, bundle / 's1-observe')
    (bundle / 's1-observe').chmod(0o500)
    shutil.copyfile(observer_receipt, bundle / 'receipts/s1-observer-build.json')
    # Offline measured from stock SNS_CLIENT_SVC IDL object, see evidence note.
    # The SERVREG instance 74 is a different namespace from the SNS instance.
    (bundle / 'sns-service-id').write_text('400\n')
    shutil.copyfile(HERE / 's1-registry-ram.sh', bundle / 's1-registry-ram.sh')
    nseed = registry_archive(seed, bundle / 'registry-seed.tar', bundle / 'registry.sha256')
    shutil.copytree(config, bundle / 'vendor-config')
    # This provenance includes private seed hashes: never commit this output.
    provenance = {
        'stage_result': 'PREPARED_OFFLINE', 'execution_admission': 'NO_GO',
        'image_strategy': 'exact hardware-passed D1 copies; no kernel or DT change',
        'images': IMAGES, 'd1_bundle_tuple': D1_TUPLE,
        'registry_seed': 'PRIVATE_COPY_STAGED_FOR_RAM_ONLY', 'registry_files': nseed,
        'registry_transport': 'NOT_IMPLEMENTED', 'enumeration_result': 'NOT_RUN',
        'blockers': ['D1 FastRPC driver and DT disabled',
                     'no verified upstream-compatible sensorspd/default registry listener',
                     'no hardware observation in this offline preparation'],
        'recipes': {p.name: sha(p) for p in HERE.glob('s1-*') if p.is_file()},
    }
    (bundle / 'prep.json').write_text(json.dumps(provenance, indent=2) + '\n')
    files = [p for p in sorted(bundle.rglob('*')) if p.is_file()]
    (bundle / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.relative_to(bundle).as_posix() + '\n' for p in files))
    (bundle / 'tuple').write_text(sha(bundle / 'SHA256SUMS') + '\n')
    pending = {**provenance, 'stage': 'S1', 'tuple': sha(bundle / 'SHA256SUMS'),
               'frozen_manifest_sha256': sha(bundle / 'SHA256SUMS'),
               'independent_review': 'PENDING', 'service_admission': 'PENDING',
               'fallback_owner': 'PENDING', 'rehearsal': False}
    (out / 'admission.pending.json').write_text(json.dumps(pending, indent=2) + '\n')
    if shutil.disk_usage('/').free < FLOOR:
        raise RuntimeError('free space below 15 GB; ask coordinator, stop')
    print('S1 PRIVATE OFFLINE INPUTS PREPARED; execution admission NO_GO:', out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--d1', type=Path, default=WORK / 'adsp/d1-20261017')
    parser.add_argument('--seed', type=Path, default=WORK / 'sensors/stock/persist-sensors/sensors')
    parser.add_argument('--vendor-config', type=Path, default=WORK / 'sensors/stock/sensors')
    parser.add_argument('--observer', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.output, args.d1, args.seed, args.vendor_config, args.observer)
