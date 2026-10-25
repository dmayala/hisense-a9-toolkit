#!/usr/bin/env python3
"""A5 host-only external modules using a small private prepared-header tree.

The R4 Image/config/ABI stay unchanged. Build intermediates are removed after
success; logs, source patches and output hashes are retained. No phone tools.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = Path.home() / 'A9-build/mainline'
BASE = ROOT / 'demo-integrated-20261019'
R4 = ROOT / 'integrated-latency-v3-runtime-probe-r4-20261019'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def stage_module_dependencies(mods, base_obj, env):
    """Complete the external audio closure, including --package-only caches."""
    def key(name):
        return name.removesuffix('.ko').replace('_', '-')

    sources = {key(p.name): p for p in base_obj.rglob('*.ko')}
    sources.update({key(p.name): p for p in mods.glob('*.ko')})
    ordered, visiting, done, dependencies = [], set(), set(), {}

    def visit(name):
        name = key(name)
        if name in done:
            return
        if name in visiting:
            raise RuntimeError('module dependency cycle: ' + name)
        if name not in sources:
            raise RuntimeError('missing audio module dependency: ' + name)
        visiting.add(name)
        source = sources[name]
        deps = sp.check_output(['modinfo', '-F', 'depends', str(source)], env=env, text=True).strip()
        dependencies[source.name] = [key(dep) for dep in deps.split(',') if dep]
        for dep in dependencies[source.name]:
            visit(dep)
        dest = mods / source.name
        if source != dest:
            shutil.copyfile(source, dest)
            sp.run(['aarch64-unknown-linux-gnu-strip', '--strip-debug', str(dest)], env=env, check=True)
        ordered.append(dest.name)
        visiting.remove(name)
        done.add(name)

    # ASoC's DT component links are not ELF dependencies: register the machine
    # driver after the controller/codecs, as in the original manual list.
    for module in sorted(mods.glob('*.ko'), key=lambda p: (p.name == 'snd-soc-a9-audio.ko', p.name)):
        visit(module.name)
    (mods / 'modules.audio-poc').write_text(''.join(name + '\n' for name in ordered))
    return dependencies


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=ROOT / 'audio-20261020')
    p.add_argument('--boot', type=Path, default=R4 / 'images/boot.img')
    p.add_argument('--base-obj', type=Path, default=BASE / 'obj')
    p.add_argument('--kernel-source', type=Path, help='kernel source matching --base-obj (default: its source link)')
    p.add_argument('--vendor', action='store_true')
    p.add_argument('--package-only', action='store_true', help='reuse already built modules; refresh tools/DT/overlay only')
    p.add_argument('--keep-intermediates', action='store_true')
    a = p.parse_args()
    os.umask(0o077)
    out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env['PATH'] = ':'.join(json.loads((BASE / 'source.json').read_text())['tools']) + ':' + env['PATH']
    src = (a.kernel_source or (a.base_obj / 'source')).resolve()
    obj, ext = out / 'objects-r2', out / 'external-r2'
    overlay = out / 'overlay-r2'; mods = overlay / 'lib/modules'
    mods.mkdir(parents=True, exist_ok=True)
    commands = []

    def run(cmd, log):
        cmd = list(map(str, cmd)); commands.append(cmd)
        with (out / log).open('w') as f:
            sp.run(cmd, env=env, check=True, stdout=f, stderr=sp.STDOUT)

    before = {n: sha(a.base_obj / n) for n in ('.config', 'Module.symvers')}
    fresh_modules = {}
    if not a.package_only:
        # M= needs prepared headers/tools and the exact ABI, not the full
        # kernel object tree. Keep the shared kernel objects read-only.
        obj.mkdir(exist_ok=True)
        for directory in ('include', 'scripts', 'arch/arm64/include'):
            shutil.copytree(a.base_obj / directory, obj / directory,
                            symlinks=True, dirs_exist_ok=True)
        for name in ('.config', 'Module.symvers', 'Makefile'):
            shutil.copyfile(a.base_obj / name, obj / name)
        if not (obj / 'source').exists():
            (obj / 'source').symlink_to(src)
        # Never change Kconfig to make these modules compile: that could alter
        # built-in data structures while retaining a misleading kernel release.
        before = {n: sha(a.base_obj / n) for n in ('.config', 'Module.symvers')}
        for directory in ('codecs', 'qcom', 'qcom/qdsp6', 'pinctrl', 'clk', 'soundwire'):
            (ext / directory).mkdir(parents=True, exist_ok=True)
        for directory, upstream in [('codecs', 'sound/soc/codecs'), ('qcom', 'sound/soc/qcom'),
                                    ('qcom/qdsp6', 'sound/soc/qcom/qdsp6'), ('pinctrl', 'drivers/pinctrl/qcom'),
                                    ('clk', 'drivers/clk/qcom'), ('soundwire', 'drivers/soundwire')]:
            for f in (src / upstream).glob('*.h'):
                shutil.copyfile(f, ext / directory / f.name)
        for f in (src / 'drivers/pinctrl').glob('*.h'):
            shutil.copyfile(f, ext / f.name)
        payloads = {
            'pinctrl': ('drivers/pinctrl/qcom', ['pinctrl-lpass-lpi.c', 'pinctrl-sm6115-lpass-lpi.c']),
            'clk': ('drivers/clk/qcom', ['lpasscc-sm6115.c']),
            'soundwire': ('drivers/soundwire', ['qcom.c']),
            'codecs': ('sound/soc/codecs', ['wcd937x.c', 'wcd937x-sdw.c', 'lpass-rx-macro.c', 'lpass-tx-macro.c', 'lpass-va-macro.c']),
            'qcom': ('sound/soc/qcom', ['sdw.c']),
            'qcom/qdsp6': ('sound/soc/qcom/qdsp6', ['q6dsp-common.c', 'q6dsp-lpass-ports.c', 'q6dsp-lpass-clocks.c', 'q6afe.c', 'q6asm.c', 'q6asm-dai.c']),
        }
        for dest, (upstream, files) in payloads.items():
            for n in files: shutil.copyfile(src / upstream / n, ext / dest / n)
        for n in ('es9218-a9.c', 'es9218-a9-seq.h', 'a9-aw87519.c'):
            shutil.copyfile(HERE / 'driver' / n, ext / 'codecs' / n)
        run(['patch', '--batch', '--fuzz=0', '-d', ext/'qcom/qdsp6', '-p1',
             '-i', HERE/'output/q6asm-reprepare.patch'], 'q6asm-reprepare.log')
        shutil.copyfile(HERE / 'driver/a9-audio-card.c', ext / 'qcom/a9-audio-card.c')
        for directory in ('qcom', 'codecs'):
            shutil.copyfile(HERE / 'driver/a9-speaker.h', ext / directory / 'a9-speaker.h')
        shutil.copyfile(HERE / 'driver/a9-clock-hold.h', ext / 'qcom/a9-clock-hold.h')
        for target, patch in [('pinctrl/pinctrl-lpass-lpi.c', 'lpi-static-groups.patch'),
                              ('soundwire/qcom.c', 'soundwire-version.patch'),
                              ('codecs/wcd937x.c', 'wcd-a9-supplies.patch'),
                              ('qcom/sdw.c', 'sdw-channel-map.patch'),
                              ('qcom/qdsp6/q6afe.c', 'q6afe-dma-mask.patch')]:
            run(['patch', ext / target, HERE / 'poc-kernel' / patch], patch + '.log')
        ports = ext / 'qcom/qdsp6/q6dsp-lpass-ports.c'
        text = ports.read_text(); start = text.index('.stream_name = "Secondary MI2S Playback",')
        text = text[:start] + text[start:].replace('.formats = SNDRV_PCM_FMTBIT_S16_LE,',
                                                  '.formats = SNDRV_PCM_FMTBIT_S16_LE | SNDRV_PCM_FMTBIT_S32_LE,', 1)
        ports.write_text(text)
        makefiles = {
            '': 'obj-m += pinctrl/ clk/ soundwire/ codecs/ qcom/\n',
            'pinctrl': 'obj-m += pinctrl-lpass-lpi.o pinctrl-sm6115-lpass-lpi.o\n',
            'clk': 'obj-m += lpasscc-sm6115.o\n',
            'soundwire': 'obj-m += soundwire-qcom.o\nsoundwire-qcom-y := qcom.o\n',
            'codecs': 'ccflags-y += -DCONFIG_SND_SOC_WCD937X_SDW_MODULE=1\nobj-m += snd-soc-wcd937x.o snd-soc-wcd937x-sdw.o snd-soc-lpass-rx-macro.o snd-soc-lpass-tx-macro.o snd-soc-lpass-va-macro.o snd-soc-es9218-a9.o snd-soc-a9-aw87519.o\nsnd-soc-wcd937x-y := wcd937x.o\nsnd-soc-wcd937x-sdw-y := wcd937x-sdw.o\nsnd-soc-lpass-rx-macro-y := lpass-rx-macro.o\nsnd-soc-lpass-tx-macro-y := lpass-tx-macro.o\nsnd-soc-lpass-va-macro-y := lpass-va-macro.o\nsnd-soc-es9218-a9-y := es9218-a9.o\nsnd-soc-a9-aw87519-y := a9-aw87519.o\n',
            'qcom': 'obj-m += snd-soc-a9-audio.o snd-soc-qcom-sdw.o qdsp6/\nsnd-soc-a9-audio-y := a9-audio-card.o\nsnd-soc-qcom-sdw-y := sdw.o\n',
            'qcom/qdsp6': 'obj-m += snd-q6dsp-common.o q6afe.o q6asm.o q6asm-dai.o\nsnd-q6dsp-common-y := q6dsp-common.o q6dsp-lpass-ports.o q6dsp-lpass-clocks.o\n',
        }
        for d, text in makefiles.items(): (ext / d / 'Makefile').write_text(text)
        run(['make', '-C', src, 'O=' + str(obj), 'M=' + str(ext), 'ARCH=arm64',
             'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc', 'LOCALVERSION=+', '-j4', 'modules'], 'modules-r2.log')
        for n, digest in before.items():
            assert sha(obj / n) == digest and sha(a.base_obj / n) == digest, 'base ABI/config changed'
        strip = 'aarch64-unknown-linux-gnu-strip'
        for f in ext.rglob('*.ko'):
            dest = mods / f.name; shutil.copyfile(f, dest)
            run([strip, '--strip-debug', dest], 'strip.log')
            fresh_modules[f.name] = {
                'sha256': sha(dest),
                'vermagic': sp.check_output(['modinfo', '-F', 'vermagic', str(dest)],
                                            env=env, text=True).strip(),
            }
    # Do this in package-only too: old caches omitted regmap-sdw/slimbus, and
    # insmod does not resolve dependencies. Include all modinfo imports in order.
    dependencies = stage_module_dependencies(mods, a.base_obj, env)
    (out / 'audio-module-dependencies.json').write_text(json.dumps(dependencies, indent=2)+'\n')
    # Existing sensors load q6dsp-common and qcom-sdw early, so vendor must
    # contain the new replacements before boot (never hot-replace a module).
    bins = overlay / 'bin'; bins.mkdir(exist_ok=True)
    run(['make', '-C', HERE, 'OUT=' + str(out / 'tools'), 'android'], 'tools-r2.log')
    for n in ('a9-audio-ctl', 'a9-audio-pcm'):
        shutil.copyfile(out / 'tools' / (n + '-android'), bins / n); (bins / n).chmod(0o755)
    shutil.copyfile(HERE / 'a9ml-audio.sh', bins / 'a9ml-audio.sh'); (bins / 'a9ml-audio.sh').chmod(0o755)
    etc = overlay / 'etc'; etc.mkdir(exist_ok=True)
    shutil.copyfile(HERE / 'primary-a5.xml', etc / 'primary_audio_policy_configuration.xml')
    # The sensor starter verifies this manifest through firmware_class.path.
    # Integrated Wi-Fi links qcom to /vendor/firmware/qcom, so the new tuning
    # must exist there too; keep the ADSP copy for standalone staging.
    fwname = 'qcom/sm6115/hisense/hlte556n/aw87xxx_pid_59_music_0.bin'
    fw = overlay / 'firmware/a9adsp' / fwname; fw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'sol-audio/stock/amp-tuning/aw87xxx_pid_59_music_0.bin', fw)
    shared_fw = overlay / 'firmware' / fwname
    shared_fw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fw, shared_fw)
    manifest = overlay / 'firmware/a9adsp.SHA256SUMS'
    run(['debugfs', '-R', 'dump /firmware/a9adsp.SHA256SUMS ' + str(manifest), R4 / 'images/vendor.img'], 'firmware-manifest.log')
    manifest.write_text(manifest.read_text() + sha(fw) + '  ' + fwname + '\n')
    run(['gcc', '-E', '-nostdinc', '-undef', '-D__DTS__', '-x', 'assembler-with-cpp',
         '-I' + str(src / 'include'), HERE / 'audio.dtso', '-o', out / 'audio.pp.dtso'], 'audio-cpp.log')
    run(['dtc', '-@', '-I', 'dts', '-O', 'dtb', '-o', out / 'audio.dtbo', out / 'audio.pp.dtso'], 'audio-dtc.log')
    # Apply to the caller's cumulative DT, preserving other feature changes.
    sys.path.insert(0, str(HERE))
    import importlib.util
    spec = importlib.util.spec_from_file_location('pack', HERE / 'build-poc.py')
    pack = importlib.util.module_from_spec(spec); spec.loader.exec_module(pack)
    image = a.boot.read_bytes(); _, blobs = pack.replace_dtb(image, b'')
    # replace_dtb replaces element 4; extract the original DTB independently.
    import struct
    page = struct.unpack_from('<I', image, 36)[0]
    sizes = [struct.unpack_from('<I', image, off)[0] for off in (8,16,24,1632,1648)]
    offset = page + sum((n+page-1)//page*page for n in sizes[:4])
    (out / 'input.dtb').write_bytes(image[offset:offset+sizes[4]])
    run(['fdtoverlay', '-i', out / 'input.dtb', '-o', out / 'audio.dtb', out / 'audio.dtbo'], 'audio-overlay.log')
    # Older A5 boots placed this same card at /sound-ess. Reapplying the new
    # canonical /sound graph must not leave a second machine device behind.
    legacy = sp.run(['fdtget', str(out / 'audio.dtb'), '/sound-ess', 'compatible'],
                    env=env, text=True, capture_output=True)
    if legacy.returncode == 0 and 'hisense,a9-audio-card' in legacy.stdout.split():
        run(['fdtput', '-r', out / 'audio.dtb', '/sound-ess'], 'audio-legacy-card.log')
    # Release only the proven audio pins; retain cumulative vibration/key/etc
    # reservation changes from the caller's DT instead of restoring R4 ranges.
    tlmm = '/soc@0/pinctrl@500000'
    ranges = list(map(int, sp.check_output(['fdtget', '-t', 'i', str(out / 'input.dtb'), tlmm,
                                          'gpio-reserved-ranges'], env=env, text=True).split()))
    reserved = set()
    for start, count in zip(ranges[::2], ranges[1::2]): reserved.update(range(start, start+count))
    reserved -= {65, 66, 68, 69, 92, 107}
    pairs = []
    for pin in sorted(reserved):
        if pairs and pairs[-2] + pairs[-1] == pin: pairs[-1] += 1
        else: pairs.extend([pin, 1])
    run(['fdtput', '-t', 'i', out / 'audio.dtb', tlmm, 'gpio-reserved-ranges', *pairs], 'audio-pins.log')
    (out / 'boot.img').write_bytes(pack.pack_boot(image, blobs[0], (out / 'audio.dtb').read_bytes()))
    if a.vendor:
        run(['bash', REPO / 'scripts/mainline/android/build-vendor.sh', '--refresh-integrated',
             '--out', out / 'vendor-r2', '--overlay', overlay], 'vendor-r2.log')
    files = [out / 'boot.img', out / 'audio.dtb', *sorted(f for f in overlay.rglob('*') if f.is_file())]
    if a.vendor: files.append(out / 'vendor-r2/vendor.img')
    result = {'status':'HOST BUILT, PHONE UNTESTED', 'config_unchanged':before,
              'fresh_modules': fresh_modules,
              'kernel_source': str(src), 'base_obj': str(a.base_obj.resolve()),
              'module_dependencies':dependencies,
              'commands':commands, 'module_make':['make','-C',str(src),'O='+str(obj),'M='+str(ext),'ARCH=arm64','CROSS_COMPILE=aarch64-unknown-linux-gnu-','HOSTCC=gcc','LOCALVERSION=+','-j4','modules'],
              'source_sha256':{str(f.relative_to(HERE)):sha(f) for directory in (HERE/'driver',HERE/'poc-kernel') for f in sorted(directory.rglob('*')) if f.is_file()},
              'source_commit':sp.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
              'sha256':{str(f.relative_to(out)):sha(f) for f in files}}
    (out / 'a5-build.json').write_text(json.dumps(result,indent=2)+'\n')
    (out / 'A5-SHA256SUMS').write_text(''.join(v+'  '+n+'\n' for n,v in result['sha256'].items()))
    if not a.keep_intermediates:
        if obj.exists(): shutil.rmtree(obj)
        if ext.exists(): shutil.rmtree(ext)
    print('A5 host build complete: ' + str(out / 'a5-build.json'))


if __name__ == '__main__': main()
