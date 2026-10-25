#!/usr/bin/env python3
"""Bounded passive stat/readlink observer checks; synthetic files only."""
import argparse
import hashlib
import importlib.util
import json
import os
import resource
from pathlib import Path
import shutil
import subprocess as sp
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('attest_build',HERE/'attest-build.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
ROOT=Path('/home/user/A9-build/mainline')
SYSTEM=ROOT/'adsp/d2-v8-20261017/system-image/system'
QEMU='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
NATIVE='/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin/gcc'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path)
a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
assert shutil.disk_usage(out).free>=15*1024**3+5*1024**2
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
results={}

def run(cmd,**kw):
    r=sp.run(list(map(str,cmd)),capture_output=True,text=True,timeout=20,**kw)
    if r.returncode:raise RuntimeError(r.stdout+r.stderr)
    return r

for flavor,cc in [('android',b.b.CC),('native',NATIVE)]:
    dest=out/flavor;dest.mkdir(exist_ok=True)
    include=dest/'include/android';include.mkdir(parents=True,exist_ok=True)
    (include/'log.h').write_text('#define ANDROID_LOG_WARN 5\nint __android_log_print(int,const char*,const char*,...);\n')
    common=[cc,'-O2','-fno-builtin','-Wall','-Wextra','-Werror']
    if flavor=='native':common+=['-I'+str(dest/'include')]
    run(common+['-shared','-fPIC',HERE/'backend-test-log.c','-Wl,-soname,liblog.so','-o',dest/'liblog.so'])
    run(common+['-shared','-fPIC',HERE/'backend-test-next.c','-ldl','-Wl,-soname,libbackend-next.so','-o',dest/'libbackend-next.so'])
    if flavor=='android':shim=b.build(dest/'observer')
    else:
        shim=dest/'liba9-attest-fs.so'
        run(common+['-shared','-fPIC',HERE/'attest-fs.c',HERE/'backend-fs.c','-Wl,--version-script='+str(HERE/'attest-fs.map'),
                    '-L'+str(dest),'-llog','-ldl','-o',shim])
    run(common+[HERE/'backend-test.c','-L'+str(dest),'-lbackend-next','-llog','-ldl','-Wl,-rpath,'+str(dest),'-o',dest/'harness'])
    preload=str(shim)+':'+str(dest/'libbackend-next.so')
    if flavor=='android':
        # Reuse the prior worker-owned Android linker fixture read-only; do not
        # copy a full sysroot or mutate earlier evidence for this small task.
        root=ROOT/'fbe-keystore-store-host/tests/android/sysroot'
        assert (root/'system/bin/linker64').is_file()
        libs=':'.join(map(str,[dest,SYSTEM/'lib64/bootstrap',SYSTEM/'lib64']))
        command=[QEMU,'-L',root,'-E','LD_LIBRARY_PATH='+libs,'-E','LD_PRELOAD='+preload,dest/'harness'];env=None
    else:command=[dest/'harness'];env=dict(os.environ,LD_LIBRARY_PATH=str(dest),LD_PRELOAD=preload)
    logs=[]
    for mode in ('stat','link','filter'):logs.append(run(command+[mode],env=env).stdout)
    fixture=dest/'synthetic-file';assert not fixture.exists()
    logs.append(run(command+['files',fixture],env=env).stdout)
    (dest/'tests.log').write_text(''.join(logs))
    results[flavor+'_stat_readlink_exact_call_result_errno_buffer']='PASS'
    results[flavor+'_bounded_returned_bytes_path_redaction_caps']='PASS'
# The production build manifest must include the new source and export list.
manifest=json.loads((out/'android/observer/build.json').read_text())
for name in ('attest-fs.c','backend-fs.c','attest-fs.map','attest-build.py'):
    assert manifest['sources'][name]==hashlib.sha256((HERE/name).read_bytes()).hexdigest()
results['production_build_source_pins']='PASS'
results['library_sha256']=manifest['sha256'];results['device_tested']=False
(out/'tests.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
