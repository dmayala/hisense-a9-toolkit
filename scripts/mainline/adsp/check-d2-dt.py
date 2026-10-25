#!/usr/bin/env python3
"""Exact offline D1 -> EAR-only D2 DT contract. A PASS is not live admission."""
import argparse
from importlib.machinery import SourceFileLoader
from pathlib import Path
import struct

HERE = Path(__file__).resolve().parent
d1 = SourceFileLoader('d2_base_dt', str(HERE/'check-d1-dt.py')).load_module()
read_fdt = d1.d0.read_fdt

def cells(*values): return struct.pack('>'+str(len(values))+'I', *values)
def strings(*values): return b''.join(v.encode()+b'\0' for v in values)

def check(base, candidate):
    old, reserves = base
    new, new_reserves = candidate
    assert reserves == new_reserves, 'memreserve changed'
    handles = [p['phandle'] for p in new.values() if 'phandle' in p]
    assert all(len(p)==4 and p not in (cells(0),cells(0xffffffff)) for p in handles)
    assert len(handles) == len(set(handles)), 'duplicate phandle'
    # Normalize only actual phandle positions. Treating every four-byte DT
    # cell as a possible phandle would also forgive changed register offsets,
    # GPIO numbers or thermal sensor indices when their values coincide.
    old_handles = {p['phandle']:path for path,p in old.items() if 'phandle' in p}
    single = {'trip','remote-endpoint','vpanel-supply','epd-supply',
              'hisense,epd-pmic','hisense,transport-panel'}
    def same(path,key):
        a,b=old[path].get(key),new[path].get(key)
        if a==b or key=='phandle': return True
        if a is None or b is None or len(a)!=len(b) or len(a)%4: return False
        if key in single:
            args_property=None
        elif key=='pinctrl-0':
            args_property=None
        elif key=='thermal-sensors':
            args_property='#thermal-sensor-cells'
        elif key=='cooling-device':
            args_property='#cooling-cells'
        else: return False
        if key in single and len(a)!=4: return False
        offset=0
        while offset<len(a):
            target=old_handles.get(a[offset:offset+4])
            if target not in new or new[target].get('phandle')!=b[offset:offset+4]: return False
            count=0
            if args_property:
                v=old[target].get(args_property)
                if v is None or len(v)!=4:return False
                count=struct.unpack('>I',v)[0]
            step=(count+1)*4
            if offset+step>len(a):return False
            if a[offset+4:offset+step]!=b[offset+4:offset+step]: return False
            offset+=step
        return True
    def label(name): return old['/__symbols__'][name].rstrip(b'\0').decode()
    def ref(path):
        assert path in new and len(new[path].get('phandle', b'')) == 4, ('missing phandle',path)
        return new[path]['phandle']
    soc = d1.RP.rsplit('/',1)[0]
    tlmm, lpi, afecc = label('tlmm'), d1.LPI, label('q6afecc')
    l9 = label('pm6125_l9')
    l14 = l9.rsplit('/',1)[0]+'/l14'
    codec = '/a9-d2-codec'
    rx, va = soc+'/codec@a600000', soc+'/codec@a730000'
    swrx, swva = soc+'/soundwire@a610000', soc+'/soundwire@a740000'
    rxslave, txslave = swrx+'/codec@0,4', swva+'/codec@0,3'
    cc1, cc2 = soc+'/clock-controller@a6a9000', soc+'/clock-controller@a7ec000'
    reset = tlmm+'/a9-d2-reset-state'
    pins = {n:lpi+'/a9-d2-'+n+'-state' for n in ('tx-clk','tx-data','rx-clk','rx-data')}
    expected = {
        l14: {'regulator-min-microvolt':cells(1800000),'regulator-max-microvolt':cells(1800000)},
        codec: {'compatible':strings('hisense,a9-d2-wcd937x'),'#sound-dai-cells':cells(1),
                'reset-gpios':ref(tlmm)+cells(92,1),'pinctrl-names':strings('default'),
                'pinctrl-0':ref(reset),'vdd-rxtx-supply':ref(l9),'vdd-px-supply':ref(l9),
                'vdd-buck-supply':ref(l14),'qcom,rx-device':ref(rxslave),'qcom,tx-device':ref(txslave)},
        reset: {'pins':strings('gpio92'),'function':strings('gpio'),'drive-strength':cells(16),
                'bias-disable':b'','output-low':b''},
    }
    for name, numbers, function, bias in (
        ('tx-clk',(0,),'swr_tx_clk','bias-disable'),
        ('tx-data',(1,2),'swr_tx_data','bias-bus-hold'),
        ('rx-clk',(3,),'swr_rx_clk','bias-disable'),
        ('rx-data',(4,5),'swr_rx_data','bias-bus-hold')):
        expected[pins[name]] = {'pins':strings(*(f'gpio{i}' for i in numbers)),
            'function':strings(function),'drive-strength':cells(10),'slew-rate':cells(3),bias:b''}
    for path, compatible, addr in ((cc1,'qcom,sm6115-lpassaudiocc',0xa6a9000),
                                    (cc2,'qcom,sm6115-lpasscc',0xa7ec000)):
        expected[path] = {'compatible':strings(compatible),'reg':cells(0,addr,0,0x1000),'#reset-cells':cells(1)}
    expected[rx] = {'compatible':strings('qcom,sm6115-lpass-rx-macro'),'reg':cells(0,0xa600000,0,0x1000),
        '#sound-dai-cells':cells(1),'#clock-cells':cells(0),'clock-output-names':strings('a9-d2-rx-mclk'),
        'clocks':ref(afecc)+cells(59,1)+ref(afecc)+cells(60,1)+ref(afecc)+cells(103,1)+ref(va),
        'clock-names':strings('mclk','npl','dcodec','fsgen')}
    expected[va] = {'compatible':strings('qcom,sm6115-lpass-va-macro'),'reg':cells(0,0xa730000,0,0x1000),
        '#sound-dai-cells':cells(1),'#clock-cells':cells(0),'clock-output-names':strings('a9-d2-fsgen'),
        'clocks':ref(afecc)+cells(56,1)+ref(afecc)+cells(103,1)+ref(afecc)+cells(61,1),
        'clock-names':strings('mclk','dcodec','npl'),'qcom,dmic-sample-rate':cells(600000)}
    for path, addr, irq, cc, p0, p1, name, din, dout, table in (
        (swrx,0xa610000,297,cc1,pins['rx-clk'],pins['rx-data'],'RX',0,5,
         ((3,31,31,7,0),(0,0,11,1,0),(0,0,11,0,0),(255,3,255,255,255),
          (255,6,255,255,255),(1,7,4,255,255),(255,0,1,255,255),(255,255,255,255,0),(1,0,0,0,0))),
        (swva,0xa740000,296,cc2,pins['tx-clk'],pins['tx-data'],'VA',3,0,
         ((3,3,3),(1,2,1),(0,0,0),(255,)*3,(255,)*3,(255,)*3,(255,)*3,(255,)*3,(0,)*3))):
        props = {'compatible':strings('hisense,a9-d2-soundwire'),'reg':cells(0,addr,0,0x2000),
            'interrupts':cells(0,irq,4)+(cells(0,79,4) if path==swva else b''),
            'clocks':ref(rx if path==swrx else va),'clock-names':strings('iface'),
            'resets':ref(cc)+cells(0),'reset-names':strings('swr_audio_cgcr'),
            'pinctrl-names':strings('default'),'pinctrl-0':ref(p0)+ref(p1),'label':strings(name),
            'qcom,din-ports':cells(din),'qcom,dout-ports':cells(dout),'#sound-dai-cells':cells(1),
            '#address-cells':cells(2),'#size-cells':cells(0)}
        for key, row in zip(('sinterval-low','offset1','offset2','hstart','hstop','word-length',
                             'block-pack-mode','block-group-count','lane-control'),table):
            props['qcom,ports-'+key] = bytes(row)
        expected[path] = props
    expected[rxslave] = {'compatible':strings('sdw20217010a00'),'reg':cells(0,4),
                         'qcom,rx-port-mapping':cells(1,2,3,4,5)}
    expected[txslave] = {'compatible':strings('sdw20217010a00'),'reg':cells(0,3),
                         'qcom,tx-port-mapping':cells(1,2,3,3)}
    expected.update({
        '/sound':{'compatible':strings('hisense,a9-d2-sndcard'),'model':strings('A9D2Earpiece'),
                  'widgets':strings('Speaker','Earpiece'),
                  'audio-routing':strings('Earpiece','EAR','IN1_HPHL','HPHL_OUT')},
        '/sound/mm1-dai-link':{'link-name':strings('MultiMedia1')},
        '/sound/mm1-dai-link/cpu':{'sound-dai':ref(d1.ASM)+cells(0)},
        '/sound/ear-playback-dai-link':{'link-name':strings('D2 Earpiece Playback')},
        '/sound/ear-playback-dai-link/cpu':{'sound-dai':ref(label('q6afedai'))+cells(113)},
        '/sound/ear-playback-dai-link/platform':{'sound-dai':ref(label('q6routing'))},
        '/sound/ear-playback-dai-link/codec':{'sound-dai':ref(codec)+cells(0)+ref(swrx)+cells(0)+ref(rx)+cells(0)},
    })
    removed = {'/sound/mm2-dai-link','/sound/mm2-dai-link/cpu'}
    assert old.keys()-new.keys() == removed, ('unexpected removed nodes',old.keys()-new.keys())
    assert new.keys()-old.keys() == expected.keys()-old.keys(), 'unexpected added nodes'
    for path, props in expected.items():
        got = {k:v for k,v in new[path].items() if k != 'phandle'}
        assert got == props, ('D2 node contract',path,got.keys() ^ props.keys())
    def pins_reserved(data):
        v = struct.unpack('>'+str(len(data)//4)+'I',data)
        assert len(v)%2 == 0
        result = set()
        for start, count in zip(v[::2],v[1::2]):
            assert count > 0 and start+count <= 128
            row = set(range(start,start+count))
            assert not result & row, 'overlapping GPIO reserved ranges'
            result |= row
        return result
    assert pins_reserved(new[tlmm]['gpio-reserved-ranges']) == pins_reserved(old[tlmm]['gpio-reserved-ranges'])-{92}, 'GPIO unreservation must be exactly 92'
    assert new[lpi]['status'] == b'okay\0'
    changed = {(tlmm,'gpio-reserved-ranges'),(lpi,'status')}
    for path in old.keys() & new.keys():
        if path in expected or path == '/__symbols__': continue
        for key in old[path].keys() | new[path].keys():
            assert (path,key) in changed or same(path,key), ('unexpected baseline property change',path,key)
    # Symbols are metadata, but existing labels may never be retargeted.
    for key, value in old['/__symbols__'].items():
        assert new['/__symbols__'].get(key) == value, ('retargeted old symbol',key)
    for key in new['/__symbols__'].keys()-old['/__symbols__'].keys():
        assert new['/__symbols__'][key].rstrip(b'\0').decode() in expected, ('unexpected symbol',key)
    return len(expected), len(new.keys()-old.keys())

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline',type=Path); p.add_argument('candidate',type=Path)
    a=p.parse_args(); count,added=check(read_fdt(a.baseline),read_fdt(a.candidate))
    print(f'D2-DT PASS: {count} exact node contracts, {added} additions, only GPIO92 unreserved; D1 memory/APR/other hardware unchanged')

if __name__ == '__main__': main()
