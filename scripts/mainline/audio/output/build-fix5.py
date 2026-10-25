#!/usr/bin/env python3
"""Host-only FIX5: start WCD jack detection and ship the attended launcher."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def main():
    os.umask(0o077)
    m = load('audio_package', HERE/'build-fix2.py')
    m.OUT = ROOT/'plain-integ-audiofix5-20261022'
    m.BASE = ROOT/'plain-integ-audiofix4-20261022'
    m.BASE_SHA = '3050225ce4e7026186b50c495aaa2c0d57c6714a39214da7048c031729f24618'
    m.OVERLAY = m.OUT/'vendor-overlay'
    m.OUT.mkdir(exist_ok=True)
    if (m.OUT/'vendor.img').exists():
        raise RuntimeError('never overwrite a delivered vendor')
    if shutil.disk_usage(m.OUT).free < 2*1024**3 + 500*1024**2:
        raise RuntimeError('need existing 2GiB floor plus 500MiB build budget')
    assert m.sha(m.BASE/'vendor.img') == m.BASE_SHA
    boot = ROOT/'plain-deadline-logfix1-wdt-20261022/boot.img'
    boot_sha = m.sha(boot)
    original = m.OUT/'original-card.ko'
    original.write_bytes(sp.check_output(['debugfs', '-R',
        'cat /lib/modules/snd-soc-a9-audio.ko', m.BASE/'vendor.img'], stderr=sp.DEVNULL))
    load('card', HERE/'build-card.py').build(m.OUT, original)
    # The existing builder builds the pair. Do not replace the unchanged amp.
    amp = m.OVERLAY/'lib/modules/snd-soc-a9-aw87519.ko'
    amp.rename(m.OUT/'rebuilt-unchanged-amp.ko')
    m.run(['python3', HERE/'test-card.py', '--out', m.OUT], 'jack-card-test.log')
    m.run(['bash', '-n', HERE/'audio-jack-earpiece.sh'], 'jack-host-syntax.log')
    body = (HERE/'audio-jack-earpiece.sh').read_text().split("<<'A9_JACK_CHECK'\n", 1)[1].split('\nA9_JACK_CHECK\n', 1)[0]
    device = m.OUT/'jack-device-syntax.txt'; device.write_text(body+'\n')
    q = '/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
    system = ROOT/'adsp/d2-v8-20261017/system-image/system'
    libs = ':'.join(map(str, [system/'lib64/bootstrap', system/'lib64']))
    m.run([q, '-L', m.BASE/'tests/sysroot', '-E', 'LD_LIBRARY_PATH='+libs,
           system/'bin/sh', '-n', device], 'jack-android-syntax.log')
    hashes = {str(p.relative_to(m.OVERLAY)): m.sha(p)
              for p in m.OVERLAY.rglob('*') if p.is_file()}
    assert list(hashes) == ['lib/modules/snd-soc-a9-audio.ko']
    (m.OUT/'overlay-hashes.json').write_text(json.dumps(hashes, indent=2)+'\n')
    m.package(boot=boot)
    assert m.sha(boot) == boot_sha, 'boot changed during packaging'
    shutil.copyfile(HERE/'audio-jack-earpiece.sh', m.OUT/'audio-jack-earpiece.sh')
    (m.OUT/'audio-jack-earpiece.sh').chmod(0o755)
    result = json.loads((m.OUT/'result.json').read_text())
    result.pop('playback_held_user_afk', None)
    result.update(boot_reused=str(boot), kernel_image_changed=False,
                  capture_executed=False, hardware_tested=False, jack_retest_pending=True,
                  fix4_ear_hph_stereo_and_no_pops_attended_confirmed=True)
    result['sha256']['audio-jack-earpiece.sh'] = m.sha(m.OUT/'audio-jack-earpiece.sh')
    (m.OUT/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    (m.OUT/'SHA256SUMS').write_text(''.join(v+'  '+n+'\n' for n,v in result['sha256'].items()))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
