"""Assemble a private analysis ELF from generic modem segments and decoded pages."""
import argparse
from pathlib import Path
import struct,hashlib
parser=argparse.ArgumentParser(description=__doc__)
for arg in ['firmware','high','data','output']: parser.add_argument(arg, type=Path)
args=parser.parse_args()
src=args.firmware.read_bytes()
ph=struct.unpack_from('<I',src,28)[0];num=struct.unpack_from('<H',src,44)[0]
regions=[]
for i in range(num):
    typ,off,va,pa,size,mem,flags,align=struct.unpack_from('<8I',src,ph+32*i)
    if typ==1 and size and i not in (24,25,26):regions.append((f'.seg{i}',va,flags&7,src[off:off+size]))
regions.extend([('.clade_high',0xd8000000,5,args.high.read_bytes()),('.clade2_data',0xd0000000,6,args.data.read_bytes())])
names=b'\0';nameoffs={}
for name,va,flags,data in regions:
    nameoffs[name]=len(names);names+=name.encode()+b'\0'
nameoffs['.shstrtab']=len(names);names+=b'.shstrtab\0'
out=bytearray(0x1000);headers=[];sections=[bytes(40)]
for name,va,flags,data in regions:
    offset=len(out);out+=data;out+=bytes((-len(out))%0x1000)
    headers.append(struct.pack('<8I',1,offset,va,va,len(data),len(data),flags,0x1000))
    sections.append(struct.pack('<10I',nameoffs[name],1,2|(4 if flags&1 else 0)|(1 if flags&2 else 0),va,offset,len(data),0,0,4,0))
nameoff=len(out);out+=names;out+=bytes((-len(out))%4);shoff=len(out)
sections.append(struct.pack('<10I',nameoffs['.shstrtab'],3,0,0,nameoff,len(names),0,0,1,0))
out+=b''.join(sections)
out[:52]=struct.pack('<16sHHIIIIIHHHHHH',src[:16],2,0xa4,1,0xc0b00000,52,shoff,struct.unpack_from('<I',src,36)[0],52,32,len(headers),40,len(sections),len(sections)-1)
out[52:52+len(headers)*32]=b''.join(headers)
dest=args.output;dest.write_bytes(out)
print(dest,len(out),hashlib.sha256(out).hexdigest())
