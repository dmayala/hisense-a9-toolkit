#!/usr/bin/env python3
"""Build a static Android arm64 bridge, no device operation or binary in git."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess as sp
HERE = Path(__file__).resolve().parent
NDK = Path(os.environ.get('ANDROID_NDK_HOME', '/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973'))
CC = NDK / 'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang'

def build(out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    binary = out / 'a9-rpmb-diag'
    command = [CC, '-O2', '-Wall', '-Wextra', '-Werror', '-static', '-fstack-protector-strong',
               HERE / 'main.c', HERE / 'diag.c', HERE / 'transport.c', '-o', binary]
    sp.run(list(map(str, command)), check=True)
    result = dict(command=list(map(str, command)), sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                  sources={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(HERE.glob('*.[ch]'))})
    (out / 'build.json').write_text(json.dumps(result, indent=2)+'\n')
    return binary

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--out', type=Path, required=True)
    print(build(p.parse_args().out))
