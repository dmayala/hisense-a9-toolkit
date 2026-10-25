#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run test-stage.py with extracted Android sh AND Toybox through qemu user mode.

No device or image writes. --android-root holds system/bin/{sh,toybox}, their
runtime linker and libraries (e.g. the v7 extraction used by the BT rehearsal).
Every external utility used by the phone script is either a fake kernel/tool
in test-stage.py or this Android Toybox. Host wrappers and the fake kernel
remain native. Each phone shell is its own session leader to exercise Android
mksh's TERM re-raise behaviour when finish kills its sleeping watchdog.
"""
import argparse, hashlib, os, pathlib, shlex, shutil, subprocess, tempfile
here = pathlib.Path(__file__).resolve().parent
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--android-root', type=pathlib.Path, required=True)
p.add_argument('--qemu', type=pathlib.Path, required=True)
p.add_argument('--dtb', type=pathlib.Path)
a = p.parse_args(); root = a.android_root.resolve(); qemu = a.qemu.resolve()
for f in ('system/bin/sh', 'system/bin/toybox'):
    path = root/f
    if not path.is_file(): p.error(f'missing {path}')
    print(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}', flush=True)
base = [str(qemu), '-L', str(root), '-E', 'LD_LIBRARY_PATH=' + str(root/'system/lib64')]
subprocess.run(base + [str(root/'system/bin/sh'), '-c', 'echo "$KSH_VERSION"'], check=True)
with tempfile.TemporaryDirectory(prefix='audio-android-sh-') as td:
    td = pathlib.Path(td); bins = td/'toybox-bin'; bins.mkdir()
    # Kernel-dependent commands are intercepted first by the fixture's bin.
    for name in ('cat', 'cut', 'find', 'grep', 'head', 'mkdir', 'rm', 'sed', 'sha256sum', 'sleep', 'tail', 'tr'):
        f = bins/name
        f.write_text('#!/bin/sh\nexec ' + shlex.join(base + [str(root/'system/bin/toybox'), name]) + ' "$@"\n')
        f.chmod(0o755)
    wrapper = td/'android-sh'
    wrapper.write_text('#!/bin/sh\n' +
        'PATH="$A9_AUDIO_ROOT/bin":' + shlex.quote(str(bins)) + ':"$PATH"\nexport PATH\nexec ' +
        shlex.join(base + [str(root/'system/bin/sh')]) + ' "$@"\n')
    wrapper.chmod(0o755)
    args = [shutil.which('python3'), '-I', str(here/'test-stage.py'), '--shell', str(wrapper)]
    if a.dtb: args += ['--dtb', str(a.dtb)]
    subprocess.run(args, check=True)
print('PASS Android sh + Toybox qemu rehearsal (session leader, watchdog kill finish)', flush=True)
