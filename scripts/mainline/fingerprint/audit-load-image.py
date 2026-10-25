#!/usr/bin/env python3
"""Host evidence: stock split files reproduce the FP4 LOAD_APP geometry."""
from pathlib import Path
import argparse,hashlib,json,struct
ROOT=Path('/home/user/A9-build/mainline')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out',type=Path,default=ROOT/'integ-20261023-fp5')
parser.add_argument('--assets-root',type=Path,default=ROOT/'integ-20261023-fp2')
args=parser.parse_args();OUT=args.out;SRC=args.assets_root
fw=SRC/'overlay/firmware'
assets=json.loads((SRC/'stock-assets.json').read_text())['files']
files={p.name:p.read_bytes() for p in fw.glob('sw_fp.*')}
for name,data in files.items():assert hashlib.sha256(data).hexdigest()==assets['firmware/'+name]['sha256']
mdt=files['sw_fp.mdt'];assert mdt[:5]==b'\x7fELF\x01'
off=struct.unpack_from('<I',mdt,28)[0];ents,n=struct.unpack_from('<HH',mdt,42)
ph=[struct.unpack_from('<8I',mdt,off+i*ents) for i in range(n)]
assert n==8 and all(len(files[f'sw_fp.b{i:02d}'])==h[4] for i,h in enumerate(ph))
assert mdt==files['sw_fp.b00']+files['sw_fp.b01']
request=mdt+b''.join(files[f'sw_fp.b{i:02d}'] for i in range(n))
assert len(mdt)==7260 and len(request)==3894274
footprint=max(h[3]+h[5] for h in ph if h[0]==1)
assert footprint < 0x4400000
# TZ APP_START checks the sum although the stock library includes MDT in IMG.
validated=len(mdt)+len(request)
old_shared=(len(request)+4095)&~4095
new_shared=(validated+4095)&~4095
assert (validated,old_shared,new_shared)==(3901534,3895296,3903488)
assert old_shared<validated<=new_shared
# Preserve the complete stock request, with zero-owned padding for TZ's range check.
staging=request+bytes(new_shared-len(request))
assert staging[:len(request)]==request and not any(staging[len(request):])
result=dict(validated_bytes=validated,fp5_shared_bytes=old_shared,fp6_shared_bytes=new_shared,fp5_validation_uncovered_bytes=validated-old_shared,fp6_validation_covered=True,staging_payload_unchanged=True,staging_padding_zero=True,app_name='sw_fp',elf_class=1,segments=n,mdt_bytes=len(mdt),image_bytes=len(request),request_sha256=hashlib.sha256(request).hexdigest(),load_segment_footprint=footprint,stock_secure_region_bytes=0x4400000,all_stock_file_digests_match=True,mdt_is_header_plus_hash_segment=True,host_only=True,hardware_tested=False)
(OUT/'load-layout.json').write_text(json.dumps(result,indent=2)+'\n')
print('PASS stock sw_fp bytes/lengths unchanged; FP6 shared extent covers TZ MDT + IMG validation')
