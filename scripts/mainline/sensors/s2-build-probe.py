#!/usr/bin/env python3
"""Build the static AArch64 S2a SNS probe with the already-installed musl toolchain (no downloads)."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
DEFAULT_CC = Path('/nix/store/4vpsm867635vxx8cxyp8kxr1jpisqqr5-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc')
SOURCES = ('s2-sns.c', 's2-client.c', 's2-qrtr.c', 's2-sns-probe.c', 's2-sns.h', 's2-client.h', 's2-qrtr.h')


def build(out, compiler):
    output = out.expanduser().absolute()
    allowed = (Path('/tmp'), Path.home() / 'A9-build/mainline/sensors-s1')
    if (output.parent.resolve() != output.parent or
            not any(output.parent.is_relative_to(root) for root in allowed) or
            output.exists() or output.is_symlink()):
        raise ValueError('new private output below /tmp or ~/A9-build/mainline/sensors-s1 required')
    if shutil.disk_usage('/').free < 15_010_000_000:
        raise RuntimeError('need 15 GB free on /; ask coordinator')
    if not compiler.is_file():
        raise ValueError('cached compiler not found; no download permitted')
    output.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(compiler), '-std=c11', '-static', '-pthread', '-O2', '-Wall', '-Wextra', '-Werror',
            *(str(HERE / n) for n in SOURCES if n.endswith('.c')), '-lm', '-o', str(output)]
    subprocess.run(argv, check=True)
    spec = importlib.util.spec_from_file_location('s1_prepare', HERE / 's1-prepare.py')
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    prepare.static_observer(output)
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    receipt = {'sources': {name: sha(HERE / name) for name in SOURCES}, 'output_sha256': sha(output),
               'compiler': str(compiler), 'compiler_sha256': sha(compiler), 'argv': argv,
               'static_aarch64': True, 'device_contact': False}
    output.chmod(0o500)
    output.with_name(output.name + '.build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('S2 static SNS probe built offline:', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--cc', type=Path, default=DEFAULT_CC)
    args = parser.parse_args()
    build(args.output, args.cc)
