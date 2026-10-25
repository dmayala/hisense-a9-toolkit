#!/usr/bin/env python3
"""Offline build: cached NDK and binder headers/libs only. Never runs target code."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT=Path('/home/user/A9-build/mainline/gnss-integrated-20261020')/'out'
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin')
CACHE = Path('/home/user/A9-build/mainline/sensors')

def main():
    global OUT
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,default=OUT)
    OUT=ap.parse_args().out.resolve()
    OUT.mkdir(parents=True,exist_ok=True)
    binder = CACHE / 'aosp/binder-ndk'
    flags = ['-O2', '-Wall', '-Wextra', '-Werror', '-fstack-protector-strong', '-D_FORTIFY_SOURCE=2']
    service = [str(NDK/'aarch64-linux-android34-clang++'), '-std=c++17', '-DBINDER_STABILITY_SUPPORT',
               *flags, '-fPIE', '-pie', '-static-libstdc++', '-Wl,-z,relro,-z,now', '-Wl,--allow-shlib-undefined']
    for name in ['include_cpp', 'include_ndk', 'include_platform']:
        service += ['-I'+str(binder/name)]
    service += [str(HERE/'service.cpp'), str(HERE/'runtime.cpp'), '-L'+str(CACHE/'gsi-libs'), '-lbinder_ndk',
                '-o', str(OUT/'android.hardware.gnss-service.a9')]
    helper = [str(NDK/'aarch64-linux-android30-clang'), *flags, '-static', '-D_GNU_SOURCE',
              str(HERE/'upstream/g1-loc.c'), str(HERE/'loc-main.c'), '-lm', '-o', str(OUT/'a9-gnss-loc')]
    for argv in [service, helper]:
        subprocess.run(argv, check=True)
    receipt = {'offline': True, 'target_executed': False, 'argv': [service, helper],
               'outputs': {name: hashlib.sha256((OUT/name).read_bytes()).hexdigest()
                           for name in ['android.hardware.gnss-service.a9','a9-gnss-loc']},
               'inputs': {str(p.relative_to(HERE)):hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts}}
    (OUT/'build.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print('GNSS service and confined client built offline')

if __name__ == '__main__':
    main()
