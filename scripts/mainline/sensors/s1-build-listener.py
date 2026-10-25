#!/usr/bin/env python3
"""Build static upstream-FastRPC RAM listener from cached local source/tools."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
CC = Path('/nix/store/4vpsm867635vxx8cxyp8kxr1jpisqqr5-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc')
SOURCES = ('s1-listener-main.c', 's1-rpc.c', 's1-registry-fs.c', 's1-registry-sandbox.c')
HEADERS = ('s1-rpc.h', 's1-registry-fs.h', 's1-registry-sandbox.h')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output, cc, root="/mnt/vendor/persist/s1-listener-root"):
    output = output.expanduser().absolute()
    allowed = Path.home() / 'A9-build/mainline'
    if output.parent.resolve() != output.parent or not output.is_relative_to(allowed) or output.exists() or output.is_symlink():
        raise ValueError('new private output below ~/A9-build/mainline required')
    if shutil.disk_usage('/').free < 15_010_000_000:
        raise RuntimeError('15 GB floor; ask coordinator for space')
    if not cc.is_file():
        raise ValueError('cached compiler missing; no downloads')
    os.umask(0o077)
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [str(cc), '-std=c11', '-static', '-pthread', '-O2', '-Wall', '-Wextra', '-Werror',
            '-DS1_LISTENER_ROOT="'+root+'"', *(str(HERE / name) for name in SOURCES), '-o', str(output)]
    subprocess.run(args, check=True)
    spec = importlib.util.spec_from_file_location('prepare', HERE / 's1-prepare.py')
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    prepare.static_observer(output)
    output.chmod(0o500)
    receipt = {'status': 'PASS-STATIC-AARCH64-LINK', 'output_sha256': sha(output),
               'sources': {name: sha(HERE / name) for name in SOURCES + HEADERS},
               'compiler': str(cc), 'compiler_sha256': sha(cc), 'argv': args,
               'static_aarch64': True, 'device_contact': False, 'registry_root': root,
               'execution_admission': 'NO_GO_INDEPENDENT_REVIEW_REQUIRED'}
    output.with_name(output.name + '.build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('S1 static RAM listener built offline:', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--cc', type=Path, default=CC)
    parser.add_argument('--root', choices=('/mnt/vendor/persist/s1-listener-root', '/mnt/vendor/a9-sensors/s1-listener-root'), default='/mnt/vendor/persist/s1-listener-root')
    a = parser.parse_args()
    build(a.output, a.cc, a.root)
