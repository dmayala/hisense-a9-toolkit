#!/usr/bin/env python3
"""Replay the actual production enqueue method against the vb2 DMA-BUF length contract.

Extracts the method without editing its body; real format/allocation helpers are linked
from the supplied library. Queue/frame transport is synthetic. No device operations.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True, help='fully patched V4L2Encoder.cpp')
    ap.add_argument('--codec2-out', type=Path, required=True, help='baseline flags/link inputs')
    ap.add_argument('--library-dir', type=Path, required=True, help='candidate libv4l2_codec2.so')
    ap.add_argument('--runtime-dir', type=Path, required=True, help='private dependency closure')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--system', type=Path, default=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system'))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    source = a.source.read_text()
    start = source.index('bool V4L2Encoder::enqueueInputBuffer(')
    end = source.index('\nbool V4L2Encoder::enqueueOutputBuffer(', start)
    method = source[start:end].replace('V4L2Encoder::enqueueInputBuffer(',
                                      'InputQueueReplay::enqueueInputBuffer(', 1)
    cpp = a.out/'input-queue.cpp'
    cpp.write_text((HERE/'input-queue.cpp').read_text().replace('// PRODUCTION_METHOD', method))
    flags = [f for f in json.loads((a.codec2_out/'compile-flags.json').read_text()) if f != '-DNDEBUG']
    link = json.loads((a.codec2_out/'codec-link-argv.json').read_text())
    libs = [f for f in link if f.startswith(('-l', '-L', '-Wl,')) and not f.startswith('-Wl,-soname')]
    exe = a.out/'input-queue'
    with (a.out/'build.log').open('w') as log:
        sp.run(list(map(str, [NDK/'bin/aarch64-linux-android34-clang++', *flags,
            '-nostdlib++', cpp, '-L'+str(a.library_dir), '-lv4l2_codec2', *libs, '-o', exe])),
            stdout=log, stderr=sp.STDOUT, check=True)
    paths = ':'.join(map(str, [a.library_dir, a.runtime_dir, a.system/'lib64/bootstrap']))
    result = sp.run([QEMU, a.system/'bin/bootstrap/linker64', exe],
        env=dict(os.environ, LD_LIBRARY_PATH=paths), capture_output=True, text=True, timeout=30)
    (a.out/'test.log').write_text(result.stdout+result.stderr)
    print(result.stdout, end='')
    result.check_returncode()
    assert result.stdout.count(' PASS') == 5


if __name__ == '__main__': main()
