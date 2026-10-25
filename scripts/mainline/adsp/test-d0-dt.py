#!/usr/bin/env python3
"""Check real compiled D0 DTs plus mutations that must refuse admission."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile

spec = importlib.util.spec_from_file_location('d0', Path(__file__).with_name('check-d0-dt.py'))
d0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d0)


def main():
    base, candidate = map(d0.read_fdt, sys.argv[1:])
    assert d0.check(base, candidate) == 5
    count = 1

    def refused(value):
        nonlocal count
        try:
            d0.check(base, value)
        except (ValueError, KeyError):
            count += 1
        else:
            raise AssertionError('accepted unsafe DT mutation')

    # A bare baseline and a previous overlay without FastRPC/LPI isolation fail.
    refused(base)
    for node in (d0.RP, d0.RP + '/glink-edge/apr', d0.RP + '/glink-edge/fastrpc', d0.LPI):
        value = copy.deepcopy(candidate)
        value[0][node].pop('status')
        refused(value)
    for node, prop, replacement in (
        (d0.RP, 'firmware-name', b'adsp.mdt\0'),
        (d0.RP, 'memory-region', b'\0\0\0\0'),
        ('/reserved-memory/memory@53800000', 'reg', b'bad'),
        ('/soc@0/remoteproc@6080000', 'status', b'okay\0'),
        ('/soc@0/remoteproc@b300000', 'status', b'okay\0'),
        ('/', 'model', b'accidental unrelated change\0'),
    ):
        value = copy.deepcopy(candidate)
        value[0][node][prop] = replacement
        refused(value)
    value = copy.deepcopy(candidate)
    value[1].append((0x55600000, 0x1000))
    refused(value)
    value = copy.deepcopy(candidate)
    value[0]['/extra'] = {}
    refused(value)
    with tempfile.TemporaryDirectory(prefix='adsp-fdt-') as directory:
        path = Path(directory) / 'truncated.dtb'
        path.write_bytes(Path(sys.argv[2]).read_bytes()[:60])
        try:
            d0.read_fdt(path)
        except ValueError:
            count += 1
        else:
            raise AssertionError('accepted truncated DTB')
    print(f'D0 DT tests PASS: {count} checks on real compiled inputs and unsafe mutations')


if __name__ == '__main__':
    main()
