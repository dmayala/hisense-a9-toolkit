#!/usr/bin/env python3
"""Host-only FIX2: stock Music control/retries/gain on the audio diagnostic vendor."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')
BASE = ROOT/'plain-integ-audiodiag-20261022'
OUT = ROOT/'plain-integ-audiofix2-20261022'
BASE_SHA = 'fc81f725b865f74b09b3442aa470553668243ac0b91282e91887af03c4156506'
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
CC = NDK/'bin/aarch64-linux-android30-clang'
OVERLAY = OUT/'vendor-overlay'


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run(argv, log):
    with (OUT/log).open('w') as f:
        sp.run(list(map(str, argv)), stdout=f, stderr=sp.STDOUT, check=True)


def prepare(*, reuse_modules=False):
    OUT.mkdir(exist_ok=True)
    # Preserve the selected plain-integ floor, including this build's budget.
    if shutil.disk_usage(OUT).free < 2*1024**3 + 500*1024**2:
        raise RuntimeError('need existing 2GiB floor plus 500MiB FIX2 budget')
    assert sha(BASE/'vendor.img') == BASE_SHA
    if not reuse_modules:
        original = OUT/'original-card.ko'
        original.write_bytes(sp.check_output(['debugfs','-R',
            'cat /lib/modules/snd-soc-a9-audio.ko', BASE/'vendor.img'], stderr=sp.DEVNULL))
        module('card', HERE/'build-card.py').build(OUT, original)
    for relative, source in [('etc/mixer_paths_a9.xml',HERE/'mixer_paths_a9.xml'),
                             ('bin/a9ml-audio.sh',HERE.parent/'a9ml-audio.sh')]:
        dest = OVERLAY/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest)
    lines = ['struct route_ctl { const char *name; const char *value; };']
    for path in ET.parse(HERE/'mixer_paths_a9.xml').getroot():
        lines.append('static const struct route_ctl route_'+path.get('name').replace('-','_')+'[] = {')
        for ctl in path:
            lines.append('    {'+json.dumps(ctl.get('name'))+', '+json.dumps(ctl.get('value'))+'},')
        lines.append('};')
    (OUT/'routes.h').write_text('\n'.join(lines)+'\n')
    library=OVERLAY/'lib64/a9-audio/liba9audioroute.so'
    library.parent.mkdir(parents=True,exist_ok=True)
    run([CC,'-O2','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-fPIC','-shared',
         '-Wl,-z,defs','-Wl,-soname,liba9audioroute.so','-I'+str(OUT),
         HERE/'a9-audio-route.c',HERE/'a9-audio-memfd.c','-ldl','-llog','-o',library], 'adapter-build.log')
    run([CC,'-O2','-Wall','-Wextra','-Werror','-static',HERE.parent/'a9-audio-pcm.c',
         '-o',OVERLAY/'bin/a9-audio-pcm'], 'pcm-build.log')
    # Existing focused production-function and adapter fixtures, no phone emulator.
    if not reuse_modules:
        run(['python3',HERE/'test-speaker.py','--out',OUT/'speaker-test'], 'speaker-test.log')
    test=OUT/'tests'
    test.mkdir(exist_ok=True)
    run([CC,'-Wall','-Wextra','-Werror','-fPIC','-shared',HERE/'test-tinyalsa.c',
         '-Wl,-soname,libtinyalsav2.so','-o',test/'libtinyalsav2.so'], 'test-tinyalsa-build.log')
    run([CC,'-Wall','-Wextra','-Werror','-I'+str(OUT),HERE/'test-route.c','-L'+str(test),
         '-ltinyalsav2','-ldl','-Wl,--export-dynamic','-o',test/'test-route'], 'test-route-build.log')
    q='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
    system=ROOT/'adsp/d2-v8-20261017/system-image/system'
    sysroot=test/'sysroot'
    (sysroot/'system/bin').mkdir(parents=True,exist_ok=True)
    linker=sysroot/'system/bin/linker64'
    if not linker.exists():
        linker.symlink_to(ROOT/'android/dl/cf_arm64_x/tf/VENDOR_BOOT/RAMDISK/system/bin/linker64')
    libs=':'.join(map(str,[test,system/'lib64/bootstrap',system/'lib64']))
    run([q,'-L',sysroot,'-E','LD_LIBRARY_PATH='+libs,'-E','LD_PRELOAD='+str(library),test/'test-route'], 'route-test.log')
    run(['bash','-n',HERE/'audio-live.sh'], 'host-syntax.log')
    text=(HERE/'audio-live.sh').read_text()
    body=text.split("/system/bin/sh <<'A9_AUDIO_CAPTURE'\n",1)[1].split('\nA9_AUDIO_CAPTURE\n',1)[0]
    (OUT/'device-syntax.txt').write_text(body+'\n')
    libs=':'.join(map(str,[system/'lib64/bootstrap',system/'lib64']))
    for source,name in [(OUT/'device-syntax.txt','capture'),(HERE.parent/'a9ml-audio.sh','manual-route')]:
        run([q,'-L',sysroot,'-E','LD_LIBRARY_PATH='+libs,system/'bin/sh','-n',source], name+'-android-syntax.log')
    (OUT/'overlay-hashes.json').write_text(json.dumps({str(p.relative_to(OVERLAY)):sha(p)
        for p in sorted(OVERLAY.rglob('*')) if p.is_file()},indent=2)+'\n')


def package(*, boot=None):
    image=OUT/'vendor.img'
    if image.exists():
        raise RuntimeError('output exists; never overwrite a handed-off candidate')
    if shutil.disk_usage(OUT).free < 2*1024**3 + 450*1024**2:
        raise RuntimeError('need existing 2GiB floor plus 450MiB sparse-copy budget')
    assert sha(BASE/'vendor.img')==BASE_SHA
    hashes=json.loads((OUT/'overlay-hashes.json').read_text())
    meta=module('metadata', HERE.parents[1]/'android/refresh-integrated-vendor.py')
    commands=[]
    before={}
    for i,(path,digest) in enumerate(hashes.items()):
        source=OVERLAY/path
        assert sha(source)==digest
        info=meta.metadata(BASE/'vendor.img',path)
        before[path]=info
        label=OUT/('label-'+str(i))
        label.write_bytes(info['label'].encode()+b'\0')
        commands += [f'rm /{path}',f'write {source} /{path}',f'sif /{path} mode 010{info["mode"]}',
                     f'sif /{path} uid {info["uid"]}',f'sif /{path} gid {info["gid"]}',
                     f'ea_set -f {label} /{path} security.selinux']
        for field in ['atime','mtime','ctime','crtime']:
            commands.append(f'sif /{path} {field} @{int(info[field],16)}')
    (OUT/'install.debugfs').write_text('\n'.join(commands)+'\n')
    sp.run(['cp','--reflink=auto','--sparse=always',BASE/'vendor.img',image],check=True)
    run(['debugfs','-w','-f',OUT/'install.debugfs',image],'install.log')
    for path,digest in hashes.items():
        data=sp.check_output(['debugfs','-R','cat /'+path,image],stderr=sp.DEVNULL)
        assert hashlib.sha256(data).hexdigest()==digest,path
        assert meta.metadata(image,path)==before[path],path
    run(['e2fsck','-fn',image],'fsck.log')
    assert sha(BASE/'vendor.img')==BASE_SHA
    (OUT/'boot.img').symlink_to(boot or ROOT/'plain-integ-20261022/boot.img')
    shutil.copyfile(HERE/'audio-live.sh',OUT/'audio-live.sh')
    (OUT/'audio-live.sh').chmod(0o755)
    final={n:sha(OUT/n) for n in ['vendor.img','boot.img','audio-live.sh']}
    (OUT/'SHA256SUMS').write_text(''.join(v+'  '+n+'\n' for n,v in final.items()))
    (OUT/'vendor.img.sha256').write_text(final['vendor.img']+'  vendor.img\n')
    (OUT/'result.json').write_text(json.dumps(dict(base=str(BASE/'vendor.img'),base_sha256=BASE_SHA,
        changed=hashes,metadata_preserved=True,sha256=final,host_checks='PASS',hardware_tested=False,
        playback_held_user_afk=True,free_after=shutil.disk_usage(OUT).free),indent=2)+'\n')
    print(json.dumps(final,indent=2))


if __name__=='__main__':
    import sys
    os.umask(0o077)
    if sys.argv[1:]==['--package-only']:
        package()
    else:
        prepare()
        package()
