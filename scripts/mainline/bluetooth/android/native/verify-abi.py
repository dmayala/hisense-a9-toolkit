#!/usr/bin/env python3
"""Static versioned Binder symbol admission; never loads either Android ELF."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--hal', type=Path, required=True)
parser.add_argument('--binder', type=Path, required=True)
parser.add_argument('--readelf', default=os.environ.get('LLVM_READELF',
    '/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/'
    'libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf'))
args = parser.parse_args()


def symbols(path, undefined):
    output = subprocess.run([args.readelf, '--dyn-syms', '--wide', str(path)],
                            check=True, text=True, capture_output=True).stdout
    result = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 8 or not fields[0].endswith(':'):
            continue
        if (fields[6] == 'UND') != undefined:
            continue
        symbol = fields[7]
        if '@LIBBINDER_NDK' in symbol:
            name, version = symbol.replace('@@', '@').rsplit('@', 1)
            result.add((name, version))
    return result


required = symbols(args.hal, True)
provided = symbols(args.binder, False)
missing = sorted(required - provided)
if not required or missing:
    raise SystemExit('Binder ABI check failed: missing/empty versioned symbol set')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
print(json.dumps({'scope': 'static ELF symbol/version comparison; no Android execution',
                  'hal_sha256': sha(args.hal), 'binder_sha256': sha(args.binder),
                  'required_binder_symbols': len(required), 'missing': missing,
                  'result': 'PASS'}, indent=2))
