#!/usr/bin/env python3
"""s2c2-judge.py -- S2c-2 (adaptive frontlight) sample judge. Offline; reads the sampler stream.

  s2c2-judge.py stop  <samples.txt> <baseline.json>   one line: OK | STOP <reason>   (rc 0 / 3)
  s2c2-judge.py final <samples.txt> <baseline.json>   the pass/fail table + PASS|FAIL <why> (rc 0 / 1)

Sampler lines (shell user on the phone, run-s2c2-attempt.sh):
  S <uptime> short=<p> open=<p> wc=<led> ww=<led> state=<s> hal=<pid> ss=<system_server pid>   every 1 s
  D <uptime> mode=<0|1> brt=<0..255> lux=<mAmbientLux> strat=<...>                            every 5 s
  T <uptime> <zone>=<milli-C> ...                                                              every 60 s
  E <uptime> gate=<vendor.a9ml.eink.gate> fault=<vendor.a9ml.eink.fault>                      every 60 s
  W <uptime> trig=<n>   init's "processing action (sys.linevibrator_short=*)" lines in the kernel log
                        buffer so far (every property WRITE, not only the 1 Hz samples)  every 60 s
baseline.json: {"hal": pid, "ss": pid, "switch_uptime": s, "sampler_start": s, "short_before": v, "thermal": {zone: mC},
                "eink": {"gate": g, "fault": f}}

Stops (any): HAL or system_server pid change (a sensors/sensorservice restart); sensors state not
`up`; a write storm = sustained > 1 write/s: init's sys.linevibrator_short trigger count rising by
more than 60 per minute in two consecutive W samples, or (what 1 Hz sampling can see) the sampled
property or white LED changing in >= 50 of 60 samples in two consecutive minutes; any thermal zone valid at baseline (0..120 C) rising
more than 15 C over baseline or above 75 C; the E Ink gate turning faulted/refused or the E Ink
fault property changing from its baseline; no new S sample
for 30 s (the sampler/transport died).

Pass (final): no stop; auto mode on (every D after the switch has mode=1); the LED tracks the
property: white LED == floor(short * 75 / 100) for 0 <= short <= 2200, and UNCHANGED when short is
above 2200 or not a number (compat rejects it; eink-compat.c number(text, 2200), lamps[1]) in
>= 98 % of S samples after the switch, counting a sample whose LED equals the previous sample's
expected value (one-sample lag) as tracking; brightness responds to the lux: lux > 0 in the D
samples and, after the switch, either sys.linevibrator_short left its manual value or the display's
brightness strategy is automatic (strat contains 'Automatic').
"""
import json
import re
import sys

KV = re.compile(r'(\w+)=(\S*)')


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(path):
    S, D, T, E, Wl = [], [], [], [], []
    for line in open(path, errors='replace'):
        line = line.replace('\r', '')          # adb may add CR; the host pipeline strips it too (review 79n B1)
        p = line.split()
        if len(p) < 2 or p[0] not in ('S', 'D', 'T', 'E', 'W') or num(p[1]) is None:
            continue
        rec = {'t': float(p[1]), **dict(KV.findall(line))}
        {'S': S, 'D': D, 'T': T, 'E': E, 'W': Wl}[p[0]].append(rec)
    return S, D, T, E, Wl


def expected(short):
    """compat (eink-compat.c number(text, 2200)): a non-numeric value or one above 2200 is REJECTED and the
    white LED keeps its last value (no clamp; the clamp is for the edge LEDs only) -- review 79n N1."""
    v = num(short)
    if v is None or v < 0 or v > 2200 or v != int(v):
        return None
    return int(v) * 75 // 100


def storms(S, key):
    """True if `key` changed in >= 50 of the ~60 samples of each of two consecutive 60 s windows."""
    if not S:
        return False
    changes = [S[i]['t'] for i in range(1, len(S)) if S[i].get(key) != S[i - 1].get(key)]
    t0, t1 = S[0]['t'], S[-1]['t']
    wins = []
    w = t0
    while w <= t1:
        wins.append(sum(1 for c in changes if w <= c < w + 60) >= 50)
        w += 60
    return any(a and b for a, b in zip(wins, wins[1:]))


def trigger_storm(Wl):
    rates = []
    for a, b in zip(Wl, Wl[1:]):
        x, y = num(a.get('trig')), num(b.get('trig'))
        dt = b['t'] - a['t']
        rates.append(x is not None and y is not None and dt > 0 and (y - x) * 60 / dt > 60)
    return any(a and b for a, b in zip(rates, rates[1:]))


