#!/usr/bin/env python3
"""Summarise the thermal-zones of a decompiled stock DT (dtc -O dts output):
zone, sensor cells, polling, trips (name temp/1000 hyst type), cooling maps.
Usage: stock-zones.py base_0.dts [dtbo_0.dts]. Read-only, offline."""
import re, sys

def zones(path):
    out, stack, cur = [], [], None
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        m = re.match(r'^([A-Za-z0-9_,@.-]+(?:: [A-Za-z0-9_,@.-]+)?) \{$', s)
        if m:
            name = m.group(1).split(': ')[-1]
            stack.append(name)
            if len(stack) >= 2 and stack[-2] == 'thermal-zones':
                cur = {'zone': name, 'sens': '', 'trips': [], 'maps': [], 'poll': ''}
                out.append(cur)
            continue
        if s == '};':
            if stack:
                stack.pop()
            continue
        if cur is None or 'thermal-zones' not in stack:
            continue
        def num(v):
            v = v.strip()
            return int(v, 16) if v.startswith('0x') else int(v)
        if s.startswith('thermal-sensors'):
            cur['sens'] = s.split('=', 1)[1].strip(' <>;')
        elif s.startswith('polling-delay ='):
            cur['poll'] = str(num(s.split('<')[1].split('>')[0]))
        elif s.startswith('temperature'):
            t = num(s.split('<')[1].split('>')[0])
            if t >= 0x80000000:
                t -= 1 << 32
            cur['trips'].append([stack[-1], t, None, None])
        elif s.startswith('hysteresis') and cur['trips']:
            cur['trips'][-1][2] = num(s.split('<')[1].split('>')[0])
        elif s.startswith('type') and cur['trips']:
            cur['trips'][-1][3] = s.split('"')[1]
        elif s.startswith('trip ='):
            cur['maps'].append([stack[-1], s.split('=', 1)[1].strip(' <>;'), ''])
        elif s.startswith('cooling-device') and cur['maps']:
            cur['maps'][-1][2] = s.split('=', 1)[1].strip(' <>;')
    return out

for p in sys.argv[1:]:
    print(f'# {p}')
    for z in zones(p):
        trips = ', '.join(f'{n} {t} h{h} {ty}' for n, t, h, ty in z['trips'])
        print(f"{z['zone']:28s} sens<{z['sens']}> poll={z['poll']} | {trips}")
        for n, tr, cd in z['maps']:
            print(f"{'':30s}map {n}: trip<{tr}> cdev<{cd}>")
