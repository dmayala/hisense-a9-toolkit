#!/usr/bin/env python3
"""Build all sweep assets offline; never installs, probes USB, or runs device tools."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import struct
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
MAINLINE = HERE.parent
DEFAULT = '/home/user/A9-build/mainline/acceptance-assets'
DISPLAY_FILES = ('a9-waveform.bin', 'a9-ctrl-template.bin')


def stage_display_firmware(out, source=Path('/home/user/A9-build/mainline/firmware/hisense')):
    """Include the display inputs when replacing an inherited asset bundle."""
    target = out/'firmware'
    target.mkdir(exist_ok=True)
    for name in DISPLAY_FILES:
        shutil.copyfile(source/name, target/name)
        (target/name).chmod(0o600)
    return ['firmware/'+name for name in DISPLAY_FILES]


def build_mtp(out):
    """Compile only; never invoke the resulting USB client on this host."""
    include = Path('/nix/store/kicl0w76cdfrg322blnm4w9acz5lrm1p-libmtp-1.1.22-dev/include')
    lib = Path('/nix/store/6dvn3nvhgwsc053lggb2glr7kfinh242-libmtp-1.1.22/lib')
    sp.run(['/nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin/cc',
            '-Wall', '-Wextra', '-Werror', '-I'+str(include), HERE/'mtp-session.c',
            '-L'+str(lib), '-Wl,-rpath,'+str(lib), '-Wl,--export-dynamic-symbol=libusb_reset_device',
            '-lmtp', '-o', out/'mtp-session'], check=True)


def build_drain_clock(out):
    ndk=Path(os.environ.get('ANDROID_NDK_HOME', '/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973'))
    cc=ndk/'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang'
    sp.run(list(map(str,[cc,'-O2','-Wall','-Wextra','-Werror','-static',HERE/'drain-clock.c','-o',out/'drain-clock'])),check=True)


def build_vulkan(out):
    tools=Path('/nix/store/r34y043lh1vy0yj3gw6370rqdc09jakj-glslang-15.4.0-bin/bin')
    spv=out/'vulkan.spv'
    sp.run([tools/'glslangValidator','-V','--target-env','vulkan1.0',HERE/'vulkan.comp','-o',spv],check=True)
    sp.run([tools/'spirv-val','--target-env','vulkan1.0',spv],check=True)
    data=spv.read_bytes()
    words=struct.unpack('<'+'I'*(len(data)//4),data)
    (out/'vulkan-shader.h').write_text('static const uint32_t a9_vulkan_shader[] = {\n'+
        ','.join(hex(word) for word in words)+'\n};\n')
    ndk=Path(os.environ.get('ANDROID_NDK_HOME', '/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973'))
    cc=ndk/'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang'
    library=out/'liba9vulkan.so'
    sp.run(list(map(str,[cc,'-shared','-fPIC','-O2','-Wall','-Wextra','-Werror','-I',out,
                        HERE/'vulkan-probe.c','-lvulkan','-o',library])),check=True)
    return library


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=Path(DEFAULT))
    args = ap.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('camera_build', MAINLINE/'cameras/camera2-probe/build.py')
    sdk = importlib.util.module_from_spec(spec); spec.loader.exec_module(sdk)
    env = dict(os.environ, JAVA_HOME=str(sdk.JAVA), PATH=str(sdk.JAVA/'bin') + ':' + os.environ['PATH'])
    def run(argv):
        sp.run(list(map(str, argv)), env=env, check=True)
    for source, dest in [('cameras/camera2-probe', 'photo'), ('video/playback', 'decoder')]:
        run(['python3', '-B', MAINLINE/source/'build.py', '--out', out/dest])
    key = out/'test-only.p12'
    if not key.exists():
        run([sdk.JAVA/'bin/keytool', '-genkeypair', '-keystore', key, '-storepass', 'android', '-keypass', 'android',
             '-alias', 'test', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '3650', '-dname', 'CN=A9 Acceptance Test'])
        key.chmod(0o600)
    apk = out/'acceptance.apk'
    vulkan = build_vulkan(out)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        w = Path(tmp); (w/'classes').mkdir(); (w/'dex').mkdir()
        run([sdk.JAVA/'bin/javac', '--release', '8', '-classpath', sdk.SDK, '-d', w/'classes', HERE/'Probe.java'])
        run([sdk.TOOLS/'d8', '--lib', sdk.SDK, '--min-api', '30', '--output', w/'dex', *sorted((w/'classes').rglob('*.class'))])
        run([sdk.TOOLS/'aapt2', 'link', '-I', sdk.SDK, '--manifest', HERE/'AndroidManifest.xml', '-o', w/'unsigned.apk'])
        with zipfile.ZipFile(w/'unsigned.apk', 'a') as z:
            z.write(w/'dex/classes.dex', 'classes.dex')
            z.write(vulkan, 'lib/arm64-v8a/liba9vulkan.so')
        run([sdk.TOOLS/'zipalign', '-f', '4', w/'unsigned.apk', w/'aligned.apk'])
        run([sdk.TOOLS/'apksigner', 'sign', '--ks', key, '--ks-pass', 'pass:android', '--key-pass', 'pass:android', '--out', apk, w/'aligned.apk'])
    run([sdk.TOOLS/'apksigner', 'verify', apk])
    # Pinned, already present host library. No package installation/network.
    dev = Path('/nix/store/blq676h0hx4dq5hlrn6gbxa7zygkvqvz-libsepol-3.8.1-dev')
    lib = Path('/nix/store/q97pqkiy6bc27zh80167c8w478wbvg42-libsepol-3.8.1/lib')
    run(['/nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin/cc', '-Wall', '-Wextra', '-Werror', '-I'+str(dev/'include'), HERE/'permissive.c', lib/'libsepol.a', '-o', out/'permissive'])
    build_mtp(out)
    build_drain_clock(out)
    files = ['acceptance.apk', 'photo/a9-camera2-probe.apk', 'decoder/a9-codec2-playback.apk', 'permissive', 'mtp-session', 'drain-clock']
    files += stage_display_firmware(out)
    # Runtime verifies these pins and source identity; rebuild after changing sources.
    manifest = {'assets': {p: hashlib.sha256((out/p).read_bytes()).hexdigest() for p in files},
                'sources': {str(p.relative_to(MAINLINE)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in [HERE/'Probe.java', HERE/'AndroidManifest.xml', HERE/'permissive.c', HERE/'mtp-session.c', HERE/'drain-clock.c',
                                      HERE/'vulkan-probe.c', HERE/'vulkan.comp', HERE/'build.py',
                                      MAINLINE/'cameras/camera2-probe/CaptureActivity.java',
                                      MAINLINE/'video/playback/PlaybackActivity.java', MAINLINE/'video/playback/YuvToRgb.java']}}
    (out/'assets.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(out/'assets.json')

if __name__ == '__main__': main()
