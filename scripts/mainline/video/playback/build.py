#!/usr/bin/env python3
"""Build/sign the Codec2 Surface playback APK offline. Never installs or accesses a phone."""
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
    parser.add_argument('--out', type=Path, default=Path.home()/'A9-build/mainline/codec2-playback-20261020')
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
             '-validity', '3650', '-dname', 'CN=A9 Codec2 Test'])
        key.chmod(0o600)
    ffmpeg = Path('/nix/store/j1b3q6akcmxjamapv9qac6ibpd4g1d8b-ffmpeg-headless-7.1.1-bin/bin/ffmpeg')
    clip = out/'test.mp4'
    run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
         '-i', 'testsrc2=size=320x240:rate=10', '-frames:v', '60', '-an',
         '-c:v', 'libx264', '-threads', '1', '-profile:v', 'baseline', '-level:v', '3.0',
         '-pix_fmt', 'yuv420p', '-g', '10', '-bf', '0', '-crf', '24',
         '-fflags', '+bitexact', '-flags:v', '+bitexact', '-map_metadata', '-1',
         '-movflags', '+faststart', clip])
    probe = subprocess.check_output([str(ffmpeg.with_name('ffprobe')), '-v', 'error',
        '-count_frames', '-show_streams', '-of', 'json', str(clip)], text=True)
    stream = json.loads(probe)['streams'][0]
    assert (stream['codec_name'], stream['profile'], stream['width'], stream['height'],
            stream['nb_read_frames']) == ('h264', 'Constrained Baseline', 320, 240, '60')
    (out/'fixture.json').write_text(probe)
    # Decode the whole encoded fixture on the host as a corruption smoke check.
    run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-xerror', '-i', clip, '-f', 'null', '-'])
    apk = out/'a9-codec2-playback.apk'
    with tempfile.TemporaryDirectory(prefix='compile-', dir=out) as temporary:
        work = Path(temporary)
        classes, dex = work/'classes', work/'dex'
        classes.mkdir(); dex.mkdir()
        run([JAVA/'bin/javac', '--release', '8', '-classpath', SDK, '-d', classes,
             HERE/'PlaybackActivity.java', HERE/'YuvToRgb.java'])
        run([TOOLS/'d8', '--lib', SDK, '--min-api', '30', '--output', dex, *sorted(classes.rglob('*.class'))])
        unsigned = work/'unsigned.apk'
        run([TOOLS/'aapt2', 'link', '-I', SDK, '--manifest', HERE/'AndroidManifest.xml', '-o', unsigned])
        with zipfile.ZipFile(unsigned, 'a') as archive:
            archive.write(dex/'classes.dex', 'classes.dex')
            archive.write(clip, 'assets/test.mp4', compress_type=zipfile.ZIP_STORED)
        run([TOOLS/'zipalign', '-f', '4', unsigned, work/'aligned.apk'])
        run([TOOLS/'apksigner', 'sign', '--ks', key, '--ks-pass', 'pass:android',
             '--key-pass', 'pass:android', '--out', apk, work/'aligned.apk'])
    run([TOOLS/'apksigner', 'verify', '--verbose', apk])
    run([TOOLS/'zipalign', '-c', '4', apk])
    run([TOOLS/'aapt2', 'dump', 'badging', apk])
    result = {'status': 'HOST BUILT; PHONE PLAYBACK UNTESTED', 'apk_sha256': sha(apk), 'fixture_sha256': sha(clip),
              'source_commit': subprocess.check_output(['git', '-C', HERE, 'rev-parse', 'HEAD'], text=True).strip(),
              'sources': {name: sha(HERE/name) for name in ['AndroidManifest.xml', 'PlaybackActivity.java', 'YuvToRgb.java', 'build.py']}}
    (out/'build-result.json').write_text(json.dumps(result, indent=2)+'\n')
    (out/'SHA256SUMS').write_text(sha(apk)+'  '+apk.name+'\n'+sha(clip)+'  '+clip.name+'\n')
    print(str(apk))


if __name__ == '__main__':
    main()
