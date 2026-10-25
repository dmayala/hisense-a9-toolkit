#!/usr/bin/env python3
"""Build the B6 POC module/DT derivative and refresh vendor from main. Host only."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('A9_BATTERY_OUT', str(Path.home()/'A9-build/mainline/battery-20261020')))
BASE = Path.home()/'A9-build/mainline/demo-integrated-20261019'
R4 = Path.home()/'A9-build/mainline/integrated-latency-v3-runtime-probe-r4-20261019'
MAIN = HERE.parents[3]
DTC = Path('/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin')
ENV = dict(os.environ, PATH=':'.join(json.loads((BASE/'source.json').read_text())['tools'])+':'+os.environ['PATH'])


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def run(cmd, log):
    with (ROOT/log).open('w') as f:
        sp.run(list(map(str, cmd)), env=ENV, check=True, stdout=f, stderr=sp.STDOUT)


def find_compatible(dtb, compatibles):
    # Parse only paths/compatible: never dump the private address properties.
    data = dtb.read_bytes()
    strings_off = struct.unpack_from('>I', data, 12)[0]
    pos = struct.unpack_from('>I', data, 8)[0]
    stack = []
    while True:
        token = struct.unpack_from('>I', data, pos)[0]
        pos += 4
        if token == 1:
            end = data.index(b'\0', pos)
            stack.append(data[pos:end].decode())
            pos = (end+4)//4*4
        elif token == 2:
            stack.pop()
        elif token == 3:
            size, offset = struct.unpack_from('>II', data, pos)
            pos += 8
            end = data.index(b'\0', strings_off+offset)
            name = data[strings_off+offset:end]
            value = data[pos:pos+size]
            if name == b'compatible' and set(value.rstrip(b'\0').split(b'\0')) & set(compatibles):
                return '/'.join(stack)
            pos = (pos+size+3)//4*4
        elif token == 4:
            pass
        elif token == 9:
            break
        else:
            raise ValueError('bad FDT token')
    raise ValueError(f'DT node not found: {compatibles!r}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--boot', type=Path, help='boot from an earlier integrated feature step')
    ap.add_argument('--stage-only', action='store_true', help='emit boot and vendor-overlay for full integration')
    args = ap.parse_args()
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(ROOT).free < 15*1024**3 + 1024**3:
        raise RuntimeError('need 15 GiB floor plus 1 GiB build budget')
    obj, module, out, overlay = (ROOT/n for n in ('obj', 'module', 'out', 'vendor-overlay'))
    for p in (obj, module, out, overlay/'lib/modules', overlay/'etc/init'):
        p.mkdir(parents=True, exist_ok=True)
    # Only prepared headers/tools and exact R4 config/symbols are needed for M=.
    # Never run make in the shared output, never copy 1.8 GiB of unrelated objects.
    for rel in ('include', 'scripts', 'arch/arm64/include'):
        shutil.copytree(BASE/'obj'/rel, obj/rel, symlinks=True, dirs_exist_ok=True)
    for name in ('.config', 'Module.symvers', 'Makefile'):
        shutil.copyfile(BASE/'obj'/name, obj/name)
    if not (obj/'source').exists():
        (obj/'source').symlink_to((BASE/'obj/source').resolve())
    for name in ('Makefile', 'pmi632-battery.c', 'policy.h', 'profile.h', 'input-policy.h', 'status.h', 'drain-qg.h', 'pmi632-drain.h'):
        shutil.copyfile(HERE/name, module/name)
    shutil.copyfile(HERE.parent/'pmi632-policy.h', module/'pmi632-policy.h')
    cmd = ['make', '-C', BASE/'kernel', 'O='+str(obj), 'M='+str(module), 'ARCH=arm64',
           'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc', 'LOCALVERSION=+', '-j4', 'W=1', 'modules']
    (ROOT/'module-command.json').write_text(json.dumps(list(map(str, cmd)), indent=2)+'\n')
    run(cmd, 'module-build.log')
    if sha(obj/'.config') != sha(BASE/'obj/.config') or sha(obj/'Module.symvers') != sha(BASE/'obj/Module.symvers'):
        raise RuntimeError('R4 module ABI/config changed')
    shutil.copyfile(module/'pmi632-battery.ko', out/'pmi632-battery.ko')

    image = (args.boot or (R4/'images/boot.img')).read_bytes()
    if not args.boot and hashlib.sha256(image).hexdigest() != '02571e365120f8b7fd290110304591fb225a82bffaba194dab6139db05e14dfd':
        raise RuntimeError('R4 boot changed')
    values = struct.unpack_from('<9I', image, 8)
    page = values[7]
    pad = lambda n: (n+page-1)//page*page
    sizes = [values[0], values[2], values[4], struct.unpack_from('<I', image, 1632)[0], struct.unpack_from('<I', image, 1648)[0]]
    blobs, pos = [], page
    for size in sizes:
        blobs.append(image[pos:pos+size])
        pos += pad(size)
    dtb = out/'sm6115-hisense-hlte556n-battery.dtb'
    dtb.write_bytes(blobs[4])
    charger = find_compatible(dtb, [b'hisense,hlte556n-pmi632-power', b'hisense,hlte556n-pmi632-battery'])
    sp.run([str(DTC/'fdtput'), '-t', 's', str(dtb), charger, 'compatible', 'hisense,hlte556n-pmi632-battery'], check=True)
    for prop, value in [('qcom,usb-icl-ua', 2000000), ('qcom,float-icl-ua', 900000)]:
        sp.run([str(DTC/'fdtput'), '-t', 'u', str(dtb), charger, prop, str(value)], check=True)
    # Existing built-in DWC3 vbus_draw passes enumeration/reset/suspend votes.
    dwc3 = find_compatible(dtb, [b'snps,dwc3'])
    sp.run([str(DTC/'fdtput'), '-t', 's', str(dtb), dwc3, 'usb-psy-name', 'a9-usb'], check=True)
    blobs[4] = dtb.read_bytes()
    header = bytearray(image[:page])
    struct.pack_into('<I', header, 1648, len(blobs[4]))
    digest = hashlib.sha1()
    for blob in blobs:
        digest.update(blob)
        digest.update(struct.pack('<I', len(blob)))
    header[576:608] = digest.digest()+b'\0'*12
    (out/'boot.img').write_bytes(header+b''.join(b+b'\0'*(pad(len(b))-len(b)) for b in blobs))
    shutil.copyfile(out/'pmi632-battery.ko', overlay/'lib/modules/pmi632-battery.ko')
    shutil.copyfile(HERE/'init.a9ml-battery.rc', overlay/'etc/init/init.a9ml-battery.rc')
    if args.stage_only:
        print('Battery module, cumulative boot DT and vendor-overlay built')
        return
    # Explicitly use current worktree's integrated composition, not the 07b9875 stager.
    refresh = MAIN/'scripts/mainline/android/refresh-integrated-vendor.py'
    run(['bash', MAIN/'scripts/mainline/android/build-vendor.sh', '--refresh-integrated',
         '--out', ROOT/'vendor-refresh', '--overlay', overlay], 'vendor-refresh.log')
    shutil.copyfile(ROOT/'vendor-refresh/vendor.img', out/'vendor.img')
    run(['e2fsck', '-fn', out/'vendor.img'], 'vendor-fsck.log')
    # Verify R4 carries the sysfs health HAL rather than Cuttlefish's fake battery.
    health = ROOT/'health-readback'
    health.unlink(missing_ok=True)
    run(['debugfs', '-R', f'dump /bin/hw/android.hardware.health-service.cuttlefish {health}', out/'vendor.img'], 'health-readback.log')
    # The patched identity is recorded by the existing health gate.
    gate = (HERE.parents[1]/'android/health/health-gate.py').read_text()
    import re
    patched_sha = re.search(r'PATCHED_SHA\s*=\s*["\']([0-9a-f]{64})', gate).group(1)
    if sha(health) != patched_sha:
        raise RuntimeError('integrated health HAL is not the established sysfs patch')
    files = ['boot.img', 'vendor.img', 'pmi632-battery.ko', dtb.name]
    hashes = {name: sha(out/name) for name in files}
    (out/'SHA256SUMS').write_text(''.join(h+'  '+n+'\n' for n,h in hashes.items()))
    result = {'status': 'HOST BUILT; PHONE UNTESTED; B6 PARITY NOT CLAIMED', 'sha256': hashes,
              'kernel': 'R4 Image unchanged; separately compiled module; DT compatible selects new driver',
              'dt_node': charger, 'source_base': '07b9875',
              'refresh_main_commit': sp.check_output(['git','-C',str(MAIN),'rev-parse','HEAD'], text=True).strip(),
              'refresh_recipe_sha256': sha(refresh), 'health_sha256': sha(health),
              'config_sha256': sha(obj/'.config'), 'symbols_sha256': sha(obj/'Module.symvers')}
    (ROOT/'artifacts.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
