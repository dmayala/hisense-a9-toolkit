#!/usr/bin/env python3
"""Host-only, modular Venus POC; unchanged integrated R4 kernel, added video DT.

Build media modules without copying/rebuilding unrelated objects or source.
package accepts a cumulative integrated boot, allowing B10 to run last in the
integrated recipe. Firmware is the A9's local, hash-pinned OEM-signed payload.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BUILD = Path.home() / 'A9-build/mainline'
BASE = BUILD / 'demo-integrated-20261019'
R4 = BUILD / 'integrated-latency-v3-runtime-probe-r4-20261019'
FW_SHA = '44c39a0ff5a25433626ae4b21e3267b0713cdf4c5338cdf88763489d72a3da05'
DTC = Path('/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin')
MODULES = {'mc', 'videodev', 'v4l2-mem2mem', 'videobuf2-common', 'videobuf2-v4l2',
           'videobuf2-memops', 'videobuf2-dma-contig', 'venus-core', 'venus-dec', 'venus-enc'}


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def run(cmd, log, env=None):
    with log.open('w') as f:
        sp.run(list(map(str, cmd)), check=True, env=env, stdout=f, stderr=sp.STDOUT)


def environment():
    env = dict(os.environ)
    env['PATH'] = ':'.join(json.loads((BASE / 'source.json').read_text())['tools']) + ':' + env['PATH']
    return env


def stock_firmware(out, directory):
    """Join the A9's signed split image without changing authenticated bytes."""
    lock = json.loads((HERE / 'stock-firmware.lock.json').read_text())
    if not (directory / 'venus.mdt').exists():
        directory.mkdir(parents=True, exist_ok=True)
        mcopy = next(Path('/nix/store').glob('*mtools*/bin/mcopy'))
        run([mcopy, '-i', Path.home() / 'a9-assets/HLTE556N/NON-HLOS_001360E1.bin',
             '::image/venus.*', directory], out / 'firmware-extract.log')
    for name, digest in lock.items():
        if sha(directory / name) != digest:
            raise RuntimeError('stock Venus input differs: ' + name)
    mdt = (directory / 'venus.mdt').read_bytes()
    phoff = struct.unpack_from('<I', mdt, 28)[0]
    count = struct.unpack_from('<H', mdt, 44)[0]
    headers = [struct.unpack_from('<8I', mdt, phoff + i * 32) for i in range(count)]
    data = bytearray(max(p[1] + p[4] for p in headers if p[4]))
    for index, p in enumerate(headers):
        if p[4]:
            segment = (directory / f'venus.b{index:02d}').read_bytes()
            assert len(segment) == p[4]
            data[p[1]:p[1] + p[4]] = segment
    # qcom_mdt_read_metadata() concatenates these two regions for PAS init.
    assert data[:headers[0][4]] + data[headers[1][1]:headers[1][1] + headers[1][4]] == mdt
    target = out / 'venus-stock.mbn'
    target.write_bytes(data)
    if sha(target) != FW_SHA:
        raise RuntimeError('unexpected joined stock Venus image')
    return target


def apply_compat(src, out):
    for name in ('core.c', 'vdec.c', 'venc.c', 'hfi.c', 'pm_helpers.c', 'helpers.c', 'hfi_cmds.c', 'hfi_helper.h', 'hfi_venus.c'):
        path = src / 'drivers/media/platform/qcom/venus' / name
        data = path.read_bytes()
        path.unlink()
        path.write_bytes(data)
    run(['patch', '--batch', '--fuzz=0', '-d', src, '-p1', '-i', HERE / 'sm6115-stock-firmware.patch'],
        out / 'stock-compat-patch.log')
    run(['patch', '--batch', '--fuzz=0', '-d', src, '-p1', '-i', HERE / 'sm6115-encoder-diag.patch'],
        out / 'encoder-diag-patch.log')
    run(['patch', '--batch', '--fuzz=0', '-d', src, '-p1', '-i', HERE / 'sm6115-mapped-eos.patch'],
        out / 'mapped-eos-patch.log')
    run(['patch', '--batch', '--fuzz=0', '-d', src, '-p1', '-i', HERE / 'sm6115-encoder-variants.patch'],
        out / 'encoder-variants-patch.log')
    run(['patch', '--batch', '--fuzz=0', '-d', src, '-p1', '-i', HERE / 'sm6115-first-frame.patch'],
        out / 'first-frame-patch.log')


def stage_modules(out):
    destination = out / 'built-modules'
    destination.mkdir(exist_ok=True)
    built = {p.stem: p for p in (out / 'kernel/drivers/media').rglob('*.ko') if p.stem in MODULES}
    if set(built) != MODULES:
        raise RuntimeError('missing modules: ' + str(MODULES - set(built)))
    for p in built.values():
        shutil.copyfile(p, destination / p.name)
    shutil.copyfile(out / 'kernel/drivers/media/Module.symvers', out / 'media-Module.symvers')


