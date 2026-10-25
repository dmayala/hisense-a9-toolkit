#!/usr/bin/env python3
"""Summarize actual idle2 captures; absent hardware counters remain unavailable."""
from pathlib import Path
import json
import re
import statistics
import sys


def parse(text):
    out = dict(boot=re.findall(r'^BOOT (.+)$', text, re.M), power={}, cpu={}, soc={})
    clocks = re.findall(r'^(?:START_|END_)?CLOCK boot_ns=(\d+) mono_ns=(\d+) counter_hz=(\d+)$', text, re.M)
    if clocks:
        out['boot_ns'] = sum(int(c[0]) for c in clocks) / len(clocks)
        out['mono_ns'] = sum(int(c[1]) for c in clocks) / len(clocks)
        out['snapshot_span_s'] = (int(clocks[-1][0])-int(clocks[0][0])) / 1e9
        out['counter_hz'] = int(clocks[0][2])
    for line in text.splitlines():
        parts = line.split('\t')
        if len(parts) == 4 and parts[0] == 'POWER': out['power'].setdefault(parts[1], {})[parts[2]] = parts[3]
        if len(parts) == 5 and parts[0] == 'CPUIDLE': out['cpu'].setdefault('/'.join(parts[1:3]), {})[parts[3]] = parts[4]
    for path, data in re.findall(r'^SOC_BEGIN\t([^\n]+)\n(.*?)^SOC_END$', text, re.M | re.S):
        fields = {k: int(v) for k,v in re.findall(r'^(Count|Accumulated Duration|Last Entered At|Last Exited At):\s*(\d+)\s*$', data, re.M)}
        if fields: out['soc'][path] = fields
        for row in data.splitlines():
            m = re.match(r'(DDR .*?)\s*count:(\d+)\s+Duration \(ticks\):(\d+)', row)
            if m: out['soc'][path+' / '+m[1].strip()] = {'Count':int(m[2]), 'DDR Duration ticks':int(m[3])}
    return out


def integer(value):
    try: return int(value)
    except (ValueError,TypeError): return None


def phase(label, a, b):
    same_boot = bool(a['boot']) and a['boot'] == b['boot']
    duration = (b.get('boot_ns',0)-a.get('boot_ns',0))/1e9
    result = dict(same_boot=same_boot, elapsed_s=duration, soc={}, cpu={}, battery={})
    print(f'\n{label}: elapsed_boottime_s={duration:.3f} same_boot={same_boot}')
    if not same_boot or duration <= 0:
        print('phase deltas unavailable: missing identity/clock or reset'); return result
    print(f"snapshot endpoint uncertainty <= {a.get('snapshot_span_s',0)/2+b.get('snapshot_span_s',0)/2:.3f}s")
    if 'mono_ns' in a and 'mono_ns' in b:
        result['monotonic_s'] = (b['mono_ns']-a['mono_ns'])/1e9
        result['timekeeping_suspend_s'] = duration-result['monotonic_s']
        print(f"timekeeping suspend_s={result['timekeeping_suspend_s']:.3f} (logical timekeeping, not a hardware-state counter)")
    rate = a.get('counter_hz') if a.get('counter_hz') == b.get('counter_hz') else None
    for name, end in b['soc'].items():
        start = a['soc'].get(name, {})
        delta = {k:end[k]-start[k] for k in ['Count','Accumulated Duration','DDR Duration ticks'] if k in start and k in end}
        if not delta: continue
        result['soc'][name] = delta
        info = ' '.join(f'{k}_delta={v}' for k,v in delta.items())
        ticks = delta.get('Accumulated Duration')
        if ticks is not None and ticks >= 0 and rate:
            seconds = ticks/rate
            delta.update(duration_s=seconds, counter_hz=rate)
            info += f' duration_s={seconds:.6f} residency_pct={seconds/duration*100:.3f}'
        print(f'firmware sleep {name}: {info}')
    if not result['soc']: print('firmware sleep counters: unavailable (see raw SOC_STATS_UNAVAILABLE/read errors)')
    for name, end in b['cpu'].items():
        start = a['cpu'].get(name, {})
        delta = {}
        for field in ['usage','time','rejected','above','below','s2idle/usage','s2idle/time']:
            x,y = integer(start.get(field)),integer(end.get(field))
            if x is not None and y is not None: delta[field] = y-x
        result['cpu'][name] = delta
        print(f"kernel cpuidle {name} {end.get('name','?')}: {delta} time_us; disable={end.get('disable','?')}")
    if not result['cpu']: print('cpuidle states: unavailable/driver not registered (see IDLE_CONTROL and PSCI_STARTUP)')
    for name, end in b['power'].items():
        start = a['power'].get(name, {})
        if end.get('type') != 'Battery' and name not in ('battery','bms'): continue
        x,y = integer(start.get('charge_counter')),integer(end.get('charge_counter'))
        values = dict(status_before=start.get('status'),status_after=end.get('status'),
                      endpoint_current_uA_before=integer(start.get('current_now')),
                      endpoint_current_uA_after=integer(end.get('current_now')))
        if x is not None and y is not None:
            values.update(charge_counter_delta_uAh=y-x, net_charge_current_uA=(y-x)*3600/duration)
            print(f'battery {name}: charge_counter delta={y-x}uAh net_average={values["net_charge_current_uA"]:.1f}uA (+ net charging)')
        else: print(f'battery {name}: charge_counter unavailable; suspend/phase average current=N/A')
        print(f'battery {name}: endpoint current_now={values["endpoint_current_uA_before"]}->{values["endpoint_current_uA_after"]}uA status={values["status_before"]}->{values["status_after"]}')
        result['battery'][name] = values
    return result


