#!/usr/bin/env python3
"""Offline D0 DT delta check. A PASS is not authorization to start ADSP."""
import argparse
from pathlib import Path
import struct

RP = '/soc@0/remoteproc@a400000'
LPI = '/soc@0/pinctrl@a7c0000'
FW = b'qcom/sm6115/hisense/hlte556n/adsp.mdt\0'


def read_fdt(path):
    data = Path(path).read_bytes()
    if len(data) < 40:
        raise ValueError('truncated FDT header')
    magic, total, start, strings, reserve, version, _, _, nstrings, nstruct = struct.unpack_from('>10I', data)
    if (magic != 0xd00dfeed or version != 17 or total != len(data)
            or start + nstruct > total or strings + nstrings > total):
        raise ValueError('unsupported or truncated FDT')
    reservations = []
    while True:
        if reserve + 16 > total:
            raise ValueError('truncated reservation table')
        addr, size = struct.unpack_from('>2Q', data, reserve)
        reserve += 16
        if not (addr or size):
            break
        reservations.append((addr, size))
    i, end, stack, nodes = start, start + nstruct, [], {}
    while i + 4 <= end:
        token = struct.unpack_from('>I', data, i)[0]
        i += 4
        if token == 1:
            stop = data.index(b'\0', i, end)
            stack.append(data[i:stop].decode())
            i = (stop + 4) & ~3
            key = '/'.join(stack) or '/'
            if key in nodes:
                raise ValueError('duplicate FDT node')
            nodes[key] = {}
        elif token == 2:
            if not stack:
                raise ValueError('unbalanced FDT node')
            stack.pop()
        elif token == 3:
            if not stack or i + 8 > end:
                raise ValueError('invalid FDT property')
            size, nameoff = struct.unpack_from('>2I', data, i)
            i += 8
            if i + size > end or nameoff >= nstrings:
                raise ValueError('truncated FDT property')
            stop = data.index(b'\0', strings + nameoff, strings + nstrings)
            name = data[strings + nameoff:stop].decode()
            props = nodes['/'.join(stack) or '/']
            if name in props:
                raise ValueError('duplicate FDT property')
            props[name] = data[i:i + size]
            i = (i + size + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            if stack:
                raise ValueError('unterminated FDT node')
            return nodes, reservations
        else:
            raise ValueError('invalid FDT token')
    raise ValueError('missing FDT end')


def check(base, candidate):
    old, old_reserve = base
    new, new_reserve = candidate
    if old.keys() != new.keys() or old_reserve != new_reserve:
        raise ValueError('D0 must preserve every node and memreserve entry')
    expected = {
        (RP, 'firmware-name'): (FW.replace(b'.mdt', b'.mbn'), FW),
        (RP, 'status'): (b'disabled\0', b'okay\0'),
        (RP + '/glink-edge/apr', 'status'): (None, b'disabled\0'),
        (RP + '/glink-edge/fastrpc', 'status'): (None, b'disabled\0'),
        (LPI, 'status'): (None, b'disabled\0'),
    }
    actual = {(path, prop): (old[path].get(prop), new[path].get(prop))
              for path in old for prop in old[path].keys() | new[path].keys()
              if old[path].get(prop) != new[path].get(prop)}
    if actual != expected:
        raise ValueError('unexpected DT delta: ' + repr(sorted(actual)))
    if new[RP].get('compatible') != b'qcom,sm6115-adsp-pas\0':
        raise ValueError('wrong PAS compatible')
    mem = new['/reserved-memory/memory@53800000']
    if (mem.get('reg') != struct.pack('>4I', 0, 0x53800000, 0, 0x1e00000)
            or mem.get('no-map') != b'' or new[RP].get('memory-region') != mem.get('phandle')
            or 'phandle' not in mem):
        raise ValueError('wrong stock ADSP reservation or phandle')
    for rp in ('/soc@0/remoteproc@b300000', '/soc@0/remoteproc@6080000'):
        if new[rp].get('status') != b'disabled\0':
            raise ValueError('CDSP/MPSS must remain disabled')
    return len(expected)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    args = p.parse_args()
    count = check(read_fdt(args.baseline), read_fdt(args.candidate))
    print(f'D0-DT PASS: exactly {count} property changes; stock memory preserved; APR/FastRPC/LPI disabled')
    print('OFFLINE ONLY: PAS auto-boot is unchanged; no phone-trial GO')


if __name__ == '__main__':
    main()
