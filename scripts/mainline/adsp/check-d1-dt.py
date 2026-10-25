#!/usr/bin/env python3
"""Offline D1 DT delta check (baseline = integ-1 DTB). A PASS is not a phone GO.

D1 = D0's remoteproc/LPI/FastRPC changes, APR left enabled as upstream, plus
exactly one /sound node with two codec-less front-end links and nothing else.
"""
import argparse
from importlib.machinery import SourceFileLoader
from pathlib import Path
import struct

d0 = SourceFileLoader('check_d0_dt', str(Path(__file__).with_name('check-d0-dt.py'))).load_module()
RP, LPI, FW = d0.RP, d0.LPI, d0.FW
ASM = RP + '/glink-edge/apr/service@7/dais'
APR = RP + '/glink-edge/apr'
SOUND = '/sound'
CARD = {
    SOUND: {'compatible': b'qcom,qrb4210-rb2-sndcard\0', 'model': b'A9D1NoOutput\0'},
    SOUND + '/mm1-dai-link': {'link-name': b'MultiMedia1\0'},
    SOUND + '/mm2-dai-link': {'link-name': b'MultiMedia2\0'},
}
# Services whose enablement is the D1 payload (no status property = okay).
SERVICES = {'service@3': b'qcom,q6core\0', 'service@4': b'qcom,q6afe\0',
            'service@7': b'qcom,q6asm\0', 'service@8': b'qcom,q6adm\0'}


def renumbered(old, new):
    """Property deltas that are only dtc phandle renumbering are not deltas.

    Adding the /sound node makes q6asmdai a referenced label, so dtc renumbers
    the phandles of every later labelled node (measured: all shifts are +1).
    A differing 4-byte cell is accepted ONLY when old cell == phandle of node X
    in the baseline and new cell == phandle of the same path X in the candidate.
    Cells equal in both trees are never touched, so a coincidental constant
    cannot hide a real change.
    """
    o2p = {p['phandle']: path for path, p in old.items() if 'phandle' in p}
    p2n = {path: p['phandle'] for path, p in new.items() if 'phandle' in p}

    def same(path, name):
        a, b = old[path].get(name), new[path].get(name)
        if a == b:
            return True
        if name == 'phandle':
            return True  # the numbering itself; referencing cells are checked below
        if a is None or b is None or len(a) != len(b) or len(a) % 4:
            return False
        for i in range(0, len(a), 4):
            x, y = a[i:i + 4], b[i:i + 4]
            if x != y and not (x in o2p and p2n.get(o2p[x]) == y):
                return False
        return True
    return same


def check(base, candidate):
    old, old_reserve = base
    new, new_reserve = candidate
    new_raw = new
    same = renumbered(old, new)
    if old_reserve != new_reserve:
        raise ValueError('D1 must preserve every memreserve entry')
    added = new.keys() - old.keys()
    cpu1, cpu2 = SOUND + '/mm1-dai-link/cpu', SOUND + '/mm2-dai-link/cpu'
    if old.keys() - new.keys() or added != set(CARD) | {cpu1, cpu2}:
        raise ValueError('unexpected node delta: ' + repr(sorted(added ^ (set(CARD) | {cpu1, cpu2}))))
    expected = {
        (RP, 'firmware-name'): (FW.replace(b'.mdt', b'.mbn'), FW),
        (RP, 'status'): (b'disabled\0', b'okay\0'),
        (RP + '/glink-edge/fastrpc', 'status'): (None, b'disabled\0'),
        (LPI, 'status'): (None, b'disabled\0'),
    }
    actual = {(path, prop): (old[path].get(prop), new[path].get(prop))
              for path in old for prop in old[path].keys() | new[path].keys()
              if not same(path, prop)}
    if actual != expected:
        raise ValueError('unexpected DT property delta: ' + repr(sorted(actual)))
    # The /sound subtree is exact: no extra property anywhere (no pinctrl,
    # gpio, regulator, clock, audio-routing, widgets, codec/platform child).
    for path, props in CARD.items():
        if new_raw[path] != props:
            raise ValueError('/sound node property mismatch at ' + path + ': ' + repr(sorted(new[path])))
    asm = new_raw[ASM].get('phandle')
    if asm is None:
        raise ValueError('q6asm dais has no phandle')
    for cpu, dai in ((cpu1, 0), (cpu2, 1)):
        if new_raw[cpu] != {'sound-dai': asm + struct.pack('>I', dai)}:
            raise ValueError('cpu node must be exactly sound-dai = <&q6asmdai %d>' % dai)
    # APR tree is the upstream payload and must still be enabled, unchanged.
    if new[APR].get('compatible') != b'qcom,apr-v2\0' or 'status' in new[APR]:
        raise ValueError('APR node must stay enabled upstream apr-v2')
    if new[APR].get('qcom,glink-channels') != b'apr_audio_svc\0':
        raise ValueError('unexpected APR glink channel')
    for name, compat in SERVICES.items():
        n = new.get(APR + '/' + name)
        if n is None or n.get('compatible') != compat or 'status' in n:
            raise ValueError('APR service %s must stay enabled' % name)
        if n.get('qcom,protection-domain') != b'avs/audio\0msm/adsp/audio_pd\0':
            raise ValueError('unexpected protection domain for ' + name)
    if new_raw[RP].get('compatible') != b'qcom,sm6115-adsp-pas\0':
        raise ValueError('wrong PAS compatible')
    mem = new_raw['/reserved-memory/memory@53800000']
    if (mem.get('reg') != struct.pack('>4I', 0, 0x53800000, 0, 0x1e00000)
            or mem.get('no-map') != b'' or new_raw[RP].get('memory-region') != mem.get('phandle')
            or 'phandle' not in mem):
        raise ValueError('wrong stock ADSP reservation or phandle')
    for rp in ('/soc@0/remoteproc@b300000', '/soc@0/remoteproc@6080000'):
        if new[rp].get('status') != b'disabled\0':
            raise ValueError('CDSP/MPSS must remain disabled')
    return len(expected), len(added)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    a = p.parse_args()
    props, nodes = check(d0.read_fdt(a.baseline), d0.read_fdt(a.candidate))
    print(f'D1-DT PASS: {props} property changes + {nodes} new /sound nodes; stock memory preserved;'
          ' APR enabled, FastRPC/LPI disabled, no pinctrl/codec/backend/amplifier reference')
    print('OFFLINE ONLY: PAS auto-boot unchanged at DT level; no phone-trial GO')


if __name__ == '__main__':
    main()
