#!/usr/bin/env python3
"""Build and query the actual encoder interface; property reads are inert stubs."""
import argparse,json,subprocess as sp
from pathlib import Path
HERE=Path(__file__).resolve().parent
NDK=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--codec2-out',type=Path,required=True);ap.add_argument('--library-dir',type=Path,required=True);ap.add_argument('--runtime-dir',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
r=a.codec2_out;flags=[f for f in json.loads((r/'compile-flags.json').read_text()) if f!='-DNDEBUG']
libs=[x for x in json.loads((r/'codec-link-argv.json').read_text()) if x.startswith(('-l','-L','-Wl,')) and not x.startswith('-Wl,-soname')]
exe=a.out/'test';command=[NDK/'bin/aarch64-linux-android34-clang++',*flags,'-UNDEBUG','-nostdlib++','-Wl,--export-dynamic',HERE/'input-usage.cpp','-L'+str(a.library_dir),'-lv4l2_codec2',*libs,'-o',exe]
with (a.out/'compile.log').open('w') as f:sp.run(list(map(str,command)),stdout=f,stderr=sp.STDOUT,check=True)
system=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system')
paths=':'.join(map(str,[a.library_dir,a.runtime_dir,system/'lib64/bootstrap']))
cmd=['/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64','-E','LD_LIBRARY_PATH='+paths,system/'bin/bootstrap/linker64',exe]
p=sp.run(list(map(str,cmd)),capture_output=True,text=True,timeout=30);(a.out/'test.log').write_text(p.stdout+p.stderr);print(p.stdout+p.stderr);p.check_returncode();assert ' PASS' in p.stdout
