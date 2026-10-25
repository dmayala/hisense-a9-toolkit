#!/usr/bin/env python3
"""Build the bounded QRTR/QMI observer with an already-installed static toolchain."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
DEFAULT_CC = Path('/nix/store/4vpsm867635vxx8cxyp8kxr1jpisqqr5-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc')


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
    source = HERE / 's1-observe.c'
    argv = [str(compiler), '-std=c11', '-static', '-O2', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(output)]
    subprocess.run(argv, check=True)
    spec = importlib.util.spec_from_file_location('s1_prepare', HERE / 's1-prepare.py')
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    prepare.static_observer(output)
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    receipt = {'source_sha256': sha(source), 'output_sha256': sha(output),
               'compiler': str(compiler), 'compiler_sha256': sha(compiler), 'argv': argv,
               'static_aarch64': True, 'device_contact': False}
    output.chmod(0o500)
    output.with_name(output.name + '.build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('S1 static observer built offline:', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--cc', type=Path, default=DEFAULT_CC)
    args = parser.parse_args()
    build(args.output, args.cc)
