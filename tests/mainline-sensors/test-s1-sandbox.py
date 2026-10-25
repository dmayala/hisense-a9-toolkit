#!/usr/bin/env python3
"""Exercise production BPF and real namespace/chroot/capability/seccomp policy."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cc', default=shutil.which('cc') or shutil.which('gcc'))
parser.add_argument('--root-dir', type=Path, default=Path('/dev/shm'))
args = parser.parse_args()
assert args.cc, 'cached native compiler required'
with tempfile.TemporaryDirectory(prefix='s1-sandbox-compile-') as compiled:
    compiled = Path(compiled)
    include = ROOT / 'scripts/mainline/sensors'
    common = [str(args.cc), '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-I', str(include)]
    bpf = compiled / 'bpf'
    subprocess.run(common + [str(ROOT / 'tests/mainline-sensors/test-s1-sandbox-bpf.c'), '-o', str(bpf)], check=True)
    subprocess.run([str(bpf)], check=True)
    native = compiled / 'actual'
    subprocess.run(common + [str(ROOT / 'tests/mainline-sensors/test-s1-sandbox.c'), str(include / 's1-registry-fs.c'),
                             str(include / 's1-registry-sandbox.c'), '-o', str(native)], check=True)
    with tempfile.TemporaryDirectory(prefix='s1-sandbox-', dir=args.root_dir) as fixture:
        result = subprocess.run(['unshare', '-Ur', str(native), fixture], capture_output=True, text=True, timeout=30)
        print(result.stdout, end='')
        print(result.stderr, end='')
        assert result.returncode == 0, result.returncode
