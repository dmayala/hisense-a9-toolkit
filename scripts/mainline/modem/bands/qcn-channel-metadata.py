"""Identity-skipping QCN metadata audit. No NV or EFS payload is extracted.

Only numbered record headers are read. Static dictionary names select channel
item IDs; modern 20000+ RFNV formats remain explicitly unresolved.
"""
import sys,struct,re,json
from pathlib import Path
import olefile
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("qcn", type=Path, help="user-owned QCN backup (read-only)")
parser.add_argument("output", type=Path, help="band-only JSON output")
parser.add_argument("dictionary", type=Path, help="public nv_complete.txt name dictionary")
args=parser.parse_args()
R=Path(__file__).resolve().parent
names={}
for line in args.dictionary.read_text().splitlines():
    m=re.match(r'(\d+)\^"([^"]+)',line)
    if m:names[int(m[1])]=m[2]
us={'LTE B2':[], 'LTE B4':[], 'LTE B12':[], 'LTE B13':[], 'LTE B14':[], 'LTE B17':[], 'LTE B25':[], 'LTE B26':[], 'LTE B66':[], 'LTE B71':[], 'WCDMA B2':[], 'WCDMA B4':[]}
allowed={}
for nv,name in names.items():
    if not re.search(r'(?:RX|TX) CAL(?:IBRATION)? CHAN',name,re.I):continue
    band=None
    m=re.match(r'LTE B(\d+) ',name,re.I)
    if m and f'LTE B{m[1]}' in us:band=f'LTE B{m[1]}'
    elif re.search(r'WCDMA 1900 ',name,re.I):band='WCDMA B2'
    elif re.search(r'WCDMA BC4 ',name,re.I):band='WCDMA B4'
    if band:allowed[nv]=(band,name)
seen=set();record_counts=[]
with olefile.OleFileIO(args.qcn,write_mode=False) as o:
    for path in o.listdir():
        if path[-1] not in {'NV_ITEM_ARRAY','NV_ITEM_ARRAY_SIM_1','NV_ITEM_ARRAY_SIM_2'}:continue
        size=o.get_size(path);assert size%136==0
        record_counts.append({'stream':path[-1],'records':size//136})
        s=o.openstream(path)
        for i in range(size//136):
            s.seek(i*136);length,typ,nv=struct.unpack('<HHI',s.read(8))
            assert length==136 and typ==1
            # Deliberately never read any item payload, including matching ones.
            if nv in allowed or nv in {148,1878}:seen.add(nv)
for nv,(band,name) in sorted(allowed.items()):
    us[band].append({'nv_item':nv,'name':name,'record_present':nv in seen})
result={'method':'numbered-record headers only; no NV/EFS payloads read or retained',
        'source':'user supplied QCN; no private file hash retained',
        'record_counts':record_counts,'rf_selector_records':{str(n):n in seen for n in (148,1878)},
        'channel_item_dictionary':'fenrir-naru/mbn_utils nv_complete.txt; incomplete for modern RFNV IDs',
        'bands':us,
        'limit':'No matching old channel record does not prove absence of modern RFNV calibration.'}
args.output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'selector_records':result['rf_selector_records'],
 'known_channel_records':{b:{'mapped_ids':len(v),'present':sum(x['record_present'] for x in v)} for b,v in us.items()}},indent=2))
