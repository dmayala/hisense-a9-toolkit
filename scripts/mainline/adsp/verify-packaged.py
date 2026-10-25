#!/usr/bin/env python3
"""Read back the actual packed boot/vendor images, offline and without mounts."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess as sp
import tempfile

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def inventory(root):
    return {str(p.relative_to(root)):({'symlink':str(p.readlink())} if p.is_symlink() else {'sha256':sha(p)})
            for p in sorted(root.rglob('*')) if p.is_symlink() or p.is_file()}

def verify(out):
    snap=out/'snapshot'; aw=out/'android'
    boot=aw/'out/a9-android-eink-boot-gpu.img'
    vendor=aw/'out/vendor_b-eink-gpu-bt-health-thermal.img'
    with tempfile.TemporaryDirectory(prefix='readback-',dir=out) as td:
        t=Path(td)
        p=sp.run(['unpack_bootimg','--boot_img',str(boot),'--out',str(t/'boot')],capture_output=True,text=True,check=True)
        wanted={'kernel':snap/'Image.gz','ramdisk':aw/'out/ramdisk-android.cpio.gz',
            'dtb':snap/'sm6115-hisense-hlte556n-android-gpu.dtb','recovery_dtbo':snap/'recovery_dtbo.img'}
        for name,path in wanted.items(): assert sha(t/'boot'/name)==sha(path),name
        (t/'vendor').mkdir()
        p2=sp.run(['debugfs','-R','rdump / '+str(t/'vendor'),str(vendor)],capture_output=True,text=True,check=True)
        expected=inventory(aw/'work/vendor-stage-bt'); actual=inventory(t/'vendor')
        actual={k:v for k,v in actual.items() if k!='lost+found' and not k.startswith('lost+found/')}
        assert actual==expected, {'missing':sorted(expected.keys()-actual.keys()),
            'added':sorted(actual.keys()-expected.keys()),'changed':[k for k in expected.keys()&actual.keys() if expected[k]!=actual[k]]}
        result={'status':'PASS','boot_sha256':sha(boot),'vendor_sha256':sha(vendor),
            'boot_sections':{k:sha(v) for k,v in wanted.items()},'vendor_entries':len(actual),
            'vendor_inventory':actual,
            'header':p.stdout,'vendor_diagnostics':p2.stderr}
    (out/'packaged-readback.json').write_text(json.dumps(result,indent=2)+'\n')
    print('ADSP-D0-PACKAGED READBACK PASS:',len(actual),'vendor files/links, four boot sections')

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('output',type=Path); verify(p.parse_args().output)