def stop_reason(S, D, T, E, base, now=None, Wl=()):
    if not S:
        # review 79n B1: only a stop once the sampler has had > 30 s (phone uptime) to deliver a line
        start = base.get('sampler_start')
        if now is not None and start is not None and now - start <= 30:
            return None
        return 'no samples'
    for r in S:
        if r.get('hal') != str(base['hal']):
            return f"HAL pid {base['hal']} -> {r.get('hal')} at {r['t']}"
        if r.get('ss') != str(base['ss']):
            return f"system_server pid {base['ss']} -> {r.get('ss')} at {r['t']}"
        if r.get('state') != 'up':
            return f"sensors state {r.get('state')} at {r['t']}"
    for key in ('short', 'wc'):
        if storms(S, key):
            return f'write storm on {key} (changing in >= 50/60 samples, 2 min)'
    if trigger_storm(Wl):
        return 'write storm: sys.linevibrator_short written > 60/min for 2 min (init triggers)'
    for r in T:
        for z, b in base.get('thermal', {}).items():
            v = num(r.get(z))
            if v is not None and (v > b + 15000 or v > 75000):
                return f'thermal {z} {b} -> {v} at {r["t"]}'
    eb = base.get('eink', {})
    for r in E:
        if r.get('gate') in ('faulted', 'refused') and r.get('gate') != eb.get('gate'):
            return f'E Ink gate {eb.get("gate")} -> {r.get("gate")} at {r["t"]}'
        if r.get('fault') != eb.get('fault', '-'):
            return f'E Ink fault {eb.get("fault")} -> {r.get("fault")} at {r["t"]}'
    if now is not None and now - S[-1]['t'] > 30:
        return f'no sample for {now - S[-1]["t"]:.0f} s'
    return None


def main():
    mode, path, bpath = sys.argv[1], sys.argv[2], sys.argv[3]
    base = json.load(open(bpath))
    S, D, T, E, Wl = parse(path)
    now = float(sys.argv[4]) if len(sys.argv) > 4 else None
    why = stop_reason(S, D, T, E, base, now, Wl)
    if mode == 'stop':
        print(f'STOP {why}' if why else 'OK')
        sys.exit(3 if why else 0)
    sw = base['switch_uptime']
    after = [r for r in S if r['t'] >= sw]
    Da = [r for r in D if r['t'] >= sw]
    ok_led, n = 0, 0
    held = None          # the LED value compat last applied (at the last sample with a valid short)
    for i, r in enumerate(S):
        e, led = expected(r.get('short')), num(r.get('wc'))
        if led is None:
            continue
        counted = r['t'] >= sw
        if e is None:                      # rejected by compat: the LED must keep the value last applied
            if counted and held is not None:
                n += 1
                ok_led += led == held
            continue
        if counted:
            n += 1
            prev = expected(S[i - 1].get('short')) if i else None
            ok_led += led == e or (prev is not None and led == prev)
        held = led
    track = n and ok_led / n
    lux = [num(r.get('lux')) for r in Da if num(r.get('lux')) is not None]
    shorts = {r.get('short') for r in after}
    responded = (any(v != str(base['short_before']) for v in shorts)
                 or any('Automatic' in (r.get('strat') or '') for r in Da))
    auto = bool(Da) and all(r.get('mode') == '1' for r in Da)
    pairs = sorted({(r.get('lux'), r.get('brt')) for r in Da})
    rows = [
        ('no stop condition', why is None, why or 'none'),
        ('auto mode on after the switch', auto, f'{len(Da)} display samples'),
        ('LED tracks short x 0.75 (>= 98 %)', bool(n) and track >= 0.98, f'{ok_led}/{n} samples'),
        ('lux > 0 seen', bool(lux) and max(lux) > 0, f'lux min {min(lux) if lux else None} max {max(lux) if lux else None}'),
        ('brightness responded (property left the manual value, or strategy automatic)', responded,
         f"short before {base['short_before']}, after {sorted(shorts)[:6]}"),
    ]
    for name, good, detail in rows:
        print(f"{'ok  ' if good else 'FAIL'} {name}: {detail}")
    print('lux -> screen_brightness pairs (after switch):', pairs[:20])
    tr = [num(r.get('trig')) for r in Wl if num(r.get('trig')) is not None]
    print('sys.linevibrator_short writes (init triggers) over the run:', (tr[-1] - tr[0]) if len(tr) > 1 else 'n/a')
    bad = [name for name, good, _ in rows if not good]
    print('S2C2-RESULT ' + ('PASS' if not bad else 'FAIL ' + '; '.join(bad)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
