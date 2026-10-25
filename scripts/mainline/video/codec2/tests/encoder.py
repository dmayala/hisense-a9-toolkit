#!/usr/bin/env python3
"""Build/run production encoder helper tests on Android ARM64 under QEMU."""
import argparse,json,os
from pathlib import Path
import subprocess as sp

HERE=Path(__file__).resolve().parent
NDK=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
QEMU='/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--codec2-out',type=Path,required=True)
    ap.add_argument('--system',type=Path,default=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system'))
    args=ap.parse_args();r=args.codec2_out.resolve();system=args.system.resolve()
    flags=[f for f in json.loads((r/'compile-flags.json').read_text()) if f!='-DNDEBUG']
    link=json.loads((r/'codec-link-argv.json').read_text())
    libs=[f for f in link if f.startswith(('-l','-L','-Wl,')) and not f.startswith('-Wl,-soname')]
    exe=r/'out/encoder-test'
    cmd=[NDK/'bin/aarch64-linux-android34-clang++',*flags,'-nostdlib++',HERE/'encoder.cpp',
         '-lv4l2_codec2',*libs,'-o',exe]
    with (r/'encoder-test-build.log').open('w') as log:sp.run(list(map(str,cmd)),stdout=log,stderr=sp.STDOUT,check=True)
    paths=':'.join(map(str,[r.parent/'vendor-overlay/lib64/a9-video',system/'lib64/bootstrap']))
    result=sp.run([QEMU,system/'bin/bootstrap/linker64',exe],env=dict(os.environ,LD_LIBRARY_PATH=paths),capture_output=True,text=True,timeout=30)
    (r/'encoder-test.log').write_text(result.stdout+result.stderr)
    print(result.stdout,end='');result.check_returncode()
    assert result.stdout.count(' PASS')==3

if __name__=='__main__':main()
