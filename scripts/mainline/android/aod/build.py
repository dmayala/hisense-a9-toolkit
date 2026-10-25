#!/usr/bin/env python3
"""Build the small AOD preference editor DEX offline; no image or device access."""
import argparse
from pathlib import Path
import os
import subprocess
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
JAVA = Path('/nix/store/fnshd55b29s6z88mfl3ilmdqfb7l3dqi-openjdk-21.0.7+6')
SDK = Path('/nix/store/ynv47k6r22z1x1062cyqvxyvxwmbv42h-android-sdk-platforms-36/libexec/android-sdk/platforms/android-36/android.jar')
TOOLS = Path('/nix/store/iqyx3zkviyc1ik9snyhhw77aanc4b3zx-android-sdk-build-tools-36.0.0/libexec/android-sdk/build-tools/36.0.0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, JAVA_HOME=str(JAVA), PATH=str(JAVA / 'bin') + ':' + os.environ['PATH'])
    with tempfile.TemporaryDirectory(prefix='aod-prefs-', dir=args.out) as temporary:
        work = Path(temporary)
        classes, dex = work / 'classes', work / 'dex'
        classes.mkdir(); dex.mkdir()
        subprocess.run([str(JAVA / 'bin/javac'), '--release', '8', '-classpath', str(SDK),
                        '-d', str(classes), str(HERE / 'Preferences.java')], check=True, env=env)
        subprocess.run([str(TOOLS / 'd8'), '--lib', str(SDK), '--min-api', '30', '--output',
                        str(dex), *map(str, sorted(classes.rglob('*.class')))], check=True, env=env)
        with zipfile.ZipFile(args.out / 'prefs.jar', 'w') as jar:
            member = zipfile.ZipInfo('classes.dex', (2020, 1, 1, 0, 0, 0))
            jar.writestr(member, (dex / 'classes.dex').read_bytes())
    print(args.out / 'prefs.jar')


if __name__ == '__main__':
    main()
