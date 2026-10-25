#!/usr/bin/env python3
"""Mutation checks on real compiled D1/D2 trees, not an invented DT fixture."""
import argparse
import copy
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
m=SourceFileLoader('d2_dt_test',str(ROOT/'scripts/mainline/adsp/check-d2-dt.py')).load_module()
p=argparse.ArgumentParser();p.add_argument('baseline',type=Path);p.add_argument('candidate',type=Path)
a=p.parse_args();base=m.read_fdt(a.baseline);good=m.read_fdt(a.candidate)
m.check(base,good);tested=0
def refuse(nodes,reserves=None):
    global tested
    try: m.check(base,(nodes,good[1] if reserves is None else reserves))
    except (AssertionError,ValueError,KeyError,TypeError):tested+=1;return
    raise AssertionError('unsafe mutated DT accepted')
new=good[0]
paths=(new.keys()-base[0].keys())|{'/sound','/sound/mm1-dai-link','/sound/mm1-dai-link/cpu'}
for path in sorted(paths):
    for prop,value in new[path].items():
        if prop=='phandle':continue
        n=copy.deepcopy(new);n[path][prop]=bytes([value[0]^1])+value[1:] if value else b'x'
        refuse(n)
    n=copy.deepcopy(new);n[path]['unexpected-enable']=b'';refuse(n)
for path in sorted(new.keys() & base[0].keys()):
    if path.startswith(('/sound','/__symbols__')):continue
    # Every unchanged register/reservation/enable/firmware property must remain
    # protected when the analog path is introduced.
    for prop in ('reg','status','firmware-name','compatible'):
        value=new[path].get(prop)
        if value:
            n=copy.deepcopy(new);n[path][prop]=bytes([value[0]^1])+value[1:];refuse(n)
n=copy.deepcopy(new);n['/speaker-amp']={'compatible':b'awinic,aw87519\0'};refuse(n)
n=copy.deepcopy(new);n['/a9-d2-codec']['vdd-mic-bias-supply']=m.cells(1);refuse(n)
n=copy.deepcopy(new);n['/a9-d2-codec']['phandle']=new[m.d1.RP]['phandle'];refuse(n)
n=copy.deepcopy(new);n[m.d1.LPI]['status']=b'disabled\0';refuse(n)
# Regression: a register value can equal a renumbered phandle numerically;
# that never makes a change to the register a permitted reference fixup.
old_handles={p['phandle']:path for path,p in base[0].items() if 'phandle' in p}
found=False
for path,props in base[0].items():
    value=props.get('reg',b'')
    if path not in new or path.startswith('/sound') or len(value)%4:continue
    for offset in range(0,len(value),4):
        target=old_handles.get(value[offset:offset+4])
        if target in new:
            changed=new[target].get('phandle')
            if changed and changed!=value[offset:offset+4]:
                n=copy.deepcopy(new);v=new[path]['reg'];n[path]['reg']=v[:offset]+changed+v[offset+4:]
                refuse(n);found=True;break
    if found:break
assert found,'real compiled tree must exercise coincidental integer/phandle regression'
refuse(copy.deepcopy(new),[(0x53800000,0x1000)])
print(f'ADSP-D2-DT MUTATIONS PASS: {tested} refusals on real compiled trees')
