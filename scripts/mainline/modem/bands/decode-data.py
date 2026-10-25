"""Host-only CLADE2 data decode; fixed generic firmware input, no NV input.

REA libclade2 read_metadata/lib_uncompress/deltaUncompress establish 24-bit
metadata, 16/32/48/64-byte slots and four-anchor delta coding. Bootstrap ELF
file0x413c gives compPA4f7a0000,size880000,metaPA50020000,outsize15d8000.
The four following allocator ranges end501c1fc0 and four zero anchor words
follow at0x416c. Output's logical address is validated with its references.
"""
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("firmware", type=Path, help="reassembled generic modem ELF (read-only)")
parser.add_argument("output", type=Path, help="private decoded output; do not commit")
args=parser.parse_args()
import struct,json,hashlib
ROOT=Path(__file__).resolve().parent
RAW=args.firmware.read_bytes()
START=0x261c000; META=START+0x880000; LENGTH=0x15d8000

def line(index):
    m=int.from_bytes(RAW[META+index*3:META+index*3+3],'little')
    if m==0xffffff:return bytes(64)
    off=(m&0x3fffff)<<4; size=((m>>22)+1)*16
    if off+size>0x880000 or off%64+size>64 or (off%64)%size:
        raise ValueError((index,hex(m),hex(off),size))
    raw=RAW[START+off:START+off+size]
    if size==64:return raw
    bits=int.from_bytes(raw,'little'); used=0; anchors=[0]*4; current=3; out=[]
    def get(n):
        nonlocal bits,used
        v=bits&((1<<n)-1); bits>>=n;used+=n
        if used>size*8:raise ValueError(('bit overflow',index))
        return v
    for _ in range(16):
        code=get(2)
        if code==0: word=0
        elif code==1:word=anchors[get(2)]
        elif code==2:
            a=get(2);word=anchors[a]=(anchors[a]&~1023)|get(10)
        else:
            word=get(32);current=(current+1)%4;anchors[current]=word
        out.append(word)
    return struct.pack('<16I',*out)

if __name__=='__main__':
    out=b''.join(line(i) for i in range(LENGTH//64))
    dest=args.output;dest.write_bytes(out)
    print(dest,len(out),hashlib.sha256(out).hexdigest())
