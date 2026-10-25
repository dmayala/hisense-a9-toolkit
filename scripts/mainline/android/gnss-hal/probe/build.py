#!/usr/bin/env python3
"""Offline APK build for the LocationManager diagnostic observer; no install."""
import argparse
import os
import subprocess
import zipfile
from pathlib import Path
S=Path(__file__).resolve().parent
B=Path('/home/user/A9-build/mainline/gnss-integrated-20261020')
TOOLS=Path('/nix/store/iqyx3zkviyc1ik9snyhhw77aanc4b3zx-android-sdk-build-tools-36.0.0/libexec/android-sdk/build-tools/36.0.0')
JAVA=Path('/nix/store/fnshd55b29s6z88mfl3ilmdqfb7l3dqi-openjdk-21.0.7+6')
SDK=Path('/nix/store/ynv47k6r22z1x1062cyqvxyvxwmbv42h-android-sdk-platforms-36/libexec/android-sdk/platforms/android-36/android.jar')
def main():
    global B
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,default=B)
    B=ap.parse_args().out.resolve()
    (B/'out').mkdir(parents=True,exist_ok=True)
    w=B/'probe-work'; w.mkdir(exist_ok=True); (w/'classes').mkdir(exist_ok=True); (w/'dex').mkdir(exist_ok=True)
    env=dict(os.environ,JAVA_HOME=str(JAVA),PATH=str(JAVA/'bin')+':'+os.environ['PATH'])
    def run(args): subprocess.run(list(map(str,args)),check=True,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    run([JAVA/'bin/javac','-source','8','-target','8','-Xlint:-options','-classpath',SDK,'-d',w/'classes',S/'Start.java',S/'Probe.java'])
    run([TOOLS/'d8','--lib',SDK,'--min-api','34','--output',w/'dex',*sorted((w/'classes').rglob('*.class'))])
    run([TOOLS/'aapt2','link','-I',SDK,'--manifest',S/'AndroidManifest.xml','-o',w/'unsigned.apk'])
    with zipfile.ZipFile(w/'unsigned.apk','a') as z: z.write(w/'dex/classes.dex','classes.dex')
    key=w/'test-only.p12'
    if not key.exists():
        run([JAVA/'bin/keytool','-genkeypair','-keystore',key,'-storepass','android','-keypass','android','-alias','test','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=A9 Offline Test'])
        key.chmod(0o600)
    run([TOOLS/'zipalign','-f','4',w/'unsigned.apk',w/'aligned.apk'])
    target=B/'out/a9-gnss-count-probe.apk'
    run([TOOLS/'apksigner','sign','--ks',key,'--ks-pass','pass:android','--key-pass','pass:android','--out',target,w/'aligned.apk'])
    run([TOOLS/'apksigner','verify',target])
    print('GNSS diagnostic probe APK built and signature verified; not installed')
if __name__=='__main__': main()
