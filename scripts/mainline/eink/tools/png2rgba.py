import sys, zlib, struct
def dec(p):
    d=open(p,'rb').read(); assert d[:8]==b'\x89PNG\r\n\x1a\n'
    i=8; idat=b''; 
    while i<len(d):
        n,t=struct.unpack('>I4s',d[i:i+8]); c=d[i+8:i+8+n]; i+=12+n
        if t==b'IHDR': w,h,bd,ct,_,_,il=struct.unpack('>IIBBBBB',c)
        elif t==b'IDAT': idat+=c
    assert bd==8 and il==0 and ct in (2,6,0),(bd,il,ct)
    ch={2:3,6:4,0:1}[ct]; raw=zlib.decompress(idat); st=w*ch; prev=bytearray(st); out=[]; k=0
    for y in range(h):
        f=raw[k]; ln=bytearray(raw[k+1:k+1+st]); k+=1+st
        if f==1:
            for x in range(ch,st): ln[x]=(ln[x]+ln[x-ch])&255
        elif f==2:
            for x in range(st): ln[x]=(ln[x]+prev[x])&255
        elif f==3:
            for x in range(st): ln[x]=(ln[x]+(((ln[x-ch] if x>=ch else 0)+prev[x])>>1))&255
        elif f==4:
            for x in range(st):
                a=ln[x-ch] if x>=ch else 0; b=prev[x]; c=prev[x-ch] if x>=ch else 0
                pa=abs(b-c); pb=abs(a-c); pc=abs(a+b-2*c)
                pr=a if pa<=pb and pa<=pc else (b if pb<=pc else c)
                ln[x]=(ln[x]+pr)&255
        out.append(ln); prev=ln
    rgba=bytearray()
    for ln in out:
        if ch==4: rgba+=ln
        elif ch==3:
            for x in range(0,st,3): rgba+=ln[x:x+3]+b'\xff'
        else:
            for x in range(st): rgba+=bytes([ln[x]])*3+b'\xff'
    return w,h,bytes(rgba)
w,h,r=dec(sys.argv[1]); print(sys.argv[1],w,h,file=sys.stderr)
open(sys.argv[2],'wb').write(r)
