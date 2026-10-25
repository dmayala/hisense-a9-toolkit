#!/usr/bin/env python3
"""Host-only IPA4.2 external module + R4 DT boot derivative. No full kernel copy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = Path.home() / 'A9-build/mainline'
BASE = ROOT / 'demo-integrated-20261019'
R4 = ROOT / 'integrated-latency-v3-runtime-probe-r4-20261019'
DTC = Path('/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=ROOT / 'cellular-20261020/ipa')
    ap.add_argument('--boot', type=Path, default=R4 / 'images/boot.img', help='R4-compatible input; preserve its cumulative DT changes')
    ap.add_argument('--stock-vendor', type=Path, default=ROOT / 'android/golden/vendor_b.img')
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    if shutil.disk_usage(out).free < 15*1024**3 + 128*1024**2:
        raise RuntimeError('need 15GiB floor plus 128MiB incremental build budget')
    env = dict(os.environ)
    env['PATH'] = ':'.join(json.loads((BASE / 'source.json').read_text())['tools']) + ':' + env['PATH']
    def run(argv, log=None, **kw):
        if log:
            with (out / log).open('w') as stream:
                return subprocess.run(list(map(str, argv)), env=env, check=True,
                                      stdout=stream, stderr=subprocess.STDOUT, **kw)
        return subprocess.run(list(map(str, argv)), env=env, check=True, **kw)
    private = out / 'kernel-delta'
    source = private / 'drivers/net/ipa'
    if not source.exists():
        shutil.copytree(BASE / 'kernel/drivers/net/ipa', source,
                        ignore=shutil.ignore_patterns('*.o', '*.ko', '*.cmd', '*.mod', '*.mod.c', 'Module.symvers', 'modules.order'))
        binding = Path('Documentation/devicetree/bindings/net/qcom,ipa.yaml')
        (private / binding).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BASE / 'kernel' / binding, private / binding)
        run(['git', 'apply', HERE / 'kernel/sm6115-ipa.patch'], cwd=private)
    before = {n: sha(BASE / 'obj' / n) for n in ['.config', 'Module.symvers']}
    run(['make', '-C', BASE / 'kernel', 'O=' + str(BASE / 'obj'), 'M=' + str(source),
         'ARCH=arm64', 'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc',
         'LOCALVERSION=+', '-j4', 'modules'], 'ipa-module-build.log')
    after = {n: sha(BASE / 'obj' / n) for n in before}
    if after != before:
        raise RuntimeError('base config/symbols changed during external build')
    overlay = out / 'vendor-overlay'
    modules = overlay / 'lib/modules'
    modules.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / 'ipa.ko', modules / 'ipa.ko')
    shutil.copyfile(BASE / 'obj/drivers/net/ethernet/qualcomm/rmnet/rmnet.ko', modules / 'rmnet.ko')
    # Fixed new closure. Its other dependencies are already shipped by R4.
    dep = subprocess.check_output(['aarch64-unknown-linux-gnu-readelf', '-p', '.modinfo', modules / 'ipa.ko'], text=True, env=env)
    (out / 'ipa-module-info.txt').write_text(dep)
    fw = overlay / 'firmware/qcom/sm6115/hisense/hlte556n'
    fw.mkdir(parents=True, exist_ok=True)
    firmware = {}
    for name in ['ipa_fws.mdt', *['ipa_fws.b%02d' % i for i in range(5)]]:
        dest = fw / name
        # Read the saved stock image, never a phone or real modem store.
        run(['debugfs', '-R', f'dump /firmware/{name} {dest}', args.stock_vendor],
            capture_output=True)
        firmware[name] = {'bytes': dest.stat().st_size, 'sha256': sha(dest)}
    # Apply the overlay to the public R4 DTB. No private MAC image is read.
    boot = args.boot.read_bytes()
    page = struct.unpack_from('<I', boot, 36)[0]
    pad = lambda n: (n + page - 1) // page * page
    sizes = [struct.unpack_from('<I', boot, n)[0] for n in [8, 16, 24, 1632, 1648]]
    pos, blobs = page, []
    for size in sizes:
        blobs.append(boot[pos:pos+size])
        pos += pad(size)
    dtb = out / 'base-r4.dtb'
    dtb.write_bytes(blobs[4])
    pp = out / 'a9-ipa.pp.dts'
    run(['gcc', '-E', '-nostdinc', '-undef', '-D__DTS__', '-x', 'assembler-with-cpp',
         '-I' + str(BASE / 'kernel/include'), HERE / 'kernel/a9-ipa.dtso', '-o', pp])
    dtbo = out / 'a9-ipa.dtbo'
    run([DTC / 'dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', dtbo, pp], 'dtc.log')
    result_dtb = out / 'sm6115-hisense-hlte556n-ipa.dtb'
    run([DTC / 'fdtoverlay', '-i', dtb, '-o', result_dtb, dtbo], 'dt-overlay.log')
    blobs[4] = result_dtb.read_bytes()
    header = bytearray(boot[:page])
    struct.pack_into('<I', header, 1648, len(blobs[4]))
    digest = hashlib.sha1()
    for blob in blobs:
        digest.update(blob)
        digest.update(struct.pack('<I', len(blob)))
    header[576:608] = digest.digest() + b'\0'*12
    (out / 'boot-ipa.img').write_bytes(header + b''.join(b + b'\0'*(pad(len(b))-len(b)) for b in blobs))
    receipt = {'source_commit': subprocess.check_output(['git', '-C', HERE, 'rev-parse', 'HEAD'], text=True).strip(),
               'base_config_symbols': before, 'base_unchanged': before == after,
               'kernel_Image_unchanged': True, 'boot_base_sha256': sha(args.boot),
               'boot_sha256': sha(out / 'boot-ipa.img'), 'dtb_sha256': sha(result_dtb),
               'modules': {p.name: sha(p) for p in modules.glob('*.ko')},
               'firmware_source': str(args.stock_vendor), 'firmware': firmware,
               'device_tested': False}
    (out / 'ipa-build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
