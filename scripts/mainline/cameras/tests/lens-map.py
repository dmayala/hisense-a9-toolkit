#!/usr/bin/env python3
"""Compile the production IPA and replay reset/configure/control IPC under QEMU."""
import argparse
import json
import os
from pathlib import Path
import resource
import shlex
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build', type=Path, required=True)
p.add_argument('--system', type=Path, default=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system'))
a = p.parse_args(); build = a.build/'build'; source = a.build/'libcamera'
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
c = next(c for c in json.loads((build/'compile_commands.json').read_text()) if c['file'].endswith('/simple/soft_simple.cpp'))
args = shlex.split(c['command']); flags = []; i = 1
while i < len(args):
    if args[i] in ('-o', '-MQ', '-MF'): i += 2
    elif args[i] in ('-c', '-MD') or args[i] == c['file']: i += 1
    else: flags.append(args[i]); i += 1
production = source/'src/ipa/simple/soft_simple.cpp'
with tempfile.TemporaryDirectory(prefix='lens-map-') as tmp:
    root = Path(tmp); cpp = root/'test.cpp'; exe = root/'test'
    # Include its dependencies before redefining private, so only the IPA
    # class's test access changes. The implementation itself is compiled whole.
    includes = '\n'.join(line for line in production.read_text().splitlines() if line.startswith('#include'))
    production_text = production.read_text()
    test = Path(__file__).with_suffix('.cpp').read_text()
    objects = build/'src/ipa/simple/ipa_soft_simple.so.p'
    command = [args[0], *flags, '-UNDEBUG', '-I'+str(source/'src/ipa/simple'), str(cpp),
                    *map(str, sorted(o for o in objects.glob('*.o') if o.name != 'soft_simple.cpp.o')),
                    str(build/'src/ipa/libipa/libipa.a'), str(build/'src/libcamera/libcamera.so'),
                    str(build/'src/libcamera/base/libcamera-base.so'), '-static-libstdc++', '-latomic', '-llog',
                    '-o', str(exe)]
    libraries = ':'.join(map(str, [build/'src/libcamera', build/'src/libcamera/base',
                                   a.system/'lib64/bootstrap', a.system/'lib64']))
    run = ['/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64',
           '-E', 'LD_LIBRARY_PATH='+libraries, str(a.system/'bin/bootstrap/linker64'), str(exe)]
    # First omit the configure renewal: the init map demonstrably survives
    # a front reconfigure, which should have cleared it. Do not read freed IDs.
    needle = 'lensInfoMap_ = configInfo.lensControls;'
    assert production_text.count(needle) == 1
    for legacy in (True, False):
        implementation = root/'soft.cpp'
        implementation.write_text(production_text.replace(needle, '' if legacy else needle))
        cpp.write_text(includes+'\n#define private public\n#include "'+str(implementation)+'"\n#undef private\n'+test)
        subprocess.run(command, cwd=build, check=True)
        result = subprocess.run(run, env=os.environ, capture_output=True, text=True)
        if legacy:
            assert result.returncode != 0 and 'ipa.lensInfoMap_.empty()' in result.stderr, result.stderr
            print('PASS old init-only map fails the configure-generation regression')
        else:
            assert result.returncode == 0, result.stdout+result.stderr
            print(result.stdout.strip())
