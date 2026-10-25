#!/usr/bin/env python3
"""Build a small external module against immutable R4 headers/CRCs. Host only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
BASE = Path.home() / 'A9-build/mainline/demo-integrated-20261019'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    obj, mod = out / 'obj', out / 'module'
    obj.mkdir(parents=True, exist_ok=True)
    mod.mkdir(exist_ok=True)
    # Only prepared headers and build tools, not a second complete kernel tree.
    for name in ('include', 'scripts', 'arch/arm64/include'):
        shutil.copytree(BASE / 'obj' / name, obj / name, symlinks=True, dirs_exist_ok=True)
    for name in ('.config', 'Module.symvers', 'Makefile'):
        shutil.copyfile(BASE / 'obj' / name, obj / name)
    if not (obj / 'source').exists():
        (obj / 'source').symlink_to(BASE / 'kernel')
    for name in ('Makefile', 'a9-usb-compat.c'):
        shutil.copyfile(HERE / name, mod / name)
    env = dict(os.environ)
    env['PATH'] = ':'.join(json.loads((BASE / 'source.json').read_text())['tools']) + ':' + env['PATH']
    command = ['make', '-C', str(BASE / 'kernel'), 'O=' + str(obj), 'M=' + str(mod),
               'ARCH=arm64', 'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc',
               'LOCALVERSION=+', '-j4', 'modules']
    with (out / 'module-build.log').open('w') as log:
        subprocess.run(command, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)
    shutil.copyfile(mod / 'a9-usb-compat.ko', out / 'a9-usb-compat.ko')


if __name__ == '__main__':
    main()
