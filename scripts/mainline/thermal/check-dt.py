#!/usr/bin/env python3
"""check-dt.py base.dts thermal.dts -- assertions on the decompiled DTs."""
import re, sys
base, th = (open(p).read() for p in sys.argv[1:3])
fails = 0
def ok(c, m):
    global fails
    print(("ok   " if c else "FAIL ") + m); fails += not c
def node(src, path):
    """text of the node at a /-separated path (decompiled dtc output)"""
    pos = 0; depth_text = src
    for part in path.strip('/').split('/'):
        m = re.compile(r'\n(\t*)(?:[\w-]+: )?' + re.escape(part) + r' \{\n').search(depth_text, pos)
        if not m: return None
        start = m.end(); ind = m.group(1)
        end = depth_text.index('\n' + ind + '};', start)
        depth_text = depth_text[start:end]; pos = 0
    return depth_text
def phandle(src, path):
    n = node(src, path)
    m = n and re.search(r'^\t*phandle = <(0x[0-9a-f]+)>;', n, re.M)
    return m and m.group(1)
for cpu in ('cpu@0', 'cpu@100'):
    n = node(th, f'/cpus/{cpu}')
    ok(n is not None and '#cooling-cells = <0x02>' in n, f'{cpu} has #cooling-cells = <2>')
for cpu in ('cpu@1', 'cpu@101'):
    ok('#cooling-cells' not in (node(th, f'/cpus/{cpu}') or ''), f'{cpu} not a cooling device (policy CPU only)')
silver, gold = phandle(th, '/cpus/cpu@0'), phandle(th, '/cpus/cpu@100')
for z, cpu in [('cpu4-thermal', gold), ('cpu5-thermal', gold), ('cpu6-thermal', gold), ('cpu7-thermal', gold),
               ('cpu45-thermal', gold), ('cpu67-thermal', gold), ('cpu0123-thermal', silver), ('quiet-therm-thermal', None)]:
    n = node(th, f'/thermal-zones/{z}')
    ok(n is not None, f'zone {z} present')
    if n is None: continue
    maps = re.findall(r'trip = <(0x[0-9a-f]+)>;\n\t*cooling-device = <(0x[0-9a-f]+) 0xffffffff 0xffffffff>;', n)
    ok(len(maps) == 2, f'{z}: 2 cooling maps')
    trips = dict((ph, t) for t, ph in re.findall(r'temperature = <(0x[0-9a-f]+)>;[^}]*?phandle = <(0x[0-9a-f]+)>', n, re.S))
    for tr, cd in maps:
        ok(tr in trips, f'{z}: map trip {tr} is a trip of this zone ({int(trips.get(tr, "0"), 16) // 1000} C)')
        if cpu: ok(cd == cpu, f'{z}: cooling device = {"gold cpu4" if cpu == gold else "silver cpu0"}')
    if z != 'quiet-therm-thermal':
        crit = re.search(r'temperature = <0x1adb0>;\n\t*hysteresis = <0x3e8>;\n\t*type = "critical"', n)
        ok(crit is not None, f'{z}: critical 110 C trip kept')
        ok('polling-delay-passive = <0xfa>' in n, f'{z}: passive polling 250 ms')
q = node(th, '/thermal-zones/quiet-therm-thermal') or ''
qm = re.findall(r'cooling-device = <(0x[0-9a-f]+) ', q)
ok(qm == [gold, silver], 'quiet-therm: gold at 45 C, silver at 48 C')
for lab, reg in [('pa_therm0', '0x4d'), ('quiet_therm', '0x4e'), ('camera_flash_therm', '0x52'), ('emmc_ufs_therm', '0x53')]:
    ok(re.search(r'reg = <' + reg + r'>;\n(?:\t*[^\n]*\n){0,4}?\t*label = "' + lab + '"', th) is not None, f'pm6125 adc {lab} {reg}')
ok(th.count('label = "skin_therm"') == 1, 'pmi632 skin_therm declared once')
ok(th.count('compatible = "generic-adc-thermal"') == 1, 'one generic-adc-thermal sensor (PM6125 only)')
# nothing else changed: the thermal DT minus the additions equals base in node count of reserved gpios etc.
gb = re.search(r'gpio-reserved-ranges = <([^>]*)>', base).group(1)
gt = re.search(r'gpio-reserved-ranges = <([^>]*)>', th).group(1)
ok(gb == gt, 'TLMM reserved ranges unchanged')
for s in ('remoteproc@', 'status = "okay"'):
    ok(base.count(s) == th.count(s), f'count of {s!r} unchanged ({base.count(s)})')
print('PASS' if not fails else f'FAIL ({fails})'); sys.exit(1 if fails else 0)
