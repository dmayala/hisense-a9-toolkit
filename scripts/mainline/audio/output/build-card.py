#!/usr/bin/env python3
"""Build the A9 machine/amp pair with the unchanged build-m kernel ABI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = Path.home() / 'A9-build/mainline'
BASE = ROOT / 'demo-integrated-20261019'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(out, original):
    base_obj = BASE / 'obj'
    src = (base_obj / 'source').resolve()
    pins = {'.config': '2690b86b960727b4f99242b56724a9b7f357034f2fc4f21bd84d054e61274ea3',
            'Module.symvers': 'b74085e7ec76d2285b44f2a1f3242359b54387f5fc4a5645f5e5076d0fd0a325'}
    assert all(sha(base_obj / n) == v for n, v in pins.items())
    env = dict(os.environ)
    env['PATH'] = ':'.join(json.loads((BASE / 'source.json').read_text())['tools']) + ':' + env['PATH']
    commands = []
    log = []

    def run(cmd):
        cmd = list(map(str, cmd)); commands.append(cmd)
        r = sp.run(cmd, env=env, capture_output=True, text=True)
        log.append(r.stdout + r.stderr)
        (out / 'card-build.log').write_text('\n'.join(log))
        if r.returncode: raise RuntimeError('card build/check failed; see card-build.log')
        return r.stdout

    def versions(module):
        return {name: int(crc, 16) for crc, name in
                (line.split() for line in run(['modprobe', '--dump-modversions', module]).splitlines())}

    with tempfile.TemporaryDirectory(prefix='card-build-', dir=out) as tmp:
        tmp = Path(tmp); obj = tmp / 'obj'; ext = tmp / 'external'
        obj.mkdir(); ext.mkdir()
        for directory in ('include', 'scripts', 'arch/arm64/include'):
            shutil.copytree(base_obj / directory, obj / directory, symlinks=True)
        for name in ('.config', 'Module.symvers', 'Makefile'):
            shutil.copyfile(base_obj / name, obj / name)
        (obj / 'source').symlink_to(src)
        for f in (src / 'sound/soc/qcom').glob('*.h'): shutil.copyfile(f, ext / f.name)
        (ext / 'qdsp6').mkdir()
        for f in (src / 'sound/soc/qcom/qdsp6').glob('*.h'): shutil.copyfile(f, ext / 'qdsp6' / f.name)
        shutil.copyfile(HERE.parent / 'driver/a9-audio-card.c', ext / 'a9-audio-card.c')
        shutil.copyfile(HERE.parent / 'driver/a9-aw87519.c', ext / 'a9-aw87519.c')
        shutil.copyfile(HERE.parent / 'driver/a9-speaker.h', ext / 'a9-speaker.h')
        shutil.copyfile(HERE.parent / 'driver/a9-clock-hold.h', ext / 'a9-clock-hold.h')
        (ext / 'Makefile').write_text('obj-m += snd-soc-a9-audio.o snd-soc-a9-aw87519.o\nsnd-soc-a9-audio-y := a9-audio-card.o\nsnd-soc-a9-aw87519-y := a9-aw87519.o\n')
        # External qcom_sdw exports are not in the built-in kernel symvers.
        # The original module's version imports supply the exact existing CRCs.
        old = versions(original)
        symbols = {line.split()[1] for line in (obj / 'Module.symvers').read_text().splitlines()}
        with (obj / 'Module.symvers').open('a') as f:
            for name, crc in old.items():
                if name not in symbols:
                    f.write(f'0x{crc:08x}\t{name}\ta9-existing-audio\tEXPORT_SYMBOL_GPL\t\n')
        run(['make', '-C', src, 'O=' + str(obj), 'M=' + str(ext), 'ARCH=arm64',
             'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc', 'LOCALVERSION=+', '-j2', 'modules'])
        module = out / 'vendor-overlay/lib/modules/snd-soc-a9-audio.ko'
        module.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ext / module.name, module)
        run(['aarch64-unknown-linux-gnu-strip', '--strip-debug', module])
        amp = module.with_name('snd-soc-a9-aw87519.ko')
        shutil.copyfile(ext / amp.name, amp)
        run(['aarch64-unknown-linux-gnu-strip', '--strip-debug', amp])
        new = versions(module)
        amp_versions = versions(amp)
        known = dict(old)
        known.update({line.split()[1]: int(line.split()[0], 16)
                      for line in (base_obj / 'Module.symvers').read_text().splitlines()})
        exports = {line.split()[1]: int(line.split()[0], 16)
                   for line in (ext / 'Module.symvers').read_text().splitlines()}
        # Amp Music selection is a mixer control, not a machine START export.
        assert 'a9_aw87519_set_stream' not in new
        known.update(exports)
        assert all(name in known and known[name] == crc for name, crc in new.items())
        assert all(name in known and known[name] == crc for name, crc in amp_versions.items())
        vermagic = run(['modinfo', '-F', 'vermagic', module]).strip()
        assert vermagic == run(['modinfo', '-F', 'vermagic', original]).strip()
        assert vermagic == run(['modinfo', '-F', 'vermagic', amp]).strip()
        assert sha(obj / '.config') == pins['.config']
    assert all(sha(base_obj / n) == v for n, v in pins.items())
    result = {'clock_hold_sha256': sha(HERE.parent/'driver/a9-clock-hold.h'), 'header_sha256': sha(HERE.parent/'driver/a9-speaker.h'), 'amp_activation': 'Music mixer before PCM; no machine START dependency', 'amp_sha256': sha(amp), 'amp_crc_imports_match': len(amp_versions), 'amp_source_sha256': sha(HERE.parent/'driver/a9-aw87519.c'), 'sha256': sha(module), 'vermagic': vermagic, 'crc_imports_match': len(new),
              'config_symvers_preserved': pins, 'source_sha256': sha(HERE.parent / 'driver/a9-audio-card.c'),
              'original_sha256': sha(original), 'commands': commands}
    (out / 'card-build.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
