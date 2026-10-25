#!/usr/bin/env python3
"""Build the standalone static Android linear-input probe; never execute it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    os.umask(0o077)
    a.out.mkdir()  # Preserve existing bundles.
    command = [str(NDK/'bin/aarch64-linux-android30-clang'), '-std=c11', '-O2',
               '-Wall', '-Wextra', '-Werror', '-static', str(HERE/'linear.c'), '-o', str(a.out/'linear')]
    with (a.out/'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    (a.out/'manifest.json').write_text(json.dumps(dict(hardware_tested=False, command=command,
        source_sha256=sha(HERE/'linear.c'), binary_sha256=sha(a.out/'linear')), indent=2)+'\n')
    print('HVENC_LINEAR_BUILD_PASS hardware_tested=0')


if __name__ == '__main__':
    main()
