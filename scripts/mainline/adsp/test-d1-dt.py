#!/usr/bin/env python3
"""Check real compiled D1 DTs plus mutations that must refuse admission."""
import copy
from importlib.machinery import SourceFileLoader
from pathlib import Path
import struct
import sys
import tempfile

d1 = SourceFileLoader('check_d1_dt', str(Path(__file__).with_name('check-d1-dt.py'))).load_module()
d0 = d1.d0


def main():
    base, cand = map(d0.read_fdt, sys.argv[1:])
    assert d1.check(base, cand) == (4, 5)
    count = 1

    def refused(value):
        nonlocal count
        try:
            d1.check(base, value)
        except (ValueError, KeyError):
            count += 1
        else:
            raise AssertionError('accepted unsafe DT mutation')

    refused(base)
    S = d1.SOUND
    # Required isolation / enablement removed.
    for node in (d1.RP, d1.RP + '/glink-edge/fastrpc', d1.LPI):
        v = copy.deepcopy(cand); v[0][node].pop('status'); refused(v)
    v = copy.deepcopy(cand); v[0][d1.APR]['status'] = b'disabled\0'; refused(v)
    for svc in d1.SERVICES:
        v = copy.deepcopy(cand); v[0][d1.APR + '/' + svc]['status'] = b'disabled\0'; refused(v)
    # Anything that would reach hardware from the card node.
    for prop, val in (('pinctrl-0', b'\0\0\0\1'), ('pinctrl-names', b'default\0'),
                      ('audio-routing', b'a\0b\0'), ('widgets', b'x\0'),
                      ('reset-gpios', b'\0' * 12), ('clocks', b'\0' * 8),
                      ('status', b'okay\0'), ('aux-devs', b'\0\0\0\1'),
                      ('compatible', b'qcom,sm8250-sndcard\0'), ('model', b'Other\0')):
        v = copy.deepcopy(cand); v[0][S][prop] = val; refused(v)
    # Extra nodes: a codec/platform child, a back-end link, an unrelated node.
    for extra in (S + '/mm1-dai-link/codec', S + '/mm1-dai-link/platform', S + '/hdmi-dai-link',
                  S + '/mm3-dai-link', '/extra', '/soc@0/audio-codec@0'):
        v = copy.deepcopy(cand); v[0][extra] = {}; refused(v)
    # Missing front end, wrong DAI index / provider, backend DAI in a cpu node.
    for n in (S + '/mm2-dai-link', S + '/mm2-dai-link/cpu'):
        v = copy.deepcopy(cand); v[0].pop(n); refused(v)
    v = copy.deepcopy(cand); v[0][S + '/mm1-dai-link/cpu']['sound-dai'] = v[0][d1.ASM]['phandle'] + struct.pack('>I', 2); refused(v)
    v = copy.deepcopy(cand); v[0][S + '/mm1-dai-link/cpu']['sound-dai'] = b'\0\0\0\7' + struct.pack('>I', 0); refused(v)
    v = copy.deepcopy(cand); v[0][S + '/mm1-dai-link/cpu']['sound-dai'] += b'\0\0\0\1'; refused(v)
    # A real re-pointing hidden next to the benign renumbering must still refuse.
    v = copy.deepcopy(cand)
    v[0][d1.LPI]['clocks'] = v[0][d1.LPI]['clocks'][:4] + b'\0\0\0\1' + v[0][d1.LPI]['clocks'][8:]
    refused(v)
    v = copy.deepcopy(cand); v[0][d1.RP]['clocks'] = b'\0\0\0\1\0\0\0\2'; refused(v)
    # Inherited D0 invariants.
    for node, prop, repl in (
        (d1.RP, 'firmware-name', b'adsp.mdt\0'), (d1.RP, 'memory-region', b'\0\0\0\0'),
        ('/reserved-memory/memory@53800000', 'reg', b'bad'),
        ('/soc@0/remoteproc@6080000', 'status', b'okay\0'),
        ('/soc@0/remoteproc@b300000', 'status', b'okay\0'),
        (d1.APR, 'qcom,glink-channels', b'other\0'),
        (d1.APR + '/service@4', 'qcom,protection-domain', b'avs/audio\0\0'),
        ('/', 'model', b'accidental unrelated change\0')):
        v = copy.deepcopy(cand); v[0][node][prop] = repl; refused(v)
    v = copy.deepcopy(cand); v[1].append((0x55600000, 0x1000)); refused(v)
    # D0's DTB (APR disabled, no card) must NOT be accepted as D1.
    with tempfile.TemporaryDirectory(prefix='adsp-d1-fdt-') as directory:
        p = Path(directory) / 'truncated.dtb'
        p.write_bytes(Path(sys.argv[2]).read_bytes()[:60])
        try: d0.read_fdt(p)
        except ValueError: count += 1
        else: raise AssertionError('accepted truncated DTB')
    print(f'D1 DT tests PASS: {count} checks on real compiled inputs and unsafe mutations')


if __name__ == '__main__':
    main()
