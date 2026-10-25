#!/usr/bin/env python3
"""Compile/run real broker filesystem syscalls on a private actual tmpfs tree.

Use --root-dir /dev/shm (needs filesystem permission outside the workspace).
No syscall interposition: openat2, proc-FD reopening and filesystem policy are
the production implementations. Optional --qemu runs static AArch64 tests.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root-dir', required=True, type=Path)
parser.add_argument('--cc', default=shutil.which('cc') or shutil.which('gcc'))
parser.add_argument('--qemu', type=Path)
args = parser.parse_args()
assert args.cc, 'cached C compiler required'
with tempfile.TemporaryDirectory(prefix='s1-fs-compile-') as compiled:
    binary = Path(compiled) / 'test-s1-registry-fs'
    command = [str(args.cc), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror']
    if args.qemu:
        command.append('-static')
    command += ['-I', str(ROOT / 'scripts/mainline/sensors'),
                str(ROOT / 'tests/mainline-sensors/test-s1-registry-fs.c'),
                str(ROOT / 'scripts/mainline/sensors/s1-registry-fs.c'), '-o', str(binary)]
    subprocess.run(command, check=True)
    print(json.dumps({'fixture': 'actual tmpfs, no syscall interposition',
                      'compiler': str(args.cc), 'qemu': str(args.qemu) if args.qemu else None,
                      'sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (ROOT / 'scripts/mainline/sensors/s1-registry-fs.c',
                                              ROOT / 'scripts/mainline/sensors/s1-registry-fs.h',
                                              ROOT / 'tests/mainline-sensors/test-s1-registry-fs.c',
                                              ROOT / 'tests/mainline-sensors/test-s1-registry-fs.py')}}, sort_keys=True), flush=True)
    with tempfile.TemporaryDirectory(prefix='s1-fs-', dir=args.root_dir) as fixture:
        os.chmod(fixture, 0o700)
        invocation = ([str(args.qemu)] if args.qemu else []) + [str(binary), fixture, compiled]
        result = subprocess.run(invocation, check=False, capture_output=True, text=True, timeout=90)
        print(result.stdout, end='')
        print(result.stderr, end='')
        assert result.returncode == 0, result.returncode
