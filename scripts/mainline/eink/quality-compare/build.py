#!/usr/bin/env python3
"""Offline pattern/APK build; uses the cached SDK already used by camera2-probe."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess as sp
import tempfile
import zipfile
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
DEFAULT = Path('/home/user/A9-build/mainline/eink-quality-20261023')


def patterns(out):
    out.mkdir(parents=True, exist_ok=True)
    base = Image.new('L', (824,1648), 255)
    draw = ImageDraw.Draw(base)
    # Concentric fiducials; TL has two extra nesting levels to encode orientation.
    for index, (x,y) in enumerate([(64,64),(760,64),(760,1584),(64,1584)]):
        sizes = [80,60,40,24,10] if index == 0 else [80,52,24]
        for j,size in enumerate(sizes):
            draw.rectangle((x-size//2,y-size//2,x+size//2-1,y+size//2-1),fill=255*(j%2))
    for name in ['white','black','ramp','checker']:
        im=base.copy(); d=ImageDraw.Draw(im)
        if name=='black': d.rectangle((120,160,703,1487),fill=0)
        if name=='ramp':
            for n in range(16):
                x=120+(n%4)*146; y=160+(n//4)*332
                d.rectangle((x,y,x+145,y+331),fill=n*17)
        if name=='checker':
            for row in range(16):
                for col in range(8):
                    d.rectangle((120+col*73,160+row*83,192+col*73,242+row*83),fill=255*((row+col)%2))
        im.save(out/(name+'.png'))
    # Embed host-rendered glyphs in the APK: identical pixels on both Android stacks.
    candidates = list(Path('/nix/store').glob('*dejavu-fonts*/share/fonts/truetype/DejaVuSans.ttf'))
    font_path = next(iter(candidates),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    font=ImageFont.truetype(str(font_path),24)
    long=Image.new('L',(584,1856),255); d=ImageDraw.Draw(long)
    sentences=['E Ink retains a page after power off.', 'Sixteen greys show tone separation.',
               'Black letters reveal residual history.', 'Compare the glass in fixed lighting.',
               'A checker leaves a measurable trace.', 'Scroll slowly through this test page.',
               'The final white page reveals ghosting.']
    for n in range(54):
        d.text((12,n*33+5),f'{n+1:02d}  '+sentences[n%len(sentences)],font=font,fill=0)
    long.save(out/'text-long.png')
    for name,offset in [('text',0),('scroll',264)]:
        im=base.copy(); im.paste(long.crop((0,offset,584,1328+offset)),(120,160)); im.save(out/(name+'.png'))


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--out',type=Path,default=DEFAULT); a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True); assets=a.out/'patterns'; patterns(assets)
    spec=importlib.util.spec_from_file_location('sdk',HERE.parents[1]/'cameras/camera2-probe/build.py')
    sdk=importlib.util.module_from_spec(spec); spec.loader.exec_module(sdk)
    env=dict(os.environ,JAVA_HOME=str(sdk.JAVA),PATH=str(sdk.JAVA/'bin')+':'+os.environ['PATH'])
    def run(args): sp.run(list(map(str,args)),check=True,env=env)
    key=a.out/'test-only.p12'
    if not key.exists():
        run([sdk.JAVA/'bin/keytool','-genkeypair','-keystore',key,'-storepass','android','-keypass','android',
             '-alias','test','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=A9 EInk Test'])
        key.chmod(0o600)
    apk=a.out/'eink-compare.apk'
    with tempfile.TemporaryDirectory(dir=a.out,prefix='compile-') as tmp:
        w=Path(tmp); (w/'classes').mkdir(); (w/'dex').mkdir()
        run([sdk.JAVA/'bin/javac','--release','8','-classpath',sdk.SDK,'-d',w/'classes',HERE/'PatternActivity.java'])
        run([sdk.TOOLS/'d8','--lib',sdk.SDK,'--min-api','30','--output',w/'dex',*sorted((w/'classes').rglob('*.class'))])
        run([sdk.TOOLS/'aapt2','link','-I',sdk.SDK,'--manifest',HERE/'AndroidManifest.xml','-A',assets,'-o',w/'unsigned.apk'])
        with zipfile.ZipFile(w/'unsigned.apk','a') as z: z.write(w/'dex/classes.dex','classes.dex')
        run([sdk.TOOLS/'zipalign','-f','4',w/'unsigned.apk',w/'aligned.apk'])
        run([sdk.TOOLS/'apksigner','sign','--ks',key,'--ks-pass','pass:android','--key-pass','pass:android','--out',apk,w/'aligned.apk'])
    run([sdk.TOOLS/'apksigner','verify',apk]); run([sdk.TOOLS/'zipalign','-c','4',apk])
    hashes={str(f.relative_to(a.out)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [apk,*sorted(assets.glob('*.png'))]}
    (a.out/'build.json').write_text(json.dumps(hashes,indent=2)+'\n'); print(apk)


if __name__=='__main__': main()
