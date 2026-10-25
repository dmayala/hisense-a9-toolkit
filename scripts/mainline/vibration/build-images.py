#!/usr/bin/env python3
"""Host-only B1 image derivative. Reuses the exact R4 kernel and its module ABI.

Run from a worktree containing --refresh-integrated vendor recipe:
  python3 -I scripts/mainline/vibration/build-images.py
No phone tools. Build artifacts and private vendor data stay under A9-build.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = Path(os.environ.get('A9_VIBRATION_OUT', str(Path.home()/'A9-build/mainline/vibration-20261020')))
BASE = Path.home()/'A9-build/mainline/demo-integrated-20261019'
R4 = Path.home()/'A9-build/mainline/integrated-latency-v3-runtime-probe-r4-20261019'
DTC = '/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin/'

def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def run(cmd, log):
    with (ROOT/log).open('w') as f:
        sp.run(list(map(str, cmd)), env=ENV, check=True, stdout=f, stderr=sp.STDOUT)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--boot', type=Path, help='boot from an earlier integrated feature step')
    ap.add_argument('--kernel-build', type=Path, help='reuse this private kernel/obj build for the module')
    ap.add_argument('--vendor', type=Path, help='already refreshed integrated vendor; skip refresh')
    ap.add_argument('--hal', type=Path, help='reuse the unchanged HAL for an incremental module update')
    ap.add_argument('--gnss', action='store_true', help='opt in to experimental GNSS when refreshing')
    args = ap.parse_args()
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True)
    floor_gib = float(os.environ.get('A9_VIBRATION_MIN_FREE_GIB', '15'))
    if shutil.disk_usage(ROOT).free < floor_gib*1024**3 + 1024**3:
        raise RuntimeError(f'need {floor_gib:g} GiB floor plus 1 GiB module/vendor budget')
    obj, mod, out = ROOT/'obj', ROOT/'module', ROOT/'out'
    out.mkdir(exist_ok=True)
    mod.mkdir(exist_ok=True)
    kernel = BASE/'kernel'
    if args.kernel_build:
        obj = args.kernel_build.resolve()/'obj'
        kernel = args.kernel_build.resolve()/'kernel'
    else:
        # External modules need prepared headers/tools, not all kernel objects.
        obj.mkdir(exist_ok=True)
        for rel in ('include', 'scripts', 'arch/arm64/include'):
            shutil.copytree(BASE/'obj'/rel, obj/rel, symlinks=True, dirs_exist_ok=True)
        for name in ('.config', 'Module.symvers', 'Makefile'):
            shutil.copyfile(BASE/'obj'/name, obj/name)
        if not (obj/'source').exists():
            (obj/'source').symlink_to(kernel)
    for name in ('Makefile', 'a9-aw86xx.c', 'sine.h'):
        shutil.copyfile(HERE/name, mod/name)
    for line in ('CONFIG_INPUT_FF_MEMLESS=y', 'CONFIG_INPUT_EVDEV=y', 'CONFIG_REGMAP_I2C=y'):
        if line not in (obj/'.config').read_text().splitlines():
            raise RuntimeError('R4 is missing '+line)
    run(['make', '-C', kernel, 'O='+str(obj), 'M='+str(mod),
         'ARCH=arm64', 'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc',
         'LOCALVERSION=+', '-j4', 'modules'], 'module-build.log')
    if (obj/'Module.symvers').read_bytes() != (BASE/'obj/Module.symvers').read_bytes():
        raise RuntimeError('base symbol versions changed')
    shutil.copyfile(mod/'a9-aw86xx.ko', out/'a9-aw86xx.ko')
    if args.hal:
        shutil.copyfile(args.hal, out/'android.hardware.vibrator-service.a9')
    else:
        run(['bash', HERE/'build-hal.sh'], 'hal-build.log')

    # R4 boot header v2: preserve every segment except DTB and recompute boot ID.
    boot = (args.boot or (R4/'images/boot.img')).read_bytes()
    if not args.boot and hashlib.sha256(boot).hexdigest() != '02571e365120f8b7fd290110304591fb225a82bffaba194dab6139db05e14dfd':
        raise RuntimeError('unexpected R4 boot')
    values = struct.unpack_from('<9I', boot, 8)
    page = values[7]
    pad = lambda n: (n+page-1)//page*page
    sizes = [values[0], values[2], values[4], struct.unpack_from('<I', boot, 1632)[0],
             struct.unpack_from('<I', boot, 1648)[0]]
    pos, blobs = page, []
    for size in sizes:
        blobs.append(boot[pos:pos+size])
        pos += pad(size)
    dtb = out/'sm6115-hisense-hlte556n-vibration.dtb'
    dtb.write_bytes(blobs[4])
    node = '/soc@0/geniqup@4ac0000/i2c@4a80000/haptics@5a'
    for prop, value in [('compatible', 'awinic,haptic_hv'), ('status', 'okay')]:
        sp.run([DTC+'fdtput', '-t', 's', str(dtb), node, prop, value], check=True)
    # R4 reserves 37..70, including the stock reset pin. Release exactly 67;
    # gpiolib otherwise rejects devm_gpiod_get() with -EINVAL before reset.
    tlmm = '/soc@0/pinctrl@500000'
    ranges = list(map(int, sp.check_output(
        [DTC+'fdtget', '-t', 'u', str(dtb), tlmm, 'gpio-reserved-ranges'], text=True).split()))
    reserved = []
    for start, count in zip(ranges[::2], ranges[1::2]):
        end = start + count
        if start <= 67 < end:
            if start < 67:
                reserved += [start, 67-start]
            if 68 < end:
                reserved += [68, end-68]
        else:
            reserved += [start, count]
    sp.run([DTC+'fdtput', '-t', 'u', str(dtb), tlmm, 'gpio-reserved-ranges',
            *map(str, reserved)], check=True)
    blobs[4] = dtb.read_bytes()
    header = bytearray(boot[:page])
    struct.pack_into('<I', header, 1648, len(blobs[4]))
    digest = hashlib.sha1()
    for blob in blobs:
        digest.update(blob)
        digest.update(struct.pack('<I', len(blob)))
    header[576:608] = digest.digest() + b'\0'*12
    (out/'boot.img').write_bytes(header + b''.join(b+b'\0'*(pad(len(b))-len(b)) for b in blobs))

    # Refresh main's integrated composition first, then install the small B1 delta.
    refresh = REPO/'scripts/mainline/android/refresh-integrated-vendor.py'
    if not refresh.exists():
        raise RuntimeError('merge main integrated vendor refresh before packaging')
    if args.vendor:
        vendor_source = args.vendor.resolve()
    else:
        run(['bash', REPO/'scripts/mainline/android/build-vendor.sh', '--refresh-integrated',
             *(['--gnss'] if args.gnss else []), '--out', ROOT/'vendor-refresh'], 'vendor-refresh.log')
        vendor_source = ROOT/'vendor-refresh/vendor.img'
    vendor = out/'vendor.img'
    shutil.copyfile(vendor_source, vendor)
    labeldir = ROOT/'labels'
    labeldir.mkdir(exist_ok=True)
    commands = ['rm /apex/com.android.hardware.vibrator.apex']
    payload = {
        'lib/modules/a9-aw86xx.ko': (out/'a9-aw86xx.ko', 0o644, 'vendor_file'),
        'bin/hw/android.hardware.vibrator-service.a9': (out/'android.hardware.vibrator-service.a9', 0o755, 'hal_vibrator_default_exec'),
        'etc/init/android.hardware.vibrator-service.a9.rc': (HERE/'android.hardware.vibrator-service.a9.rc', 0o644, 'vendor_configs_file'),
        'etc/vintf/manifest/android.hardware.vibrator-service.a9.xml': (HERE/'android.hardware.vibrator-service.a9.xml', 0o644, 'vendor_configs_file'),
    }
    for path, (source, mode, label) in payload.items():
        labelpath = labeldir/label
        labelpath.write_bytes(('u:object_r:'+label+':s0').encode()+b'\0')
        commands += [f'rm /{path}', f'write {source} /{path}', f'sif /{path} mode 0{0o100000|mode:o}',
                     f'ea_set -f {labelpath} /{path} security.selinux']
    batch = ROOT/'vibration.debugfs'
    batch.write_text('\n'.join(commands)+'\n')
    run(['debugfs', '-w', '-f', batch, vendor], 'vendor-install.log')
    for path, (source, _, _) in payload.items():
        tmp = ROOT/'readback'
        tmp.unlink(missing_ok=True)
        run(['debugfs', '-R', f'dump /{path} {tmp}', vendor], 'readback.log')
        if tmp.read_bytes() != source.read_bytes():
            raise RuntimeError('vendor readback mismatch: '+path)
    run(['e2fsck', '-fn', vendor], 'vendor-fsck.log')
    files = ['boot.img', 'vendor.img', 'a9-aw86xx.ko', 'android.hardware.vibrator-service.a9', dtb.name]
    hashes = {name: sha(out/name) for name in files}
    for name in ('boot.img', 'vendor.img'):
        (out/(name+'.sha256')).write_text(hashes[name]+'  '+name+'\n')
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in hashes.items()))
    result = {'status': 'HOST BUILT; PHONE UNTESTED', 'sha256': hashes,
              'kernel': 'inherited boot kernel; external module against R4-compatible config and symbol CRCs',
              'source_commit': sp.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
              'config_sha256': sha(obj/'.config'), 'symbols_sha256': sha(obj/'Module.symvers'),
              'vendor_refresh_recipe': sha(refresh)}
    (ROOT/'artifacts.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))

ENV = dict(os.environ)
ENV['PATH'] = ':'.join(json.loads((BASE/'source.json').read_text())['tools'])+':'+ENV['PATH']
if __name__ == '__main__':
    main()