def kernel(out, media_build=None, common_modules=None):
    if shutil.disk_usage(out).free < 15 * 1024**3 + 160 * 1024**2:
        raise RuntimeError('need 15 GiB reserve plus 160 MiB module budget')
    src, obj = out / 'kernel', out / 'obj'
    if src.exists() or obj.exists():
        raise RuntimeError('staging exists; resume the recorded make command')
    # Only media is built as an external module subtree; make writes its .o/.ko
    # beside those source links. Keep its directories private. Other source
    # directories can be linked as a whole, avoiding 100k long file symlinks.
    src.mkdir()
    for p in (BASE / 'kernel').iterdir():
        if p.name == '.git':
            continue
        if p.name == 'drivers':
            (src / 'drivers').mkdir()
            for child in p.iterdir():
                target = src / 'drivers' / child.name
                if child.name == 'media':
                    run(['cp', '-as', child, target], out / 'source-links.log')
                else:
                    target.symlink_to(child)
        else:
            (src / p.name).symlink_to(p)
    obj.mkdir()
    config = media_build / 'obj/.config' if media_build else BASE / 'obj/.config'
    (obj / '.config').write_text(config.read_text() + ('\n' + (HERE / 'a9-video.config').read_text() if not media_build else ''))
    apply_compat(src, out)
    env = environment()
    command = ['make', '-C', src, 'O=' + str(obj), 'ARCH=arm64',
               'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc',
               'HOSTCFLAGS=-I/nix/store/4rw03gwh97laxj6sgf630xdbnynkj43z-openssl-3.5.1-dev/include',
               'HOSTLDFLAGS=-L/nix/store/j09xkbypljdhxwspfiqd8sf64zgxhp52-openssl-3.5.1/lib',
               'LOCALVERSION=+', '-j4']
    (out / 'make-command.json').write_text(json.dumps(list(map(str, command)), indent=2) + '\n')
    run(command + ['olddefconfig'], out / 'config.log', env)
    if 'CONFIG_VIDEO_QCOM_VENUS=m\n' not in (obj / '.config').read_text():
        raise RuntimeError('Venus not selected')
    run(command + ['modules_prepare'], out / 'prepare.log', env)
    symbols = (BASE / 'obj/Module.symvers').read_text()
    if media_build:
        symbols += ''.join(line for line in (media_build / 'kernel/drivers/media/Module.symvers').read_text().splitlines(True)
                           if '/venus/' not in line)
    elif common_modules:
        symbols += ''.join(line for line in (common_modules.parent / 'media-Module.symvers').read_text().splitlines(True)
                           if '/venus/' not in line)
    (obj / 'Module.symvers').write_text(symbols)
    if common_modules:
        run(command + ['M=drivers/media/platform/qcom/venus', 'modules'], out / 'module-build.log', env)
        shutil.copyfile(src / 'drivers/media/platform/qcom/venus/Module.symvers', out / 'venus-Module.symvers')
        dest = out / 'built-modules'
        dest.mkdir()
        for name in MODULES - {'venus-core', 'venus-dec', 'venus-enc'}:
            shutil.copyfile(common_modules / (name + '.ko'), dest / (name + '.ko'))
        for module in (src / 'drivers/media/platform/qcom/venus').glob('*.ko'):
            shutil.copyfile(module, dest / module.name)
    else:
        run(command + ['M=drivers/media', 'modules'], out / 'module-build.log', env)
        stage_modules(out)
    # Keep binaries, config, symvers, argv and logs; prune only our generated
    # source-link farm/intermediates. Packaging uses the frozen source headers.
    shutil.rmtree(src)


