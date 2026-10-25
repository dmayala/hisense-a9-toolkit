#!/usr/bin/env python3
"""Apply the camera DT cumulatively and stage the built Android vendor payload.

Inputs are host-built by build.py and android/{fetch.py,build.sh,package.py}.
This deliberately reuses the existing R4-compatible modules, no kernel rebuild.
"""
import argparse,hashlib,json,shutil,struct,subprocess,os,importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path
HERE=Path(__file__).resolve().parent
DTC=Path('/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(cmd):
 env=dict(os.environ)
 base=Path.home()/'A9-build/mainline/demo-integrated-20261019'
 if (base/'source.json').exists():
  env['PATH']=':'.join(json.loads((base/'source.json').read_text())['tools'])+':'+env['PATH']
 subprocess.run(list(map(str,cmd)),check=True,env=env)
def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--boot',required=True,type=Path)
 ap.add_argument('--out',required=True,type=Path)
 ap.add_argument('--overlay',required=True,type=Path)
 ap.add_argument('--built',type=Path,default=Path.home()/'A9-build/mainline/cameras-20261020')
 ap.add_argument('--af-torch-built', type=Path, help='build-af-torch.py output for a reused camera build')
 ap.add_argument('--android-built',type=Path,help='built Android bundle; default BUILT/android')
 graph=ap.add_mutually_exclusive_group()
 graph.add_argument('--front-probe',action='store_true',help='isolated front graph, rear is absent')
 graph.add_argument('--rear-only',action='store_true',help='isolated proven rear graph, front is absent')
 ap.add_argument('--base-vendor',type=Path,default=Path.home()/'A9-build/mainline/integrated-latency-v3-runtime-probe-r4-20261019/images/vendor.img')
 a=ap.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);o=a.overlay.resolve();o.mkdir(parents=True,exist_ok=True)
 # This is an additive overlay; retain unrelated integrated feature files.
 for source in [a.built/'overlay',(a.android_built or (a.built/'android'))/'overlay']:
  shutil.copytree(source,o,dirs_exist_ok=True)
 if a.af_torch_built:
  shutil.copytree(a.af_torch_built/'overlay',o,dirs_exist_ok=True)
 if not a.front_probe:
  for name in ('dw9714p-a9.ko','pmi632-a9-torch.ko'):
   if not (o/'lib/modules'/name).is_file():
    raise RuntimeError('rear AF/torch modules missing; build them and pass --af-torch-built')
 # Refresh the small policy/config files from this checkout, like other steps.
 shutil.copytree(HERE/'android/vendor',o,dirs_exist_ok=True)
 # The legacy camera provider needs framework HIDL services. The VNDK-less
 # R4 base declares FCM 202504, which filters out this GSI's max-level=8
 # hwservicemanager/ashmem entries and makes hwservicemanager disable itself.
 # Keep existing HAL declarations and older targets; cap only camera vendors.
 manifest=o/'etc/vintf/manifest.xml'
 if not manifest.exists():
  run(['debugfs','-R',f'dump /etc/vintf/manifest.xml {manifest}',a.base_vendor])
 tree=ET.parse(manifest);root=tree.getroot()
 if root.tag!='manifest' or root.get('type')!='device':raise RuntimeError('expected device VINTF manifest')
 if int(root.attrib['target-level'])>8:
  root.set('target-level','8');ET.indent(tree,space='    ')
  tree.write(manifest,encoding='unicode');manifest.write_text(manifest.read_text()+'\n')
 module=a.built/'front-probe/overlay/lib/modules/hi556-a9.ko'
 if module.exists():shutil.copyfile(module,o/'lib/modules/hi556-a9.ko')
 if not a.rear_only and not module.exists():raise RuntimeError('build the front module first (default graph includes both sensors)')
 shutil.copyfile(HERE/'camera-load.sh',o/'bin/a9-camera-load')
 (o/'bin/a9-camera-load').chmod(0o755)
 # Preserve all prior device permissions, then append the camera fragment once.
 uevent=o/'etc/ueventd.rc';uevent.parent.mkdir(parents=True,exist_ok=True)
 if not uevent.exists():
  run(['debugfs','-R',f'dump /etc/ueventd.rc {uevent}',a.base_vendor])
 fragment=(o/'etc/libcamera/ueventd.camera.rc').read_bytes()
 if fragment not in uevent.read_bytes():uevent.write_bytes(uevent.read_bytes()+b'\n'+fragment)
 data=a.boot.read_bytes();page=struct.unpack_from('<I',data,36)[0]
 pad=lambda n:(n+page-1)//page*page
 sizes=[struct.unpack_from('<I',data,off)[0] for off in (8,16,24,1632,1648)]
 blobs=[];pos=page
 for n in sizes:blobs.append(data[pos:pos+n]);pos+=pad(n)
 (out/'input.dtb').write_bytes(blobs[4])
 # Replacing a graph on an already camera-enabled base must remove the other
 # sensor/port; CAMSS otherwise waits for both async endpoints.
 opposite=('rear@10','port@0') if a.front_probe else ('front@20','port@2')
 removed=['/soc@0/cci@5c1b000/i2c-bus@0/'+opposite[0],
          '/soc@0/camss@5c11000/ports/'+opposite[1]] if a.front_probe or a.rear_only else []
 for path in removed:
  exists=subprocess.run([str(DTC/'fdtget'),'-l',str(out/'input.dtb'),path],capture_output=True).returncode==0
  if exists:run([DTC/'fdtput','-r',out/'input.dtb',path])
 command=['gcc','-E','-nostdinc','-undef','-D__DTS__','-x','assembler-with-cpp',
          '-I'+str((a.af_torch_built/'obj/source/include') if a.af_torch_built else (a.built/'kernel/include'))]
 if a.front_probe:command+=['-DA9_FRONT_PROBE']
 if a.rear_only:command+=['-DA9_REAR_ONLY']
 run(command+[HERE/'a9-cameras.dtso','-o',out/'camera.pre.dts'])
 run([DTC/'dtc','-@','-I','dts','-O','dtb','-o',out/'camera.dtbo',out/'camera.pre.dts'])
 run([DTC/'fdtoverlay','-i',out/'input.dtb','-o',out/'cameras.dtb',out/'camera.dtbo'])
 # Retain the coordinator's pin reservation fix; front has its own reset/LDO.
 spec=importlib.util.spec_from_file_location('camera_build',HERE/'build.py')
 builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
 builder.release_gpios(out/'cameras.dtb',{18,20,22,23,93} if a.rear_only else ({22,23,24,27,93,106} if a.front_probe else {18,20,22,23,24,27,93,106}),out/'dt-gpios.log')
 blobs[4]=(out/'cameras.dtb').read_bytes();header=bytearray(data[:page])
 struct.pack_into('<I',header,1648,len(blobs[4]));digest=hashlib.sha1()
 for b in blobs:digest.update(b);digest.update(struct.pack('<I',len(b)))
 header[576:608]=digest.digest()+b'\0'*12
 (out/'boot.img').write_bytes(header+b''.join(b.ljust(pad(len(b)),b'\0') for b in blobs))
 result={'status':'HOST STAGED; PHONE UNTESTED','graph':'front-only' if a.front_probe else ('rear-only' if a.rear_only else 'rear-and-front'),
         'input_boot_sha256':sha(a.boot),'boot_sha256':sha(out/'boot.img'),
         'camera_payload':{str(p.relative_to(o)):sha(p) for p in sorted(o.rglob('*')) if p.is_file()}}
 (out/'camera-stage.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='camera_payload'},indent=2))
if __name__=='__main__':main()
