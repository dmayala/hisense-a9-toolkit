#!/usr/bin/env python3
"""Offline build only. Local API-36 SDK + JDK, no gradle/network/device/image changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

HERE=Path(__file__).resolve().parent
FILES=['s2-target.sh','reg.awk','channel.awk','power.awk','s2-encode.py']
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk',type=Path,required=True)
    p.add_argument('--jdk',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    out=a.out.absolute()
    if out.exists() or any(x.is_symlink() for x in (out,*out.parents)) or not (
        str(out).startswith('/tmp/') or str(out).startswith('/home/user/A9-build/')):
        p.error('new build directory under /tmp or A9-build required')
    if shutil.disk_usage(out.parent).free<15*1024**3:p.error('15 GiB floor')
    sdk=a.sdk.resolve();jdk=a.jdk.resolve();api=sdk/'platforms/android-36/android.jar'
    d8=sdk/'build-tools/36.0.0/lib/d8.jar'
    for f in (api,d8,jdk/'bin/javac',jdk/'bin/java'):
        if not f.is_file():p.error('local build tool missing')
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='s2-build-',dir=out.parent) as t:
        work=Path(t);classes=work/'classes';classes.mkdir();dex=work/'dex';dex.mkdir()
        subprocess.run([str(jdk/'bin/javac'),'--release','17','-cp',str(api),'-d',str(classes),
                        str(HERE/'Protocol.java'),str(HERE/'PublicScan.java'),str(HERE/'Bridge.java')],check=True)
        subprocess.run([str(jdk/'bin/java'),'-cp',str(d8),'com.android.tools.r8.D8','--min-api','36',
                        '--lib',str(api),'--output',str(dex),*map(str,sorted(classes.rglob('*.class')))],check=True)
        bundle=work/'bundle';bundle.mkdir(mode=0o700)
        with zipfile.ZipFile(bundle/'bridge.jar','w',compression=zipfile.ZIP_DEFLATED) as z:
            for f in sorted(dex.glob('*.dex')):
                info=zipfile.ZipInfo(f.name,(2026,10,18,0,0,0));info.external_attr=0o400<<16
                z.writestr(info,f.read_bytes())
        for f in FILES:shutil.copyfile(HERE/f,bundle/f)
        provenance={'schema':1,'kind':'offline-review-candidate','api':36,
                    'sources':{f:sha(HERE/f) for f in ['Protocol.java','PublicScan.java','Bridge.java','build.py',*FILES]},
                    'tools':{'android.jar':sha(api),'d8.jar':sha(d8),'javac':sha(jdk/'bin/javac'),'java':sha(jdk/'bin/java')}}
        (bundle/'provenance.json').write_text(json.dumps(provenance,indent=2,sort_keys=True)+'\n')
        hashes={f.name:sha(f) for f in bundle.iterdir()}
        (bundle/'SHA256SUMS').write_text(''.join(f'{hashes[f]}  {f}\n' for f in ['bridge.jar',*FILES,'provenance.json']))
        for f in bundle.iterdir():f.chmod(0o400)
        bundle.rename(out)
    print('S2_BUILD candidate=ready files=8 GO=absent')

if __name__=='__main__':main()
