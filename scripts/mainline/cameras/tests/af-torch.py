#!/usr/bin/env python3
"""Replay production RAW10 statistics, AF search and HAL torch helper in QEMU."""
import argparse, json, os, shlex, subprocess, tempfile
from pathlib import Path
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--build', type=Path, required=True, help='Android libcamera build directory parent')
p.add_argument('--system', type=Path, default=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system'))
p.add_argument('--qemu', default='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64')
a=p.parse_args(); build=a.build/'build'; source=a.build/'libcamera'
command=next(c for c in json.loads((build/'compile_commands.json').read_text()) if c['file'].endswith('/android/camera_device.cpp'))
args=shlex.split(command['command']); flags=[]; i=1
while i<len(args):
    if args[i] in ('-o','-MQ','-MF'): i+=2
    elif args[i] in ('-c','-MD') or args[i]==command['file']: i+=1
    else: flags.append(args[i]); i+=1
with tempfile.TemporaryDirectory(prefix='a9-af-torch-') as tmp:
    exe=Path(tmp)/'test'
    subprocess.run([args[0],*flags,'-UNDEBUG','-I'+str(source),str(Path(__file__).with_suffix('.cpp').resolve()),
        str(build/'src/libcamera/libcamera.so'),str(build/'src/libcamera/base/libcamera-base.so'),
        '-static-libstdc++','-llog','-o',str(exe)],cwd=build,check=True)
    libs=':'.join(map(str,[build/'src/libcamera',build/'src/libcamera/base',a.system/'lib64/bootstrap',a.system/'lib64']))
    subprocess.run([a.qemu,'-E','LD_LIBRARY_PATH='+libs,str(a.system/'bin/bootstrap/linker64'),str(exe)],check=True,env=os.environ)
