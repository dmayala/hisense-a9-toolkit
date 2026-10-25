#!/usr/bin/env python3
"""Build only the phone-proven native boot mapper. No formatter/writer interface."""
import argparse,hashlib,json,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent
CC='/nix/store/4vpsm867635vxx8cxyp8kxr1jpisqqr5-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc'
def build(out,cc=CC,fbe=False):
    out.mkdir(parents=True,exist_ok=True);dst=out/'a9ml-row29-map'
    cmd=[cc,'-static','-Os','-Wall','-Wextra','-Werror','-Wno-unused-function','-ffunction-sections','-fdata-sections','-Wl,--gc-sections',*(['-DROW29_FBE'] if fbe else []),'-o',str(dst),str(HERE/'map.c')]
    subprocess.run(cmd,check=True)
    (out/'build.json').write_text(json.dumps({'command':cmd,'sha256':hashlib.sha256(dst.read_bytes()).hexdigest()},indent=2)+'\n')
    return dst
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--cc',default=CC);p.add_argument('--fbe',action='store_true');a=p.parse_args();build(a.out,a.cc,a.fbe)
