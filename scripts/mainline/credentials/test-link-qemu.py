#!/usr/bin/env python3
"""Run the actual Android linker and a Gatekeeper dlopen probe under QEMU.
No phone, mounts, properties, Binder service or TA is emulated or qualified.
"""
import argparse,hashlib,importlib.util,json,os,shutil,subprocess as sp
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('hal_needed',HERE.parent/'android/check-hal-needed.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
QEMU=Path('/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64')
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--vendor',type=Path,required=True);p.add_argument('--system-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
root=a.out/'root';assert not root.exists();root.mkdir()
fs=m.Files(a.out);fs.mount('/vendor',m.Image(a.vendor));fs.mount('/system',m.Tree(a.system_root))
for n in ('system_ext','product'):
 if (a.system_root/n).is_dir():fs.mount('/'+n,m.Tree(a.system_root/n))
for directory in (a.system_root/'apex',a.system_root/'system_ext/apex',a.system_root/'product/apex'):
 for f in sorted(directory.glob('*.apex')):fs.apex(f.read_bytes(),str(f),True)
llndk=(a.system_root/'etc/llndk.libraries.txt').read_text().splitlines();audit=m.Audit(fs,a.out,llndk)
service='/vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti';impl='/vendor/lib64/hw/android.hardware.gatekeeper@1.0-impl-qti.so'
for target in (service,impl):audit.check(target,[])
if audit.failures:
 (a.out/'missing.json').write_text(json.dumps(audit.failures,indent=2)+'\n');raise SystemExit('QEMU preflight: missing NEEDED dependency; see missing.json')
pins={}
for target,row in audit.cache.items():
 if row:
  f=root/target.lstrip('/');f.parent.mkdir(parents=True,exist_ok=True);b=fs.read(target);f.write_bytes(b);f.chmod(0o755);pins[target]=hashlib.sha256(b).hexdigest()
linker=fs.read('/apex/com.android.runtime/bin/linker64');assert linker and linker[:4]==b'\x7fELF'
f=root/'system/bin/linker64';f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(linker);f.chmod(0o755)
# These are empty guest directories, never host device nodes.
for n in ('dev','dev/__properties__','dev/socket','proc','linkerconfig'):(root/n).mkdir(exist_ok=True,parents=True)
source=a.out/'probe.c';source.write_text('#include <dlfcn.h>\n#include <stdio.h>\nint main(void) { void *p=dlopen("'+impl+'",RTLD_NOW|RTLD_LOCAL); if(!p){fprintf(stderr,"DLOPEN_FAIL %s\\n",dlerror());return 1;} if(!dlsym(p,"HIDL_FETCH_IGatekeeper")){fprintf(stderr,"FACTORY_MISSING\\n");return 2;} puts("GATEKEEPER_DLOPEN_PASS factory=1");return 0;}\n')
cc=m.READELF.with_name('aarch64-linux-android30-clang');probe=root/'vendor/bin/gatekeeper-link-probe';sp.run([cc,'-O2',source,'-ldl','-o',probe],check=True)
# QEMU translates absolute guest paths under -L; the guest receives only normal
# vendor/system/runtime paths, never the private stock-ABI bundle.
paths='/vendor/lib64:/system/lib64:/apex/com.android.runtime/lib64/bionic'
cmd=[str(QEMU),'-L',str(root),'-E','LD_LIBRARY_PATH='+paths]
def run(argv,name,seconds):
 try:
  r=sp.run(cmd+argv,capture_output=True,timeout=seconds);rc=r.returncode;data=r.stdout+r.stderr
 except sp.TimeoutExpired as e:rc=124;data=(e.stdout or b'')+(e.stderr or b'')
 (a.out/(name+'.log')).write_bytes(data);return rc,data
rc,data=run([str(probe)],'dlopen',10)
assert rc==0 and b'GATEKEEPER_DLOPEN_PASS factory=1' in data,(rc,data.decode(errors='replace'))
# The real service can reach missing host Android facilities or wait on the
# real listener property. A bounded stop is recorded, never called boot/PIN PASS.
service_rc,service_log=run(['-strace',str(root/service.lstrip('/'))],'service',5)
link_error=any(t in service_log for t in (b'CANNOT LINK EXECUTABLE',b'cannot locate symbol',b'DLOPEN_FAIL',b'dlopen failed:'))
assert not link_error,service_log.decode(errors='replace')[-2500:]
result=dict(dlopen='PASS',hidl_factory_symbol=True,needed_closure='PASS',actual_service_executed=True,service_rc=service_rc,service_no_linker_error=True,service_plugin_open_observed=impl.encode() in service_log,service_runtime_qualified=False,ta_or_pin_tested=False,phone_access=False,library_paths=paths,sha256=pins,interpreter_sha256=hashlib.sha256(linker).hexdigest())
(a.out/'tests.json').write_text(json.dumps(result,indent=2)+'\n')
for source in fs.mounts.values():
 if isinstance(source,m.Image) and source.path.parent==a.out:source.path.unlink()
print('PASS actual Android RTLD_NOW Gatekeeper plugin/factory; real service executed, runtime qualification pending, rc='+str(service_rc))