def main():
    out = Path(sys.argv[1])
    texts = {n:(out/(n+'.txt')).read_text(errors='replace') for n in ['awake-before','awake-after','awake-current','before','after']}
    captures = {n:parse(t) for n,t in texts.items() if n != 'awake-current'}
    print('USB remains attached: signed battery charge flow is NOT total device power consumption.')
    print('A9 PMI632 current_now is positive into battery (charging), negative out. Endpoint samples occur awake, not inside CPU-off intervals.')
    result = {'awake':phase('awake control',captures['awake-before'],captures['awake-after']),
              'idle':phase('natural idle/suspend window',captures['before'],captures['after'])}
    samples = {}
    for name,value in re.findall(r'^POWER\t([^\t]+)\tcurrent_now\t(-?\d+)\s*$',texts['awake-current'],re.M): samples.setdefault(name,[]).append(int(value))
    for name,values in samples.items(): print(f'awake sampled current {name}: n={len(values)} mean={statistics.mean(values):.1f} min={min(values)} max={max(values)}uA')
    result['awake_current_samples_uA'] = samples
    for n in ['before','after']:
        print(n+' supply status: '+json.dumps(captures[n]['power'],sort_keys=True))
    before,after = texts['before'],texts['after']
    a,b = [dict(re.findall(r'^STAT ([^=]+)=(.*)$',t,re.M)) for t in [before,after]]
    for key in sorted(a.keys()|b.keys()):
        x,y = integer(a.get(key)),integer(b.get(key))
        print(f'suspend_stat {key}: {a.get(key,"?")} -> {b.get(key,"?")}' + (f' delta={y-x}' if x is not None and y is not None else ''))
    def wakes(text):
        return {p[1]:(p[2],*map(int,p[3:])) for line in text.splitlines() if line.startswith('WAKE\t') and len(p:=line.split('\t'))==6}
    wa,wb = wakes(before),wakes(after)
    for key,(name,*values) in wb.items():
        if key in wa:
            delta = [y-x for x,y in zip(wa[key][1:],values)]
            if any(delta): print(f'wake {key} {name}: active/event/wake delta={delta}')
    begin = end = pending = None; intervals = []
    for line in after.splitlines():
        m = re.search(r'(\d+\.\d+): (?:tracing_mark_write: (PLAIN_IDLE_BEGIN|PLAIN_IDLE_END)|suspend_resume: machine_suspend\[1\] (begin|end))',line)
        if not m: continue
        t = float(m[1])
        if m[2] == 'PLAIN_IDLE_BEGIN': begin=t
        elif m[2] == 'PLAIN_IDLE_END': end=t
        elif m[3] == 'begin': pending=t
        elif pending is not None: intervals.append((pending,t));pending=None
    overruns = sum(map(int,re.findall(r'^\s*(?:commit )?overrun:\s*(\d+)',after,re.M)))
    valid = result['idle']['same_boot'] and not overruns and begin is not None and end is not None and end>begin and pending is None
    if valid:
        asleep = sum(max(0,min(y,end)-max(x,begin)) for x,y in intervals)
        result['logical_s2idle'] = dict(window_s=end-begin,s2idle_s=asleep,intervals=len(intervals),residency_pct=100*asleep/(end-begin),trace_clock='boot')
        print('logical s2idle: '+json.dumps(result['logical_s2idle']))
    else: print(f'logical s2idle unavailable: trace_overruns={overruns} markers={begin},{end} pending={pending}')
    print('Firmware sleep counters and kernel CPU-state accounting are separate evidence; neither is an external rail-power measurement.')
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0 if valid else 1


if __name__ == '__main__': sys.exit(main())
