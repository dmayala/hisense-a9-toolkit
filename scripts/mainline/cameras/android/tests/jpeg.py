#!/usr/bin/env python3
"""Run four focused groups using cached production Android HAL objects under QEMU.

Needs an existing Android build.sh result, NDK and qemu-user; no phone access.
"""
import argparse, json, os, resource, shlex, subprocess, tempfile
from pathlib import Path
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build', type=Path, default=Path('/home/user/A9-build/mainline/cameras-20261020/android'))
p.add_argument('--system', type=Path, default=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system'))
p.add_argument('--qemu', default='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64')
a = p.parse_args()
b = a.build / 'build'
commands = json.loads((b / 'compile_commands.json').read_text())
c = next(c for c in commands if c['file'].endswith('/jpeg/encoder_libjpeg.cpp'))
args = shlex.split(c['command'])
flags = []
i = 1
while i < len(args):
    if args[i] in ('-o', '-MQ', '-MF'):
        i += 2
    elif args[i] in ('-c', '-MD') or args[i] == c['file']:
        i += 1
    else:
        flags.append(args[i]); i += 1
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
with tempfile.TemporaryDirectory(prefix='a9-camera-jpeg-') as work:
    executable = Path(work) / 'jpeg-test'
    objects = b / 'src/android/libcamera-hal.so.p'
    subprocess.run([args[0], *flags, '-UNDEBUG', '-I' + str(a.build / 'libcamera/src/android'),
                    str(Path(__file__).with_suffix('.cpp').resolve()),
                    str(objects / 'jpeg_encoder_libjpeg.cpp.o'),
                    str(objects / 'mm_generic_camera_buffer.cpp.o'),
                    str(b / 'src/libcamera/libcamera.so'),
                    str(b / 'src/libcamera/base/libcamera-base.so'),
                    str(a.build / 'sysroot/lib/libjpeg.a'),
                    '-static-libstdc++', '-latomic', '-llog', '-o', str(executable)],
                   cwd=b, check=True)
    libraries = ':'.join(map(str, [b / 'src/libcamera', b / 'src/libcamera/base',
                                   a.system / 'lib64/bootstrap', a.system / 'lib64']))
    subprocess.run([a.qemu, '-E', 'LD_LIBRARY_PATH=' + libraries,
                    str(a.system / 'bin/bootstrap/linker64'), str(executable)],
                   env={**os.environ, 'TMPDIR': work}, check=True)
