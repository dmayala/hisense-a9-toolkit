#!/usr/bin/env python3
"""Check nested kernel proposal, Kconfig symbols and enabled/observe DT offline.
Does not run build.sh, load modules, read captures, or communicate with hardware.
"""
import os
import pathlib
import re
import shlex
import subprocess
import tempfile

here = pathlib.Path(__file__).resolve().parents[1]
repo = here.parents[2]
mainline = pathlib.Path(os.environ.get('A9_MAINLINE', pathlib.Path.home() / 'A9-build/mainline'))
src = mainline / 'linux-7.2.10'
work = mainline / 'sol-pmic'


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def nix(args):
    run(['nix-shell', repo / 'scripts/mainline/kernel/shell.nix', '--run', shlex.join(map(str, args))])


with tempfile.TemporaryDirectory(prefix='integration-', dir=work) as td:
    tmp = pathlib.Path(td)
    project = tmp / 'project'
    for rel in ['scripts/mainline/kernel/build.sh', 'scripts/mainline/kernel/a9.config']:
        dst = project / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((repo / rel).read_bytes())
    run(['patch', '-s', '-p1', '-d', project, '-i', here / 'kernel-telemetry-PROPOSAL.patch'])
    build = (project / 'scripts/mainline/kernel/build.sh').read_text()
    config = (project / 'scripts/mainline/kernel/a9.config').read_text()
    run(['bash', '-n', project / 'scripts/mainline/kernel/build.sh'])
    assert 'CONFIG_CHARGER_PMI632_A9=m' not in config
    for name in ['POWER_SUPPLY', 'CHARGER_PMI632_A9', 'CHARGER_PMI632_A9_TELEMETRY_ONLY',
                 'CHARGER_PMI632_A9_OBSERVE', 'CHARGER_PMI632_A9_FV_STOCK']:
        assert f'CONFIG_{name}=y' in config
    assert 'panel-hisense-hlte556n-eink pmi632-a9 <<' not in build
    private = tmp / 'linux-files'
    baseline_files = ['drivers/power/supply/Kconfig', 'drivers/power/supply/pmi632-a9.c',
                      'drivers/power/supply/pmi632-policy.h']
    if (src / 'drivers/power/supply/pmi632-fv.h').exists():
        baseline_files.append('drivers/power/supply/pmi632-fv.h')
    for rel in baseline_files:
        dst = private / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((src / rel).read_bytes())
    inner_patches = list((project / 'scripts/mainline/kernel/patches').glob('*.patch'))
    assert len(inner_patches) == 1
    inner = inner_patches[0]
    # Concurrent agents own the real series; never reuse an occupied number.
    prefix = inner.name.split('-', 1)[0]
    assert not list((repo / 'scripts/mainline/kernel/patches').glob(prefix + '-*.patch'))
    run(['git', 'mailinfo', tmp / 'mail-message', tmp / 'mail-patch'],
        input=inner.read_bytes(), stdout=subprocess.DEVNULL)
    assert 'diff --git' in (tmp / 'mail-patch').read_text()
    # The coordinator's build applies kernel patches with git am, not patch.
    git = ['git', '-C', private, '-c', 'user.name=PMIC proposal',
           '-c', 'user.email=pmic@example.invalid', '-c', 'commit.gpgsign=false']
    run(git + ['init', '-q'])
    run(git + ['add'] + baseline_files)
    run(git + ['commit', '-qm', 'Temporary validation baseline'])
    run(git + ['am', '--quiet', inner])
    for name in ['pmi632-a9.c', 'pmi632-policy.h', 'pmi632-fv.h']:
        assert (private / 'drivers/power/supply' / name).read_bytes() == (here / name).read_bytes()

    # Actual Kconfig parser, with explicit stubs for platform dependencies.
    # This checks the new symbol/dependency relationship, not a full defconfig.
    top = tmp / 'Kconfig'
    top.write_text('mainmenu "PMI632 proposal validation"\n' + ''.join(
        f'config {n}\n\tbool\n\tdefault y\n' for n in ['OF', 'MFD_SPMI_PMIC', 'IIO', 'REGMAP']) +
        f'source "{private}/drivers/power/supply/Kconfig"\n')
    cf = tmp / 'config'
    cf.write_text('CONFIG_POWER_SUPPLY=y\nCONFIG_CHARGER_PMI632_A9=y\n'
                  'CONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY=y\n')
    env = dict(os.environ, KCONFIG_CONFIG=str(cf))
    run([work / 'build-7.2.10/scripts/kconfig/conf', '--olddefconfig', top], cwd=tmp, env=env,
        stdout=subprocess.DEVNULL)
    assert 'CONFIG_CHARGER_PMI632_A9=y' in cf.read_text()
    assert 'CONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY=y' in cf.read_text()
    assert 'CONFIG_CHARGER_PMI632_A9_OBSERVE=y' in cf.read_text()
    assert '# CONFIG_CHARGER_PMI632_A9_FV_STOCK is not set' in cf.read_text()
    # The telemetry proposal explicitly opts in; pure observe defaults to 0.
    cf.write_text('\n'.join(line for line in config.splitlines() if line.startswith(
        ('CONFIG_POWER_SUPPLY=', 'CONFIG_CHARGER_PMI632_A9'))) + '\n')
    run([work / 'build-7.2.10/scripts/kconfig/conf', '--olddefconfig', top], cwd=tmp, env=env,
        stdout=subprocess.DEVNULL)
    assert 'CONFIG_CHARGER_PMI632_A9_FV_STOCK=y' in cf.read_text()

    # Reproduce the private DTS copy (plus initial integration if still needed).
    board = tmp / 'board.dts'
    text = (repo / 'docs/mainline/sm6115-hisense-hlte556n.dts').read_text()
    include = '#include "a9-pmi632.dtsi"'
    # Execute only the generated include assurance, never the real build.sh.
    board.write_text(text)
    if "<<'PMIC_DTS'" in build:
        include_step = build.split('\tif ! grep -qx ', 1)[1].split("\tcat >>", 1)[0]
        include_step = 'if ! grep -qx ' + include_step
        include_step = include_step.replace('"$SRC/arch/arm64/boot/dts/qcom/$DTS.dts"', shlex.quote(str(board)))
        for unused in range(2):
            run(['bash', '-c', include_step])
        override = build.split("<<'PMIC_DTS'\n", 1)[1].split('\nPMIC_DTS', 1)[0]
        board.write_text(board.read_text() + '\n' + override + '\n')
    text = board.read_text()
    assert len(re.findall(r'^' + re.escape(include) + r'$', text, re.M)) == 1
    (tmp / 'a9-pmi632.dtsi').write_bytes((here / 'a9-pmi632.dtsi').read_bytes())
    pp, dtb = tmp / 'board.pp.dts', tmp / 'board.dtb'
    nix(['aarch64-unknown-linux-gnu-gcc', '-E', '-nostdinc', '-undef', '-D__DTS__',
         '-x', 'assembler-with-cpp', '-I', src / 'include',
         '-I', src / 'arch/arm64/boot/dts/qcom', board, '-o', pp])
    nix(['dtc', '-q', '-@', '-I', 'dts', '-O', 'dtb', '-o', dtb, pp])
    node = '/soc@0/spmi@1c40000/pmic@2/charger@1000'
    # Use a single Nix call for the final property assertions.
    cmd = (shlex.join(['fdtget', '-t', 's', str(dtb), node, 'status']) + ' > ' + shlex.quote(str(tmp / 'status')) +
           ' && ' + shlex.join(['fdtget', '-p', str(dtb), node]) + ' > ' + shlex.quote(str(tmp / 'props')))
    run(['nix-shell', repo / 'scripts/mainline/kernel/shell.nix', '--run', cmd])
    assert (tmp / 'status').read_text().strip() == 'okay'
    assert 'hisense,charging-validated' not in (tmp / 'props').read_text().splitlines()
print('PASS: nested proposal and git am, shell syntax, observe + FV telemetry config and pure-observe default, Kconfig parser, canonical sources, enabled DT without charging validation')
