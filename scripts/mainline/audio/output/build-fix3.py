#!/usr/bin/env python3
"""Host-only FIX3: negotiated tinyalsa ring and Q6ASM PREPARE cursor reset."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')
BASE = ROOT/'plain-integ-audiofix2-20261022'
OUT = ROOT/'plain-integ-audiofix3-20261022'
BASE_SHA = '5c21795696055c153f43941bea6393ce649219e167663c159e8a5e801df84c9f'
TINY_SHA = 'bc39aebfb55f510c3b34f3a223106cc5e1d85c95e1265ba29f38471fc5edce87'
OVERLAY = OUT/'vendor-overlay'


def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


def run(argv,log,env=None,cwd=None):
    with (OUT/log).open('w') as f:
        sp.run(list(map(str,argv)),stdout=f,stderr=sp.STDOUT,check=True,env=env,cwd=cwd)


def read(path):
    return sp.check_output(['debugfs','-R','cat /'+path,BASE/'vendor.img'],stderr=sp.DEVNULL)


def modules():
    base=ROOT/'demo-integrated-20261019';obj=base/'obj';src=(obj/'source').resolve()
    env=dict(os.environ,PATH=':'.join(json.loads((base/'source.json').read_text())['tools'])+':'+os.environ['PATH'])
    pins={n:sha(obj/n) for n in ['.config','Module.symvers']}
    assert pins=={'.config':'2690b86b960727b4f99242b56724a9b7f357034f2fc4f21bd84d054e61274ea3',
                  'Module.symvers':'b74085e7ec76d2285b44f2a1f3242359b54387f5fc4a5645f5e5076d0fd0a325'}
    def versions(p):
        return {n:int(c,16) for c,n in map(str.split,sp.check_output(['modprobe','--dump-modversions',p],env=env,text=True).splitlines())}
    originals={}
    for n in ['q6asm','q6asm-dai']:
        p=OUT/('original-'+n+'.ko');p.write_bytes(read('lib/modules/'+n+'.ko'));originals[n]=p
    result={'pins':pins,'source_sha256':{},'modules':{}}
    with tempfile.TemporaryDirectory(prefix='q6-build-',dir=OUT) as tmp:
        tmp=Path(tmp);prepared=tmp/'obj';ext=tmp/'external';prepared.mkdir();ext.mkdir()
        for directory in ['include','scripts','arch/arm64/include']:
            shutil.copytree(obj/directory,prepared/directory,symlinks=True)
        for n in ['.config','Makefile']:shutil.copyfile(obj/n,prepared/n)
        (prepared/'source').symlink_to(src)
        for h in (src/'sound/soc/qcom/qdsp6').glob('*.h'):shutil.copyfile(h,ext/h.name)
        for n in originals:
            p=src/('sound/soc/qcom/qdsp6/'+n+'.c');result['source_sha256'][n]=sha(p);shutil.copyfile(p,ext/p.name)
        run(['patch','--batch','--fuzz=0','-p1','-i',HERE/'q6asm-reprepare.patch'],'q6-patch.log',cwd=ext)
        for n in originals:shutil.copyfile(ext/(n+'.c'),OUT/(n+'-fixed.c'))
        (ext/'Makefile').write_text('obj-m += q6asm.o q6asm-dai.o\n')
        rows=[l for l in (obj/'Module.symvers').read_text().splitlines() if Path(l.split()[2]).name not in originals]
        known={l.split()[1]:int(l.split()[0],16) for l in (obj/'Module.symvers').read_text().splitlines()}
        for p in originals.values():
            for n,c in versions(p).items():
                if n not in known:
                    rows.append(f'0x{c:08x}\t{n}\ta9-existing-audio\tEXPORT_SYMBOL_GPL\t');known[n]=c
        (prepared/'Module.symvers').write_text('\n'.join(rows)+'\n')
        run(['make','-C',src,'O='+str(prepared),'M='+str(ext),'ARCH=arm64',
             'CROSS_COMPILE=aarch64-unknown-linux-gnu-','HOSTCC=gcc','LOCALVERSION=+','-j2','modules'],'q6-build.log',env)
        for row in (ext/'Module.symvers').read_text().splitlines():
            c,n=row.split()[:2];assert n in known and int(c,16)==known[n],n
        for n,original in originals.items():
            dest=OVERLAY/'lib/modules'/(n+'.ko');dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ext/dest.name,dest)
            sp.run(['aarch64-unknown-linux-gnu-strip','--strip-debug',dest],env=env,check=True)
            imports=versions(dest)
            assert all(known.get(n)==c for n,c in imports.items())
            get_magic=lambda p:sp.check_output(['modinfo','-F','vermagic',p],env=env,text=True).strip()
            assert get_magic(dest)==get_magic(original)
            result['modules'][n]={'sha256':sha(dest),'imports_match':len(imports),'vermagic':get_magic(dest)}
        assert all(sha(prepared/n)==c for n,c in pins.items() if n=='.config')
    assert all(sha(obj/n)==c for n,c in pins.items())
    (OUT/'q6-build.json').write_text(json.dumps(result,indent=2)+'\n')


def fix_tinyalsa(data):
    data=bytearray(data)
    # Also accept the exact previously corrected donor when refreshing FIX3.
    original=bytearray(data)
    if data[0xa698:0xa69c]==bytes.fromhex('1f2003d5'):
        original[0xa698:0xa69c]=bytes.fromhex('f40301aa')
    assert hashlib.sha256(original).hexdigest()==TINY_SHA
    # Ghidra12.1.4 pcm_set_config0x10a650, file0xa650. x20 initially points
    # to pcm->config. Caller data has already been copied at0xa690/0xa694.
    # Remove only the switch back to caller data, so negotiated fields drive
    # buffer size and SW params. No ABI, dynamic symbol or layout change.
    assert original[0xa698:0xa69c]==bytes.fromhex('f40301aa') # mov x20,x1
    data[0xa698:0xa69c]=bytes.fromhex('1f2003d5') # nop: retain internal config
    return bytes(data)


def tinyalsa():
    original=OUT/'original-libtinyalsav2.so';original.write_bytes(read('lib64/a9-audio/libtinyalsav2.so'))
    data=fix_tinyalsa(original.read_bytes())
    dest=OVERLAY/'lib64/a9-audio/libtinyalsav2.so';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
    assert len(data)==original.stat().st_size
    changed=[i for i,(a,b) in enumerate(zip(original.read_bytes(),data)) if a!=b]
    assert changed in [[],list(range(0xa698,0xa69c))]
    (OUT/'tinyalsa-patch.json').write_text(json.dumps(dict(original_sha256=TINY_SHA,sha256=sha(dest),
        file_offset='0xa698',analysis_address='0x10a698',before='mov x20,x1',after='nop',
        source_equivalent='After copying config, use &pcm->config through negotiation and SW setup.'),indent=2)+'\n')


def tests():
    build=load('fix2',HERE/'build-fix2.py');test=OUT/'tests';test.mkdir(exist_ok=True)
    shutil.copyfile(BASE/'routes.h',OUT/'routes.h')
    run([build.CC,'-Wall','-Wextra','-Werror','-fPIC','-shared',HERE/'test-tinyalsa.c',
         '-Wl,-soname,libtinyalsav2.so','-o',test/'libtinyalsav2.so'],'test-tinyalsa-build.log')
    run([build.CC,'-Wall','-Wextra','-Werror','-I'+str(OUT),HERE/'test-route.c','-L'+str(test),
         '-ltinyalsav2','-ldl','-Wl,--export-dynamic','-o',test/'test-route'],'test-route-build.log')
    q='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
    system=ROOT/'adsp/d2-v8-20261017/system-image/system';sysroot=ROOT/'audio-output-20261020/tests/sysroot'
    libs=':'.join(map(str,[test,ROOT/'android/dl/cf_arm64_x/apex/com.android.hardware.audio/root/lib64',system/'lib64/bootstrap',system/'lib64']))
    base=[q,'-L',sysroot,'-E','LD_LIBRARY_PATH='+libs]
    for flag,library,log in [('--config-before',OUT/'original-libtinyalsav2.so','config-before.log'),
                             ('--config',OVERLAY/'lib64/a9-audio/libtinyalsav2.so','config-after.log')]:
        run(base+[test/'test-route',flag,library],log)
    # Existing adapter/diag regression groups with the already built FIX2 SO.
    run(base+['-E','LD_PRELOAD='+str(BASE/'vendor-overlay/lib64/a9-audio/liba9audioroute.so'),test/'test-route'],'route-test.log')


if __name__=='__main__':
    os.umask(0o077);OUT.mkdir(exist_ok=True)
    if shutil.disk_usage(OUT).free < 2*1024**3+500*1024**2:raise RuntimeError('need existing2GiB floor plus500MiB build budget')
    assert sha(BASE/'vendor.img')==BASE_SHA
    if (OUT/'vendor.img').exists():raise RuntimeError('do not overwrite a handed-off vendor')
    modules();tinyalsa();tests()
    (OUT/'overlay-hashes.json').write_text(json.dumps({str(p.relative_to(OVERLAY)):sha(p)
        for p in sorted(OVERLAY.rglob('*')) if p.is_file()},indent=2)+'\n')
    pack=load('package',HERE/'build-fix2.py')
    pack.OUT=OUT;pack.BASE=BASE;pack.BASE_SHA=BASE_SHA;pack.OVERLAY=OVERLAY
    pack.package()
