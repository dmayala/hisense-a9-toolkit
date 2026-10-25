#!/usr/bin/env python3
"""Four focused production IPA/ownership replays; cached NDK/QEMU, no device."""
import argparse, json, os, resource, shlex, subprocess, tempfile
from pathlib import Path
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build', type=Path, default=Path('/home/user/A9-build/mainline/cameras-20261020/android'))
p.add_argument('--system', type=Path, default=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system'))
p.add_argument('--qemu', default='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64')
a = p.parse_args(); b = a.build / 'build'
c = next(c for c in json.loads((b / 'compile_commands.json').read_text()) if c['file'].endswith('/simple/algorithms/agc.cpp'))
args = shlex.split(c['command']); flags = []; i = 1
while i < len(args):
    if args[i] in ('-o', '-MQ', '-MF'): i += 2
    elif args[i] in ('-c', '-MD') or args[i] == c['file']: i += 1
    else: flags.append(args[i]); i += 1
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
with tempfile.TemporaryDirectory(prefix='a9-quality-test-') as work:
    executable = Path(work) / 'test'
    objects = b / 'src/ipa/simple/ipa_soft_simple.so.p'
    subprocess.run([args[0], *flags, '-UNDEBUG', str(Path(__file__).with_suffix('.cpp').resolve()),
                    *[str(objects / ('algorithms_' + n + '.cpp.o')) for n in ['agc', 'awb', 'blc', 'ccm']],
                    str(b / 'src/ipa/libipa/libipa.a'), str(b / 'src/libcamera/libcamera.so'),
                    str(b / 'src/libcamera/base/libcamera-base.so'), '-static-libstdc++', '-latomic', '-llog',
                    '-o', str(executable)], cwd=b, check=True)
    libraries = ':'.join(map(str, [b / 'src/libcamera', b / 'src/libcamera/base', a.system / 'lib64/bootstrap', a.system / 'lib64']))
    subprocess.run([a.qemu, '-E', 'LD_LIBRARY_PATH=' + libraries, str(a.system / 'bin/bootstrap/linker64'),
                    str(executable), str(a.build / 'colour/s5k3l6.yaml')], check=True, env=os.environ)
