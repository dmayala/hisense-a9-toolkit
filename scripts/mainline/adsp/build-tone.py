#!/usr/bin/env python3
"""Build the fixed D2 helper offline; validate static AArch64 ELF and rehearse describe.

Usage: build-tone.py --out BUILD_OUTPUT --rehearsal /private/d2-tone-rehearsal.json
The rehearsal receipt binds already completed native/sanitized/AArch64 mock
tests to the exact current production and test source. No device tool is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).with_name('d2-tone.c')
TEST = ROOT / 'tests/mainline-adsp/test-d2-tone.c'
CC = Path('/nix/store/4vpsm867635vxx8cxyp8kxr1jpisqqr5-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc')
QEMU = Path('/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64')
DESCRIPTION = ('A9D2Earpiece PCM0p mono S16_LE 48000Hz 1000Hz 48000frames '
               'peak1036 (-30dBFS), 5ms ramps, 1 START, 1 nonblocking DRAIN '
               '<=1.2s, DROP/close; kernel PA deadline 1.5s\n')
FLOOR = 15_000_000_000


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def floor():
    free = shutil.disk_usage('/').free
    if free < FLOOR:
        raise SystemExit(f'D2 helper disk STOP: {free} bytes < {FLOOR}')


def elf(path):
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01':
        raise SystemExit('helper is not ELF64 little endian')
    e_type, machine = struct.unpack_from('<HH', data, 16)
    phoff, shoff = struct.unpack_from('<QQ', data, 32)
    phsize, phnum, shsize, shnum = struct.unpack_from('<HHHH', data, 54)
    if e_type != 2 or machine != 183 or phsize != 56 or not phnum:
        raise SystemExit('helper is not an AArch64 EXEC executable')
    if phoff + phsize * phnum > len(data) or shoff + shsize * shnum > len(data):
        raise SystemExit('truncated ELF tables')
    # PT_INTERP and PT_DYNAMIC are forbidden. The latter excludes DT_NEEDED.
    types = [struct.unpack_from('<I', data, phoff + j * phsize)[0]
             for j in range(phnum)]
    if 2 in types or 3 in types or 1 not in types:
        raise SystemExit('helper has interpreter/dynamic dependencies or no load segment')
    if shsize != 64:
        raise SystemExit('unexpected ELF section header size')
    for j in range(shnum):
        if struct.unpack_from('<I', data, shoff + j * shsize + 4)[0] == 6:
            raise SystemExit('helper has a dynamic section')
    return {'class': 'ELF64', 'machine': 'AArch64', 'type': 'EXEC',
            'PT_INTERP': False, 'PT_DYNAMIC': False, 'DT_NEEDED': []}


def rehearsal(path):
    r = json.loads(path.read_text())
    if r.get('source_sha256') != sha(SOURCE) or r.get('test_sha256') != sha(TEST):
        raise SystemExit('rehearsal source/test hashes do not match current source')
    expected = {'native', 'asan_ubsan', 'aarch64_qemu'}
    if set(r.get('checks', {})) != expected:
        raise SystemExit('rehearsal must include native, ASan/UBSan, and AArch64 qemu')
    for name, check in r['checks'].items():
        log = Path(check['log'])
        if check.get('result') != 'PASS' or sha(log) != check['log_sha256']:
            raise SystemExit(f'invalid {name} rehearsal log receipt')
        lines = log.read_text().splitlines()
        if not lines or not lines[-1].startswith('D2-TONE-MOCK PASS:'):
            raise SystemExit(f'{name} rehearsal has no final PASS')
    return r


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--rehearsal', required=True, type=Path)
    p.add_argument('--cc', type=Path, default=CC)
    p.add_argument('--qemu', type=Path, default=QEMU)
    a = p.parse_args()
    out = a.out.resolve()
    permitted = [Path('/home/user/A9-build'), Path('/tmp')]
    if not any(out.is_relative_to(r) for r in permitted):
        raise SystemExit('output must be under A9-build or /tmp')
    floor()
    checks = rehearsal(a.rehearsal.resolve())
    cc, qemu = a.cc.resolve(strict=True), a.qemu.resolve(strict=True)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='d2-tone-build-', dir=out) as td:
        binary = Path(td) / 'd2-tone'
        command = [str(cc), '-static', '-std=c11', '-Wall', '-Wextra', '-Werror',
                   '-O2', str(SOURCE), '-o', str(binary)]
        subprocess.run(command, check=True, timeout=120)
        info = elf(binary)
        describe = subprocess.check_output([str(qemu), str(binary), '--describe'],
                                           text=True, timeout=10)
        if describe != DESCRIPTION:
            raise SystemExit('helper --describe differs from the fixed D2 contract')
        floor()
        receipt = {'schema': 'a9-adsp-d2-tone-build-v1', 'source_sha256': sha(SOURCE),
                   'builder_sha256': sha(__file__),
                   'test_sha256': sha(TEST), 'compiler': str(cc),
                   'compiler_sha256': sha(cc),
                   'compiler_version': subprocess.check_output([str(cc), '--version'], text=True),
                   'compiler_flags': command[1:-3], 'qemu': str(qemu),
                   'qemu_sha256': sha(qemu), 'elf': info, 'description': describe,
                   'binary_sha256': sha(binary), 'binary_bytes': binary.stat().st_size,
                   'rehearsal_receipt_sha256': sha(a.rehearsal), 'checks': checks['checks'],
                   'sanitizer_options': checks.get('sanitizer_options', '')}
        for name, check in receipt['checks'].items():
            dest = out / f'd2-tone-test-{name}.log'
            floor()
            shutil.copyfile(check['log'], dest)
            check['log'] = str(dest)
        floor()
        binary.replace(out / 'd2-tone')
        (out / 'd2-tone-build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('D2-TONE-BUILD PASS ' + receipt['binary_sha256'])


if __name__ == '__main__':
    main()
