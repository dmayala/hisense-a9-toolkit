#!/usr/bin/env python3
"""Build static user-run ARM64 tools and native host-test counterparts."""
import json,os,subprocess as sp,sys
from pathlib import Path
src=Path(__file__).resolve().parent
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True);(out/'host').mkdir(exist_ok=True)
cc=os.environ.get('HOST_CC','/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin/gcc')
cross=os.environ.get('ARM64_CC','/nix/store/4vpsm867635vxx8cxyp8kxr1jpisqqr5-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc')
results=[]
for name in ['latency-hold','phase5-watch']:
 for compiler,dest,flags,tag in [(cc,out/'host'/name,[],'host'),(cross,out/name,['-static','-s'],'arm64')]:
  cmd=[compiler,'-std=c11','-O2','-Wall','-Wextra','-Werror',*flags,str(src/(name+'.c')),'-o',str(dest)]
  p=sp.run(cmd,capture_output=True,text=True);(out/(name+'.'+tag+'.build.log')).write_text(p.stdout+p.stderr)
  results.append({'command':cmd,'rc':p.returncode});(out/'build-result.json').write_text(json.dumps(results,indent=2)+'\n')
  if p.returncode:raise SystemExit(p.returncode)
print('PASS: native and static ARM64 builds')
