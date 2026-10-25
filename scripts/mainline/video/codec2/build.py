#!/usr/bin/env python3
"""Build and stage Android16 HIDL1.2 V4L2 Codec2, without an Android checkout.

Uses content-pinned public source archives and platform libraries from the same
LOS23 GSI as R4. No target execution except an optional early QEMU link check.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess as sp
import tarfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
BUILD = Path.home() / 'A9-build/mainline'
SERVICE = 'android.hardware.media.c2@1.2-service-v4l2-64'
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
QEMU = '/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_tree_sha(archive):
    # Gitiles regenerates tar/gzip metadata. Pin names, file contents and link
    # targets rather than depending on timestamps or compression choices.
    entries = []
    with tarfile.open(archive) as tar:
        for member in tar:
            if member.isfile():
                content = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
            elif member.issym() or member.islnk():
                content = member.linkname
            else:
                continue
            entries.append((member.name, member.type.decode('ascii'), content))
    return hashlib.sha256(json.dumps(sorted(entries), separators=(',', ':')).encode()).hexdigest()


def run(argv, log, **kwargs):
    with log.open('w') as stream:
        sp.run(list(map(str, argv)), check=True, stdout=stream, stderr=sp.STDOUT, **kwargs)


def prepare(root, fetch):
    def archive_filter(member, destination):
        # Keep aliases inside the selected subtree; discard aliases into an
        # unarchived parent tree (several Android header directories have both).
        try:
            return tarfile.data_filter(member, destination)
        except tarfile.FilterError:
            return None

    lock = json.loads((HERE / 'sources.lock.json').read_text())
    for name, entry in lock.items():
        archive = root / ('aosp/v4l2_codec2.tgz' if name == 'v4l2_codec2' else f'deps/{name}.tgz')
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            if not fetch:
                raise RuntimeError(f'missing {archive}; use --fetch for public sources')
            with urllib.request.urlopen(entry['url'], timeout=120) as response:
                archive.write_bytes(response.read())
        if source_tree_sha(archive) != entry['tree_sha256']:
            raise RuntimeError(f'source content hash mismatch: {archive}')
        dest = root / ('source' if name == 'v4l2_codec2' else f'deps/{name}')
        marker = dest / '.archive-sha256'
        # source is always replayed so a changed patch cannot use stale objects.
        receipt = 'safe-links-v2 ' + entry['tree_sha256']
        if name != 'v4l2_codec2' and marker.exists() and marker.read_text().strip() == receipt:
            continue
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(dest, filter=archive_filter)
        marker.write_text(receipt + '\n')
    for patch in sorted((HERE / 'patches').glob('*.patch')):
        run(['patch', '--batch', '--fuzz=0', '-p1', '-i', patch], root / (patch.stem + '.log'), cwd=root / 'source')


def platform(root, system):
    apex = system / 'apex/com.android.media.swcodec.apex'
    marker = root / 'gsi-libs/apex.sha256'
    if not marker.exists() or marker.read_text().strip() != sha(apex):
        dest = root / 'gsi-libs'
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir()
        with zipfile.ZipFile(apex) as archive:
            with archive.open('apex_payload.img') as source, (root / 'swcodec-payload.img').open('wb') as output:
                shutil.copyfileobj(source, output)
        run(['debugfs', '-R', f'rdump /lib64 {dest}', root / 'swcodec-payload.img'], root / 'apex-libs.log')
        if not (dest / 'lib64/libcodec2_vndk.so').exists():
            raise RuntimeError('swcodec APEX library extraction failed')
        marker.write_text(sha(apex) + '\n')


def compile_payload(root, system):
    run(['python3', '-I', HERE / 'build-host.py', root], root / 'host-hidl-build.log')
    generated = root / 'generated'
    generated.mkdir(exist_ok=True)
    argv = [root / 'host-hidl/hidl-gen', '-L', 'c++-headers', '-o', generated, '-R',
            '-r', f'android.hardware.media:{root}/deps/hal-media',
            '-r', f'android.hardware.graphics:{root}/deps/hal-graphics',
            '-r', f'android.hidl:{root}/deps/hidl/transport',
            'android.hardware.media.c2@1.0', 'android.hardware.media.c2@1.1', 'android.hardware.media.c2@1.2']
    (root / 'generate-argv.json').write_text(json.dumps(list(map(str, argv)), indent=2) + '\n')
    run(argv, root / 'generate.log')
    for group in ('common', 'components', 'v4l2', 'plugin_store', 'service'):
        print('Compiling Codec2 ' + group, flush=True)
        run(['python3', '-I', HERE / 'build-target.py', root, group, '--force'], root / (group + '-compile.log'))
    for group in ('plugin', 'codec', 'service'):
        run(['python3', '-I', HERE / 'link.py', root, system, group], root / (group + '-link-driver.log'))


def stage(root, system, overlay):
    shutil.copytree(HERE / 'vendor', overlay, dirs_exist_ok=True)
    service = overlay / 'bin/hw' / SERVICE
    service.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / 'out' / SERVICE, service)
    service.chmod(0o755)
    (overlay / 'bin/a9-video-start').chmod(0o755)
    run([NDK / 'bin/aarch64-linux-android34-clang', '-std=gnu11', '-Os',
         '-Wall', '-Wextra', '-Werror', HERE / 'codec2-one-frame.c', '-lmediandk',
         '-o', overlay / 'bin/codec2-one-frame'], root / 'probe-build.log')
    run([NDK / 'bin/aarch64-linux-android34-clang', '-std=gnu11', '-Os',
         '-Wall', '-Wextra', '-Werror', HERE / 'codec2-encode.c', '-lmediandk', '-llog',
         '-o', overlay / 'bin/codec2-encode'], root / 'encode-probe-build.log')
    policy = overlay / 'etc/seccomp_policy/android.hardware.media.c2-default-seccomp_policy'
    shutil.copyfile(root / 'deps/codec2/hal/services/seccomp_policy/android.hardware.media.c2-default-arm64.policy', policy)
    # Resolve the entire non-Bionic DT_NEEDED closure into a private LD path.
    # Framework/GPU libraries in the vendor root are never overwritten.
    search = [root / 'out', root / 'gsi-libs/lib64', system / 'lib64', system / 'lib64/bootstrap']
    bionic = {'libc.so', 'libdl.so', 'libm.so', 'ld-android.so'}
    pending = [root / 'out' / name for name in (SERVICE, 'libv4l2_codec2.so', 'libc2plugin_store.so')]
    resolved = {}
    while pending:
        source = pending.pop()
        if source.name in resolved:
            continue
        resolved[source.name] = source
        dynamic = sp.check_output([NDK / 'bin/llvm-readelf', '-d', source], text=True)
        for name in re.findall(r'\(NEEDED\).*?\[([^]]+)\]', dynamic):
            if name in bionic or name in resolved:
                continue
            candidate = next((p / name for p in search if (p / name).is_file()), None)
            if candidate is None:
                raise RuntimeError(f'{source.name}: missing DT_NEEDED {name}')
            pending.append(candidate)
    libs = overlay / 'lib64/a9-video'
    libs.mkdir(parents=True, exist_ok=True)
    for name, source in sorted(resolved.items()):
        if name != SERVICE:
            shutil.copyfile(source, libs / name)
    env = dict(os.environ, LD_LIBRARY_PATH=str(libs) + ':' + str(system / 'lib64/bootstrap'))
    run([QEMU, system / 'bin/bootstrap/linker64', service, '--link-check'], root / 'packaged-link-check.log', env=env)
    result = {
        'status': 'HOST BUILD + PACKAGED QEMU LINK PASS; PHONE ENCODE/DECODE PENDING',
        'system': str(system), 'swcodec_apex_sha256': sha(system / 'apex/com.android.media.swcodec.apex'),
        'sources_lock_sha256': sha(HERE / 'sources.lock.json'),
        'patches': {p.name: sha(p) for p in sorted((HERE / 'patches').glob('*.patch'))},
        'library_inputs': {name: {'path': str(path), 'sha256': sha(path)} for name, path in sorted(resolved.items())},
        'overlay_sha256': {str(p.relative_to(overlay)): sha(p) for p in sorted(overlay.rglob('*')) if p.is_file()},
    }
    (root / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'Codec2 staged: {service}; private libraries {len(resolved)-1}; QEMU link check PASS')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=BUILD / 'video-20261020/codec2')
    parser.add_argument('--system', type=Path, default=BUILD / 'adsp/d2-v8-20261017/system-image/system')
    parser.add_argument('--overlay', type=Path, required=True)
    parser.add_argument('--fetch', action='store_true', help='download missing hash-pinned public source archives')
    args = parser.parse_args()
    root, system = args.out.resolve(), args.system.resolve()
    os.umask(0o077)
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 15 * 1024**3 + 512 * 1024**2:
        raise RuntimeError('need 15 GiB reserve plus 512 MiB Codec2 build budget')
    prepare(root, args.fetch)
    platform(root, system)
    compile_payload(root, system)
    stage(root, system, args.overlay.resolve())


if __name__ == '__main__':
    main()
