#!/usr/bin/env python3
"""Host-only Unicode kernel and all 100 vendor modules in one Kbuild/modpost.

Reconstruct the FIX9 source; retain the proven FIX10 vendor and boot payloads.
Enable Unicode and the modular camera/audio providers already shipped separately
by that vendor, so every module uses the same generated headers and real CRCs.
Never modify the shared source/object cache or reuse its module CRC rows.
"""
import argparse
import difflib
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess as sp
import build as b


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = b.ROOT
BASE = ROOT / 'plain-wrapped-fix10-20261023'
PARENT = ROOT / 'plain-wrapped-fix9-20261023'


def prepare(out):
    fix9 = load('fix9', HERE / 'build-fix9.py')
    fix9.prepare(out)
    provenance = {}

    def copy(source, relative):
        data = source.read_bytes()
        fix9.write(b.K / relative, data)
        provenance[relative] = dict(source=str(source), sha256=b.sha(source))

    # Retain the published audio FIX3/4 queue reset and machine/amp lineage.
    audio = ROOT / 'plain-integ-audio-merged-20261022/audio'
    for name in ('q6asm', 'q6asm-dai'):
        copy(audio / (name + '-fixed.c'), 'sound/soc/qcom/qdsp6/' + name + '.c')
    camera = ROOT / 'cameras-20261020'
    for p in (camera / 'kernel/drivers/media/platform/qcom/camss').iterdir():
        if p.suffix in ('.c', '.h') or p.name in ('Makefile', 'Kconfig'):
            copy(p, 'drivers/media/platform/qcom/camss/' + p.name)
    for name, frozen in [('hi556', 'round7-front-module'), ('s5k3l6', 'colour/rear-module')]:
        for suffix in ('-a9.c', '-stock.h'):
            source = (REPO / 'scripts/mainline/cameras' if suffix == '-a9.c' else camera / frozen) / (name + suffix)
            copy(source, 'drivers/media/i2c/' + name + suffix)
    for name, data in [('Makefile', '\nobj-$(CONFIG_VIDEO_HI556_A9) += hi556-a9.o\nobj-$(CONFIG_VIDEO_S5K3L6_A9) += s5k3l6-a9.o\n'),
                       ('Kconfig', '\nconfig VIDEO_HI556_A9\n\ttristate "A9 Hi556"\n\tselect V4L2_CCI_I2C\nconfig VIDEO_S5K3L6_A9\n\ttristate "A9 S5K3L6"\n\tselect V4L2_CCI_I2C\n')]:
        p = b.K / 'drivers/media/i2c' / name
        assert 'VIDEO_HI556_A9' not in p.read_text()
        fix9.write(p, p.read_bytes() + data.encode())
    ext = b.K / 'drivers/a9-fix11'
    ext.mkdir()
    for source in (REPO / 'scripts/mainline/frontlight/driver').iterdir():
        if source.suffix in ('.c', '.h'):
            copy(source, 'drivers/a9-fix11/' + source.name)
    for source in (REPO / 'scripts/mainline/audio/driver').iterdir():
        if source.suffix in ('.c', '.h'):
            copy(source, 'drivers/a9-fix11/' + source.name)
    # This vendor still ships the clock-hold/stream-gated machine and amp pair.
    relative = 'scripts/mainline/audio/driver/a9-audio-card.c'
    expected = hashlib_sha_git(REPO, '89a7099', relative)
    import hashlib
    for commit in sp.check_output(['git', '-C', str(REPO), 'rev-list', 'HEAD', '--', relative], text=True).splitlines():
        data = sp.check_output(['git', '-C', str(REPO), 'show', commit + ':' + relative])
        if hashlib.sha256(data).hexdigest() == expected:
            fix9.write(ext / 'a9-audio-card.c', data)
            provenance['drivers/a9-fix11/a9-audio-card.c'] = dict(commit=commit, sha256=expected)
            break
    else:
        raise RuntimeError('published FIX4 card source not found')
    for source in (b.K / 'sound/soc/qcom').glob('*.h'):
        copy(source, 'drivers/a9-fix11/' + source.name)
    for source in (b.K / 'sound/soc/qcom/qdsp6').glob('*.h'):
        copy(source, 'drivers/a9-fix11/qdsp6/' + source.name)
    for source in [REPO / 'scripts/mainline/usb/a9-usb-compat.c',
                   ROOT / 'suspend-novtcon-20261021/built-android-r22e/module/pmi632-battery.c',
                   REPO / 'scripts/mainline/vibration/a9-aw86xx.c']:
        copy(source, 'drivers/a9-fix11/' + source.name)
    (ext / 'Makefile').write_text('''obj-m += a9-frontlight.o a9-usb-compat.o pmi632-battery.o a9-aw86xx.o
obj-m += snd-soc-a9-audio.o snd-soc-a9-aw87519.o snd-soc-es9218-a9.o
a9-frontlight-y := leds-a9-frontlight.o a9-frontlight-core.o
snd-soc-a9-audio-y := a9-audio-card.o
snd-soc-a9-aw87519-y := a9-aw87519.o
snd-soc-es9218-a9-y := es9218-a9.o
''')
    fix9.write(b.K / 'drivers/Makefile', (b.K / 'drivers/Makefile').read_bytes() + b'\nobj-y += a9-fix11/\n')
    fragment = ('CONFIG_UNICODE=y\n' + (REPO / 'scripts/mainline/audio/a9-audio.config').read_text() +
                (REPO / 'scripts/mainline/cameras/a9-cameras.config').read_text() +
                '\nCONFIG_VIDEO_HI556_A9=m\nCONFIG_V4L_MEM2MEM_DRIVERS=y\nCONFIG_VIDEO_QCOM_VENUS=m\n'
                'CONFIG_PINCTRL_LPASS_LPI=m\n')
    (out / 'unicode-modules.config').write_text(fragment)
    with (b.OBJ / '.config').open('a') as stream:
        stream.write('\n' + fragment)
    (out / 'module-source-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    # Save the original path map before the single-target Kbuild replaces modules.order.
    targets = {Path(p).stem: p[:-2] + '.ko' for p in (b.CACHE / 'obj/modules.order').read_text().splitlines()}
    paths = load('metadata', HERE.parent / 'android/refresh-integrated-vendor.py')
    shipped = sorted(p for p in paths.vendor_files(BASE / 'vendor.img') if p.endswith('.ko'))
    extra = {n: 'drivers/a9-fix11/' + n + '.ko' for n in
             ['a9-frontlight', 'a9-usb-compat', 'pmi632-battery', 'a9-aw86xx', 'snd-soc-a9-audio', 'snd-soc-a9-aw87519', 'snd-soc-es9218-a9']}
    for directory, names in [
        ('drivers/media/i2c', ['hi556-a9', 's5k3l6-a9']),
        ('drivers/media/mc', ['mc']),
        ('drivers/media/v4l2-core', ['videodev', 'v4l2-async', 'v4l2-cci', 'v4l2-fwnode', 'v4l2-mem2mem']),
        ('drivers/media/common/videobuf2', ['videobuf2-common', 'videobuf2-dma-contig', 'videobuf2-dma-sg', 'videobuf2-memops', 'videobuf2-v4l2']),
        ('drivers/media/platform/qcom/camss', ['qcom-camss']),
        ('drivers/media/platform/qcom/venus', ['venus-core', 'venus-dec', 'venus-enc']),
        ('drivers/clk/qcom', ['lpasscc-sm6115']),
        ('drivers/pinctrl/qcom', ['pinctrl-lpass-lpi', 'pinctrl-sm6115-lpass-lpi']),
        ('drivers/soundwire', ['soundwire-qcom']),
        ('sound/soc/codecs', ['snd-soc-lpass-rx-macro', 'snd-soc-lpass-tx-macro', 'snd-soc-lpass-va-macro', 'snd-soc-wcd937x', 'snd-soc-wcd937x-sdw'])]:
        extra.update({n: directory + '/' + n + '.ko' for n in names})
    targets.update(extra)
    manifest = {p: targets[Path(p).stem] for p in shipped}
    assert len(manifest) == 100
    (out / 'module-targets.json').write_text(json.dumps(manifest, indent=2) + '\n')


def hashlib_sha_git(repo, commit, path):
    import hashlib
    return hashlib.sha256(sp.check_output(['git', '-C', str(repo), 'show', commit + ':' + path])).hexdigest()


def restore_shipped_module_sources(out):
    """Keep the module-only fixes absent from the original core kernel cache."""
    writer = load('lineage_writer', HERE / 'build-fix9.py')
    record = out / 'module-source-provenance.json'
    provenance = json.loads(record.read_text())
    for source in (REPO / 'scripts/mainline/eink/driver').iterdir():
        if source.suffix in ('.c', '.h'):
            relative = 'drivers/gpu/drm/a9_eink/' + source.name
            writer.write(b.K / relative, source.read_bytes())
            provenance[relative] = dict(source=str(source), sha256=b.sha(source))
    for name in ['a9-audio-card.c', 'a9-aw87519.c', 'a9-speaker.h', 'a9-clock-hold.h']:
        source = 'scripts/mainline/audio/driver/' + name
        relative = 'drivers/a9-fix11/' + name
        writer.write(b.K / relative, sp.check_output(['git', '-C', str(REPO), 'show', '89a7099:' + source]))
        provenance[relative] = dict(commit='89a7099', source=source, sha256=b.sha(b.K / relative))
    patches = [REPO / 'scripts/mainline/sensors/fastrpc-restore-context.patch',
               *sorted((REPO / 'scripts/mainline/audio/poc-kernel').glob('*.patch'))]
    for patch in patches:
        paths = [line[6:].split('\t')[0] for line in patch.read_text().splitlines() if line.startswith('+++ b/')]
        for relative in paths:
            writer.write(b.K / relative, (b.K / relative).read_bytes())
        command = ['patch', '--batch', '--fuzz=0', '-p1', '-d', str(b.K), '-i', str(patch)]
        dry = sp.run(command + ['--dry-run'], capture_output=True)
        if dry.returncode == 0:
            b.run(command, 'module-lineage-' + patch.stem)
        else:
            reverse = sp.run(command + ['--dry-run', '-R'], capture_output=True)
            assert reverse.returncode == 0, (patch, dry.stdout.decode(), dry.stderr.decode())
        for relative in paths:
            provenance[relative] = dict(patch=str(patch), sha256=b.sha(b.K / relative))
    assert b.sha(b.K / 'drivers/misc/fastrpc.c') == 'dafab838d60439d1c2807620359be412588bfabc7c340c1c1874783f02045e24'
    record.write_text(json.dumps(provenance, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    out = args.output.absolute()
    out.mkdir(parents=True, exist_ok=True)
    b.OUT = out
    if args.resume:
        b.K, b.OBJ = out / 'scratch/kernel', out / 'scratch/obj'
    else:
        prepare(out)
    restore_shipped_module_sources(out)
    # Shared LPASS provider must be selected before its SM6115 child. Also
    # support resuming the first build whose old split fragment omitted it.
    with (b.OBJ / '.config').open('a') as stream:
        stream.write('\nCONFIG_PINCTRL_LPASS_LPI=m\nCONFIG_PINCTRL_SM6115_LPASS_LPI=m\n')
    provenance_path = out / 'module-source-provenance.json'
    provenance = json.loads(provenance_path.read_text())
    headers = [REPO / 'scripts/mainline/vibration/sine.h']
    headers += sorted((ROOT / 'suspend-novtcon-20261021/built-android-r22e/module').glob('*.h'))
    writer = load('fix9_write', HERE / 'build-fix9.py')
    for source in headers:
        relative = 'drivers/a9-fix11/' + source.name
        writer.write(b.K / relative, source.read_bytes())
        provenance[relative] = dict(source=str(source), sha256=b.sha(source))
    provenance_path.write_text(json.dumps(provenance, indent=2) + '\n')
    import re
    for source in (b.K / 'drivers/a9-fix11').rglob('*'):
        if source.suffix in ('.c', '.h'):
            for header in re.findall(r'^#include "([^"]+)"', source.read_text(), re.M):
                assert (source.parent / header).exists(), (source, header)
    wdt = load('wdt', HERE / 'build-wdt.py')
    make = wdt.make_command()
    b.run(make + ['olddefconfig'], 'olddefconfig')
    config = (b.OBJ / '.config').read_text()
    assert 'CONFIG_UNICODE=y\n' in config
    assert 'CONFIG_F2FS_FS=y\n' in config and 'CONFIG_FS_ENCRYPTION=y\n' in config
    assert 'CONFIG_PINCTRL_LPASS_LPI=m\n' in config and 'CONFIG_PINCTRL_SM6115_LPASS_LPI=m\n' in config
    (out / 'config.patch').write_text(''.join(difflib.unified_diff(
        (PARENT / 'kernel.config').read_text().splitlines(True), config.splitlines(True),
        fromfile='fix9/kernel.config', tofile='fix11/kernel.config')))
    b.run(make + ['Image.gz'], 'kernel-build')
    # One single-target build and modpost sees the new vmlinux and ALL providers.
    targets = json.loads((out / 'module-targets.json').read_text())
    b.run(make + sorted(targets.values()), 'all-100-modules-build')
    for source, target in [('arch/arm64/boot/Image.gz', 'Image.gz'), ('.config', 'kernel.config'),
                           ('Module.symvers', 'Module.symvers'), ('vmlinux.symvers', 'vmlinux.symvers')]:
        shutil.copyfile(b.OBJ / source, out / target)
    built = {}
    strip = shutil.which('aarch64-unknown-linux-gnu-strip', path=b.ENV['PATH'])
    for destination, source in targets.items():
        target = out / 'modules' / Path(destination).name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(b.OBJ / source, target)
        sp.run([strip, '--strip-debug', str(target)], check=True)
        built[destination] = dict(target=source, sha256=b.sha(target))
    (out / 'built-modules.json').write_text(json.dumps(built, indent=2) + '\n')
    assert b'fscrypt: Android wrappedkey_v0 add raw_size=' in gzip.decompress((out / 'Image.gz').read_bytes())
    usb = load('usb', HERE / 'build-usb-disarm.py')
    usb.repack(PARENT, out, 'boot.img')
    shutil.copyfile(PARENT / 'recovery_dtbo.img', out / 'recovery_dtbo.img')
    b.run(['/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin/python3', '-P',
           REPO / 'scripts/abl-check.py', out / 'boot.img', 'boot', '--dtbo', out / 'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((out / 'abl-check.log').read_text())['result'] == 'PASS'
    print('FIX11 kernel, 100 freshly built modules and ABL PASS', flush=True)


if __name__ == '__main__':
    main()
