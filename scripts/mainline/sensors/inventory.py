#!/usr/bin/env python3
"""inventory.py <vendor etc/sensors/config> <persist sensors dir>
The A9's physical sensors from the stock Qualcomm SEE registry (offline).

Stock A9 sensors are NOT AP-side devices: there is no sensor node in the stock
DT/DTBO; every driver runs inside the ADSP sensors PD (SEE). The vendor
ships registry JSONs for several candidate parts (bengal QRD); the ADSP probes
them and the factory wrote what it found into persist (sensors_list.txt plus
the registry with calibration). This prints, per candidate: present in the
persist registry, in sensors_list.txt, bus (SEE bus_instance), 7-bit address,
IRQ (TLMM pin when irq_is_chip_pin=1), rails, orientation, calibration keys."""
import json, os, sys

cfg_dir, pers = sys.argv[1:3]
reg_dir = os.path.join(pers, 'registry', 'registry')
listed = open(os.path.join(pers, 'sensors_list.txt')).read().split()
reg_names = set(os.listdir(reg_dir))

def flat(o, p=''):
    if isinstance(o, dict):
        if 'type' in o and 'data' in o:
            yield p, o['data']; return
        for k, v in o.items():
            if k != 'owner':
                yield from flat(v, p + '/' + k if p else k)

def reg(name):
    path = os.path.join(reg_dir, name)
    if not os.path.exists(path): return {}
    return {k.split('/')[-1]: v for k, v in flat(json.load(open(path)))}

alljson = {f: json.load(open(os.path.join(cfg_dir, f))) for f in sorted(os.listdir(cfg_dir)) if f.endswith('.json')}
def streams(drv):
    out = set()
    for d in alljson.values():
        for k, _ in flat(d.get(drv, {})):
            if k.startswith('.'): out.add(k.split('/')[0].lstrip('.'))
    return sorted(out)
rows = []
for f in sorted(os.listdir(cfg_dir)):
    if not f.endswith('.json'): continue
    d = json.load(open(os.path.join(cfg_dir, f)))
    for top, v in d.items():
        if not top.endswith('_platform'): continue
        drv = top[:-len('_platform')]
        c = {k.split('/')[-1]: x for k, x in flat(v) if k.startswith('.config/')}
        if 'slave_config' not in c: continue
        o = {k.split('/')[-1]: x for k, x in flat(v) if k.startswith('.orient/')}
        p_cfg = reg(top + '.config')
        cal = sorted(n[len(top) + 1:] for n in reg_names if n.startswith(top + '.') and 'fac_cal' in n)
        subs = streams(drv)
        rows.append([drv, f, 'yes' if top + '.config' in reg_names else 'no', ','.join(subs),
                     c.get('bus_instance'), '0x%02x' % int(c['slave_config']),
                     ('tlmm' if c.get('irq_is_chip_pin') == '1' else 'pin') + str(c.get('dri_irq_num')),
                     c.get('vdd_rail', '-') + '|' + c.get('vddio_rail', '-'),
                     ''.join(o.get(a, '?') for a in ('x', 'y', 'z')) if o else '-',
                     ','.join(cal) or '-',
                     'same' if p_cfg and all(p_cfg.get(k) == c.get(k) for k in ('bus_instance', 'slave_config', 'dri_irq_num')) else ('-' if not p_cfg else 'DIFFERS')])
print('# sensors_list.txt (factory): ' + ' '.join(listed))
print('\t'.join(['driver', 'json', 'in_persist', 'streams', 'bus', 'addr7', 'irq', 'rails', 'orient', 'fac_cal', 'persist_cfg']))
for r in rows: print('\t'.join(str(x) for x in r))
