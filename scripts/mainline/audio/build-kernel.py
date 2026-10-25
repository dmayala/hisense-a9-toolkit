#!/usr/bin/env python3
"""Offline, default-off A9 audio kernel build. Never touches a shared kernel tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

from snapshot import private_path, no_symlinks

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DEFAULT = Path.home() / 'A9-build/mainline/sol-audio/build/mainline'
BASE = 'sm6115-hisense-hlte556n'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def output_path(path):
    path = private_path(path)
    for parent in (path,*path.parents):
        if parent.exists() and not parent.is_dir():
            raise ValueError('private build parent is not a directory '+str(parent))
    return path


def validate_tree(root, allowed_roots):
    """Allow Linux's internal links, reject every link escaping private roots."""
    no_symlinks(root)
    if root.exists() and not root.is_dir():
        raise ValueError('private tree is not a directory '+str(root))
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:
            path=Path(base)/name
            if path.is_symlink():
                target=path.resolve()
                if not any(target==allowed or allowed in target.parents for allowed in allowed_roots):
                    raise ValueError('private tree symlink escapes private source/objects '+str(path))
            elif path.is_file():
                if path.stat().st_nlink != 1:
                    raise ValueError('private tree has a shared hardlink '+str(path))
            elif not path.is_dir():
                raise ValueError('private tree contains a nonregular file '+str(path))


def write_target(path):
    no_symlinks(path)
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():
            raise ValueError('generated parent is not a directory '+str(parent))
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError('generated destination is not an unshared regular file '+str(path))
    return path


def copy_generated(source,destination):
    destination=write_target(destination)
    shutil.copy2(source,destination)


def generated_block(path,key,addition,legacy_tokens):
    """Replace our previous stanza; migrate an exact legacy unmarked stanza."""
    begin='# BEGIN A9_AUDIO_GENERATED '+key
    end='# END A9_AUDIO_GENERATED '+key
    text=path.read_text()
    if text.count(begin)!=text.count(end) or text.count(begin)>1:
        raise ValueError('malformed generated integration block '+str(path))
    if begin in text:
        start,finish=text.index(begin),text.index(end)
        if finish<start:
            raise ValueError('malformed generated integration block '+str(path))
        text=text[:start]+text[finish+len(end):]
    elif addition in text:
        if text.count(addition)!=1:
            raise ValueError('duplicate legacy audio integration '+str(path))
        text=text.replace(addition,'',1)
    elif any(token in text for token in legacy_tokens):
        raise ValueError('unrecognized legacy integration; choose a new --work '+str(path))
    write_target(path).write_text(text.rstrip()+'\n\n'+begin+'\n'+addition+'\n'+end+'\n')


def reserved_audit(dtb, stock, baseline, adsp):
    """Review 33 F1/F2: every stock no-map region covered, no overlap, no
    baseline Android reservation released, ADSP status exactly as requested."""
    subprocess.run(['python3', '-I', str(HERE/'dt-reserved-audit.py'), str(dtb), str(stock),
                    '--baseline', str(baseline), '--adsp', 'okay' if adsp else 'disabled'], check=True)


def run(args, cwd=None, env=None):
    subprocess.run([str(x) for x in args], cwd=cwd, env=env, check=True)


