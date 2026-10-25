#!/usr/bin/env python3
"""Host-only FIX4 vendor: WCD stereo route and shared PA-off-before-stop."""
import json
import os
from pathlib import Path
import shutil

from importlib.util import spec_from_file_location, module_from_spec

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')


def main():
    os.umask(0o077)
    spec = spec_from_file_location('audio_package', HERE/'build-fix2.py')
    m = module_from_spec(spec); spec.loader.exec_module(m)
    m.BASE = ROOT/'plain-integ-audio-merged-20261022'
    m.BASE_SHA = 'ef629c0875665689e9347a01ca4c1aedadd3506ccb575c7607df9b39cf2597dd'
    m.OUT = ROOT/'plain-integ-audiofix4-20261022'
    m.OVERLAY = m.OUT/'vendor-overlay'
    if (m.OUT/'vendor.img').exists():
        raise RuntimeError('never overwrite a delivered candidate')
    # No driver/ABI changes: preserve all proven modules and FIX3 tinyalsa.
    m.prepare(reuse_modules=True)
    m.run([m.CC, '-O2', '-Wall', '-Wextra', '-Werror', '-static',
           HERE.parent/'tests/pcm_test.c', '-o', m.OUT/'tests/pcm-test'], 'pcm-test-build.log')
    q = '/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
    m.run([q, m.OUT/'tests/pcm-test'], 'pcm-test.log')
    m.run(['bash', '-n', HERE/'audio-jack-earpiece.sh'], 'jack-host-syntax.log')
    body = (HERE/'audio-jack-earpiece.sh').read_text().split("<<'A9_JACK_CHECK'\n", 1)[1].split('\nA9_JACK_CHECK\n', 1)[0]
    device = m.OUT/'jack-device-syntax.txt'; device.write_text(body+'\n')
    system = ROOT/'adsp/d2-v8-20261017/system-image/system'
    libs = ':'.join(map(str, [system/'lib64/bootstrap', system/'lib64']))
    m.run([q, '-L', m.OUT/'tests/sysroot', '-E',
           'LD_LIBRARY_PATH='+libs, system/'bin/sh', '-n', device], 'jack-android-syntax.log')
    m.package(boot=ROOT/'plain-deadline-logfix1-20261022/boot.img')
    shutil.copyfile(HERE/'audio-jack-earpiece.sh', m.OUT/'audio-jack-earpiece.sh')
    (m.OUT/'audio-jack-earpiece.sh').chmod(0o755)
    result = json.loads((m.OUT/'result.json').read_text())
    result.pop('playback_held_user_afk', None)
    result.update(boot_reused=str((m.OUT/'boot.img').resolve()), capture_executed=False,
                  hardware_tested=False, attended_retest_pending=True, kernel_changed=False)
    result['sha256']['audio-jack-earpiece.sh'] = m.sha(m.OUT/'audio-jack-earpiece.sh')
    (m.OUT/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    (m.OUT/'SHA256SUMS').write_text(''.join(v+'  '+n+'\n' for n,v in result['sha256'].items()))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
