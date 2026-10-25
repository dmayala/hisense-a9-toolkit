#!/usr/bin/env python3
"""Compile a tiny static ARM64 observer offline; never contacts the phone."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
CC = '/nix/store/ik128k4xm49sbm27i1nzxlr06aflixma-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=Path('/home/user/A9-build/mainline/soc-sleep-20261024'))
    args = p.parse_args(); args.out.mkdir(parents=True, exist_ok=True)
    assert shutil.disk_usage(args.out).free >= 15*1024**3 + 4*1024**2
    target = args.out/'a9-resume-stats'; source = HERE/'resume-stats.c'
    command = [CC, '-static', '-O2', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(target)]
    with (args.out/'build.log').open('w') as log: sp.run(command, check=True, stdout=log, stderr=sp.STDOUT)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    (args.out/'build.json').write_text(json.dumps(dict(host_only=True,phone_access=False,command=command,
        source_sha256=digest(source),binary_sha256=digest(target),wake_alarm=False),indent=2)+'\n')
    print(target)


if __name__ == '__main__': main()
