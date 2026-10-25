#!/usr/bin/env python3
"""Read only public authorization tags from a HOST-saved QCOM key blob.

No device commands, identifiers, key bytes/hashes or unknown CBOR values are
printed. The clear authorization map is unverified until the TA accepts it.
Stock KeymasterSerialize::parseKeyBlob/getCharacteristics establish the format.
"""
import argparse
import json
from pathlib import Path
import sys


class CBOR:
    def __init__(self, data):
        if not 0 < len(data) <= 65536: raise ValueError()
        self.data, self.pos, self.budget = data, 0, 1024

    def take(self, count):
        if count < 0 or count > len(self.data)-self.pos: raise ValueError()
        value = self.data[self.pos:self.pos+count]; self.pos += count
        return value

    def item(self, depth=0):
        self.budget -= 1
        if depth > 8 or self.budget < 0: raise ValueError()
        header = self.take(1)[0]; major, ai = header >> 5, header & 31
        if ai < 24: value = ai
        elif ai < 28: value = int.from_bytes(self.take(1 << (ai-24)), 'big')
        else: raise ValueError()  # No indefinite lengths or reserved encodings.
        if major < 2: return value if major == 0 else -1-value
        if major in (2, 3): return self.take(value)
        if major in (4, 5):
            if value > 128: raise ValueError()
            if major == 4: return [self.item(depth+1) for _ in range(value)]
            result = {}
            for _ in range(value):
                key, val = self.item(depth+1), self.item(depth+1)
                if type(key) is not int or key in result: raise ValueError()
                result[key] = val
            return result
        if major == 7 and ai in (20, 21): return ai == 21
        if major == 7 and ai == 22: return None
        raise ValueError()

    def complete(self):
        value = self.item()
        if self.pos != len(self.data): raise ValueError()
        return value


def public_auth(data):
    if data.startswith(b'pKMblob\x01'): raise ValueError()  # Software provider.
    if data.startswith(b'pKMblob\x00'): data = data[8:]
    blob = CBOR(data).complete()
    if not isinstance(blob, list) or len(blob) != 5 or blob[0] != 0x4b4d4b44:
        raise ValueError()
    if not isinstance(blob[3], bytes): raise ValueError()
    characteristics = CBOR(blob[3]).complete()
    if not isinstance(characteristics, dict) or not isinstance(characteristics.get(1), dict):
        raise ValueError()
    hw = characteristics[1]
    def number(tag):
        value = hw.get(tag, 0)
        if type(value) is not int or not 0 <= value <= 99999999: raise ValueError()
        return value
    def enums(tag, allowed):
        values = hw.get(tag, [])
        if not isinstance(values, list) or any(type(v) is not int or v not in allowed for v in values):
            raise ValueError()
        return sorted(set(values))
    # Exact known full tags only. No generic tree dumping or masked tag lookup.
    result = dict(local_parse_rc=0, cryptographically_verified=0,
                  hardware_aes=int(number(0x10000002) == 32),
                  hardware_block_modes=enums(0x20000004, (1, 2, 3, 32, 64)),
                  hardware_purposes=enums(0x20000001, tuple(range(8))))
    for label, tag in [('os_version',0x300002c1), ('os_patch',0x300002c2),
                       ('vendor_patch',0x300002ce), ('boot_patch',0x300002cf)]:
        result[label+'_present'] = int(tag in hw)
        result[label] = number(tag)
    for label, tag in [('rollback_resistance',0x7000012f),('rollback_resistant',0x700002bf)]:
        result[label] = int(hw.get(tag) is True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('blob', type=Path, help='already saved host file; never a phone path')
    args = parser.parse_args()
    try:
        with args.blob.open('rb') as source: data = source.read(65537)
        output = public_auth(data)
    except (ValueError, TypeError, IndexError, OSError):
        print('{"local_parse_rc":-33,"cryptographically_verified":0}')
        return 1
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == '__main__': sys.exit(main())
