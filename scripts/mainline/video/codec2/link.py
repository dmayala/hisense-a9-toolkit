#!/usr/bin/env python3
"""Link against the target GSI platform ABI; invoked by build.py."""
import json,pathlib,subprocess,sys
r=pathlib.Path(sys.argv[1]).resolve();obj=r/'objects';out=r/'out';out.mkdir(exist_ok=True)
ndk=pathlib.Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
syslibs=pathlib.Path(sys.argv[2]).resolve()/'lib64';apex=r/'gsi-libs/lib64'
flags=['-nostdlib++','-Wl,-z,defs','-Wl,-z,relro,-z,now','-Wl,--hash-style=both','-Wl,--allow-shlib-undefined','-L'+str(out),'-L'+str(apex),'-L'+str(syslibs),'-Wl,-rpath-link,'+str(apex),'-Wl,-rpath-link,'+str(syslibs)]
libs=['codec2_vndk','codec2','chrome','codec2_soft_common','cutils','log','sfplugin_ccodec_utils','stagefright_bufferqueue_helper','stagefright_foundation','ui','utils','nativewindow','hidlbase','yuv','c++','android.hardware.graphics.common@1.0','android.hardware.graphics.bufferqueue@2.0','stagefright_bufferpool@2.0.1','codec2_hidl@1.0','codec2_hidl@1.1','codec2_hidl@1.2','android.hardware.media.c2@1.0','android.hardware.media.c2@1.1','android.hardware.media.c2@1.2']
kind=sys.argv[3]
if kind=='plugin':
 target='libc2plugin_store.so';objects=sorted(obj.glob('plugin_store-*.o'));opts=['-shared','-Wl,-soname,'+target]
elif kind=='codec':
 target='libv4l2_codec2.so';objects=[p for group in ('common','components','v4l2') for p in sorted(obj.glob(group+'-*.o'))];opts=['-shared','-Wl,-soname,'+target];libs+=['c2plugin_store']
else:
 target='android.hardware.media.c2@1.2-service-v4l2-64';objects=[obj/'service-service.o'];opts=['-pie'];libs+=['v4l2_codec2','c2plugin_store','avservices_minijail']
argv=[str(ndk/'bin/aarch64-linux-android34-clang++'),*flags,*opts,*map(str,objects),*['-l:'+lib+'.so' if lib.startswith('android.') else '-l'+lib for lib in libs],'-o',str(out/target)]
(r/(kind+'-link-argv.json')).write_text(json.dumps(argv,indent=2)+'\n')
p=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
(r/(kind+'-link.log')).write_text(p.stdout)
print(p.stdout[-14000:]);sys.exit(p.returncode)
