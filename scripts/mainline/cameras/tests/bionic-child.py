#!/usr/bin/env python3
"""Check the packaged core and replay its production pre-exec helper; no camera/phone."""
import argparse
import importlib.util
from pathlib import Path
import subprocess as sp
import tempfile
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--source',type=Path,required=True,help='isolated patched libcamera source')
a=p.parse_args()
spec=importlib.util.spec_from_file_location('payload',Path(__file__).resolve().parents[1]/'android/native-payload.py')
payload=importlib.util.module_from_spec(spec);spec.loader.exec_module(payload)
files=payload.verify(a.source.parent)
assert files['lib64/a9-camera/libcamera.so'].is_file()
ndk=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin')
system=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system')
qemu='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
with tempfile.TemporaryDirectory(prefix='a9-bionic-child-') as tmp:
 exe=Path(tmp)/'test'
 sp.run([ndk/'aarch64-linux-android30-clang++','-O2','-Wall','-Wextra','-Werror',
         '-I'+str(a.source.resolve()),'-static-libstdc++',Path(__file__).with_suffix('.cpp'),'-o',exe],check=True)
 command=[qemu,'-E','LD_LIBRARY_PATH='+str(system/'lib64/bootstrap'),system/'bin/bootstrap/linker64',exe]
 for args in [[],['worker']]:
  r=sp.run(command+args,capture_output=True,text=True,timeout=15)
  assert r.returncode==0,r.stdout+r.stderr
  assert 'fdsan' not in r.stderr.lower(),r.stderr
  print(r.stdout.strip())
