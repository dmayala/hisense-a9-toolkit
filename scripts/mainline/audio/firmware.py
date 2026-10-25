#!/usr/bin/env python3
"""Validate the copied stock split ADSP image; optionally stage private vendor files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct

BASE, SIZE = 0x53800000, 0x1e00000
FIRMWARE_DIR = 'firmware/qcom/sm6115/hisense/hlte556n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def image_geometry(mdt, blobs):
    if len(mdt) < 52 or mdt[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('expected little-endian ELF32 ADSP metadata')
    h = struct.unpack_from('<HHIIIIIHHHHHH', mdt, 16)
    if h[1] != 164 or h[8] != 32 or not 1 <= h[9] <= 100:
        raise ValueError('invalid Hexagon program headers')
    if h[4] < 52 or h[4] + h[8] * h[9] > len(mdt):
        raise ValueError('truncated program headers')
    loads = []
    for i in range(h[9]):
        typ, off, va, pa, fs, ms, flags, align = struct.unpack_from('<8I', mdt, h[4]+i*32)
        # Same selection as mainline mdt_phdr_loadable: exclude hash segments.
        if typ != 1 or not ms or flags & (7 << 24) == 2 << 24:
            continue
        if not flags & (1 << 27) or fs > ms or pa + ms > 1 << 32:
            raise ValueError('unsupported/non-relocatable or overflowing load segment')
        name = f'adsp.b{i:02d}'
        if fs and (name not in blobs or len(blobs[name]) != fs):
            raise ValueError('missing or wrong-sized split segment '+name)
        loads.append(dict(index=i, paddr=pa, filesz=fs, memsz=ms))
    if not loads:
        raise ValueError('no loadable ADSP segments')
    low = min(x['paddr'] for x in loads)
    high = max((x['paddr']+x['memsz']+4095) & ~4095 for x in loads)
    if high-low > SIZE:
        raise ValueError('ADSP image exceeds stock reserved memory')
    return {'load_segments':len(loads), 'nonzero_segments':sum(bool(x['filesz']) for x in loads),
            'original_min':low, 'original_end':high, 'relocated_base':BASE,
            'relocated_end':BASE+high-low, 'reserved_size':SIZE, 'spare_bytes':SIZE-(high-low)}


def read_assets(assets):
    assets = assets.expanduser().resolve()
    if (assets/'audio-provenance.json').is_symlink() or (assets/'adsp').is_symlink():
        raise ValueError('copied source metadata and ADSP directory must not be symlinks')
    manifest = json.loads((assets/'audio-provenance.json').read_text())
    records = {Path(x['copy']).name.lower():x for x in manifest if Path(x['copy']).parent == assets/'adsp'}
    files = {}
    for path in sorted((assets/'adsp').iterdir()):
        name = path.name.lower()
        if not re.fullmatch(r'adsp(?:r|s|ua)?\.(?:mdt|b\d\d|jsn)', name):
            raise ValueError('unexpected ADSP asset '+path.name)
        if path.is_symlink() or not path.is_file() or name in files:
            raise ValueError('ambiguous ADSP asset '+path.name)
        data = path.read_bytes()
        if name not in records or sha(data) != records[name]['sha256'] or len(data) != records[name]['bytes']:
            raise ValueError('copied asset integrity mismatch '+path.name)
        files[name] = data
    if 'adsp.mdt' not in files or 'adspua.jsn' not in files:
        raise ValueError('missing ADSP metadata/audio domain')
    domain = json.loads(files['adspua.jsn'])
    if domain['sr_domain'] != {'soc':'msm','domain':'adsp','subdomain':'audio_pd','qmi_instance_id':74}:
        raise ValueError('unexpected audio protection domain')
    if not any(x.get('provider') == 'avs' and x.get('service') == 'audio' for x in domain['sr_service']):
        raise ValueError('missing avs/audio provider')
    geometry = image_geometry(files['adsp.mdt'], files)
    return files, geometry


def stage_files(files, geometry, destination):
    if os.environ.get('A9_AUDIO', '0') != '1':
        raise ValueError('audio staging is default off; require A9_AUDIO=1')
    destination = destination.expanduser().resolve()
    if 'a9-assets' in str(destination) or (destination == Path('/share') or Path('/share') in destination.parents):
        raise ValueError('refusing writes under a9-assets')
    # Refuse either source tree: firmware must never become a worktree artifact.
    if any(g.is_symlink() or (g.exists() and (not g.is_dir() or any(g.iterdir())))
           for x in (destination, *destination.parents) for g in [x/'.git']) or any(
               (x/'HEAD').is_file() and (x/'objects').is_dir() for x in (destination, *destination.parents)):
        raise ValueError('firmware staging must stay outside every git checkout')
    payload = {FIRMWARE_DIR+'/'+name:data for name,data in files.items() if not name.endswith('.jsn')}
    # Only metadata for the Wi-Fi-owned optional userspace mapper; no second service.
    payload.update({'firmware/'+name:data for name,data in files.items() if name.endswith('.jsn')})
    info = {'firmware_name':FIRMWARE_DIR.removeprefix('firmware/')+'/adsp.mdt',
            'geometry':geometry,'files':{p:sha(d) for p,d in payload.items()},
            'authentication':'not tested; PAS signature acceptance requires an attended phone test',
            'mapper_owner':'wifi; no mapper daemon or remoteproc-start action installed'}
    payload['etc/a9-audio/firmware.json'] = (json.dumps(info,indent=2)+'\n').encode()
    for rel in payload:
        path = destination/rel
        if path.is_symlink() or not path.resolve().is_relative_to(destination):
            raise ValueError('staging path escapes via symlink')
        if path.exists() and path.stat().st_nlink != 1:
            raise ValueError('refusing an existing hardlinked staging file')
        if path.exists() and path.read_bytes() != payload[rel]:
            raise ValueError('refusing to overwrite different firmware '+rel)
    for rel,data in payload.items():
        path = destination/rel
        if path.exists():
            continue
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644)
    return info


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets',type=Path,default=Path.home()/'A9-build/mainline/sol-audio/stock')
    p.add_argument('--stage',type=Path)
    a=p.parse_args()
    files, geometry = read_assets(a.assets)
    info = stage_files(files,geometry,a.stage) if a.stage else geometry
    print(json.dumps(info,indent=2))


if __name__ == '__main__':
    main()
