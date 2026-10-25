#!/usr/bin/env python3
"""Run the real Surface converter with Android C2 plane layouts under QEMU.

Requires an existing successful codec2/build.py output. No device/GPU/Binder.
"""
import argparse
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
QEMU = '/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--codec2-out', type=Path, required=True)
    ap.add_argument('--system', type=Path, default=Path.home() / 'A9-build/mainline/adsp/d2-v8-20261017/system-image/system')
    args = ap.parse_args()
    root, system = args.codec2_out.resolve(), args.system.resolve()
    flags = [f for f in json.loads((root / 'compile-flags.json').read_text())
             if f not in ('-DNDEBUG', '-DV4L2_CODEC2_SERVICE_V4L2_STORE')]
    test = root / 'out/surface-rgba-test'
    argv = [NDK / 'bin/aarch64-linux-android34-clang++', *flags, '-UNDEBUG',
            '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-copy',
            '-Wno-unused-parameter', '-Wno-missing-field-initializers',
            '-nostdlib++', '-Wl,--allow-shlib-undefined', HERE / 'surface-rgba.cpp',
            '-L' + str(system / 'lib64'), '-L' + str(root / 'gsi-libs/lib64'),
            '-lyuv', '-lc++', '-o', test]
    (root / 'surface-rgba-test-argv.json').write_text(json.dumps(list(map(str, argv)), indent=2) + '\n')
    with (root / 'surface-rgba-test-build.log').open('w') as log:
        result = subprocess.run(list(map(str, argv)), stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit('compile failed: ' + str(root / 'surface-rgba-test-build.log'))
    paths = ':'.join(map(str, [system / 'lib64/bootstrap', system / 'lib64', root / 'gsi-libs/lib64']))
    result = subprocess.run([QEMU, '-E', 'LD_LIBRARY_PATH=' + paths,
                             system / 'bin/bootstrap/linker64', test],
                            capture_output=True, text=True, timeout=30)
    (root / 'surface-rgba-test.log').write_text(result.stdout + result.stderr)
    print(result.stdout, end='')
    if result.returncode:
        raise SystemExit('test failed: ' + str(root / 'surface-rgba-test.log'))

if __name__ == '__main__':
    main()
