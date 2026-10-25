#!/usr/bin/env python3
"""Host-only AIDL V4 radio HAL build using NDK r27 and frozen Android 16 APIs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = Path.home() / 'A9-build/mainline'
NDK = Path(os.environ.get('ANDROID_NDK_HOME', '/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973')) / 'toolchains/llvm/prebuilt/linux-x86_64/bin'
AIDL = os.environ.get('A9_AIDL', '/nix/store/iqyx3zkviyc1ik9snyhhw77aanc4b3zx-android-sdk-build-tools-36.0.0/libexec/android-sdk/build-tools/36.0.0/aidl')
MODULES = ['android.hardware.radio' + s for s in ['', '.config', '.data', '.messaging', '.modem', '.network', '.sim', '.voice']]


def run(argv):
    subprocess.run(list(map(str, argv)), check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=ROOT / 'cellular-20261020')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--uim-status-only', action='store_true', help='build only the small identifier-free UIM status/explicit power-up tool')
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.uim_status_only:
        binary = out / 'a9-uim-status'
        sources = [HERE / 'tools/uim-status.cpp', *[HERE / 'src/qmi/src' / n for n in
                   ['client.cc', 'message.cc', 'services.cc', 'qrtr_transport.cc', 'log.cc']]]
        run([NDK / 'aarch64-linux-android34-clang++', '-std=c++20', '-O2', '-static',
             '-ffunction-sections', '-fdata-sections', '-Wl,--gc-sections',
             '-I' + str(HERE / 'src/qmi/include'), *sources, '-o', binary])
        run([NDK / 'llvm-strip', '--strip-unneeded', binary])
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        (out / (binary.name + '.sha256')).write_text(digest + '  ' + str(binary) + '\n')
        print(str(binary) + '\nsha256 ' + digest)
        return
    cache = out / 'aosp'
    cache.mkdir(exist_ok=True)
    downloads = {
        'radio-aidl': 'platform/hardware/interfaces/+archive/refs/heads/android16-release/radio/aidl',
        'libbase-include': 'platform/system/libbase/+archive/refs/heads/android16-release/include',
    }
    for name, url in downloads.items():
        dest = cache / name
        if dest.is_dir():
            continue
        archive = cache / (name + '.tgz')
        urllib.request.urlretrieve('https://android.googlesource.com/' + url + '.tar.gz', archive)
        dest.mkdir()
        with tarfile.open(archive) as tf:
            tf.extractall(dest, filter='data')
    api = cache / 'radio-aidl/aidl_api'
    gen = out / 'gen'
    gen.mkdir(exist_ok=True)
    includes = ['-I' + str(api / m / '4') for m in MODULES]
    for module in MODULES:
        frozen = api / module / '4'
        run([AIDL, '--lang=ndk', '--structured', '--stability=vintf', '--version=4',
             '--hash=' + (frozen / '.hash').read_text().strip(), '--min_sdk_version=34',
             *includes, '-h', gen / 'include', '-o', gen / 'src',
             *sorted(frozen.rglob('*.aidl'))])
    binder = ROOT / 'sensors/aosp/binder-ndk'
    platform_libs = ROOT / 'android/dl/cf_arm64_x/tf/VENDOR/lib64'
    # NDK's __config_site unconditionally selects __ndk1. Preinclude a
    # local copy with the platform namespace before any libc++ header.
    config = out / 'platform-cxx-config.h'
    ndk_config = NDK.parent / 'sysroot/usr/include/c++/v1/__config_site'
    config.write_text(ndk_config.read_text().replace(
        '#define _LIBCPP_ABI_NAMESPACE __ndk1', '#define _LIBCPP_ABI_NAMESPACE __1'))
    flags = ['-include', str(config), '-std=c++20', '-O2', '-fPIE', '-ffunction-sections', '-fdata-sections',
             '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-deprecated-declarations', '-Wno-missing-field-initializers',
             '-DBINDER_STABILITY_SUPPORT',
             '-D_LIBCPP_DISABLE_DEPRECATION_WARNINGS',
             '-DANDROID_UTILS_REF_BASE_DISABLE_IMPLICIT_CONSTRUCTION']
    for path in [gen / 'include', cache / 'libbase-include', HERE / 'src/qmi/include',
                 HERE / 'src/minradio/include', HERE / 'src/hal',
                 *[binder / n for n in ['include_cpp', 'include_ndk', 'include_platform']]]:
        flags.append('-I' + str(path))
    sources = sorted(gen.glob('src/**/*.cpp')) + sorted((HERE / 'src').rglob('*.cpp')) + sorted((HERE / 'src/qmi/src').glob('*.cc'))
    objects = out / 'obj'
    objects.mkdir(exist_ok=True)
    # Reuse objects only with identical flags, source and header inputs.
    digest_input = hashlib.sha256(json.dumps(flags).encode())
    headers = [config] + [p for root in [HERE / 'src', gen / 'include', binder, cache / 'libbase-include']
               for p in root.rglob('*.h')]
    for path in sorted(set(sources + headers)):
        digest_input.update(str(path).encode())
        digest_input.update(path.read_bytes())
    fingerprint = digest_input.hexdigest()
    stamp = objects / 'build-fingerprint'
    reuse = stamp.exists() and stamp.read_text() == fingerprint
    cxx = NDK / 'aarch64-linux-android34-clang++'
    def compile_one(pair):
        index, src = pair
        obj = objects / (str(index) + '.o')
        if not reuse or not obj.exists():
            run([cxx, *flags, '-c', src, '-o', obj])
        return obj
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        objs = list(pool.map(compile_one, enumerate(sources)))
    binary = out / 'android.hardware.radio-service.a9'
    run([cxx, '-pie', '-nostdlib++', '-Wl,--gc-sections', '-Wl,--allow-shlib-undefined',
         '-Wl,-z,relro,-z,now', *objs, '-L' + str(platform_libs),
         '-L' + str(ROOT / 'sensors/gsi-libs'), '-lbase', '-lc++', '-lbinder_ndk', '-llog', '-o', binary])
    run([NDK / 'llvm-strip', '--strip-unneeded', binary])
    stamp.write_text(fingerprint)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (out / (binary.name + '.sha256')).write_text(digest + '  ' + str(binary) + '\n')
    (out / 'build.json').write_text(json.dumps({
        'source_commit': subprocess.check_output(['git', '-C', HERE, 'rev-parse', 'HEAD'], text=True).strip(),
        'binary': str(binary), 'sha256': digest, 'source_files': len(sources),
        'aidl_hashes': {m: (api / m / '4/.hash').read_text().strip() for m in MODULES},
        'flags': flags, 'target_executed': False,
    }, indent=2) + '\n')
    print(str(binary) + '\nsha256 ' + digest)


if __name__ == '__main__':
    main()
