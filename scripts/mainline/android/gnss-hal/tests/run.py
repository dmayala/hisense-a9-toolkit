#!/usr/bin/env python3
"""Offline local fixtures only. Captures subprocess output; emits summaries only."""
import argparse
import hashlib
import json
import resource
import subprocess
from pathlib import Path

S=Path(__file__).resolve().parents[1]
B=Path('/home/user/A9-build/mainline/gnss-integrated-20261020')
NDK=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin')
SYS=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system')
CACHE=Path('/home/user/A9-build/mainline/sensors')
GNSS=Path('/home/user/A9-build/mainline/android/dl/cf_arm64_x/apex/com.android.hardware.gnss/root/lib64')
QEMU='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
GCC=Path('/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin')

def run(name,cmd,timeout=120):
    r=subprocess.run(list(map(str,cmd)),capture_output=True,text=True,timeout=timeout)
    (B/'evidence'/f'{name}.log').write_text(r.stdout+r.stderr)
    if r.returncode:
        raise SystemExit(f'{name} FAILED rc={r.returncode}; private log retained (no payload echo)')
    return r.stdout.strip()

def main():
    global B
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,default=B)
    B=ap.parse_args().out.resolve()
    (B/'out').mkdir(parents=True,exist_ok=True)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    (B/"evidence").mkdir(exist_ok=True)
    results={}
    base=[NDK/'aarch64-linux-android34-clang++','-std=c++17','-static-libstdc++','-Wall','-Wextra','-Werror']
    inc=['-I'+str(CACHE/'aosp/binder-ndk'/x) for x in ['include_cpp','include_ndk','include_platform']]
    libs=['-L'+str(CACHE/'gsi-libs'),'-L'+str(GNSS),'-Wl,--allow-shlib-undefined','-lbinder_ndk']
    libpath=':'.join(map(str,[SYS/'lib64/bootstrap',CACHE/'gsi-libs',GNSS,SYS/'lib64']))
    q=[QEMU,'-E','LD_LIBRARY_PATH='+libpath,SYS/'bin/bootstrap/linker64']
    for test in ['parcel','dispatch']:
        cmd=base+inc+[S/'tests'/f'{test}.cpp']
        if test=='dispatch': cmd += [S/'runtime.cpp']
        cmd+=libs
        if test=='parcel': cmd+=['-l:android.hardware.gnss-V7-ndk.so']
        binary=B/'out'/f'{test}-test'
        run(f'build-{test}',cmd+['-o',binary])
        results[test]=run(test,q+[binary])
    binary=B/'out/runtime-test'
    run('build-runtime',[GCC/'g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=all',S/'tests/runtime.cpp',S/'runtime.cpp','-o',binary])
    results['runtime']=run('runtime',[binary])
    # Production transport (mock syscalls) and the one-shot cancellation
    # adapter. Neither main(), confine() nor a real socket is invoked.
    for arch,cc,runner in [('native',GCC/'gcc',[])]:
        binary=B/'out'/f'transport-{arch}'
        cmd=[cc,'-Wall','-Wextra','-Werror','-O2']
        if arch=='arm64': cmd+=['-static']
        cmd += [S/'tests/transport.c',S/'upstream/g1-loc.c','-lm','-o',binary]
        run('build-transport-'+arch,cmd)
        results['transport-'+arch]=run('transport-'+arch,runner+[binary])
    # Exhaustive request gate, linked to exact staged C source, no sockets.
    gate=B/'out/gate.c'
    gate.write_text('#include "'+str(S/'upstream/g1-loc.h')+'"\nint main(void){for(unsigned i=0;i<65536;i++){int e=i==0x21||i==0x22||i==0x23||i==0x38||i==0x3b||i==0x4a||i==0xba;if(g1_request_allowed(i)!=e)return 1;}return 0;}\n')
    binary=B/'out/gate-test'
    run('build-gate',[GCC/'gcc',gate,S/'upstream/g1-loc.c','-lm','-o',binary])
    run('gate',[binary]); results['allowlist']='PASS all 65536 IDs; exactly seven admitted'
    (B/'evidence/tests.json').write_text(json.dumps(results,indent=2)+'\n')
    for key,value in results.items(): print(key,value)

if __name__=='__main__': main()
