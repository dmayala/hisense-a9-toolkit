#!/usr/bin/env python3
"""Extract the A9 rear non-flash CCMs from its exact stock tuning blob, offline.

Register tables, the stock blob and this generated YAML remain local artifacts.
The CCT midpoint anchors are a simple-IPA approximation, not CamX interpolation.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

EXPECTED = '5361cd4c22bc36fdd76d7c637c707a9171fabbb7df464fdaae117717b39b259c'

def extract(source, out):
    spec = importlib.util.spec_from_file_location('a9_stock', Path(__file__).with_name('extract-stock.py'))
    stock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stock)
    module = stock.Module(source)
    digest = hashlib.sha256(module.data).hexdigest()
    if digest != EXPECTED:
        raise ValueError('unsupported stock tuning digest')
    name, start, size = module.nodes[0x388]
    assert name == 'mod_cc13_cct_data' and size == 5 * 72 and start == 0x2f398
    # Non-flash branch: LED index0 -> AEC region -> five CCT records.
    assert module.values(0x386)[:3] == [0, 1, 0x387]
    assert module.values(0x387)[-2:] == [5, 0x388]
    records = []
    yaml = '''# Generated from the A9 stock non-flash CC13 table. Keep local.
# Black64/1024 agrees with the covered-lens RAW; not the post-demux BLS zeros.
%YAML 1.1
---
version: 1
algorithms:
  - BlackLevel:
      blackLevel: 4096
  - Awb:
  - Ccm:
      ccms:
'''
    for i in range(5):
        offset = start + i * 72
        low = struct.unpack_from('<f', module.data, offset)[0]
        high = struct.unpack_from('<f', module.data, offset + 8)[0]
        assert module.data[offset + 52:offset + 72] == bytes(20)
        matrix = struct.unpack_from('<9f', module.data, offset + 16)
        assert 1500 <= low < high <= 10000
        assert all(abs(sum(matrix[row:row + 3]) - 1) < 1e-5 for row in [0, 3, 6])
        ct = round((low + high) / 2)
        yaml += f'        - ct: {ct}\n          ccm: [' + ', '.join(f'{x:.9g}' for x in matrix) + ']\n'
        records.append({'cct_record_offset': hex(offset), 'matrix_offset': hex(offset + 16),
                        'stock_trigger': [low, high], 'simple_ct_anchor': ct, 'ccm': matrix})
    yaml += '  - Adjust:\n  - Agc:\n'
    yaml += (Path(__file__).with_name('rear-af.yaml')).read_text()+'...\n'
    out.mkdir(parents=True, exist_ok=True)
    (out / 's5k3l6.yaml').write_text(yaml)
    (out / 'stock-colour.json').write_text(json.dumps({
        'source': 'stock vendor /lib64/camera/com.qti.tuned.s5k3l6_bnd32b_shine.bin',
        'sha256': digest, 'node': 'mod_cc13_cct_data', 'branch': 'non-flash LED index0',
        'matrix_layout': 'nine packed little-endian float32 values; inferred row-major RGB',
        'cct_mapping': 'stock trigger midpoints, simple linear interpolation; not a CamX emulation',
        'black_level_source': 'sensor helper64/1024, confirmed by covered-lens RAW; not stock BLS',
        'records': records}, indent=2) + '\n')
    return out / 's5k3l6.yaml'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(extract(args.source, args.out))
