#!/usr/bin/env python3
"""Exact S1 vs measured D1 DT/config checks. Offline only, never trial admission."""
import argparse
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE=Path(__file__).resolve().parent
D0=SourceFileLoader('s1_d0_dt',str(HERE.parent/'adsp/check-d0-dt.py')).load_module()
FAST=D0.RP+'/glink-edge/fastrpc'

def check_dt(baseline,candidate):
    old,old_res=baseline; new,new_res=candidate
    if old.keys()!=new.keys() or old_res!=new_res:
        raise ValueError('S1 must preserve every D1 node and reservation')
    delta={(path,prop):(old[path].get(prop),new[path].get(prop))
           for path in old for prop in old[path].keys() | new[path].keys()
           if old[path].get(prop)!=new[path].get(prop)}
    if delta!={(FAST,'status'):(b'disabled\0',b'okay\0')}:
        raise ValueError('S1 DT delta must be exactly ADSP FastRPC disabled -> okay')
    expected={3:0x1c3,4:0x1c4,5:0x1c5,6:0x1c6,7:0x1c7}
    import struct
    for cb,sid in expected.items():
        node=new[FAST+f'/compute-cb@{cb}']
        if node['reg']!=struct.pack('>I',cb) or node['iommus'][4:]!=struct.pack('>II',sid,0):
            raise ValueError('upstream ADSP FastRPC compute context identity changed')
    if new[FAST].get('label')!=b'adsp\0' or new[FAST].get('qcom,non-secure-domain')!=b'':
        raise ValueError('unexpected FastRPC domain/security identity')

def config_map(text):
    result={}
    for line in text.splitlines():
        if line.startswith('CONFIG_'): result[line.split('=')[0]]=line
        elif line.startswith('# CONFIG_'): result[line.split()[1]]=line
    return result

def check_config(old,new):
    before,after=config_map(old),config_map(new)
    delta={k:(before.get(k),after.get(k)) for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}
    if delta!={'CONFIG_QCOM_FASTRPC':('# CONFIG_QCOM_FASTRPC is not set','CONFIG_QCOM_FASTRPC=m')}:
        raise ValueError('S1 resolved config delta must be exactly QCOM_FASTRPC=m: '+repr(delta))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--d1-dtb',type=Path); ap.add_argument('--s1-dtb',type=Path)
    ap.add_argument('--d1-config',type=Path); ap.add_argument('--s1-config',type=Path)
    a=ap.parse_args()
    if a.d1_dtb and a.s1_dtb: check_dt(D0.read_fdt(a.d1_dtb),D0.read_fdt(a.s1_dtb))
    if a.d1_config and a.s1_config: check_config(a.d1_config.read_text(),a.s1_config.read_text())
    if not ((a.d1_dtb and a.s1_dtb) or (a.d1_config and a.s1_config)): ap.error('provide a complete pair')
    print('S1-KERNEL-DELTA PASS (offline only)')

if __name__=='__main__': main()