def config_values(paths):
    result = {}
    for path in paths:
        for line in path.read_text().splitlines():
            if line.startswith('CONFIG_') and '=' in line:
                result[line.split('=', 1)[0]] = line
            elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
                result[line.split()[1]] = line
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source', type=Path, default=Path.home()/'A9-build/mainline/linux-7.2.10')
    p.add_argument('--work', type=Path, default=DEFAULT)
    p.add_argument('--jobs', type=int, default=3)
    p.add_argument('--macros', action='store_true', help='compile experimental macro-probe DT variant')
    p.add_argument('--configure-only', action='store_true')
    p.add_argument('--es9218', action='store_true', help='build the experimental ESS codec/card and inactive board nodes')
    p.add_argument('--enable-es9218', action='store_true', help='enable experimental ESS DT nodes for later attended tests')
    p.add_argument('--enable-adsp', action='store_true', help='set the ADSP remoteproc okay (audio-only opt-in; PAS then boots it on load)')
    p.add_argument('--stock-dts', type=Path, default=Path.home()/'A9-build/mainline/sol-pmic/evidence/base_0.dts',
                   help='stock base DTS/DTB for the reserved-memory audit')
    p.add_argument('--pd-mapper', choices=('kernel', 'userspace'), default=os.environ.get('A9_WIFI_PD_MAPPER', 'kernel'))
    a = p.parse_args(argv)
    if os.environ.get('A9_AUDIO', '0') != '1':
        p.error('audio is default off; set A9_AUDIO=1 for this isolated offline build')
    if a.enable_es9218 and not a.es9218:
        p.error('--enable-es9218 requires --es9218')
    if not 1 <= a.jobs <= 32:
        p.error('--jobs must be 1..32')
    if not a.stock_dts.is_file():
        p.error('stock base DTS for the reserved-memory audit is missing: '+str(a.stock_dts))
    source = a.kernel_source.expanduser().resolve()
    if not (source/'.git').exists():
        p.error('local Linux 7.2.10 git source is required; this tool never fetches')
    work = output_path(a.work)
    if work == source or source in work.parents or work in source.parents:
        p.error('private build and shared kernel source must be disjoint')
    tag = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'v7.2.10^{commit}'], text=True).strip()
    patches = sorted((REPO/'scripts/mainline/kernel/patches').glob('*.patch'))
    identity = {'linux_tag': tag, 'patches': {x.name: digest(x) for x in patches}}
    work.mkdir(parents=True, exist_ok=True)
    src, obj = work/'linux-7.2.10', work/'objects'
    marker = write_target(work/'source.json')
    write_target(work/'kernel-source.tar')
    write_target(work/'pd-mapper.config')
    validate_tree(src,(src,))
    validate_tree(obj,(src,obj))
    env = dict(os.environ, GIT_ALLOW_PROTOCOL='file', NIX_CONFIG='substitute = false\nbuilders =\n')
    if marker.exists():
        if json.loads(marker.read_text()) != identity:
            p.error('kernel source inputs changed; select a new empty --work directory')
    else:
        if src.exists():
            p.error('unmarked private kernel source already exists; choose a new --work')
        src.mkdir()
        # Stream a local immutable tag; no clone/fetch, hardlinks, or shared source writes.
        archive = work/'kernel-source.tar'
        with archive.open('wb') as f:
            subprocess.run(
                ['git', '-C', str(source), 'archive', tag], stdout=f, env=env, check=True)
        with tarfile.open(archive) as t:
            t.extractall(src, filter='data')
        archive.unlink()
        validate_tree(src,(src,))
        for patch in patches:
            run(['git', 'apply', '--check', patch], cwd=src, env=env)
            run(['git', 'apply', patch], cwd=src, env=env)
        marker.write_text(json.dumps(identity, indent=2)+'\n')
    if a.es9218:
        for name in ('es9218-a9.c','es9218-a9-seq.h'):
            copy_generated(HERE/'driver'/name, src/'sound/soc/codecs'/name)
        copy_generated(HERE/'driver/a9-es9218-card.c',src/'sound/soc/qcom/a9-es9218-card.c')
        copy_generated(HERE/'bindings/hisense,a9-es9218.yaml',src/'Documentation/devicetree/bindings/sound/hisense,a9-es9218.yaml')
        for directory, suffix in (('codecs','codecs'),('qcom','qcom')):
            for name in ('Kconfig','Makefile'):
                dst = src/'sound/soc'/directory/name
                addition = (HERE/'driver/integration'/(name+'.'+suffix)).read_text().rstrip()
                symbol='SND_SOC_ES9218_A9' if directory=='codecs' else 'SND_SOC_A9_ES9218'
                generated_block(dst,directory+' '+name,addition,(symbol,))
        patch = HERE/'patches/a9-es9218-capability.patch'
        check = subprocess.run(['git','apply','--check',str(patch)],cwd=src,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if check.returncode == 0:
            run(['git','apply',patch],cwd=src)
        else:
            run(['git','apply','--reverse','--check',patch],cwd=src)
    qcom = src/'arch/arm64/boot/dts/qcom'
    copy_generated(REPO/f'docs/mainline/{BASE}.dts', qcom/f'{BASE}.dts')
    copy_generated(REPO/f'scripts/mainline/android/{BASE}-android.dts', qcom/f'{BASE}-android.dts')
    copy_generated(REPO/'scripts/mainline/pmic/a9-pmi632.dtsi', qcom/'a9-pmi632.dtsi')
    copy_generated(HERE/'a9-audio.dtsi', qcom/'a9-audio.dtsi')
    variant = ('-adsp' if a.enable_adsp else '')+('-macros' if a.macros else '')+('-ess' if a.es9218 else '')+('-enabled' if a.enable_es9218 else '')
    name = BASE+'-android-audio'+variant
    out = work/('out-audio'+variant)
    output_path(out)
    validate_tree(out,())
    text = ('#define A9_AUDIO_ENABLE_MACROS 1\n' if a.macros else '')
    if a.enable_adsp: text += '#define A9_AUDIO_ENABLE_ADSP 1\n'
    if a.enable_es9218: text += '#define A9_AUDIO_ENABLE_ES9218 1\n'
    text += f'#include "{BASE}-android.dts"\n#include "a9-audio.dtsi"\n'
    if a.es9218:
        copy_generated(HERE/'a9-es9218.dtsi',qcom/'a9-es9218.dtsi')
        text += '#include "a9-es9218.dtsi"\n'
    write_target(qcom/(name+'.dts')).write_text(text)
    dtmake = qcom/'Makefile'
    for dtb_name in (name, BASE+'-android'):
        entry = f'dtb-$(CONFIG_ARCH_QCOM) += {dtb_name}.dtb'
        if entry not in dtmake.read_text():
            with write_target(dtmake).open('a') as f:
                f.write('\n'+entry+'\n')
    obj.mkdir(exist_ok=True)
    # Kbuild will overwrite these files itself; inspect their paths before it
    # runs, including internal links that would redirect writes into source.
    for target in (obj/'.config',obj/'Module.symvers',obj/'include/config/kernel.release',
                   obj/'arch/arm64/boot/Image.gz',obj/f'arch/arm64/boot/dts/qcom/{name}.dtb',
                   obj/f'arch/arm64/boot/dts/qcom/{BASE}-android.dtb'):
        write_target(target)
    cross_compile=os.environ.get('CROSS_COMPILE','aarch64-unknown-linux-gnu-')
    make = ['make', '-C', src, f'O={obj}', 'ARCH=arm64', 'CROSS_COMPILE='+cross_compile, f'-j{a.jobs}']
    run(make+['defconfig'], env=env)
    frags = [REPO/'scripts/mainline/kernel/a9.config', REPO/'scripts/mainline/kernel/a9-android.config', HERE/'a9-audio.config']
    if a.es9218: frags.append(HERE/'a9-es9218.config')
    if a.pd_mapper == 'userspace':
        override = work/'pd-mapper.config'
        write_target(override).write_text('# CONFIG_QCOM_PD_MAPPER is not set\n')
        frags.append(override)
    run([src/'scripts/kconfig/merge_config.sh', '-m', '-O', obj, obj/'.config', *frags], cwd=src, env=dict(env, ARCH='arm64'))
    run(make+['olddefconfig'], env=env)
    actual = set((obj/'.config').read_text().splitlines())
    missing = []
    for key, line in config_values(frags).items():
        if line.startswith('# '):
            if any(x.startswith(key+'=') for x in actual):
                missing.append(line)
        elif line not in actual:
            missing.append(line)
    if missing:
        raise RuntimeError('Kconfig did not honor: '+', '.join(missing))
    if 'CONFIG_EXTRA_FIRMWARE=""' not in actual:
        raise RuntimeError('firmware embedding must remain disabled')
    if a.es9218 and not a.configure_only:
        # Compile both new drivers before the long global image/module build so
        # kernel API changes fail early against this exact configured source.
        early=['sound/soc/codecs/es9218-a9.o','sound/soc/qcom/a9-es9218-card.o']
        for target in early:
            write_target(obj/target)
        run(make+early,env=env)
    run(make+['DTC_FLAGS=-@', 'qcom/'+name+'.dtb', 'qcom/'+BASE+'-android.dtb'], env=env)
    reserved_audit(obj/f'arch/arm64/boot/dts/qcom/{name}.dtb', a.stock_dts,
                   obj/f'arch/arm64/boot/dts/qcom/{BASE}-android.dtb', a.enable_adsp)
    out.mkdir(exist_ok=True)
    copy_generated(obj/'.config', out/'config')
    copy_generated(obj/f'arch/arm64/boot/dts/qcom/{name}.dtb', out/(name+'.dtb'))
    production_inputs = [x for x in frags if x.is_relative_to(REPO)] + [HERE/'build-kernel.py', HERE/'dt-reserved-audit.py', HERE/'a9-audio.dtsi', REPO/f'docs/mainline/{BASE}.dts', REPO/f'scripts/mainline/android/{BASE}-android.dts', REPO/'scripts/mainline/pmic/a9-pmi632.dtsi']
    if a.es9218:
        production_inputs += [HERE/'a9-es9218.dtsi', *HERE.joinpath('driver').rglob('*.c'), *HERE.joinpath('driver').rglob('*.h'), *HERE.joinpath('driver/integration').iterdir(), HERE/'patches/a9-es9218-capability.patch', HERE/'bindings/hisense,a9-es9218.yaml']
    info = {'repo_commit': subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
            'kernel': identity, 'macros': a.macros, 'es9218':a.es9218, 'es9218_nodes_enabled':a.enable_es9218, 'adsp_enabled':a.enable_adsp,
            'stock_reserved_audit':{'stock':str(a.stock_dts),'sha256':digest(a.stock_dts),'result':'PASS'}, 'pd_mapper': a.pd_mapper,
            'inputs': {str(x.relative_to(REPO)):digest(x) for x in production_inputs},
            'configure_only': a.configure_only,'cross_compile':cross_compile}
    if not a.configure_only:
        run(make+['Image.gz', 'modules'], env=env)
        copy_generated(obj/'arch/arm64/boot/Image.gz', out/'Image.gz')
        copy_generated(obj/'include/config/kernel.release', out/'kernel.release')
        info['modules'] = {x.relative_to(obj).as_posix():digest(x) for x in sorted(obj.rglob('*.ko'))}
        info['module_symvers_sha256'] = digest(obj/'Module.symvers')
    artifacts=['config',name+'.dtb']+([] if a.configure_only else ['Image.gz','kernel.release'])
    info['artifacts'] = {filename:digest(out/filename) for filename in artifacts}
    write_target(out/'build.json').write_text(json.dumps(info,indent=2)+'\n')
    print('Audio build outputs:', out)


if __name__ == '__main__':
    main()
