#!/usr/bin/env python3
"""Extract the golden FAT image read-only. Private destination only, no mounts."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()

def stage(destination, mcopy):
    destination = destination.expanduser().resolve()
    parent = (Path.home()/'A9-build/mainline/adsp').resolve()
    assert parent in destination.parents and not destination.exists()
    golden = Path.home()/'a9-assets/Backups/edl_2026-10-14_110759'
    image = golden/'lun4/modem_a.bin'
    rows = dict((n.lstrip('./'), h) for h,n in
                (l.split() for l in (golden/'SHA256SUMS').read_text().splitlines()))
    assert sha(image) == rows['lun4/modem_a.bin'], 'golden modem digest mismatch'
    destination.mkdir(parents=True)
    # mcopy opens FAT input read-only; no mount, loop device or write flags.
    subprocess.run([mcopy, '-i', str(image), '::/IMAGE/ADSP*', str(destination)],check=True)
    expected = json.loads((Path.home()/'A9-build/mainline/sol-audio/stock/audio-provenance.json').read_text())
    expected = {Path(x['copy']).name.lower():x for x in expected if '/adsp/' in x['copy']}
    assert len(expected) == 39
    got = {}
    for p in sorted(destination.iterdir()):
        name = p.name.lower()
        assert re.fullmatch(r'adsp(?:r|s|ua)?\.(?:mdt|b\d\d|jsn)',name), name
        assert name in expected and sha(p) == expected[name]['sha256']
        assert p.stat().st_size == expected[name]['bytes']
        q = p.with_name(name)
        if p != q: p.rename(q)
        q.chmod(0o444)
        got[name] = {'sha256':sha(q),'bytes':q.stat().st_size}
    assert set(got) == set(expected)
    files={p.name:p.read_bytes() for p in destination.iterdir()}
    mdt=files['adsp.mdt']
    assert mdt == files['adsp.b00']+files['adsp.b01']
    assert mdt[:7]==b'\x7fELF\x01\x01\x01'
    off=struct.unpack_from('<I',mdt,28)[0]
    size,count=struct.unpack_from('<HH',mdt,42)
    assert size==32 and off+count*size<=len(mdt)
    low=1<<32; high=0; loads=0
    for i in range(count):
        typ,_,_,pa,fs,ms,flags,_=struct.unpack_from('<8I',mdt,off+i*size)
        if typ!=1 or not ms or flags&(7<<24)==2<<24: continue
        assert flags&(1<<27) and fs<=ms
        if fs: assert len(files[f'adsp.b{i:02d}'])==fs
        low=min(low,pa); high=max(high,(pa+ms+4095)&~4095); loads+=1
    assert loads and 0<high-low<=0x1e00000
    assert json.loads(files['adspua.jsn'])['sr_domain']=={
        'soc':'msm','domain':'adsp','subdomain':'audio_pd','qmi_instance_id':74}
    (destination.parent/'firmware.json').write_text(json.dumps({
        'golden_modem_sha256':rows['lun4/modem_a.bin'],
        'request_prefix':'qcom/sm6115/hisense/hlte556n/', 'files':got},indent=2)+'\n')
    print('ADSP firmware: 39 unchanged assets from golden, lowercase paths PASS')

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('destination',type=Path)
    p.add_argument('--mcopy',required=True)
    a = p.parse_args()
    stage(a.destination,a.mcopy)
