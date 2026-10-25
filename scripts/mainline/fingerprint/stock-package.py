"""Read selected files through package sparse super + LP + ext4, without an image copy."""
from pathlib import Path
import struct,bisect,json,hashlib,zipfile,io
regions=[]
import xml.etree.ElementTree as ET
root=ET.parse('/home/user/a9-assets/HLTE556N/rawprogram_unsparse0.xml').getroot()
rows=[e for e in root if e.get('filename','').startswith('super_')]
base=min(int(e.get('start_sector')) for e in rows)
for e in rows:
 p=Path('/home/user/a9-assets/HLTE556N')/e.get('filename')
 sector=int(e.get('SECTOR_SIZE_IN_BYTES','4096'))
 at=(int(e.get('start_sector'))-base)*sector
 regions.append((at,at+p.stat().st_size,p,0,None))
regions.sort();starts=[r[0] for r in regions]
files={p:p.open('rb') for _,_,p,_,_ in regions}
def raw(off,n):
 out=bytearray()
 while n:
  i=bisect.bisect_right(starts,off)-1
  if i>=0 and regions[i][0]<=off<regions[i][1]:
   lo,hi,p,pos,fill=regions[i];k=min(n,hi-off)
   if fill:out.extend((fill*((k+3)//4+1))[(off-lo)%4:][:k])
   else:
    f=files[p];f.seek(pos+off-lo);b=f.read(k);assert len(b)==k;out.extend(b)
  else:
   k=min(n,(starts[i+1]-off) if i+1<len(starts) else n);out.extend(bytes(k))
  off+=k;n-=k
 return bytes(out)
h=raw(0x3000,128);assert struct.unpack_from('<I',h)[0]==0x414c5030
hs=struct.unpack_from('<I',h,8)[0];tabs=raw(0x3000+hs,struct.unpack_from('<I',h,44)[0]);partoff,partn,partsz=struct.unpack_from('<III',h,80);extoff,extn,extsz=struct.unpack_from('<III',h,92)
parts={}
for i in range(partn):
 n,attrs,first,count,group=struct.unpack_from('<36sIIII',tabs,partoff+i*partsz);name=n.split(b'\0')[0].decode();ext=[]
 for j in range(first,first+count):
  num,typ,data,src=struct.unpack_from('<QIQI',tabs,extoff+j*extsz);assert typ==0;ext.append((num*512,data*512))
 parts[name]=ext
def partread(name,off,n):
 out=bytearray()
 for sz,start in parts[name]:
  if off>=sz:off-=sz;continue
  k=min(n,sz-off);out.extend(raw(start+off,k));off=0;n-=k
  if n==0:break
 assert n==0
 return bytes(out)
class Ext4:
 def __init__(self,read):
  self.read=read;s=read(1024,1024);assert s[56:58]==b'\x53\xef';self.bs=1024<<struct.unpack_from('<I',s,24)[0];self.ipg=struct.unpack_from('<I',s,40)[0];self.isz=struct.unpack_from('<H',s,88)[0];self.dsz=max(32,struct.unpack_from('<H',s,254)[0]);self.desc=(2 if self.bs==1024 else 1)*self.bs
 def inode(self,num):
  g,j=divmod(num-1,self.ipg);d=self.read(self.desc+g*self.dsz,self.dsz);table=struct.unpack_from('<I',d,8)[0]
  if self.dsz>=64:table|=struct.unpack_from('<I',d,40)[0]<<32
  return self.read(table*self.bs+j*self.isz,self.isz)
 def ext(self,b):
  magic,n,m,depth,gen=struct.unpack_from('<HHHHI',b);assert magic==0xf30a
  rows=[]
  for i in range(n):
   off=12+i*12
   if depth:
    logical,lo,hi,_=struct.unpack_from('<IIHH',b,off);rows+=self.ext(self.read((lo|(hi<<32))*self.bs,self.bs))
   else:
    logical,length,hi,lo=struct.unpack_from('<IHHI',b,off);rows.append((logical*self.bs,(length&0x7fff)*self.bs,(lo|(hi<<32))*self.bs))
  return rows
 def data(self,num):
  ino=self.inode(num);size=struct.unpack_from('<I',ino,4)[0]|(struct.unpack_from('<I',ino,108)[0]<<32);flags=struct.unpack_from('<I',ino,32)[0]
  if not flags&0x80000:
   assert size<=60;return ino[40:40+size]
  out=bytearray(size)
  for lo,n,phys in self.ext(ino[40:100]):
   if lo>=size:continue
   n=min(n,size-lo);out[lo:lo+n]=self.read(phys,n)
  return bytes(out)
 def ls(self,num):
  b=self.data(num);p=0;rows={}
  while p+8<=len(b):
   ino,reclen,nlen,typ=struct.unpack_from('<IHBB',b,p)
   if reclen<8:break
   if ino:rows[b[p+8:p+8+nlen].decode(errors='replace')]=(ino,typ)
   p+=reclen
  return rows
 def lookup(self,path):
  ino=2
  for name in path.strip('/').split('/'):
   if name:ino=self.ls(ino)[name][0]
  return ino

_vndk = None
def vndk(name):
 global _vndk
 if _vndk is None:
  system = Ext4(lambda o,n:partread('system_a',o,n))
  apex=system.ls(system.lookup('/system/apex'))
  selected=[x for x in apex if x.startswith('com.android.vndk.')]
  assert len(selected)==1,selected
  inode,kind=apex[selected[0]]
  if kind == 2:
   root='/system/apex/'+selected[0]
   class Directory:
    def lookup(self,path):return system.lookup(root+path)
    def data(self,inode):return system.data(inode)
   _vndk=Directory()
  else:
   data=system.data(inode)
   z=zipfile.ZipFile(io.BytesIO(data));image=z.read('apex_payload.img')
   _vndk=Ext4(lambda o,n:image[o:o+n])
 try:return _vndk.data(_vndk.lookup('/lib64/'+name))
 except KeyError:return b''

def library(name):
 data=vndk(name)
 if data.startswith(b'\x7fELF'):return data
 system=Ext4(lambda o,n:partread('system_a',o,n))
 try:return system.data(system.lookup('/system/lib64/'+name))
 except KeyError:return b''
