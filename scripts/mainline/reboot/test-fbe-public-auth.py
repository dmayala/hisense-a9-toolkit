#!/usr/bin/env python3
"""Synthetic host CBOR fixtures. Never uses keys, devices or credentials."""
import importlib.util
import json
from pathlib import Path
import subprocess as sp
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('auth', HERE/'fbe-public-auth.py')
auth = importlib.util.module_from_spec(spec); spec.loader.exec_module(auth)


def head(major, value):
    if value < 24: return bytes([(major << 5) | value])
    width = next(w for w in (1,2,4,8) if value < 1 << (8*w))
    return bytes([(major << 5) | {1:24,2:25,4:26,8:27}[width]])+value.to_bytes(width,'big')


def cbor(value):
    if type(value) is bool: return b'\xf5' if value else b'\xf4'
    if type(value) is int: return head(0,value) if value >= 0 else head(1,-1-value)
    if isinstance(value,bytes): return head(2,len(value))+value
    if isinstance(value,list): return head(4,len(value))+b''.join(map(cbor,value))
    return head(5,len(value))+b''.join(cbor(k)+cbor(v) for k,v in value.items())


def main():
    hw = {0x10000002:32,0x20000004:[32],0x20000001:[0,1],0x300002c1:160000,
          0x300002c2:202605,0x300002ce:20220405,0x300002cf:20220405,
          0xa00002bc:b'SYNTHETIC_SECRET',0xa00001f6:b'SYNTHETIC_PRIVATE_IDENTIFIER'}
    # Software metadata deliberately disagrees with hardware.
    chars = cbor({1:hw,0:{0x10000002:128,0x20000004:[1]}})
    raw = cbor([0x4b4d4b44,4,0xdeadbeef,chars,[b'SYNTHETIC_SECRET']])
    result = auth.public_auth(b'pKMblob\0'+raw)
    assert result['hardware_aes']==1 and result['hardware_block_modes']==[32]
    assert result['hardware_purposes']==[0,1] and result['boot_patch']==20220405
    assert result['cryptographically_verified']==0
    output=json.dumps(result)
    assert not any(s in output for s in ('SYNTHETIC','deadbeef',str(0xdeadbeef)))
    assert auth.public_auth(raw)==result
    for data in (b'pKMblob\1'+raw,raw[:-1],raw+b'x',b'\x9f',b'x'*65537):
        try: auth.public_auth(data)
        except (ValueError,TypeError,IndexError): pass
        else: raise AssertionError('malformed/software input accepted')
    absent = auth.public_auth(cbor([0x4b4d4b44,4,0,cbor({1:{}}),[]]))
    assert absent['boot_patch_present']==0 and absent['hardware_aes']==0
    # Exercise the header used by the interposer, compiled for Android ARM64.
    trace_spec = importlib.util.spec_from_file_location('trace_test', HERE/'test-fbe-trace.py')
    trace = importlib.util.module_from_spec(trace_spec); trace_spec.loader.exec_module(trace)
    with tempfile.TemporaryDirectory(prefix='fbe-public-native-') as tmp:
        exe=Path(tmp)/'auth-test'
        sp.run([trace.CC,'-O2','-Wall','-Wextra','-Werror','-static',
                HERE/'test-fbe-public-auth.c','-o',exe],check=True)
        def native(data):
            run=sp.run([trace.QEMU,exe],input=data,capture_output=True,timeout=5,check=True)
            return list(map(int,run.stdout.split()))
        assert native(raw)==[0,1,1,1,160000,202605,20220405,1,20220405]
        assert native(cbor([0x4b4d4b44,4,0,cbor({1:{}}),[]]))==[0]*9
        for bad in (raw[:-1],raw+b'x',b'\x9f',b'x'*65537,
                    cbor([0,4,0,chars,[]]),cbor([0x4b4d4b44,4,0,cbor({0:hw}),[]])):
            assert native(bad)==[-33]+[0]*8
    print('PASS public auth: stock HW layout, prefix, secret exclusion, malformed refusal, missing tags')


if __name__=='__main__': main()
