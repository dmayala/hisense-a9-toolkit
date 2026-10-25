"""Read-only, allowlisted band/RF extraction. Never emit other NV records."""
from pathlib import Path
import struct, json, hashlib, re, argparse

# Input must be generic firmware-package MCFG files, never device EFS dumps.
OUT = None
NV_ALLOW = {441, 946, 1877, 2954, 6828, 65633}
EXACT_FILES = {
    '/policyman/carrier_policy.xml', '/policyman/device_config.xml',
    '/policyman/device_config', '/policyman/rat_mask',
    '/nv/item_files/rfnv/00065633',
}

def allowed_name(name):
    # All input MBNs are generic package assets, never a device EFS dump.
    # Preserve only RF/band configuration and policyman XML, not identities.
    return name in EXACT_FILES or (
        name.startswith(('/nv/item_files/rfnv/', '/nv/item_files/rf/'))
        and re.search(r'(band|rfc|card|supported)', name, re.I)
    ) or (name.startswith('/nv/item_files/') and re.search(r'(band_pref|band_mask|band_cap|bc_config)',name,re.I))

def mbn(path, extract=False, label=None):
    data=path.read_bytes()
    assert data[:6] == b'\x7fELF\x01\x01'
    phoff=struct.unpack_from('<I', data,28)[0]
    phsize,phnum=struct.unpack_from('<HH',data,42)
    segments=[struct.unpack_from('<8I',data,phoff+i*phsize) for i in range(phnum)]
    seg=next(s for s in segments if data[s[1]:s[1]+4]==b'MCFG')
    pos=seg[1]; end=pos+seg[4]
    magic,version,kind,count,carrier,version_id,version_size,config_version=struct.unpack_from('<4sHHIIHHI',data,pos)
    assert magic==b'MCFG' and version_id==4995 and version_size==4
    pos+=24; result=[]; selected={}
    for i in range(count):
        length,typ,attr,reserved=struct.unpack_from('<IBBH',data,pos)
        assert 8<=length and pos+length<=end,(i,pos,length,end)
        p=pos+8; stop=pos+length
        if typ==1:
            nv,size=struct.unpack_from('<HH',data,p)
            if nv in NV_ALLOW:
                payload=data[p+4:p+4+size]
                result.append(dict(index=i,offset=pos,type='nv',nv=nv,attributes=attr,hex=payload.hex(),size=len(payload)))
        elif typ in (2,4):
            tag,n=struct.unpack_from('<HH',data,p); assert tag==1
            name=data[p+4:p+4+n].rstrip(b'\0').decode('ascii')
            p+=4+n
            tag,size=struct.unpack_from('<HH',data,p);assert tag==2
            assert p+4+size<=stop
            if allowed_name(name):
                start=p+4+(1 if typ==4 and size else 0)
                payload=data[start:p+4+size] if size else b''
                item=dict(index=i,offset=pos,type='file',name=name,attributes=attr,size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
                result.append(item)
                selected.setdefault(name,bytearray()).extend(payload)
        elif typ==8:
            # Extended RFNV object: skip pending format validation, no payload output.
            result.append(dict(index=i,offset=pos,type='unparsed-type8',size=length,attributes=attr))
        elif typ==10:
            pass # Do not inspect footer/SIM matching information.
        else:
            raise ValueError(('unsupported item type',typ,i))
        pos=stop
    assert 0<=end-pos<=4,(pos,end)
    record=dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),mcfg_offset=seg[1],mcfg_size=seg[4],format=version,kind=kind,item_count=count,trailing_bytes=end-pos,selected=result)
    if extract:
        dest=OUT/'mcfg'/label
        for name,payload in selected.items():
            target=dest/name.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
        record['selected_files_root']=str(dest)
    return record,selected

def profiles():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('firmware', type=Path, help='extracted NON-HLOS.bin FAT root')
    parser.add_argument('output', type=Path, help='private analysis output directory')
    args=parser.parse_args(); fw=args.firmware; OUT=args.output
    OUT.mkdir(parents=True,exist_ok=True)
    paths=[fw/'mcfg_hw.mbn',fw/'mcfg_sw.mbn']+sorted((fw/'modem_pr/mcfg/configs').rglob('*.mbn'))
    records=[]
    for i,path in enumerate(paths):
        record,selected=mbn(path,False,f'p{i:03d}')
        record['path']=str(path.relative_to(fw))
        records.append(record)
    (OUT/'mcfg-band-records.json').write_text(json.dumps(records,indent=2)+'\n')
    for i,r in enumerate(records):
        print(i,r['path'],r['item_count'],'items',[(x.get('nv',x.get('name')),x.get('hex',x.get('size'))) for x in r['selected']])

if __name__=='__main__':profiles()
