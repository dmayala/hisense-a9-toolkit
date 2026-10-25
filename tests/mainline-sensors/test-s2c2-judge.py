#!/usr/bin/env python3
"""Offline cases for s2c2-judge.py (synthetic sampler streams)."""
import json, subprocess, sys, tempfile
from pathlib import Path
J = Path(__file__).resolve().parents[2] / 'scripts/mainline/sensors/s2c2-judge.py'
BASE = {'hal': 2002, 'ss': 1515, 'switch_uptime': 100, 'sampler_start': 88, 'short_before': 300,
        'thermal': {'z0': 40000}, 'eink': {'gate': '-', 'fault': '-'}}

def stream(n=600, short=lambda t: 300 if t < 105 else 640, led=None, lux=80, mode='1', hal=2002, ss=1515,
           state='up', temp=41000, fault='-', gate='-', strat='AutomaticBrightnessStrategy', trig=lambda t: t // 30):
    out = []
    for i in range(n):
        t = 90 + i
        s = short(t)
        w = led(t, s) if led else s * 75 // 100
        out.append(f'S {t}.00 short={s} open=0 wc={w} ww=0 state={state if t < 400 else state} hal={hal(t) if callable(hal) else hal} ss={ss} ')
        if i % 5 == 0:
            out.append(f'D {t}.10 mode={mode if t >= 100 else 0} brt=104 lux={lux} strat={strat if t >= 100 else "Fallback"}')
        if i % 60 == 0:
            out.append(f'T {t}.20 z0={temp(t) if callable(temp) else temp}')
            out.append(f'E {t}.30 gate={gate(t) if callable(gate) else gate} fault={fault(t) if callable(fault) else fault}')
            out.append(f'W {t}.40 trig={trig(t)}')
    return '\n'.join(out) + '\n'

CASES = {
    'pass': (stream(), 0, 'S2C2-RESULT PASS', 'OK'),
    'pass-one-sample-lag': (stream(led=lambda t, s: (300 * 75 // 100) if t == 105 else min(s, 2200) * 75 // 100), 0, 'S2C2-RESULT PASS', 'OK'),
    'led-not-tracking': (stream(led=lambda t, s: s * 80 // 100), 1, 'LED tracks', 'OK'),
    # review 79n N1: compat REJECTS > 2200 (LED keeps its last value); a clamping LED is not compat
    'above-2200-led-unchanged': (stream(short=lambda t: 300 if t < 105 else 2500, led=lambda t, s: 300 * 75 // 100 if s > 2200 else s * 75 // 100), 0, 'S2C2-RESULT PASS', 'OK'),
    'above-2200-led-clamped': (stream(short=lambda t: 300 if t < 105 else 2500, led=lambda t, s: min(s, 2200) * 75 // 100), 1, 'LED tracks', 'OK'),
    'crlf-lines': (stream().replace('\n', '\r\n'), 0, 'S2C2-RESULT PASS', 'OK'),
    'no-response': (stream(short=lambda t: 300, strat='FallbackBrightnessStrategy'), 1, 'brightness responded', 'OK'),
    'manual-mode': (stream(mode='0'), 1, 'auto mode on', 'OK'),
    'lux-zero': (stream(lux=0), 1, 'lux > 0', 'OK'),
    'storm': (stream(short=lambda t: 300 if t < 105 else (600 + t % 2)), 1, 'write storm', 'STOP write storm'),
    'trigger-storm': (stream(trig=lambda t: 0 if t < 300 else (t - 300) * 3), 1, 'no stop', 'STOP write storm: sys.linevibrator_short written'),
    'one-storm-minute-only': (stream(trig=lambda t: 0 if t < 300 else (180 if t >= 330 else 0)), 0, 'S2C2-RESULT PASS', 'OK'),
    'hal-restart': (stream(hal=lambda t: 2002 if t < 300 else 2600), 1, 'no stop', 'STOP HAL pid'),
    'thermal': (stream(temp=lambda t: 40000 if t < 300 else 56000), 1, 'no stop', 'STOP thermal'),
    'eink-fault': (stream(fault=lambda t: '-' if t < 300 else 'rail:vgh_low'), 1, 'no stop', 'STOP E Ink fault'),
    'eink-gate-faulted': (stream(gate=lambda t: '-' if t < 300 else 'faulted'), 1, 'no stop', 'STOP E Ink gate'),
}
fails = 0
with tempfile.TemporaryDirectory() as td:
    b = Path(td) / 'b.json'; b.write_text(json.dumps(BASE))
    for name, (text, want_rc, want_final, want_stop) in CASES.items():
        p = Path(td) / f'{name}.txt'; p.write_text(text)
        f = subprocess.run([sys.executable, '-I', str(J), 'final', str(p), str(b)], capture_output=True, text=True)
        s = subprocess.run([sys.executable, '-I', str(J), 'stop', str(p), str(b)], capture_output=True, text=True)
        good = f.returncode == want_rc and want_final in f.stdout and s.stdout.startswith(want_stop)
        print(('ok  ' if good else 'FAIL'), name, '|', s.stdout.strip(), '|', f.stdout.strip().splitlines()[-1])
        fails += not good
    # stale sampler: last sample at 689, "now" 760
    p = Path(td) / 'pass.txt'
    s = subprocess.run([sys.executable, '-I', str(J), 'stop', str(p), str(b), '760'], capture_output=True, text=True)
    good = s.stdout.startswith('STOP no sample'); print(('ok  ' if good else 'FAIL'), 'stale-sampler |', s.stdout.strip()); fails += not good
    # review 79n B1: an empty stream is not a stop within 30 s of the sampler start (phone uptime 88)
    e = Path(td) / 'empty.txt'; e.write_text('')
    for now, want in (('103', 'OK'), ('118', 'OK'), ('119', 'STOP no samples')):
        s = subprocess.run([sys.executable, '-I', str(J), 'stop', str(e), str(b), now], capture_output=True, text=True)
        good = s.stdout.strip() == want; print(('ok  ' if good else 'FAIL'), f'empty stream at uptime {now} |', s.stdout.strip()); fails += not good
print('S2C2-JUDGE TEST', 'PASS' if not fails else f'FAIL ({fails})')
sys.exit(1 if fails else 0)
