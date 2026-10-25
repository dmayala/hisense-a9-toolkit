#!/usr/bin/env python3
"""Five focused passive-observer groups; synthetic files only, no phone."""
import argparse
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
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
if shutil.disk_usage(out).free < 15*1024**3+100*1024**2:raise RuntimeError('15GiB floor plus100MiB test budget needed')
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
results={}

def run(cmd,**kw):
    r=sp.run(list(map(str,cmd)),capture_output=True,text=True,timeout=30,**kw)
    if r.returncode:raise RuntimeError(r.stdout+r.stderr)
    return r

for flavor,cc in [('android',b.b.CC),('native',NATIVE)]:
    dest=out/flavor;dest.mkdir(exist_ok=True)
    include=dest/'include/android';include.mkdir(parents=True,exist_ok=True)
    (include/'log.h').write_text('#define ANDROID_LOG_WARN 5\nint __android_log_print(int,const char*,const char*,...);\n')
    common=[cc,'-O2','-Wall','-Wextra','-Werror']
    if flavor=='native':common+=['-I'+str(dest/'include')]
    run(common+['-shared','-fPIC',HERE/'attest-test-log.c','-Wl,-soname,liblog.so','-o',dest/'liblog.so'])
    run(common+['-shared','-fPIC',HERE/'attest-test-backend.c','-ldl','-Wl,-soname,libattest-backend.so','-o',dest/'libattest-backend.so'])
    if flavor=='android':shim=b.build(dest/'observer')
    else:
        shim=dest/'liba9-attest-fs.so'
        run(common+['-shared','-fPIC',HERE/'attest-fs.c','-Wl,--version-script='+str(HERE/'attest-fs.map'),
                    '-L'+str(dest),'-llog','-ldl','-o',shim])
    run(common+[HERE/'attest-test.c','-L'+str(dest),'-lattest-backend','-llog','-ldl',
                '-Wl,-rpath,'+str(dest),'-o',dest/'harness'])
    preload=str(shim)+':'+str(dest/'libattest-backend.so')
    if flavor=='android':
        root=dest/'sysroot';(root/'system/bin').mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/'android/dl/cf_arm64_x/tf/VENDOR_BOOT/RAMDISK/system/bin/linker64',root/'system/bin/linker64')
        (root/'system/bin/linker64').chmod(0o755)
        libs=':'.join(map(str,[dest,SYSTEM/'lib64/bootstrap',SYSTEM/'lib64']))
        command=[QEMU,'-L',root,'-E','LD_LIBRARY_PATH='+libs,'-E','LD_PRELOAD='+preload,dest/'harness']
        env=None
    else:
        command=[dest/'harness'];env=dict(os.environ,LD_LIBRARY_PATH=str(dest),LD_PRELOAD=preload)
    logs=[]
    for op in ('1','2','3','roots'):
        logs.append(run(command+[op],env=env).stdout)
    fixture=dest/'synthetic-empty-file'
    fixture.unlink(missing_ok=True)
    logs.append(run(command+['files',fixture],env=env).stdout)
    (dest/'tests.log').write_text(''.join(logs))
    results[flavor+'_api_modes_errno_exactly_once']='PASS'
    results[flavor+'_persist_tzstorage_fpdata_EROFS_no_cap_redaction']='PASS'

# Fifth group: actual production packaging function with immutable service RC,
# small synthetic ext4 readback, and existing production CIL compile. No full
# vendor image copy or real credential fixture is necessary.
spec=importlib.util.spec_from_file_location('pack',HERE.parents[1]/'credentials/prepare-vendor.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
source=ROOT/'integ-20261024-18/vendor.img';package=out/'package';package.mkdir(exist_ok=True)
payload=m.attest_observer_payload(source,package)
rc=payload[m.KEYMASTER_RC];original=m.read(source,m.KEYMASTER_RC)
assert m.attest_observer_init(rc)==rc
assert rc.replace(b'    setenv LD_PRELOAD /vendor/'+m.ATTEST_LIBRARY.encode()+b'\n',b'')==original
assert rc.count(b'    setenv LD_PRELOAD /vendor/'+m.ATTEST_LIBRARY.encode()+b'\n')==1
assert b'    setenv LD_PRELOAD /vendor/lib64/a9-wrapped-stock/liba9-fbe-trace.so\n' in rc
try:m.attest_observer_init(original.replace(b'    class core\n',b'    setenv LD_PRELOAD /vendor/unknown.so\n    class core\n',1))
except ValueError:pass
else:raise AssertionError('unexpected preload accepted')
(stage:=package/'stage').mkdir(exist_ok=True)
for path,data in payload.items():
    target=stage/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
run([m.TOOLS/'host_init_verifier',stage/m.KEYMASTER_RC])
image=package/'synthetic-vendor.img'
run(['truncate','-s','16M',image]);run(['mke2fs','-q','-F','-t','ext4','-d',stage,image])
for path,data in payload.items():assert m.read(image,path)==data
run(['e2fsck','-fn',image])
policy=package/'policy';policy.mkdir(exist_ok=True)
m.compile_policy(source,policy,(m.HERE/'gatekeeper.cil').read_bytes()+b'\n'+(HERE/'policy.cil').read_bytes(),m.TOOLS/'secilc')
assert (policy/'compile.log').stat().st_size==0
results['packaging_preload_readback_existing_policy_compile']='PASS'
results['source_storage_changed']=False
results['device_tested']=False
(out/'tests.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
