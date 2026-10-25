#!/usr/bin/env python3
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

repo=Path(__file__).resolve().parents[2]
up=Path.home()/'A9-build/mainline/sol-wifi/src'
cc=os.environ.get('CC','cc')
with tempfile.TemporaryDirectory(prefix='a9-wifi-pd-') as d:
    out=Path(d);src=out/'source';shutil.copytree(up/'pd-mapper',src,ignore=shutil.ignore_patterns('.git'))
    subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(repo/'scripts/mainline/wifi/patches/pd-mapper.patch')],cwd=src,check=True,stdout=subprocess.DEVNULL)
    test=out/'probe'
    q=up/'qrtr'
    flags=['-O1','-g','-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(src),'-I'+str(q/'include'),'-I'+str(q/'lib')]
    subprocess.run([cc,*flags,str(Path(__file__).with_name('pd-mapper.c')),str(src/'servreg_loc.c'),str(q/'lib/qmi.c'),str(q/'lib/logging.c'),'-o',str(test)],check=True)
    env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
    subprocess.run([str(test)],env=env,check=True)
