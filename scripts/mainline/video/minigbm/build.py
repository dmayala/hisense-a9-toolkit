#!/usr/bin/env python3
"""Build A9's vkms minigbm Surface fix and a standalone allocator overlay, offline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import tarfile
import urllib.request

HERE=Path(__file__).resolve().parent
DEFAULT=Path.home()/'A9-build/mainline/codec2-surface-20261020'
CACHE=Path.home()/'A9-build/mainline/video-20261020/codec2/deps'
NDK=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
VENDOR=Path.home()/'A9-build/mainline/android/work/vendor-stage/lib64'
APEX=Path.home()/'A9-build/mainline/android/dl/cf_arm64_x/apex/com.google.cf.gralloc/root'
SYSTEM=Path.home()/'A9-build/mainline/adsp/d2-v8-20261017/system-image/system'
QEMU='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
SERVICE='android.hardware.graphics.allocator-service.minigbm'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree_sha(p):
    values=[]
    with tarfile.open(p) as tar:
        for m in tar:
            if m.isfile(): values.append((m.name,hashlib.sha256(tar.extractfile(m).read()).hexdigest()))
            elif m.issym() or m.islnk(): values.append((m.name,m.linkname))
    return hashlib.sha256(json.dumps(sorted(values),separators=(',',':')).encode()).hexdigest()


def prepare(root,fetch):
    lock=json.loads((HERE/'sources.lock.json').read_text())
    sources={}
    for name,item in lock.items():
        archive=root/item['archive']
        cached=DEFAULT/item['archive']
        if not archive.exists() and cached.exists(): shutil.copyfile(cached,archive)
        if not archive.exists():
            if not fetch: raise RuntimeError(f'missing {archive}; use --fetch for pinned public sources')
            with urllib.request.urlopen(item['url'],timeout=45) as response:
                archive.write_bytes(response.read())
        if tree_sha(archive)!=item['tree_sha256']: raise RuntimeError('source content mismatch: '+str(archive))
        dest=root/('src-'+name)
        receipt=item['tree_sha256']
        if name=='minigbm': receipt+=' '+ ' '.join(sha(p) for p in sorted((HERE/'patches').glob('*.patch')))
        marker=dest/'.a9-source'
        if not marker.exists() or marker.read_text()!=receipt:
            if dest.exists(): shutil.rmtree(dest) # generated, task-owned source only
            dest.mkdir()
            def safe_member(member,destination):
                try: return tarfile.data_filter(member,destination)
                except tarfile.FilterError: return None # out-of-subtree build aliases
            with tarfile.open(archive) as tar: tar.extractall(dest,filter=safe_member)
            if name=='minigbm':
                for patch in sorted((HERE/'patches').glob('*.patch')):
                    run(['patch','--batch','--fuzz=0','-p1','-i',patch],root/'evidence'/(patch.stem+'.log'),cwd=dest)
            marker.write_text(receipt)
        sources[name]=dest
    return sources


def run(argv,log,**kwargs):
    result=sp.run(list(map(str,argv)),capture_output=True,text=True,**kwargs)
    log.write_text(result.stdout+result.stderr)
    if result.returncode: raise RuntimeError(f'command failed ({result.returncode}); see {log}')
    return result.stdout


def symbols(p,defined=True):
    flag='--defined-only' if defined else '--undefined-only'
    return {row.split()[-1].split('@')[0] for row in sp.check_output([NDK/'bin/llvm-nm','-D',flag,p],text=True).splitlines() if row.split()}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,default=DEFAULT)
    ap.add_argument('--overlay',type=Path,help='merge allocator/core/config into an existing vendor overlay')
    ap.add_argument('--fetch',action='store_true')
    args=ap.parse_args(); root=args.out.resolve()
    os.umask(0o077)
    root.mkdir(parents=True,exist_ok=True)
    for name in ['evidence','objects','out','generated']: (root/name).mkdir(exist_ok=True)
    d=prepare(root,args.fetch); source=d['minigbm']; objects=root/'objects'
    site=root/'generated/__config_site'
    site.write_text((NDK/'sysroot/usr/include/c++/v1/__config_site').read_text().replace('#define _LIBCPP_ABI_NAMESPACE __ndk1','#define _LIBCPP_ABI_NAMESPACE __1'))
    incs=[root/'generated',source,d['libdrm'],d['libdrm']/'include/drm',d['dmabufheap']/'include',d['libion']/'kernel-headers',d['libsync']/'include']
    incs += [CACHE/p for p in ['cutils/include','log/include','base/include','system/include','hardware-all','utils/include','utils/binder/include','utils/threads/include','native-libs/binder/ndk/include_ndk','native-libs/binder/ndk/include_cpp']]
    incs += list((CACHE/'native-libs').glob('*/include'))
    incs += [p for p in (CACHE/'vndk-generated').rglob('gen/include') if 'bionic' not in p.parts]
    flags=['-O2','-fPIC','-D_GNU_SOURCE=1','-D_FILE_OFFSET_BITS=64','-D__ANDROID_VENDOR__','-D__ANDROID_VNDK__','-DANDROID_API_LEVEL=36','-DHAS_DMABUF_SYSTEM_HEAP','-Wall','-Wextra','-Wno-unused-parameter','-Wno-missing-field-initializers','-Wno-deprecated-declarations','-Wno-invalid-offsetof','-ffile-prefix-map='+str(root)+'=/a9-minigbm']+['-I'+str(p) for p in incs]
    cpp=['-std=c++17','-D_LIBCPP_ABI_NAMESPACE=__1','-D_LIBCPP_ABI_VERSION=1','-fno-exceptions']
    files=[source/f for f in ['amdgpu.c','backend_mock.c','drv.c','drv_array_helpers.c','drv_helpers.c','dumb_driver.c','i915.c','mediatek.c','msm.c','rockchip.c','vc4.c','virtgpu.c','virtgpu_cross_domain.c','virtgpu_virgl.c','xe.c']]
    files += [source/'cros_gralloc'/f for f in ['cros_gralloc_buffer.cc','cros_gralloc_helpers.cc','cros_gralloc_driver.cc']]
    commands=[]
    for p in files:
        compiler=NDK/'bin'/('aarch64-linux-android34-clang++' if p.suffix=='.cc' else 'aarch64-linux-android34-clang')
        cmd=[compiler,*flags,*(cpp if p.suffix=='.cc' else ['-std=gnu11']),'-c',p,'-o',objects/(p.stem+'.o')]
        run(cmd,root/'evidence'/(p.stem+'.log'));commands.append(list(map(str,cmd)))
    library=root/'out/libminigbm_gralloc.so'
    cmd=[NDK/'bin/aarch64-linux-android34-clang++','-shared','-nostdlib++','-Wl,-z,defs','-Wl,-z,relro,-z,now','-Wl,--allow-shlib-undefined','-Wl,-soname,libminigbm_gralloc.so',*sorted(objects.glob('*.o')),'-L'+str(VENDOR),'-L'+str(SYSTEM/'lib64'),*['-l'+x for x in ['cutils','dmabufheap','drm','gralloctypes','log','nativewindow','sync','c++']],'-o',library]
    run(cmd,root/'evidence/link.log');commands.append(list(map(str,cmd)))
    old=symbols(VENDOR/'libminigbm_gralloc.so');new=symbols(library)
    if old-new: raise RuntimeError('missing old minigbm exports: '+str(sorted(old-new)))
    overlay=(args.overlay or root/'vendor-overlay').resolve()
    # Remove only byte-identical files from this stager's superseded initial
    # bundle. A preexisting integration overlay belonging to other features is
    # untouched unless it carries our old uniquely named init file.
    old_init=overlay/'etc/init/init.a9ml-minigbm.rc'
    if old_init.exists() and 'a9-gralloc' not in old_init.read_text():
        for name in ['libminigbm_gralloc4_utils.so','android.hardware.graphics.allocator-V2-ndk.so','libhidlbase.so']:
            previous=overlay/'lib64'/name
            if previous.exists() and sha(previous)==sha(APEX/'lib64'/name): previous.unlink()
    staged={}
    def copy(src,dest):
        target=overlay/dest;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,target);staged[dest]=sha(target)
    copy(library,'lib64/libminigbm_gralloc.so')
    copy(APEX/'bin/hw'/SERVICE,'bin/hw/'+SERVICE)
    # Reproduce the allocator's former APEX dependency set privately. In
    # particular, do not replace global libhidlbase/allocator-V2/libc++ versions
    # used by the existing mapper or other vendor HALs. Only the patched core
    # is shared from /vendor/lib64 by allocator and mapper.
    for p in (APEX/'lib64').glob('*.so'):
        if p.name!='libminigbm_gralloc.so': copy(p,'lib64/a9-gralloc/'+p.name)
    for p in (HERE/'vendor').rglob('*'):
        if p.is_file(): copy(p,str(p.relative_to(HERE/'vendor')))
    # Production library and prebuilt mapper under QEMU, with a mock DRM ioctl.
    test=root/'out/layout-test'
    cmd=[NDK/'bin/aarch64-linux-android34-clang++',*flags,*cpp,'-Werror','-nostdlib++','-Wl,--export-dynamic','-Wl,--allow-shlib-undefined',HERE/'tests/layout.cpp','-L'+str(root/'out'),'-L'+str(VENDOR),'-L'+str(SYSTEM/'lib64'),'-lminigbm_gralloc','-lc++','-ldl','-o',test]
    run(cmd,root/'evidence/test-build.log');commands.append(list(map(str,cmd)))
    paths=':'.join(map(str,[root/'out',overlay/'lib64',SYSTEM/'lib64/bootstrap',VENDOR,SYSTEM/'lib64']))
    q=[QEMU,'-E','LD_LIBRARY_PATH='+paths,SYSTEM/'bin/bootstrap/linker64']
    run(q+[test],root/'evidence/layout.log',timeout=30)
    run(q+[test,'--link',VENDOR/'hw/mapper.minigbm.so'],root/'evidence/mapper-link.log',timeout=30)
    # --list resolves dependencies and exits without executing the allocator's main.
    allocator_paths=':'.join(map(str,[root/'out',overlay/'lib64/a9-gralloc',SYSTEM/'lib64/bootstrap',VENDOR,SYSTEM/'lib64']))
    run([QEMU,'-E','LD_LIBRARY_PATH='+allocator_paths,SYSTEM/'bin/bootstrap/linker64','--list',APEX/'bin/hw'/SERVICE],root/'evidence/allocator-link.log',timeout=30)
    receipt={'status':'HOST BUILT; SURFACE PHONE TEST PENDING','core_sha256':sha(library),'retained_old_exports':len(old),'missing_old_exports':[],
             'source_commit':sp.check_output(['git','-C',HERE,'rev-parse','HEAD'],text=True).strip(),
             'inputs':{str(p.relative_to(HERE)):sha(p) for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts},
             'overlay':staged,
             'removed_vendor_file':'apex/com.google.cf.gralloc.apex','commands':commands}
    (root/'build-result.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print('minigbm core built; original exports retained; layout/mapper/allocator link checks passed; overlay staged')


if __name__=='__main__':main()
