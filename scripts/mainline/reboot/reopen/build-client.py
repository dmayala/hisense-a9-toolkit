#!/usr/bin/env python3
"""Build-only cached headers; output client and its entire Android library closure."""
from pathlib import Path
import argparse,hashlib,json,re,shutil,subprocess as sp
HERE=Path(__file__).resolve().parent
ROOT=Path('/home/user/A9-build/mainline')
NDK=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage',type=Path,default=ROOT/'reopen-host')
    args=ap.parse_args();stage=args.stage.resolve();out=stage/'payload';out.mkdir(exist_ok=True)
    old=json.loads((ROOT/'wrapped-fbe-test-r2-20261021/tools/health-tools-build.json').read_text())['keymaster-health']['command']
    includes=[str(stage/'deps'/s[2:].removeprefix(str(ROOT)+'/')) for s in old if s.startswith('-I'+str(ROOT))]
    includes=[s for s in includes if Path(s).is_dir()]
    assert any('tools/generated' in s for s in includes)
    system=ROOT/'adsp/d2-v8-20261017/system-image/system/lib64'
    stock=ROOT/'wrapped-fbe-test-r2-20261021/stock/lib64'
    audit=ROOT/'sol-wifi/target-audit/lib64'
    libraries=[stock,system,audit,system/'bootstrap']
    cmd=[str(NDK/'aarch64-linux-android30-clang++'),'-std=c++17','-O2','-Wall','-Wextra','-Werror',
         '-Wno-unused-parameter','-Wno-deprecated-declarations','-fno-rtti','-fno-exceptions','-nostdlib++',
         '-D__ANDROID_VNDK__','-D_LIBCPP_ABI_NAMESPACE=__1','-D_LIBCPP_ABI_VERSION=1']
    cmd+=['-I'+s for s in includes]
    cmd+=[str(HERE/'client.cpp')]+['-L'+str(p) for p in libraries]
    cmd+=['-Wl,--allow-shlib-undefined','-l:android.hardware.keymaster@4.0.so','-lhidlbase','-lutils','-lc++','-lcrypto','-ldl','-llog']
    with (stage/'build.log').open('w') as log:
        sp.run(cmd+['-o',str(out/'a9-fbe-reopen')],stdout=log,stderr=sp.STDOUT,check=True)
        sp.run(cmd+['-DREOPEN_FAKE','-o',str(stage/'fake-client')],stdout=log,stderr=sp.STDOUT,check=True)
    lib=out/'lib64';lib.mkdir(exist_ok=True)
    todo=[out/'a9-fbe-reopen'];seen=set();origins={}
    while todo:
        elf=todo.pop()
        dyn=sp.check_output([NDK/'llvm-readelf','-d',elf],text=True)
        for name in re.findall(r'Shared library: \[([^]]+)\]',dyn):
            if name in seen:continue
            seen.add(name)
            source=next((p/name for p in libraries if (p/name).is_file()),None)
            assert source is not None,name
            shutil.copyfile(source,lib/name);todo.append(lib/name);origins[name]=str(source)
    # Carry a matching linker for host QEMU testing only, not execution on A.
    linker=ROOT/'sol-wifi/target-audit/linker64'
    qemu='/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'
    tested=sp.run([qemu,'-E','LD_LIBRARY_PATH='+str(lib),str(linker),str(out/'a9-fbe-reopen'),'--self-test'],text=True,capture_output=True)
    (stage/'self-test.log').write_text(tested.stdout+tested.stderr)
    assert tested.returncode==0,tested.stderr
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    result=dict(host_only=True,source_sha256=sha(HERE/'client.cpp'),command=cmd,origins=origins,
                files={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()},
                android_arm64_self_test=True,hardware_tested=False)
    (stage/'client-build.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS Android ARM64 client build and dependency closure; no TEE access')
if __name__=='__main__':main()
