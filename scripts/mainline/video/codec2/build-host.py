#!/usr/bin/env python3
"""Build unmodified Android16 hidl-gen with the cached host tools."""
from pathlib import Path
import subprocess,os,json,concurrent.futures,sys,shutil
root=Path(sys.argv[1]).resolve(); d=root/'deps'; src=d/'hidl-tools';o=root/'host-hidl';o.mkdir(exist_ok=True)
env=dict(os.environ)
# Source/header builds must not depend on a deleted historical image directory.
tools=[]
for name,package in [('g++','gcc-wrapper'),('bison','bison'),('flex','flex')]:
 candidate=os.environ.get('A9_HOST_CXX') if name=='g++' else shutil.which(name)
 if not candidate:
  matches=([Path('/nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin/g++')] if name=='g++' else sorted(Path('/nix/store').glob('*'+package+'*/bin/'+name)))
  if not matches: raise RuntimeError('missing host tool: '+name)
  candidate=str(matches[0])
 tools.append(str(Path(candidate).parent))
env['PATH']=':'.join(tools)+':'+env['PATH']
def run(a):
 r=subprocess.run(a,cwd=o,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 if r.returncode:print('FAIL',a, r.stdout,flush=True)
 return r
r=run(['bison','--defines=hidl-gen_y.h','--output=hidl-gen_y.cpp',str(src/'hidl-gen_y.yy')]);assert r.returncode==0
r=run(['flex','-o','hidl-gen_l.cpp',str(src/'hidl-gen_l.ll')]);assert r.returncode==0
# Host liblog only exports C APIs to our GCC-built code. Its Android
# otatools debug sections use zstd, which cached GNU ld cannot read.
import shutil
libroot=Path('/home/user/A9-build/mainline/android/dl/cf_arm64_x/otatools/lib64')
r=run(['/nix/store/nasb2hacyvikadjhr9qip2r8b72ir819-llvm-19.1.7/bin/llvm-objcopy','--strip-debug',str(libroot/'liblog.so'),str(o/'liblog.so')]);assert r.returncode==0
if not (o/'libc++.so').exists(): (o/'libc++.so').symlink_to(libroot/'libc++.so')
includes=[src,src/'utils/include',src/'utils/include/hidl-util',src/'host_utils/include',src/'host_utils/include/hidl-util',src/'hashing/include',d/'jsoncpp/include',d/'base/include',d/'log/include',d/'utils/binder/include',d/'libhwbinder/include',d/'hwbinder/include',o,Path('/nix/store/qqfiw89h8j4pilcyqapldzq5vf0vqgxy-openssl-3.5.1-dev/include')]
files=sorted(src.glob('*.cpp'));files=[p for p in files];files+=sorted((src/'utils').glob('*.cpp'))+sorted((src/'host_utils').glob('*.cpp'))+[src/'hashing/Hash.cpp',o/'hidl-gen_y.cpp',o/'hidl-gen_l.cpp']
files += sorted((d/'jsoncpp/src/lib_json').glob('*.cpp'))
files += [d/'base'/x for x in ['logging.cpp','strings.cpp','stringprintf.cpp','file.cpp','posix_strerror_r.cpp','threads.cpp','hex.cpp']]
args=['g++','-std=c++20','-O0','-g0','-pthread','-D_GNU_SOURCE','-D__builtin_available(...)=1','-include','cstdint','-include','cstring','-include','algorithm']+['-I'+str(p) for p in includes]
(o/'compile-argv.json').write_text(json.dumps({'compiler_prefix':args,'sources':list(map(str,files))},indent=2))
def compile(p):
 obj=o/(str(p.relative_to(d)).replace('/','_')+'.o' if p.is_relative_to(d) else p.name+'.o')
 if obj.exists() and obj.stat().st_mtime > p.stat().st_mtime:return (0,obj)
 prefix=args
 if p.name == 'posix_strerror_r.cpp':
  prefix=[x for x in args if x not in ['-include','cstdint','cstring','algorithm']]+['-D_POSIX_C_SOURCE=200809L']
 r=run(prefix+['-c',str(p),'-o',str(obj)]);return r.returncode,obj
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(compile,files))
if any(rc for rc,p in results):sys.exit(1)
link=args+['-Wl,--disable-new-dtags']+list(str(p) for rc,p in results)+['-L/nix/store/j09xkbypljdhxwspfiqd8sf64zgxhp52-openssl-3.5.1/lib','-Wl,-rpath,/nix/store/j09xkbypljdhxwspfiqd8sf64zgxhp52-openssl-3.5.1/lib','-lcrypto','-L'+str(o),'-Wl,-rpath,'+str(o),'-Wl,-rpath,/home/user/A9-build/mainline/android/dl/cf_arm64_x/otatools/lib64','-llog','-o',str(o/'hidl-gen')]
(o/'link-argv.json').write_text(json.dumps(link,indent=2));r=run(link);print(r.stdout);sys.exit(r.returncode)
