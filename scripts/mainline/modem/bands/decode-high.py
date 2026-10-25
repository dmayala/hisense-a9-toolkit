"""Bounded host-only CLADE high-page decoder, firmware input only.

Format recovered with REA from libclade.so Packer::unpack_high,
Packer::extract_data and clade_uncompress. No QCN/NV data is accepted.
Logical base assignment must be validated separately against code references.
"""
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("firmware", type=Path, help="reassembled generic modem ELF (read-only)")
parser.add_argument("output", type=Path, help="private decoded output; do not commit")
parser.add_argument("--start", type=lambda s:int(s,0), default=0)
parser.add_argument("--length", type=lambda s:int(s,0), default=0x195d000)
args=parser.parse_args()
import struct
import sys

ROOT = Path(__file__).resolve().parent
RAW = args.firmware.read_bytes()
DICTS = [struct.unpack_from('<2048I', RAW, 0x2615000+i*0x2000) for i in range(3)]
MISSING = ([1,15], [1,2,4,5,6,7,8,9,16], [0,1,2,4,5,7,8,9,10,15,16,17,18,19,20])

def high_line(offset, start=0xaa1000, exc=0x242a680):
    assert offset % 64 == 0
    raw = RAW[start+offset:start+offset+64]
    assert len(raw)==64
    bits = int.from_bytes(raw, 'little')
    if bits & 1:
        index = (bits>>1)&0xfff
        packed = ((bits>>13)&((1<<480)-1)).to_bytes(60,'little')
        return packed + RAW[exc+index*4:exc+index*4+4]
    bits >>= 1
    words = []
    used = 0
    for _ in range(16):
        code = bits&3
        bits >>= 2
        used += 2
        if code == 3:
            word = bits&0xffffffff
            bits >>= 32
            used += 32
        else:
            word = DICTS[code][bits&2047]
            bits >>= 11
            used += 11
            for bitpos in MISSING[code]:
                lowmask = (1<<bitpos)-1
                word = ((word&~lowmask)<<1) | (word&lowmask) | ((bits&1)<<bitpos)
                bits >>= 1
                used += 1
        words.append(word)
    if used > 494:
        raise ValueError(f'compressed line overflow {offset:x}: {used}')
    return struct.pack('<16I', *words)

if __name__ == '__main__':
    start = args.start
    length = args.length
    output = args.output
    output.write_bytes(b''.join(high_line(o) for o in range(start,start+length,64)))
    print(str(output), length)
