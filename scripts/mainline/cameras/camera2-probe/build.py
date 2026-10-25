#!/usr/bin/env python3
"""Build/sign the Camera2 still APK offline. Never installs or accesses a phone."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
TOOLS = Path('/nix/store/iqyx3zkviyc1ik9snyhhw77aanc4b3zx-android-sdk-build-tools-36.0.0/libexec/android-sdk/build-tools/36.0.0')
JAVA = Path('/nix/store/fnshd55b29s6z88mfl3ilmdqfb7l3dqi-openjdk-21.0.7+6')
SDK = Path('/nix/store/ynv47k6r22z1x1062cyqvxyvxwmbv42h-android-sdk-platforms-36/libexec/android-sdk/platforms/android-36/android.jar')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path.home()/'A9-build/mainline/cameras-20261020/camera2-probe')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, JAVA_HOME=str(JAVA), PATH=str(JAVA/'bin') + ':' + os.environ['PATH'])

    def run(command):
        subprocess.run(list(map(str, command)), check=True, env=env)

    # Reuse this local test key for reinstalls; never use a platform signing key.
    key = out/'test-only.p12'
    if not key.exists():
        run([JAVA/'bin/keytool', '-genkeypair', '-keystore', key, '-storepass', 'android',
             '-keypass', 'android', '-alias', 'test', '-keyalg', 'RSA', '-keysize', '2048',
             '-validity', '3650', '-dname', 'CN=A9 Camera2 Test'])
        key.chmod(0o600)
    apk = out/'a9-camera2-probe.apk'
    with tempfile.TemporaryDirectory(prefix='compile-', dir=out) as temporary:
        work = Path(temporary)
        classes, dex = work/'classes', work/'dex'
        classes.mkdir(); dex.mkdir()
        run([JAVA/'bin/javac', '--release', '8', '-classpath', SDK, '-d', classes, HERE/'CaptureActivity.java'])
        run([TOOLS/'d8', '--lib', SDK, '--min-api', '30', '--output', dex, *sorted(classes.rglob('*.class'))])
        unsigned = work/'unsigned.apk'
        run([TOOLS/'aapt2', 'link', '-I', SDK, '--manifest', HERE/'AndroidManifest.xml', '-o', unsigned])
        with zipfile.ZipFile(unsigned, 'a') as archive:
            archive.write(dex/'classes.dex', 'classes.dex')
        run([TOOLS/'zipalign', '-f', '4', unsigned, work/'aligned.apk'])
        run([TOOLS/'apksigner', 'sign', '--ks', key, '--ks-pass', 'pass:android',
             '--key-pass', 'pass:android', '--out', apk, work/'aligned.apk'])
    run([TOOLS/'apksigner', 'verify', '--verbose', apk])
    run([TOOLS/'zipalign', '-c', '4', apk])
    run([TOOLS/'aapt2', 'dump', 'badging', apk])
    result = {'status': 'HOST BUILT; PHONE CAPTURE UNTESTED', 'apk_sha256': sha(apk),
              'source_commit': subprocess.check_output(['git', '-C', HERE, 'rev-parse', 'HEAD'], text=True).strip(),
              'sources': {name: sha(HERE/name) for name in ['AndroidManifest.xml', 'CaptureActivity.java', 'build.py']}}
    (out/'build-result.json').write_text(json.dumps(result, indent=2)+'\n')
    (out/'SHA256SUMS').write_text(sha(apk)+'  '+apk.name+'\n')
    print(str(apk))


if __name__ == '__main__':
    main()
