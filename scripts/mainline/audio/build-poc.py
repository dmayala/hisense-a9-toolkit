#!/usr/bin/env python3
"""Build the A9 audio POC entirely offline.

--tools-only preserves the initial ESS-only tool/DT preparation path.
The default delegates to the R4-compatible external module build (about 2 GiB).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess as sp

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BUILD = Path.home() / 'A9-build/mainline'
BASE = BUILD / 'demo-integrated-20261019'
R4 = BUILD / 'integrated-latency-v3-runtime-probe-r4-20261019'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def replace_dtb(image, dtb):
    page = struct.unpack_from('<I', image, 36)[0]
    assert struct.unpack_from('<I', image, 40)[0] == 2
    pad = lambda n: (n + page - 1) // page * page
    sizes = [struct.unpack_from('<I', image, off)[0] for off in (8, 16, 24, 1632, 1648)]
    blobs, pos = [], page
    for size in sizes:
        blobs.append(image[pos:pos + size])
        pos += pad(size)
    blobs[4] = dtb
    return page, blobs


def pack_boot(image, kernel, dtb):
    page, blobs = replace_dtb(image, dtb)
    pad = lambda n: (n + page - 1) // page * page
    blobs[0] = kernel
    header = bytearray(image[:page])
    struct.pack_into('<I', header, 8, len(kernel))
    struct.pack_into('<I', header, 1648, len(dtb))
    struct.pack_into('<Q', header, 1636, page + sum(pad(len(b)) for b in blobs[:3]))
    digest = hashlib.sha1()
    for blob in blobs:
        digest.update(blob)
        digest.update(struct.pack('<I', len(blob)))
    header[576:608] = digest.digest() + b'\0' * 12
    return header + b''.join(b.ljust(pad(len(b)), b'\0') for b in blobs)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=BUILD / 'audio-20261020')
    p.add_argument('--tools-only', action='store_true')
    p.add_argument('--vendor', action='store_true', help='refresh vendor after the kernel build')
    a = p.parse_args()
    if a.vendor and not (REPO / 'scripts/mainline/android/refresh-integrated-vendor.py').is_file():
        p.error('--vendor requires the integrated vendor refresh merged into this checkout')
    if not a.tools_only:
        sp.run(['python3', '-I', str(HERE / 'build-a5.py'), '--out', str(a.out),
                *(['--vendor'] if a.vendor else [])], check=True)
        return
    os.umask(0o077)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env['PATH'] = ':'.join(json.loads((BASE / 'source.json').read_text())['tools']) + ':' + env['PATH']
    commands = []

    def run(argv, log):
        argv = list(map(str, argv))
        commands.append(argv)
        with (out / log).open('w') as stream:
            sp.run(argv, env=env, check=True, stdout=stream, stderr=sp.STDOUT)

    overlay = out / 'overlay'
    bins = overlay / 'bin'
    bins.mkdir(parents=True, exist_ok=True)
    run(['make', '-C', HERE, 'OUT=' + str(out / 'tools'), 'android'], 'tools-build.log')
    for name in ('a9-audio-ctl', 'a9-audio-pcm'):
        shutil.copyfile(out / 'tools' / (name + '-android'), bins / name)
    shutil.copyfile(HERE / 'a9-audio-poc.sh', bins / 'a9-audio-poc.sh')
    for f in bins.iterdir():
        f.chmod(0o755)
    # The overlay can be compiled/checked now even when the kernel build is
    # held for disk. Never package it with the old R4 kernel's missing LPI.
    run(['gcc', '-E', '-nostdinc', '-undef', '-D__DTS__', '-x', 'assembler-with-cpp',
         '-I' + str(BASE / 'kernel/include'), HERE / 'poc.dtso', '-o', out / 'poc.pp.dtso'], 'dt-cpp.log')
    run(['dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', out / 'poc.dtbo', out / 'poc.pp.dtso'], 'dt-compile.log')
    run(['fdtoverlay', '-i', R4 / 'snapshot/sm6115-hisense-hlte556n-android-gpu.dtb',
         '-o', out / 'audio.dtb', out / 'poc.dtbo'], 'dt-apply.log')
    outputs = [out / 'audio.dtb', out / 'poc.dtbo', *sorted(bins.iterdir())]
    result = {'status': 'TOOLS ONLY' if a.tools_only else 'ESS POC BUILT; hardware untested',
              'source_commit': sp.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
              'source_sha256': {f.name: sha(f) for f in (HERE / 'build-poc.py', HERE / 'poc.dtso',
                                                       HERE / 'a9-audio-pcm.c', HERE / 'a9-audio-poc.sh')},
              'speaker_mic': 'Initial ESS-only tools; full A5 build uses build-a5.py',
              'commands': commands, 'sha256': {str(f.relative_to(out)): sha(f) for f in outputs}}
    (out / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'SHA256SUMS').write_text(''.join(v + '  ' + k + '\n' for k, v in result['sha256'].items()))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
