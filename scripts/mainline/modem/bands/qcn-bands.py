"""Extract fixed band-preference fields only; skip every other NV payload."""
from pathlib import Path
import struct,json,re,sys
import olefile
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("qcn", type=Path, help="user-owned QCN backup (read-only)")
parser.add_argument("output", type=Path, help="band-only JSON output")
args=parser.parse_args()

OUT=Path(__file__).resolve().parent
# Values are maximum bytes of the specified band item, not arbitrary record data.
SIZES={441:3,946:3,1877:8,2954:5,6828:8,65633:8,67311:8}
PATHS={'/nv/item_files/modem/mmode/lte_bandpref':8,
       '/nv/item_files/modem/mmode/tds_bandpref':8,
       '/nv/item_files/modem/mmode/tdscdma_bandpref':8}
result={'source':'user-supplied QCN backup','records':[], 'efs_band_prefs':[],
        'format':'CFB; 136-byte NV record: u16 size,u16 type,u32 item ID,128-byte payload',
        'capture_date':'unknown; file timestamp is not proof of capture date'}
with olefile.OleFileIO(args.qcn,write_mode=False) as o:
    for path in o.listdir():
        if path[-1] in {'NV_ITEM_ARRAY','NV_ITEM_ARRAY_SIM_1','NV_ITEM_ARRAY_SIM_2'}:
            size=o.get_size(path);assert size%136==0
            stream=o.openstream(path)
            for i in range(size//136):
                stream.seek(i*136)
                length,typ,nv=struct.unpack('<HHI',stream.read(8))
                assert length==136 and typ==1
                if nv not in SIZES:
                    continue # Do not read, format, hash or save this item's payload.
                payload=stream.read(SIZES[nv])
                result['records'].append({'stream':path[-1],'index':i,'nv':nv,'hex':payload.hex()})
        elif 'EFS_Dir' in path:
            # Directory metadata only. Never open any unselected EFS payload.
            metadata=o.openstream(path).read()
            for match in re.finditer(rb'/nv/item_files/[A-Za-z0-9_/.-]+',metadata):
                name=match.group().decode('ascii')
                if name not in PATHS:
                    continue
                data_path=[('EFS_Data' if x=='EFS_Dir' else x) for x in path]
                n=o.get_size(data_path);assert n<=PATHS[name]
                payload=o.openstream(data_path).read()
                result['efs_band_prefs'].append({'path':name,'hex':payload.hex()})

records={r['nv']:bytes.fromhex(r['hex']) for r in result['records'] if r['stream']=='NV_ITEM_ARRAY'}
mask=int.from_bytes(records[1877],'little')
legacy=int.from_bytes(records[441][1:],'little') | (int.from_bytes(records[946][1:],'little')<<16) | (int.from_bytes(records[2954][1:],'little')<<32)
assert legacy==mask
lte=int.from_bytes(records[6828],'little')
pref=next(bytes.fromhex(r['hex']) for r in result['efs_band_prefs'] if r['path'].endswith('/lte_bandpref'))
assert int.from_bytes(pref,'little')==lte
result['decoded']={'gw_cdma_mask_hex':f'0x{mask:016x}',
                   'gw_cdma_set_bits':[i for i in range(64) if mask>>i&1],
                   'legacy_441_946_2954_matches_1877':True,
                   'lte_bc_config_hex':f'0x{lte:016x}',
                   'lte_bandpref_matches_bc_config':True,
                   'lte_bands_1_to_43':[i+1 for i in range(43) if lte>>i&1],
                   'lte_set_bits_above_42':[i for i in range(43,64) if lte>>i&1],
                   'gsm_bands':[b for bit,b in [(7,3),(8,8),(9,8),(19,5),(21,2)] if mask>>bit&1],
                   'wcdma_bands':[b for bit,b in [(22,1),(23,2),(24,3),(25,4),(26,5),(27,6),(48,7),(49,8),(50,9),(60,19),(61,11)] if mask>>bit&1],
                   'cdma_bc0_preference':bool(mask&3)}
args.output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result['decoded'],indent=2))
