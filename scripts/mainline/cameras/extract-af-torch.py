#!/usr/bin/env python3
"""Reproduce the A9 stock AF/torch facts, offline. No device or downloads.

Addresses are file offsets in the Parameter Parser V2 rear module. The
camera.qcom.so excerpt is read-only and retains its original virtual addresses.
Outputs and proprietary inputs stay outside git.
"""
import argparse, hashlib, importlib.util, json, struct
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--rear',type=Path,required=True)
p.add_argument('--camera-hal',type=Path,help='optional existing stock camera.qcom.so')
p.add_argument('--out',type=Path,required=True)
a=p.parse_args()
s=importlib.util.spec_from_file_location('stock',HERE/'extract-stock.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
module=m.Module(a.rear)
assert hashlib.sha256(module.data).hexdigest()=='c1b737cabb0d629b54573fc26de77417d7611f0cb1962176ddb0d5e486424d03'
root=module.values(4,'actuatorDriver')
register=module.values(root[16],'registerParam')
assert register==[1,2,3,0,1,0,0,0,0]
init=module.values(root[18],'regSetting');rows=[]
for i in range(root[17]):
    slave,_,reg,n,data,atype,dtype,op,delayed,delay=init[i*10:i*10+10]
    assert slave==0 and n==1 and atype==dtype==1 and op==0
    rows.append(dict(register=reg,value=module.values(data,'registerData')[0],
                     delay_us=module.values(delay,'delayUs')[0] if delayed else 0))
region=module.values(root[23],'region')
step=struct.unpack('<f',struct.pack('<I',region[2]))[0]
initial=root[21];steps=region[0]-region[1]
result=dict(rear_sha256=hashlib.sha256(module.data).hexdigest(),
    actuator_offset=hex(module.nodes[4][1]),address_7bit=root[7]>>1,
    dac_register=register[2],init_offset=hex(module.nodes[root[18]][1]),init=rows,
    initial_dac=initial,region_offset=hex(module.nodes[root[23]][1]),
    step_count=steps,code_per_step=step,region_qvalue=region[3],
    fallback_dac_min=initial,fallback_dac_max=initial+round((steps-1)*step),
    minimum_focus_metres=struct.unpack_from('<d',module.data,0x17df0)[0],
    focus_distance_calibration='uncalibrated; no unit EEPROM read',
    flash_driver_offset=hex(module.nodes[6][1]))
a.out.mkdir(parents=True,exist_ok=True)
if a.camera_hal:
    b=a.camera_hal.read_bytes()
    assert hashlib.sha256(b).hexdigest()=='35cd15607bfc8746be70f3e48342fab0f3d0839dd9b6e1fa483509cee9d4698d'
    excerpt=b[0x83a27c:0x83a3b0]
    result['step_loop']=dict(parent_sha256=hashlib.sha256(b).hexdigest(),
        va_start='0x83a27c',va_end_exclusive='0x83a3b0',sha256=hashlib.sha256(excerpt).hexdigest(),
        limitation='partial basic-block excerpt; not a complete function')
    (a.out/'step-loop-83a27c.bin').write_bytes(excerpt)
(a.out/'stock-af-torch.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