def package(out, boot, overlay, firmware, built_modules, stock_directory, media_build):
    if firmware is None:
        firmware = stock_firmware(out, stock_directory)
    if sha(firmware) != FW_SHA:
        raise RuntimeError('expected OEM-signed A9 VIDEO.VE.6.0-00039-PROD-1 firmware')
    # Cached camera/media modules predate the stock-firmware quirk. Rebuild
    # only Venus privately, using that media build's config/export CRCs, and
    # retain every common media/camera module byte. Never mutate its tree.
    compat = out / 'stock-compat'
    compat.mkdir()
    kernel(compat, media_build, built_modules)
    built_modules = compat / 'built-modules'
    env = environment()
    run(['gcc', '-E', '-nostdinc', '-undef', '-D__DTS__', '-x', 'assembler-with-cpp',
         '-I' + str(BASE / 'kernel/include'), HERE / 'a9-video.dtso', '-o', out / 'video.pre.dts'], out / 'dt-cpp.log', env)
    run([DTC / 'dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', out / 'a9-video.dtbo', out / 'video.pre.dts'], out / 'dt-build.log')
    old = boot.read_bytes()
    assert old[:8] == b'ANDROID!' and struct.unpack_from('<I', old, 40)[0] == 2
    page = struct.unpack_from('<I', old, 36)[0]
    pad = lambda n: (n + page - 1) // page * page
    sizes = [struct.unpack_from('<I', old, off)[0] for off in (8, 16, 24, 1632, 1648)]
    blobs, pos = [], page
    for size in sizes:
        blobs.append(old[pos:pos + size])
        pos += pad(size)
    (out / 'base.dtb').write_bytes(blobs[4])
    run([DTC / 'fdtoverlay', '-i', out / 'base.dtb', '-o', out / 'video.dtb', out / 'a9-video.dtbo'], out / 'dt-apply.log')
    blobs[4] = (out / 'video.dtb').read_bytes()
    header = bytearray(old[:page])
    struct.pack_into('<I', header, 1648, len(blobs[4]))
    digest = hashlib.sha1()
    for b in blobs:
        digest.update(b)
        digest.update(struct.pack('<I', len(b)))
    header[576:608] = digest.digest() + b'\0' * 12
    (out / 'boot.img').write_bytes(header + b''.join(b.ljust(pad(len(b)), b'\0') for b in blobs))
    modules = overlay / 'lib/modules'
    modules.mkdir(parents=True, exist_ok=True)
    built = {p.stem: p for p in built_modules.glob('*.ko')}
    if set(built) != MODULES:
        raise RuntimeError('missing modules: ' + str(MODULES - set(built)))
    strip = next(Path(p) / 'aarch64-unknown-linux-gnu-strip'
                 for p in json.loads((BASE / 'source.json').read_text())['tools']
                 if (Path(p) / 'aarch64-unknown-linux-gnu-strip').exists())
    for name, p in built.items():
        dest = modules / p.name
        shutil.copyfile(p, dest)
        run([strip, '--strip-debug', dest], out / ('strip-' + name + '.log'))
    target = overlay / 'firmware/qcom/venus-6.0/venus.mbn'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(firmware, target)
    target = overlay / 'bin/a9-video-load'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / 'video-load.sh', target)
    target.chmod(0o755)
    ndk = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973')
    run([ndk / 'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang',
         '-std=gnu11', '-Os', '-static', '-Wall', '-Wextra', '-Werror', HERE / 'venus-one-frame.c',
         '-o', overlay / 'bin/venus-one-frame'], out / 'decode-tool-build.log')
    files = [out / 'boot.img', out / 'a9-video.dtbo', out / 'video.dtb', *sorted(overlay.rglob('*.ko')),
             overlay / 'firmware/qcom/venus-6.0/venus.mbn']
    (out / 'artifacts.json').write_text(json.dumps({
        'status': 'HOST BUILT KERNEL PAYLOAD; PHONE PROBE PENDING; CODEC2 IS A SEPARATE STAGING STEP',
        'base_boot': str(boot), 'base_boot_sha256': sha(boot),
        'firmware_version': 'VIDEO.VE.6.0-00039-PROD-1 (A9 OEM-signed)',
        'compat_patch_sha256': sha(HERE / 'sm6115-stock-firmware.patch'),
        'mapped_eos_patch_sha256': sha(HERE / 'sm6115-mapped-eos.patch'),
        'encoder_variants_patch_sha256': sha(HERE / 'sm6115-encoder-variants.patch'),
        'first_frame_patch_sha256': sha(HERE / 'sm6115-first-frame.patch'),
        'media_build': str(media_build) if media_build else None,
        'source_commit': sp.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
        'sha256': {str(p): sha(p) for p in files}}, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + str(p) + '\n' for p in files))
    print((out / 'SHA256SUMS').read_text())


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('step', choices=('kernel', 'package', 'firmware'))
    ap.add_argument('--out', type=Path, default=BUILD / 'video-20261020')
    ap.add_argument('--boot', type=Path, default=R4 / 'images/boot.img')
    ap.add_argument('--overlay', type=Path)
    ap.add_argument('--firmware', type=Path)
    ap.add_argument('--built-modules', type=Path, help='reuse media modules from the same R4-compatible recipe')
    ap.add_argument('--stock-firmware-dir', type=Path, default=BUILD / 'video-20261020/stock-firmware-a9')
    ap.add_argument('--media-build', type=Path, help='combined camera/media build whose config and exports Venus must match')
    a = ap.parse_args()
    os.umask(0o077)
    a.out.mkdir(parents=True, exist_ok=True)
    if a.step == 'kernel':
        kernel(a.out, a.media_build)
    elif a.step == 'firmware':
        firmware = stock_firmware(a.out, a.stock_firmware_dir)
        target = (a.overlay or a.out / 'overlay') / 'firmware/qcom/venus-6.0/venus.mbn'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(firmware, target)
        sums = sha(target) + '  ' + str(target) + '\n'
        (a.out / 'SHA256SUMS').write_text(sums)
        print(sums, end='')
    else:
        package(a.out, a.boot, a.overlay or a.out / 'overlay', a.firmware,
                a.built_modules or a.out / 'built-modules', a.stock_firmware_dir, a.media_build)
