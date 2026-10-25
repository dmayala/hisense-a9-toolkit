#!/usr/bin/env python3
"""Host-only USB gadget HAL + Android discovery/state compatibility. Uses cached NDK, AIDL and Binder NDK.

Frozen Android 16 V1 AIDL files are vendored; no network or phone access.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
BUILD = Path.home() / 'A9-build/mainline'
R4 = BUILD / 'integrated-latency-v3-runtime-probe-r4-20261019/images/vendor.img'
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin')
AIDL = '/nix/store/iqyx3zkviyc1ik9snyhhw77aanc4b3zx-android-sdk-build-tools-36.0.0/libexec/android-sdk/build-tools/36.0.0/aidl'


def build_hal(out, cache):
    gen = out / 'gen'
    gen.mkdir(exist_ok=True)
    api = HERE / 'aidl'
    subprocess.run([AIDL, '--lang=ndk', '--structured', '--stability=vintf', '--version=1',
                    '--hash=' + (api / '.hash').read_text().strip(), '--min_sdk_version=34',
                    '-I', str(api), '-h', str(gen / 'include'), '-o', str(gen / 'src'),
                    *map(str, sorted(api.rglob('*.aidl')))], check=True)
    binder = cache / 'aosp/binder-ndk'
    binary = out / 'android.hardware.usb.gadget-service.a9'
    command = [str(NDK / 'aarch64-linux-android34-clang++'), '-std=c++17', '-O2',
               '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
               '-Wno-deprecated-declarations', '-DBINDER_STABILITY_SUPPORT', '-fPIE', '-pie',
               '-fstack-protector-strong', '-D_FORTIFY_SOURCE=2', '-static-libstdc++',
               '-Wl,-z,relro,-z,now', '-Wl,--allow-shlib-undefined',
               *['-I' + str(binder / p) for p in ('include_cpp', 'include_ndk', 'include_platform')],
               '-I' + str(gen / 'include'),
               *map(str, [HERE / p for p in ('service.cpp', 'UsbGadget.cpp', 'gadget_core.cpp')]),
               *map(str, sorted((gen / 'src').rglob('*.cpp'))),
               '-L' + str(cache / 'gsi-libs'), '-lbinder_ndk', '-llog', '-o', str(binary)]
    subprocess.run(command, check=True)
    subprocess.run([str(NDK / 'llvm-strip'), '--strip-unneeded', str(binary)], check=True)
    return binary, command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=BUILD / 'usb-20261020/hal')
    parser.add_argument('--binder-cache', type=Path, default=BUILD/'sensors', help='build-time Binder NDK headers and target gsi-libs')
    parser.add_argument('--hal-only', action='store_true', help='compile only the userspace HAL; no historical image/kernel inputs')
    parser.add_argument('--overlay', type=Path, help='also stage into integrated vendor overlay')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    binary, command = build_hal(out, args.binder_cache)
    if args.hal_only:
        sources = [HERE / p for p in ('service.cpp', 'UsbGadget.cpp', 'UsbGadget.h', 'gadget_core.cpp', 'gadget_core.h')]
        sources += sorted((HERE / 'aidl').rglob('*.aidl'))
        inputs = [p for p in args.binder_cache.rglob('*') if p.is_file()]
        input_pins = {str(p.relative_to(args.binder_cache)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        result = dict(host_only=True, binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                      source_sha256={str(p.relative_to(HERE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}, binder_inputs_sha256=input_pins, command=command)
        (out / 'hal-build.json').write_text(json.dumps(result, indent=2)+'\n')
        print('USB gadget HAL compiled; no kernel rebuild, image inputs or device access')
        return
    stage = out / 'vendor-overlay'
    shutil.copytree(HERE / 'vendor', stage, dirs_exist_ok=True)
    dest = stage / 'bin/hw' / binary.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(binary, dest)
    subprocess.run(['python3', '-I', str(HERE / 'build-compat.py'), '--out', str(out / 'compat')], check=True)
    module = stage / 'lib/modules/a9-usb-compat.ko'
    module.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out / 'compat/a9-usb-compat.ko', module)

    # Preserve R4's existing boot/dead-man/session configuration. Only replace
    # its USB setup; the old helper still discovers UDC and runs debug consoles.
    def read_vendor(path):
        temp = out / 'vendor-readback'
        subprocess.run(['debugfs', '-R', f'dump /{path} {temp}', str(R4)],
                       check=True, capture_output=True)
        return temp.read_text()

    init = read_vendor('etc/init/hw/init.hlte556n.rc')
    init = init.replace('    setprop sys.usb.configfs 1', '''    # Mainline has FunctionFS MTP/PTP; legacy platform mtp.gs0 is absent.
    mkdir /config/usb_gadget/g1/functions/ffs.mtp
    mkdir /config/usb_gadget/g1/functions/ffs.ptp
    mkdir /config/usb_gadget/g1/functions/rndis.gs4
    write /config/usb_gadget/g1/os_desc/use 1
    write /config/usb_gadget/g1/os_desc/b_vendor_code 0x1
    write /config/usb_gadget/g1/os_desc/qw_sign "MSFT100"
    symlink /config/usb_gadget/g1/configs/b.1 /config/usb_gadget/g1/os_desc/b.1
    write /config/usb_gadget/g1/functions/rndis.gs4/os_desc/interface.rndis/compatible_id "RNDIS"
    write /config/usb_gadget/g1/functions/rndis.gs4/os_desc/interface.rndis/sub_compatible_id "5162001"
    mkdir /dev/usb-ffs/mtp 0770 mtp mtp
    mount functionfs mtp /dev/usb-ffs/mtp rmode=0770,fmode=0660,uid=1024,gid=1024,no_disconnect=1
    mkdir /dev/usb-ffs/ptp 0770 mtp mtp
    mount functionfs ptp /dev/usb-ffs/ptp rmode=0770,fmode=0660,uid=1024,gid=1024,no_disconnect=1
    # Preserve R4's platform-init ADB and ACM/NCM fallback until enumeration.
    setprop sys.usb.configfs 1''')
    dest = stage / 'etc/init/hw/init.hlte556n.rc'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(init)
    # Canonical helper includes R22d generation-deduplicated uevents and the
    # row27 unconditional configfs=2 handoff. Do not reconstruct the old helper.
    helper = (HERE.parent / 'android/vendor/bin/a9ml-usb.sh').read_text()
    (stage / 'bin/a9ml-usb.sh').write_text(helper)
    # The legacy class/state bridge replaces the RRO dependency. An existing
    # output directory may still have the old overlay from an earlier build.
    shutil.rmtree(stage / 'overlay/A9Usb', ignore_errors=True)
    if args.overlay:
        shutil.rmtree(args.overlay / 'overlay/A9Usb', ignore_errors=True)
    if args.overlay:
        shutil.copytree(stage, args.overlay, dirs_exist_ok=True)
    outputs = {str(p.relative_to(stage)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(stage.rglob('*')) if p.is_file()}
    (out / 'build.json').write_text(json.dumps({'outputs': outputs, 'command': command}, indent=2) + '\n')
    print('USB gadget HAL and vendor overlay built; phone acceptance pending')


if __name__ == '__main__':
    main()
