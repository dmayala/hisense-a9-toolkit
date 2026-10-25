#!/usr/bin/env python3
"""Five focused adapter groups with real Android linker/libc under QEMU; no device."""
import argparse
from pathlib import Path
import subprocess as sp
import os
import shutil
import json
HERE=Path(__file__).resolve().parent
ROOT=Path.home()/'A9-build/mainline'
NDK=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
SYSTEM=ROOT/'adsp/d2-v8-20261017/system-image/system'
LINKER=ROOT/'android/dl/cf_arm64_x/tf/VENDOR_BOOT/RAMDISK/system/bin/linker64'
QEMU='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,default=ROOT/'audio-output-20261020');a=ap.parse_args();out=a.out.resolve();test=out/'tests';test.mkdir(exist_ok=True)
cc=NDK/'bin/aarch64-linux-android30-clang'
cmds=[
 [cc,'-Wall','-Wextra','-Werror','-fPIC','-shared',HERE/'test-tinyalsa.c','-Wl,-soname,libtinyalsav2.so','-o',test/'libtinyalsav2.so'],
 [cc,'-Wall','-Wextra','-Werror','-I'+str(out),HERE/'test-route.c','-L'+str(test),'-ltinyalsav2','-ldl','-Wl,--export-dynamic','-o',test/'test-route'],
 [cc,'-Wall','-Wextra','-Werror',HERE/'test-memfd.c','-L'+str(out/'vendor-overlay/lib64/a9-audio'),'-la9audioroute','-o',test/'test-memfd']]
for cmd in cmds:sp.run(list(map(str,cmd)),check=True)
sysroot=test/'sysroot';(sysroot/'system/bin').mkdir(parents=True,exist_ok=True)
link=sysroot/'system/bin/linker64'
if link.is_symlink(): link.unlink()
shutil.copyfile(LINKER,link);link.chmod(0o755)
envpath=':'.join(map(str,[test,SYSTEM/'lib64/bootstrap',SYSTEM/'lib64']))
cmd=[QEMU,'-L',str(sysroot),'-E','LD_LIBRARY_PATH='+envpath,'-E','LD_PRELOAD='+str(out/'vendor-overlay/lib64/a9-audio/liba9audioroute.so'),str(test/'test-route')]
r=sp.run(cmd,capture_output=True,text=True,timeout=30);(out/'route-test.log').write_text(r.stdout+r.stderr)
(out/'route-test-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
print(r.stdout+r.stderr);assert r.returncode==0
# Exercise the production shared-memory implementation with real kernel memfds.
cmd=[QEMU,'-L',str(sysroot),'-E','LD_LIBRARY_PATH='+str(out/'vendor-overlay/lib64/a9-audio')+':'+envpath,str(test/'test-memfd')]
r=sp.run(cmd,capture_output=True,text=True,timeout=15);(out/'memfd-test.log').write_text(r.stdout+r.stderr)
print(r.stdout+r.stderr);assert r.returncode==0
# Check the original primary HAL's entire dependency closure using Android's
# linker. No service is started; --list never enters the HAL main function.
cmd=[QEMU,'-L',str(sysroot),'-E','LD_LIBRARY_PATH='+str(out/'vendor-overlay/lib64/a9-audio')+':'+envpath,'-E','LD_PRELOAD='+str(out/'vendor-overlay/lib64/a9-audio/liba9audioroute.so'),str(link),'--list',str(out/'vendor-overlay/bin/hw/android.hardware.audio.service-aidl.a9')]
r=sp.run(cmd,capture_output=True,text=True,timeout=30);(out/'linker-list.log').write_text(r.stdout+r.stderr)
print('dependency linker --list rc',r.returncode);assert r.returncode==0
