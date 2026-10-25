#!/usr/bin/env python3
"""Read native SM6115 CAMSS resources or Venus minimum firmware from ELF64 RELA.

Offsets are the matching Linux 7.2 arm64 camss_subdev_resources layout. Unknown
layouts, stripped modules and missing relocation targets fail the --check gate.
"""
import argparse
from pathlib import Path
import struct


def inspect(path, check=False):
    blob = path.read_bytes()
    assert blob[:6] == b'\x7fELF\x02\x01', 'little-endian ELF64 required'
    assert struct.unpack_from('<H', blob, 18)[0] == 183, 'arm64 required'
    offset = struct.unpack_from('<Q', blob, 40)[0]
    count, stridx = struct.unpack_from('<HH', blob, 60)
    sections = [struct.unpack_from('<IIQQQQIIQQ', blob, offset+i*64) for i in range(count)]
    def data(index):
        section = sections[index]
        return blob[section[4]:section[4]+section[5]]
    def cstr(buf, at): return buf[at:buf.index(0, at)].decode()
    sec = {cstr(data(stridx), s[0]): i for i, s in enumerate(sections)}
    symidx = sec['.symtab']; strings = data(sections[symidx][6]); symbols = []
    for at in range(0, len(data(symidx)), 24):
        row = struct.unpack_from('<IBBHQQ', data(symidx), at)
        symbols.append((cstr(strings, row[0]), row[3], row[4], row[5]))
    by_name = {row[0]: row for row in symbols}
    if 'venus' in path.name:
        for name, expected in [('sm6115_min_fw', (6, 0, 39)), ('min_fw', (6, 0, 55))]:
            _, index, at, size = by_name[name]
            assert size == 12
            value = struct.unpack_from('<III', data(index), at)
            print(name, *value)
            if check: assert value == expected, (name, value)
        return
    relocations = {}
    for i, section in enumerate(sections):
        if section[1] != 4 or section[7] != sec['.rodata']: continue
        for at in range(0, section[5], 24):
            target, info, addend = struct.unpack_from('<QQq', data(i), at)
            name, index, start, size = symbols[info >> 32]
            # Other resource fields point at code or non-string tables.
            try: relocations[target] = cstr(data(index), start+addend)
            except (ValueError, UnicodeDecodeError, IndexError): continue
    for name, prefix in [('csiphy_res_6115', 'csiphy'), ('csid_res_6115', 'csid'), ('vfe_res_6115', 'vfe')]:
        _, index, at, size = by_name[name]
        assert size == 6504, 'unknown CAMSS resource layout'
        print(name, hex(at), size)
        for row in range(3):
            reg = relocations.get(at+row*2168+0x730)
            irq = relocations.get(at+row*2168+0x7b8)
            print(row, 'reg', reg, 'irq', irq)
            if check: assert reg == irq == prefix+str(row), (name, row, reg, irq)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('module', type=Path)
    args = ap.parse_args(); inspect(args.module, args.check)
