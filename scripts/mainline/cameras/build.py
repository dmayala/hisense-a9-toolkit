#!/usr/bin/env python3
"""Host-only rear-camera kernel POC derived from the integrated R4 recipe.

  python3 -I scripts/mainline/cameras/build.py kernel
  python3 -I scripts/mainline/cameras/build.py package

Stock .bin inputs must first be extracted to OUT/{rear,front}.bin and decoded
with extract-stock.py. No device tools, downloads, or writes to source inputs.
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
BASE = Path.home()/'A9-build/mainline/demo-integrated-20261019'
R4 = Path.home()/'A9-build/mainline/integrated-latency-v3-runtime-probe-r4-20261019'
DTC = Path('/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin')
MAIN = HERE.parents[2]


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def run(cmd, log, env=None):
    with log.open('w') as f:
        sp.run(list(map(str, cmd)), check=True, env=env, stdout=f, stderr=sp.STDOUT)


def replace(path, data):
    # Source files initially symlink to the frozen, read-only R4 tree.
    # Unlink the one changed file before writing; never write through a link.
    path.unlink(missing_ok=True)
    path.write_bytes(data)


def release_gpios(dtb, pins, log):
    tlmm = '/soc@0/pinctrl@500000'
    ranges = list(map(int, sp.check_output(
        [DTC/'fdtget', '-t', 'u', dtb, tlmm, 'gpio-reserved-ranges'], text=True).split()))
    reserved = sorted({pin for start, count in zip(ranges[::2], ranges[1::2])
                       for pin in range(start, start+count)} - set(pins))
    result = []
    for pin in reserved:
        if result and result[-2]+result[-1] == pin:
            result[-1] += 1
        else:
            result += [pin, 1]
    run([DTC/'fdtput', '-t', 'u', dtb, tlmm, 'gpio-reserved-ranges',
         *map(str, result)], log)


def kernel(out, video=False):
    if shutil.disk_usage(out).free < 15*1024**3 + 900*1024**2:
        raise RuntimeError('need 15 GiB reserve plus 900 MiB module and image budget')
    src, obj = out/'kernel', out/'obj'
    if src.exists() or obj.exists():
        raise RuntimeError('kernel staging already exists; resume with the recorded make command')
    # Absolute file symlinks save 1.8 GiB of immutable source duplication.
    # Only patched files below are private regular files.
    src.mkdir()
    for p in (BASE/'kernel').iterdir():
        if p.name != 'drivers':
            (src/p.name).symlink_to(p, target_is_directory=p.is_dir())
    (src/'drivers').mkdir()
    for p in (BASE/'kernel/drivers').iterdir():
        if p.name == 'media':
            run(['cp', '-as', p, src/'drivers/media'], out/'source-links.log')
        else:
            (src/'drivers'/p.name).symlink_to(p, target_is_directory=p.is_dir())
    obj.mkdir()
    shutil.copyfile(BASE/'obj/.config',obj/'.config')
    camss = src/'drivers/media/platform/qcom/camss/camss.c'
    replace(camss, camss.read_bytes())
    run(['patch', '-d', src, '-p1', '-i', HERE/'camss-sm6115.patch'], out/'patch.log')
    i2c = src/'drivers/media/i2c'
    for name in ('dw9714p-a9', 'pmi632-a9-torch'):
        replace(i2c/(name+'.c'), (HERE/(name+'.c')).read_bytes())
    replace(i2c/'s5k3l6-a9.c', (HERE/'s5k3l6-a9.c').read_bytes())
    replace(i2c/'s5k3l6-stock.h', (out/'rear/s5k3l6-stock.h').read_bytes())
    replace(i2c/'Kconfig', (i2c/'Kconfig').read_bytes()+b'\nconfig VIDEO_S5K3L6_A9\n\ttristate "A9 S5K3L6 stock-table POC"\n\tselect V4L2_CCI_I2C\n\nconfig VIDEO_DW9714P_A9\n\ttristate "A9 DW9714P lens"\n\nconfig VIDEO_PMI632_A9_TORCH\n\ttristate "A9 PMI632 torch"\n\tdepends on SPMI\n')
    replace(i2c/'Makefile', (i2c/'Makefile').read_bytes()+b'\nobj-$(CONFIG_VIDEO_S5K3L6_A9) += s5k3l6-a9.o\nobj-$(CONFIG_VIDEO_DW9714P_A9) += dw9714p-a9.o\nobj-$(CONFIG_VIDEO_PMI632_A9_TORCH) += pmi632-a9-torch.o\n')
    with (obj/'.config').open('a') as f:
        if video:
            f.write('\n'+(HERE.parent/'video/a9-video.config').read_text())
        f.write('\n'+(HERE/'a9-cameras.config').read_text())
    env = dict(os.environ)
    env['PATH'] = ':'.join(json.loads((BASE/'source.json').read_text())['tools'])+':'+env['PATH']
    command = ['make', '-C', src, 'O='+str(obj), 'ARCH=arm64',
        'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc',
        'HOSTCFLAGS=-I/nix/store/4rw03gwh97laxj6sgf630xdbnynkj43z-openssl-3.5.1-dev/include',
        'HOSTLDFLAGS=-L/nix/store/j09xkbypljdhxwspfiqd8sf64zgxhp52-openssl-3.5.1/lib',
        'LOCALVERSION=+', 'DTC_FLAGS=-@', '-j8']
    (out/'make-command.json').write_text(json.dumps(list(map(str, command)), indent=2)+'\n')
    run(command+['olddefconfig'], out/'config.log', env)
    run(command+['modules_prepare'], out/'prepare.log', env)
    shutil.copyfile(BASE/'obj/Module.symvers',obj/'Module.symvers')
    run(command+['M=drivers/media', 'modules'], out/'kernel-build.log', env)


def front(out):
    """Incremental front module against the already prepared camera object tree."""
    src, obj = out/'kernel', out/'obj'
    i2c = src/'drivers/media/i2c'
    replace(i2c/'hi556-a9.c', (HERE/'hi556-a9.c').read_bytes())
    replace(i2c/'hi556-stock.h', (out/'front/hi556-stock.h').read_bytes())
    if 'VIDEO_HI556_A9' not in (i2c/'Kconfig').read_text():
        replace(i2c/'Kconfig', (i2c/'Kconfig').read_bytes()+b'\nconfig VIDEO_HI556_A9\n\ttristate "A9 Hi556 stock-table POC"\n\tselect V4L2_CCI_I2C\n')
        replace(i2c/'Makefile', (i2c/'Makefile').read_bytes()+b'\nobj-$(CONFIG_VIDEO_HI556_A9) += hi556-a9.o\n')
    with (obj/'.config').open('a') as f:
        f.write('\nCONFIG_VIDEO_HI556_A9=m\n')
    command = json.loads((out/'make-command.json').read_text())
    env = dict(os.environ)
    env['PATH'] = ':'.join(json.loads((BASE/'source.json').read_text())['tools'])+':'+env['PATH']
    run(command+['olddefconfig'], out/'front/config.log', env)
    run(command+['modules_prepare'], out/'front/prepare.log', env)
    run(command+['M=drivers/media', 'modules'], out/'front/build.log', env)
    dest = out/'front-probe'
    dest.mkdir(exist_ok=True)
    run(['gcc', '-E', '-nostdinc', '-undef', '-D__DTS__', '-DA9_FRONT_PROBE', '-x', 'assembler-with-cpp',
        '-I'+str(src/'include'), HERE/'a9-cameras.dtso', '-o', dest/'camera.pre.dts'], dest/'dt-cpp.log', env)
    run([DTC/'dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', dest/'a9-front.dtbo', dest/'camera.pre.dts'], dest/'dt-build.log')
    run([DTC/'fdtoverlay', '-i', out/'base.dtb', '-o', dest/'cameras.dtb', dest/'a9-front.dtbo'], dest/'dt-apply.log')
    release_gpios(dest/'cameras.dtb', {22, 23, 24, 27, 93, 106}, dest/'dt-gpios.log')
    old = (R4/'images/boot.img').read_bytes()
    page = struct.unpack_from('<I', old, 36)[0]
    pad = lambda n: (n+page-1)//page*page
    sizes = [struct.unpack_from('<I', old, off)[0] for off in (8,16,24,1632,1648)]
    blobs, pos = [], page
    for size in sizes:
        blobs.append(old[pos:pos+size]); pos += pad(size)
    blobs[4] = (dest/'cameras.dtb').read_bytes()
    header = bytearray(old[:page])
    struct.pack_into('<I', header, 1648, len(blobs[4]))
    digest = hashlib.sha1()
    for b in blobs:
        digest.update(b); digest.update(struct.pack('<I', len(b)))
    header[576:608] = digest.digest()+b'\0'*12
    (dest/'boot.img').write_bytes(header+b''.join(b.ljust(pad(len(b)),b'\0') for b in blobs))
    overlay = dest/'overlay'
    shutil.copytree(out/'overlay', overlay, dirs_exist_ok=True)
    strip = next(Path(p)/'aarch64-unknown-linux-gnu-strip' for p in json.loads((BASE/'source.json').read_text())['tools'] if (Path(p)/'aarch64-unknown-linux-gnu-strip').exists())
    module = overlay/'lib/modules/hi556-a9.ko'
    shutil.copyfile(i2c/'hi556-a9.ko', module)
    run([strip, '--strip-debug', module], dest/'strip.log')
    shutil.copyfile(HERE/'camera-load.sh', overlay/'bin/a9-camera-load')
    # A separate graph keeps an unprobed front sensor from holding rear registration.
    (dest/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(dest))+'\n'
        for p in (dest/'boot.img',dest/'cameras.dtb',module)))
    print((dest/'SHA256SUMS').read_text())


def package(out, boot=None, stage_only=False, video=False):
    src, obj = out/'kernel', out/'obj'
    env = dict(os.environ)
    env['PATH'] = ':'.join(json.loads((BASE/'source.json').read_text())['tools'])+':'+env['PATH']
    run(['gcc', '-E', '-nostdinc', '-undef', '-D__DTS__', '-x', 'assembler-with-cpp',
         '-I'+str(src/'include'), HERE/'a9-cameras.dtso', '-o', out/'camera.pre.dts'], out/'dt-cpp.log', env)
    run([DTC/'dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', out/'a9-cameras.dtbo', out/'camera.pre.dts'], out/'dt-build.log')
    old = (boot or (R4/'images/boot.img')).read_bytes()
    assert boot or hashlib.sha256(old).hexdigest() == '02571e365120f8b7fd290110304591fb225a82bffaba194dab6139db05e14dfd'
    page = struct.unpack_from('<I', old, 36)[0]
    pad = lambda n: (n+page-1)//page*page
    sizes = [struct.unpack_from('<I', old, off)[0] for off in (8,16,24,1632,1648)]
    blobs, pos = [], page
    for size in sizes:
        blobs.append(old[pos:pos+size]); pos += pad(size)
    (out/'base.dtb').write_bytes(blobs[4])
    run([DTC/'fdtoverlay', '-i', out/'base.dtb', '-o', out/'cameras.dtb', out/'a9-cameras.dtbo'], out/'dt-apply.log')
    release_gpios(out/'cameras.dtb', {18, 20, 22, 23, 24, 27, 93, 106}, out/'dt-gpios.log')
    # Media is entirely modular: keep the exact R4 kernel and its imports.
    # No media built-in or kernel-image change is required for this POC.
    blobs[4] = (out/'cameras.dtb').read_bytes()
    header = bytearray(old[:page])
    struct.pack_into('<I', header, 8, len(blobs[0]))
    struct.pack_into('<I', header, 1648, len(blobs[4]))
    struct.pack_into('<Q', header, 1636, page+sum(pad(len(b)) for b in blobs[:3]))
    digest = hashlib.sha1()
    for b in blobs:
        digest.update(b); digest.update(struct.pack('<I', len(b)))
    header[576:608] = digest.digest()+b'\0'*12
    (out/'boot.img').write_bytes(header+b''.join(b.ljust(pad(len(b)),b'\0') for b in blobs))
    overlay = out/'overlay'
    modules = overlay/'lib/modules'
    modules.mkdir(parents=True, exist_ok=True)
    # R4 modules and kernel remain unchanged; add only the media extension.
    media_names = {'mc', 'videodev', 'v4l2-async', 'v4l2-fwnode', 'videobuf2-common',
        'videobuf2-v4l2', 'videobuf2-memops', 'videobuf2-dma-sg', 'v4l2-cci',
        's5k3l6-a9', 'hi556-a9', 'qcom-camss', 'dw9714p-a9', 'pmi632-a9-torch'}
    if video:
        media_names.update({'v4l2-mem2mem', 'videobuf2-dma-contig', 'venus-core', 'venus-dec', 'venus-enc'})
    for p in (src/'drivers/media').rglob('*.ko'):
        if p.stem in media_names:
            shutil.copyfile(p, modules/p.name)
    shutil.copyfile(BASE/'obj/drivers/i2c/busses/i2c-qcom-cci.ko', modules/'i2c-qcom-cci.ko')
    strip = next(Path(p)/'aarch64-unknown-linux-gnu-strip' for p in json.loads((BASE/'source.json').read_text())['tools'] if (Path(p)/'aarch64-unknown-linux-gnu-strip').exists())
    for p in modules.glob('*.ko'):
        run([strip, '--strip-debug', p], out/'module-strip.log')
    (overlay/'bin').mkdir(exist_ok=True)
    shutil.copyfile(HERE/'camera-load.sh', overlay/'bin/a9-camera-load')
    ndk = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973')
    run([ndk/'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang',
        '-std=gnu11', '-O2', '-static', '-Wall', '-Wextra', '-Werror', HERE/'a9-camcap.c',
        '-o', overlay/'bin/a9-camcap'],out/'camcap-build.log')
    if stage_only:
        print('Camera media module extension, cumulative boot DT and overlay built')
        return
    run(['bash', MAIN/'scripts/mainline/android/build-vendor.sh', '--refresh-integrated', '--out', out/'vendor', '--overlay', overlay], out/'vendor-build.log')
    snapshot = out/'snapshot'
    if not snapshot.exists():
        shutil.copytree(R4/'snapshot',snapshot,symlinks=False)
    # Keep actual R4 kernel config, Image and original module metadata. Only DT
    # changes; extra modules are built with the separate obj/.config extension.
    for name in ('sm6115-hisense-hlte556n-android.dtb','sm6115-hisense-hlte556n-android-gpu.dtb'):
        shutil.copyfile(out/'cameras.dtb',snapshot/name)
    for p in modules.glob('*.ko'):
        shutil.copyfile(p,snapshot/'modules'/p.name)
    manifest = json.loads((snapshot/'wifi-manifest.json').read_text())
    for p in modules.glob('*.ko'):
        manifest['sha256']['modules/'+p.name]=sha(p)
    for rel in manifest['sha256']:
        manifest['sha256'][rel]=sha(snapshot/rel)
    (snapshot/'wifi-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (snapshot/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(snapshot))+'\n'
        for p in sorted(snapshot.rglob('*')) if p.is_file() and p.name!='SHA256SUMS'))
    paths = [out/'boot.img',out/'vendor/vendor.img',out/'cameras.dtb',obj/'.config',obj/'Module.symvers']
    (out/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(out))+'\n' for p in paths))
    (out/'artifacts.json').write_text(json.dumps(dict(status='HOST BUILT; PHONE UNTESTED; REAR POC ONLY',
        branch='fix/cameras',source_commit=sp.check_output(['git','-C',str(HERE),'rev-parse','HEAD'],text=True).strip(),
        sha256={str(p.relative_to(out)):sha(p) for p in paths}),indent=2)+'\n')
    print((out/'SHA256SUMS').read_text())


if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--video', action='store_true', help='build CAMSS and Venus with one common media configuration')
    ap.add_argument('--boot', type=Path, help='boot from an earlier integrated feature step')
    ap.add_argument('--stage-only', action='store_true', help='emit boot/overlay without vendor refresh')
    ap.add_argument('step',choices=['kernel','front','package'])
    ap.add_argument('--out',type=Path,default=Path.home()/'A9-build/mainline/cameras-20261020')
    args=ap.parse_args();os.umask(0o077);args.out.mkdir(parents=True,exist_ok=True)
    if args.step == 'kernel':
        kernel(args.out, args.video)
    elif args.step == 'front':
        front(args.out)
    else:
        package(args.out, args.boot, args.stage_only, args.video)
