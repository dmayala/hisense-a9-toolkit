#!/usr/bin/env python3
"""Fetch versioned build dependencies into the local B9 output, no devices."""
from pathlib import Path
import argparse,hashlib,json,subprocess,tarfile
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out',type=Path,default=Path.home()/'A9-build/mainline/cameras-20261020/android')
ROOT=ap.parse_args().out.resolve()
SOURCES={
 'yaml-0.2.5': 'https://pyyaml.org/download/libyaml/yaml-0.2.5.tar.gz',
 'libjpeg-turbo-3.0.4': 'https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.0.4/libjpeg-turbo-3.0.4.tar.gz',
 'libexif-0.6.25': 'https://github.com/libexif/libexif/releases/download/v0.6.25/libexif-0.6.25.tar.gz',
 'libyuv': 'https://chromium.googlesource.com/libyuv/libyuv/+archive/500f45652c459cfccd20f83f297eb66cb7b015cb.tar.gz',
}
ROOT.mkdir(parents=True,exist_ok=True)
pins=json.loads((Path(__file__).with_name('sources.json')).read_text())
source=ROOT/'libcamera'
if not source.exists():subprocess.run(['git','clone','--depth','1','--branch','v0.7.2','https://git.libcamera.org/libcamera/libcamera.git',str(source)],check=True)
assert subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()=='191e202178f02430b5942397c70d215cdd2056fa'
record={}
for name,url in SOURCES.items():
 archive=ROOT/(name+'.tar.gz')
 if not archive.exists():
  subprocess.run(['curl','-fL','--retry','2','-o',str(archive),url],check=True)
 digest=hashlib.sha256(archive.read_bytes()).hexdigest()
 assert digest==pins[name]['sha256'], 'source archive digest differs: '+name
 record[name]={'url':url,'sha256':digest}
 if not (ROOT/name).exists():
  if name=='libyuv':
   (ROOT/name).mkdir()
   with tarfile.open(archive) as t:t.extractall(ROOT/name,filter='data')
  else:
   with tarfile.open(archive) as t:t.extractall(ROOT,filter='data')
(ROOT/'dependency-sources.json').write_text(json.dumps(record,indent=2)+'\n')
print('Camera sources fetched')
