#!/usr/bin/env python3
"""Offline tests: real command-line transformation and capture evidence gates."""
import importlib.util
import pathlib
import tempfile

here = pathlib.Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


v = module('variants', here / 'variants.py')
c = module('capture', here / 'check-capture.py')
base = ('console=ttyMSM0,115200n8 qcom_pon.beacon=2 qcom_scm.skip_qtee=1 '
        'clk_ignore_unused pd_ignore_unused regulator_ignore_unused '
        'cpuidle.off=1 maxcpus=1 cpufreq.off=1 panic=10')
for stage in ['A', 'B0', 'B1', 'C1']:
    words = v.command(base, stage).split()
    assert words.count('maxcpus=8') == 1 and not any(x == 'maxcpus=1' for x in words)
    assert ('cpufreq.off=1' in words) == (stage == 'A')
    assert ('cpuidle.off=1' in words) == (stage != 'C1')
    if stage == 'A':
        assert words == ['maxcpus=8' if x == 'maxcpus=1' else x for x in base.split()]
    else:
        assert 'cpufreq.default_governor=' + ('userspace' if stage == 'B0' else 'schedutil') in words
for bad in [base + ' maxcpus=2', base.replace('qcom_pon.beacon=2 ', ''),
            base + ' androidboot.serialno=secret', base + ' nosmp', base + ' nr_cpus=1']:
    try:
        v.command(bad, 'A')
    except AssertionError:
        pass
    else:
        raise AssertionError('unsafe/ambiguous input accepted')

with tempfile.TemporaryDirectory() as td:
    root = pathlib.Path(td)
    paths = [root / 'early', root / 'late']
    for idx, p in enumerate(paths):
        p.mkdir()
        (p / 'cpus.txt').write_text('online=0-7\npossible=0-7\npresent=0-7\n')
        (p / 'cpuinfo.txt').write_text(''.join(f'processor : {i}\n' for i in range(8)))
        (p / 'interrupts.txt').write_text(' '.join(f'CPU{i}' for i in range(8)) + '\n0: ' + ' '.join(['10'] * 8) + ' timer\n')
        (p / 'boot-id.txt').write_text('00000001-0000-0000-0000-000000000001\n')
        (p / 'uname.txt').write_text('synthetic kernel\n' + str(10 + idx * 301) + '.0 0.0\n')
        (p / 'dmesg.txt').write_text('smp: Brought up 1 node, 8 CPUs\n')
        (p / 'cmdline.txt').write_text(v.command(base, 'A'))
        (p / 'cpufreq.txt').write_text('')
        (p / 'cpu-thermal.txt').write_text(''.join(
            f'thermal_zone{i}/type=cpu{name}-thermal\nthermal_zone{i}/temp=40000\n'
            for i, name in enumerate(['4', '5', '6', '7', '45', '67', '0123'])))
    c.check('A', *paths)
    for path, data in [(paths[1] / 'boot-id.txt', 'different\n'),
                       (paths[1] / 'cpus.txt', 'online=0\n')]:
        old = path.read_text()
        path.write_text(data)
        try:
            c.check('A', *paths)
        except AssertionError:
            pass
        else:
            raise AssertionError('reset/missing-core evidence accepted')
        path.write_text(old)
    for stage in ['B0', 'B1', 'C1']:
        for idx, p in enumerate(paths):
            (p / 'cmdline.txt').write_text(v.command(base, stage))
            values = []
            for policy, cpus in [('policy0', '0 1 2 3'), ('policy4', '4 5 6 7')]:
                fields = {'scaling_driver': 'qcom-cpufreq-hw', 'related_cpus': cpus,
                          'scaling_governor': 'userspace' if stage == 'B0' else 'schedutil',
                          'scaling_available_frequencies': '300000 1000000',
                          'scaling_min_freq': '300000', 'scaling_max_freq': '1000000',
                          'scaling_cur_freq': '300000', 'stats/total_trans': str(idx)}
                values += [policy + '/' + k + '=' + x + '\n' for k, x in fields.items()]
            (p / 'cpufreq.txt').write_text(''.join(values))
            (p / 'cpuidle-driver.txt').write_text('psci_idle\n')
            (p / 'cpuidle.txt').write_text(''.join(
                f'cpu{i}/cpuidle/state1/usage={idx+1}\n'
                f'cpu{i}/cpuidle/state1/time={idx*1000}\n'
                f'cpu{i}/cpuidle/state1/rejected=0\n' for i in range(8)))
            (p / 'dmesg.txt').write_text('smp: Brought up 1 node, 8 CPUs\nInitialized CPU PM domain topology using OSI mode\n')
        c.check(stage, *paths)
    thermal = paths[1] / 'cpu-thermal.txt'
    old = thermal.read_text()
    thermal.write_text(old.replace('40000', '80000'))
    try:
        c.check('C1', *paths)
    except AssertionError:
        pass
    else:
        raise AssertionError('temperature stop threshold accepted')
    thermal.write_text(old)
    (paths[1] / 'dmesg.txt').write_text('smp: Brought up 1 node, 8 CPUs\nfailed to set OSI mode: -3\n')
    try:
        c.check('C1', *paths)
    except AssertionError:
        pass
    else:
        raise AssertionError('OSI failure accepted')
print('PASS: stage isolation, input guards, synthetic SMP/DVFS/OSI evidence, reset/missing-core/temperature/OSI rejection')
