#!/usr/bin/env python3
"""Compile one Android 16 V4L2 source group; invoked by build.py."""
import concurrent.futures,json,os,pathlib,subprocess,sys
r=pathlib.Path(sys.argv[1]).resolve();d=r/'deps';src=r/'source'
ndk=pathlib.Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
inc=[src/p/'include' for p in ('common','components','v4l2','plugin_store')]
inc += [d/'chrome',d/'chrome/base/third_party/libevent',d/'codec2/core/include',d/'codec2/vndk/include',d/'codec2/vndk/internal',d/'codec2/components/base/include',d/'codec2/sfplugin/utils/include',d/'codec2/hal/common/include',d/'av-foundation/include',d/'utils/include',d/'cutils/include',d/'system/include',d/'log/include',d/'base/include',d/'hardware-all',d/'native-opengl',d/'hidl/base/include',d/'hidl/transport/include',d/'yuv/files/include']
inc += [p for p in (d/'native-libs').glob('*/include')]
inc += [p for p in (d/'bufferpool').glob('*/include')]
inc += [p for p in (d/'codec2/hal/hidl').glob('*/utils/include')]
inc += [p for p in (d/'vndk-generated').rglob('gen') if p.is_dir() and 'bionic' not in p.parts]
inc += [p for p in (d/'vndk-generated').rglob('gen/include') if p.is_dir() and 'bionic' not in p.parts]
inc += [d/'libfmq'/p for p in ('include','base')]
inc += [d/'yuv/include',d/'gralloc-types/include',d/'hwbinder/include',d/'codec2/sfplugin/utils',d/'googletest']
inc += [d/'media-hardware',d/'openmax',d/'av-include',d/'av-minijail-header']
inc += [p.parent.parent for p in pathlib.Path('/nix/store').glob('*googletest*/include/gtest/gtest_prod.h')]
inc += [r/'generated',d/'native-libs/binder/ndk/include_ndk',d/'native-libs/binder/ndk/include_cpp']
flags=['-std=c++17','-O2','-fPIC','-D_GNU_SOURCE','-D__ANDROID_VNDK__','-D_LIBCPP_ABI_NAMESPACE=__1','-D_LIBCPP_ABI_VERSION=1','-DNO_SHM','-DNDEBUG','-Wno-unused-parameter','-Wno-deprecated-declarations','-Wno-gnu-designator','-Wno-implicit-const-int-float-conversion','-fno-exceptions','-fno-rtti','-ffile-prefix-map='+str(r)+'=/a9-video']
flags+=['-I'+str(p) for p in inc]
obj=r/'objects';obj.mkdir(exist_ok=True)
site=r/'generated/__config_site'
site.parent.mkdir(exist_ok=True)
site.write_text((ndk/'sysroot/usr/include/c++/v1/__config_site').read_text().replace('#define _LIBCPP_ABI_NAMESPACE __ndk1','#define _LIBCPP_ABI_NAMESPACE __1'))
kind=sys.argv[2]
sources=sorted((src/kind).glob('*.cpp'))
if kind=='service':sources=[src/'service/service.cpp'];flags+=['-DV4L2_CODEC2_SERVICE_V4L2_STORE']
def build(p):
 target=obj/(kind+'-'+p.stem+'.o');log=target.with_suffix('.log')
 argv=[str(ndk/'bin/aarch64-linux-android34-clang++'),*flags,'-c',str(p),'-o',str(target)]
 if target.exists() and target.stat().st_mtime>p.stat().st_mtime and '--force' not in sys.argv:return (p.name,0,'cached')
 with log.open('w') as f: ret=subprocess.run(argv,stdout=f,stderr=f).returncode
 return (p.name,ret,log.read_text()[-4500:] if ret else 'OK')
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
 results=list(ex.map(build,sources))
 for result in results: print(*result,sep='\n',flush=True)
(r/'compile-flags.json').write_text(json.dumps(flags,indent=2)+'\n')

if any(result[1] for result in results):sys.exit(1)
