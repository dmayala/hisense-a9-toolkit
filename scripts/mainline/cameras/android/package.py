#!/usr/bin/env python3
"""Stage the libcamera HAL and stock HIDL transport into a vendor overlay.

Transport libraries are private to this service. No CamX camera HAL is
included. No phone access, mounting, or Android source build is performed.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,re,shutil,subprocess,zipfile
HERE=Path(__file__).resolve().parent
NDK=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin')

def run(cmd):return subprocess.check_output(list(map(str,cmd)),stderr=subprocess.STDOUT,text=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--out',type=Path,default=Path.home()/'A9-build/mainline/cameras-20261020/android')
 ap.add_argument('--stock-vendor',type=Path,default=Path.home()/'A9-build/mainline/android/ref/vendor_a.img')
 ap.add_argument('--system-libs',type=Path,default=Path.home()/'A9-build/mainline/adsp/d2-v8-20261017/system-image/system/lib64')
 ap.add_argument('--vndk-apex',type=Path,default=Path.home()/'A9-build/mainline/adsp/d2-v8-20261017/system-image/system/system_ext/apex/com.android.vndk.v30.apex')
 ap.add_argument('--mapper-library',type=Path,default=Path.home()/'A9-build/mainline/android/work/vendor-stage/lib64/hw/mapper.minigbm.so',help='existing integrated minigbm library with HIDL v4 factory')
 args=ap.parse_args();w=args.out.resolve();o=w/'overlay';o.mkdir(exist_ok=True)
 # Match the stock transport's Android 11 VNDK ABI; do not substitute the
 # integrated vendor's Android 16 C++ runtime or overwrite its global libraries.
 vndk=w/'vndk30.img'
 if not vndk.exists():
  with zipfile.ZipFile(args.vndk_apex) as z, vndk.open('wb') as f:
   with z.open('apex_payload.img') as stream:shutil.copyfileobj(stream,f)
 origins={}

 def copy(src,rel,mode=0o644):
  p=o/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,p);p.chmod(mode);return p
 def extract(src,rel,mode=0o644):
  p=o/rel;p.parent.mkdir(parents=True,exist_ok=True);p.unlink(missing_ok=True)
  # These three platform libraries must follow libbinder_ndk/LLNDK on
  # Android 16; all stock camera C++ imports were checked against them.
  platform={'libbinder.so','libutils.so','libc++.so'}
  if Path(src).name in platform:
   origin=args.system_libs/Path(src).name
   shutil.copyfile(origin,p)
   origins[rel]={'path':str(origin),'sha256':sha(p)}
  for image in ([] if p.exists() else [args.stock_vendor,vndk]):
   run(['debugfs','-R',f'dump {src} {p}',image])
   if p.exists():
    origins[rel]={'image':str(image),'path':src,'sha256':sha(p)}
    break
  if not p.exists() and (args.system_libs/Path(src).name).is_file():
   origin=args.system_libs/Path(src).name
   shutil.copyfile(origin,p)
   origins[rel]={'path':str(origin),'sha256':sha(p)}
  if not p.exists():raise RuntimeError('missing transport dependency '+src)
  p.chmod(mode);return p
 s=w/'stage/vendor';private='lib64/a9-camera/'
 for name in ['libcamera.so','libcamera-base.so']:
  copy(s/'lib64'/name,private+name)
 copy(s/'lib64/libcamera-hal.so','lib64/hw/camera.default.so')
 for p in (s/'lib64/libcamera/ipa').glob('*.so'):copy(p,'lib64/libcamera/ipa/'+p.name)
 copy(s/'libexec/libcamera/soft_ipa_proxy','libexec/libcamera/soft_ipa_proxy',0o755)
 for p in (s/'share/libcamera/ipa/simple').glob('*.yaml'):copy(p,'share/libcamera/ipa/simple/'+p.name)
 # Transfer the exact stock rear non-flash CCM table; keep binary and generated
 # tuning outside git. extract-colour.py checks its digest and record structure.
 colour=w/'colour';colour.mkdir(exist_ok=True)
 tuned=colour/'tuned.bin'
 run(['debugfs','-R',f'dump /lib64/camera/com.qti.tuned.s5k3l6_bnd32b_shine.bin {tuned}',args.stock_vendor])
 spec=importlib.util.spec_from_file_location('a9_colour',HERE.parent/'extract-colour.py')
 colour_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(colour_module)
 profile=colour_module.extract(tuned,colour)
 copy(profile,'share/libcamera/ipa/simple/s5k3l6.yaml')
 origins['share/libcamera/ipa/simple/s5k3l6.yaml']={'source_blob_sha256':sha(tuned),'generated_profile_sha256':sha(profile),'evidence':str(colour/'stock-colour.json')}
 # Interpose only the two stock HandleImporter methods in this provider.
 guard=o/private/'liba9-camera-import.so';guard.parent.mkdir(parents=True,exist_ok=True)
 run([NDK/'aarch64-linux-android30-clang++','-std=c++17','-O2','-fPIC','-shared',
      '-fvisibility=hidden','-static-libstdc++','-Wl,--exclude-libs,ALL',
      '-Wl,--no-undefined',HERE/'import-guard.cpp','-ldl','-llog','-o',guard])
 origins[str(guard.relative_to(o))]={'source':str(HERE/'import-guard.cpp'),'source_sha256':sha(HERE/'import-guard.cpp'),'sha256':sha(guard)}
 # HIDL scans *-impl*.so, whereas stable-C loads mapper.minigbm.so. Both
 # factories exist in this exact installed library; publish its HIDL alias.
 mapper='lib64/hw/android.hardware.graphics.mapper@4.0-impl.minigbm.so'
 copy(args.mapper_library,mapper)
 origins[mapper]={'path':str(args.mapper_library),'sha256':sha(o/mapper)}
 provider=extract('/bin/hw/android.hardware.camera.provider@2.4-service_64','bin/hw/android.hardware.camera.provider@2.4-service.a9',0o755)
 adapter=extract('/lib64/hw/android.hardware.camera.provider@2.4-impl.so','lib64/hw/android.hardware.camera.provider@2.4-impl.so')
 # The passthrough HIDL loader opens these names dynamically, beyond DT_NEEDED.
 seeds=['android.hardware.camera.provider@2.4-legacy.so']+[f'camera.device@{v}-impl.so' for v in ['1.0','3.2','3.3','3.4','3.5']]
 files=[provider,adapter]+[extract('/lib64/'+n,private+n) for n in seeds]
 dependencies={};bionic={'libc.so','libm.so','libdl.so','libdl_android.so','liblog.so','libvndksupport.so','libsync.so','libnativewindow.so'}
 queued={p.name for p in files}
 for p in files:
  needed=re.findall(r'\(NEEDED\).*\[(.*?)\]',run([NDK/'llvm-readelf','-d',p]))
  dependencies[str(p.relative_to(o))]=needed
  for n in needed:
   if n in bionic or n in queued:continue
   queued.add(n)
   if 'camx' in n.lower() or n.startswith('camera.qcom'):raise RuntimeError('unexpected stock camera engine '+n)
   files.append(extract('/lib64/'+n,private+n))
 for p in o.rglob('*'):
  if p.is_file() and p.read_bytes()[:4]==b'\x7fELF':
   run([NDK/'llvm-strip','--strip-unneeded',p])
 for p in (HERE/'vendor').rglob('*'):
  if p.is_file():copy(p,str(p.relative_to(HERE/'vendor')),0o755 if '/bin/' in str(p) else 0o644)
 # Merge the exact integrated ueventd rules later in stage.py; this overlay only
 # carries a fragment, so another subsystem's permissions cannot be overwritten.
 (w/'transport-sources.json').write_text(json.dumps({'vndk_apex_sha256':sha(args.vndk_apex),'files':origins},indent=2)+'\n')
 (w/'provider-dependencies.json').write_text(json.dumps(dependencies,indent=2)+'\n')
 (w/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(o))+'\n' for p in sorted(o.rglob('*')) if p.is_file()))
 print(f'Camera Android overlay: {o} ({sum(p.stat().st_size for p in o.rglob("*") if p.is_file())} bytes)')
if __name__=='__main__':main()
