#!/usr/bin/env python3
"""Restore the shipped A9 boot/TEE version fields; never touches a device.

Stock ABL reads header v2 offset44, splits OS version and patch, and sends
them in Keymaster command0x208 before Linux. A synthetic future patch is not
an Android system upgrade. System/vendor properties remain their true levels.
"""
from pathlib import Path
import struct
import sys

STOCK_OS = (11 << 14)  # Android boot header's 7-bit version components.
STOCK_PATCH = ((2022 - 2000) << 4) | 4
STOCK_WORD = (STOCK_OS << 11) | STOCK_PATCH


def patch(source, target):
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve() or target.exists():
        raise ValueError('preserve source and existing candidates')
    data = bytearray(source.read_bytes())
    if data[:8] != b'ANDROID!' or struct.unpack_from('<I', data, 40)[0] != 2:
        raise ValueError('expected A9 Android header v2')
    previous = struct.unpack_from('<I', data, 44)[0]
    struct.pack_into('<I', data, 44, STOCK_WORD)
    target.write_bytes(data)
    return dict(previous_word=previous, stock_word=STOCK_WORD,
                os_version='11.0.0', patch_level='2022-04',
                only_header_version_word_changed=True)


if __name__ == '__main__':
    import json
    print(json.dumps(patch(*sys.argv[1:]), sort_keys=True))
