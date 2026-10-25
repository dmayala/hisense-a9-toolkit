#!/usr/bin/env python3
"""Offline A/B/C evidence gate. Reads saved captures; never prints identifiers.
Exit 0=all requested evidence passed; 1=failed/missing evidence. Requires the
proposed CPU capture fields plus existing dmesg/interrupts/uname/cmdline.
"""
import argparse
import pathlib
import re


def cpulist(value):
    out = set()
    for part in value.strip().replace(',', ' ').split():
        span = part.split('-')
        out.update(range(int(span[0]), int(span[-1]) + 1))
    return out


def fields(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)


def check(stage, early, late):
    wanted = set(range(8))
    for capture in [early, late]:
        assert cpulist(fields(capture / 'cpus.txt')['online']) == wanted, 'not all 8 CPUs online'
        ids = set(map(int, re.findall(r'^processor\s*:\s*(\d+)', (capture / 'cpuinfo.txt').read_text(), re.M)))
        assert ids == wanted, 'cpuinfo does not enumerate 8 CPUs'
        irq = (capture / 'interrupts.txt').read_text().splitlines()
        assert irq[0].split() == ['CPU' + str(i) for i in range(8)], 'missing per-CPU IRQ columns'
        totals = [0] * 8
        for line in irq[1:]:
            columns = line.split(':', 1)[-1].split()
            if len(columns) >= 8 and all(x.isdigit() for x in columns[:8]):
                totals = [x + int(y) for x, y in zip(totals, columns[:8])]
        assert all(totals), 'one or more CPUs have no observed IRQ/IPI activity'
        text = (capture / 'dmesg.txt').read_text()
        assert re.search(r'(Brought up.*8 CPUs|Total of 8 processors activated)', text), 'no 8-CPU boot log'
        bad = r'Kernel panic|Internal error:|Oops:|SError|soft lockup|hard LOCKUP|rcu.*stall|blocked for more than|failed to boot CPU|failed to come online|CPU.*failed to PSCI idle|failed to set OSI mode|failed to create CPU PM domains'
        assert not re.search(bad, text, re.I), 'panic/CPU/OSI fault in dmesg'
        # Known early informational failed-PC-mode warning is not an OSI pass/fail proxy.
        cmd = (capture / 'cmdline.txt').read_text().split()
        for key, expected in [('maxcpus', '8'), ('cpuidle.off', '1' if stage != 'C1' else None),
                              ('cpufreq.off', '1' if stage == 'A' else None)]:
            values = [x.split('=', 1)[1] for x in cmd if x.startswith(key + '=')]
            assert values == ([expected] if expected else []), 'unexpected/duplicate CPU command-line setting'
    boot_id = (early / 'boot-id.txt').read_text().strip()
    assert re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id), 'missing/malformed boot identity'
    assert boot_id == (late / 'boot-id.txt').read_text().strip(), 'boot changed/reset'
    uptime = lambda c: float((c / 'uname.txt').read_text().splitlines()[-1].split()[0])
    assert uptime(late) - uptime(early) >= 300, 'less than 300 seconds of continuous observation'
    a, b = fields(early / 'cpufreq.txt'), fields(late / 'cpufreq.txt')
    if stage == 'A':
        assert not a and not b, 'cpufreq policy unexpectedly active in A'
        return
    for capture in [early, late]:
        thermal = fields(capture / 'cpu-thermal.txt')
        sensors = {value: key.rsplit('/', 1)[0] for key, value in thermal.items()
                   if key.endswith('/type') and re.fullmatch(r'cpu\d+-thermal', value)}
        assert set(sensors) >= {'cpu4-thermal', 'cpu5-thermal', 'cpu6-thermal',
                               'cpu7-thermal', 'cpu45-thermal', 'cpu67-thermal',
                               'cpu0123-thermal'}, 'missing expected CPU TSENS observations'
        assert all(-40000 < int(thermal[zone + '/temp']) < 80000
                   for zone in sensors.values()), 'CPU temperature invalid or at conservative 80 C stop threshold'
    for data in [a, b]:
        assert {x.split('/')[0] for x in data} == {'policy0', 'policy4'}, 'need two policies'
        for policy, cpus in [('policy0', set(range(4))), ('policy4', set(range(4, 8)))]:
            get = lambda name: data[policy + '/' + name]
            assert get('scaling_driver') == 'qcom-cpufreq-hw', 'wrong driver'
            assert get('scaling_governor') == ('userspace' if stage == 'B0' else 'schedutil'), 'wrong governor'
            assert cpulist(get('related_cpus')) == cpus, 'wrong frequency domain'
            assert get('scaling_available_frequencies').split(), 'missing LUT-derived frequencies'
            assert 0 < int(get('scaling_min_freq')) <= int(get('scaling_cur_freq')) <= int(get('scaling_max_freq')), 'frequency outside policy limits'
    if stage != 'B0':
        moved = False
        for policy in ['policy0', 'policy4']:
            key = policy + '/stats/total_trans'
            moved |= int(b.get(key, '0')) > int(a.get(key, '0'))
            moved |= a[policy + '/scaling_cur_freq'] != b[policy + '/scaling_cur_freq']
        assert moved, 'no frequency transition demonstrated; collect a workload observation'
    if stage == 'C1':
        for capture in [early, late]:
            assert (capture / 'cpuidle-driver.txt').read_text().strip() == 'psci_idle', 'no PSCI idle driver'
            assert 'Initialized CPU PM domain topology using OSI mode' in (capture / 'dmesg.txt').read_text(), 'OSI not confirmed'
        x, y = fields(early / 'cpuidle.txt'), fields(late / 'cpuidle.txt')
        for cpu in range(8):
            key = f'cpu{cpu}/cpuidle/state1/usage'
            assert int(y[key]) > int(x[key]), 'core idle coverage incomplete'
            key = f'cpu{cpu}/cpuidle/state1/time'
            assert int(y[key]) > int(x[key]), 'no measured core idle residency'
            key = f'cpu{cpu}/cpuidle/state1/rejected'
            assert int(y[key]) == int(x[key]) == 0, 'CPU idle entries rejected'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['A', 'B0', 'B1', 'C1'])
    p.add_argument('early', type=pathlib.Path)
    p.add_argument('late', type=pathlib.Path)
    a = p.parse_args()
    try:
        check(a.stage, a.early, a.late)
    except (AssertionError, KeyError, ValueError, OSError) as e:
        # No raw exception/path content: captures may include device identifiers.
        print('FAIL/INCOMPLETE: ' + (str(e) if isinstance(e, AssertionError) else 'missing or malformed capture fields'))
        raise SystemExit(1)
    print('PASS: stage ' + a.stage + ' evidence gate (no claim beyond the observation window)')


if __name__ == '__main__':
    main()
