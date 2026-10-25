#!/usr/bin/env python3
"""Stage opt-in Wi-Fi assets from private, verified local build inputs only.

Never fetches, mounts or reads a partition. No asset is copied into git.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

HERE = Path(__file__).resolve().parent
REGDB = Path('/nix/store/lcdlsncvfcjigdxnyavfg606vmx2xbdf-wireless-regdb-2025.07.10-zstd/lib/firmware')

# ath10k firmware-N.bin for WCN3990/SNOC: a 60-byte feature-flag file (NON_BMI,
# bits 6,18,19,20; no executable payload). Provenance: nixpkgs linux-firmware
# 20250917 (license unfreeRedistributableFirmware), file
# ath10k/WCN3990/hw1.0/qcm2290/firmware-5.bin; the blob is read from the cached
# store at staging time and never committed to git.
ATH10K_FW = Path('/nix/store/7nm4vfj0j5q7w2pn46alb8d0ajr1h1xp-linux-firmware-20250917-zstd/lib/firmware/ath10k/WCN3990/hw1.0')
FW5_SHA256 = '80daf4f3a89e6ea8222e45387bb362c854fcd30c02e9da59ca723b39b269cdd2'
FW5_BYTES = 60
FW5_PATH = 'firmware/ath10k/WCN3990/hw1.0/hisense/hlte556n/firmware-5.bin'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def elf_static_arm64(data):
    if len(data)<64 or data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183:
        raise ValueError('Wi-Fi staging requires an ARM64 ELF')
    off=struct.unpack_from('<Q',data,32)[0]
    size,count=struct.unpack_from('<HH',data,54)
    if size<56 or off+size*count>len(data) or any(struct.unpack_from('<I',data,off+i*size)[0]==3 for i in range(count)):
        raise ValueError('invalid or dynamically linked sandbox/daemon')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--assets',type=Path,required=True)
    p.add_argument('--binaries',type=Path,required=True)
    p.add_argument('--ath10k-fw-dir',type=Path,default=Path(os.environ.get('A9_WIFI_ATH10K_FW_DIR',ATH10K_FW)))
    p.add_argument('--regdb-dir',type=Path,default=Path(os.environ.get('A9_WIFI_REGDB_DIR',REGDB)))
    a=p.parse_args()
    if any(n.is_symlink() for n in (a.stage.absolute(),*a.stage.absolute().parents)):
        p.error('vendor output must not traverse symlinks')
    root=a.assets.resolve(); stage=a.stage.resolve(); binaries=a.binaries.resolve()
    repo=HERE.parents[2]
    forbidden=[repo,Path('/home/user/A9'),Path('/home/user/a9-assets').resolve(),Path('/srv/nas/A9')]
    if any(stage==n or n in stage.parents for n in forbidden):
        p.error('vendor stage must be outside project sources and golden storage')
    if not stage.is_dir():
        p.error('vendor staging directory must already exist')
    mode=os.environ.get('A9_WIFI_PD_MAPPER','kernel')
    if mode not in ('kernel','userspace'):
        p.error('invalid PD mapper selection')
    records=json.loads((root/'manifest.json').read_text())
    known={r['path']:r for r in records}
    installed=[]; runtime=[]

    def write(rel,data,permissions=0o644):
        target=stage/rel
        for parent in (target, *target.parents):
            if parent==stage: break
            if parent.is_symlink(): raise ValueError('symlink in vendor output path')
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): raise ValueError('refusing to overwrite Wi-Fi payload: '+rel)
        with target.open('xb') as f:f.write(data)
        target.chmod(permissions)
        installed.append({'path':rel,'bytes':len(data),'sha256':digest(data)})

    def asset(rel):
        source=root/rel
        if source.is_symlink() or not source.is_file() or root not in source.resolve().parents:
            raise ValueError('invalid private asset path')
        row=known.get(rel);data=source.read_bytes()
        if not row or len(data)!=row['bytes'] or digest(data)!=row['sha256']:
            raise ValueError('private asset integrity mismatch: '+rel)
        return data

    build=json.loads((binaries/'build-manifest.json').read_text())
    if build.get('abi')!='aarch64' or build.get('static') is not True:
        raise ValueError('host/dynamic build cannot be staged')
    lock=json.loads((HERE/'sources.lock.json').read_text())
    if len(build['sources'])!=len(lock):raise ValueError('source lock incomplete')
    for expected,actual in zip(lock,build['sources']):
        if any(actual.get(k)!=v for k,v in expected.items()):raise ValueError('source lock changed; rebuild userspace')
        patch=HERE/'patches'/(expected['name']+'.patch')
        if patch.exists() and actual.get('patch_sha256')!=digest(patch.read_bytes()):raise ValueError('patch changed; rebuild userspace')
    for name in ('sandbox.c','sandbox-policy.h','sha256.h','postexec.c'):
        if build['local_sources'].get(name)!=digest((HERE/name).read_bytes()):raise ValueError('sandbox changed; rebuild userspace')
    for name in ('rmtfs','tqftpserv','diag-router','qrtr-lookup','a9-wifi-sandbox') + (('pd-mapper',) if mode=='userspace' else ()):
        source=binaries/name
        if source.is_symlink():raise ValueError('binary symlink rejected')
        data=source.read_bytes();elf_static_arm64(data)
        if digest(data)!=build['binaries'].get(name):raise ValueError('binary integrity mismatch')
        write('bin/'+name,data,0o755)
        if name not in ('a9-wifi-sandbox','qrtr-lookup'):runtime.append(f'{name} {len(data)} {digest(data)}\n')

    fw='firmware/qcom/sm6115/hisense/hlte556n/'
    for name in ['MODEM.MDT']+[f'MODEM.B{i:02d}' for i in list(range(14))+list(range(15,21))+list(range(23,30))]+['WLANMDSP.MBN','MODEMR.JSN','MODEMUW.JSN']:
        data=asset('modem/IMAGE/'+name)
        write(fw+name.lower(),data)
        if name=='WLANMDSP.MBN':
            if len(data)!=3814580:raise ValueError('unexpected WLANMDSP size')
            runtime.append(f'wlanmdsp {len(data)} {digest(data)}\n')
    bdf=asset('modem/IMAGE/BDWLAN.BIN')
    if len(bdf)!=26328:raise ValueError('unexpected A9 BDF size')
    write('firmware/ath10k/WCN3990/hw1.0/hisense/hlte556n/board.bin',bdf)
    compressed=a.ath10k_fw_dir/'qcm2290/firmware-5.bin.zst'
    if not compressed.is_file():raise ValueError('missing cached ath10k firmware-5.bin; no network fallback')
    fw5=subprocess.check_output(['zstd','-q','-d','-c',str(compressed)])
    if len(fw5)!=FW5_BYTES or digest(fw5)!=FW5_SHA256 or not fw5.startswith(b'QCA-ATH10K\0'):
        raise ValueError('unexpected ath10k firmware-5.bin')
    write(FW5_PATH,fw5)
    for name,size in [('modemst1',2097152),('modemst2',2097152),('fsg',2097152),('fsc',131072)]:
        data=asset('private/'+name+'.bin')
        if len(data)!=size:raise ValueError('unexpected private seed size')
        write('etc/a9-wifi/seeds/'+name+'.bin',data,0o400)
        runtime.append(f'{name} {len(data)} {digest(data)}\n')
    for name in ('regulatory.db','regulatory.db.p7s'):
        local=root/name
        if local.is_file() and not local.is_symlink():data=local.read_bytes()
        else:
            compressed=a.regdb_dir/(name+'.zst')
            if not compressed.is_file():raise ValueError('missing cached signed regulatory database; no network fallback')
            data=subprocess.check_output(['zstd','-q','-d','-c',str(compressed)])
        if not data:raise ValueError('empty signed regulatory database')
        write('firmware/'+name,data)
    write('etc/a9-wifi/assets.sha256',''.join(runtime).encode(),0o400)
    rc=(HERE/'a9-wifi.rc').read_text()
    if mode=='kernel':
        blocks=rc.split('\n\n')
        rc='\n\n'.join(b for b in blocks if 'start vendor.pd_mapper' not in b and 'service vendor.pd_mapper ' not in b)
    write('etc/init/init.a9ml-wifi.rc',rc.encode())
    write('bin/a9ml-wifi-firmware.sh',(HERE/'a9ml-wifi-firmware.sh').read_bytes(),0o755)
    write('bin/a9ml-wifi-modules.sh',(HERE/'a9ml-wifi-modules.sh').read_bytes(),0o755)
    write('bin/a9ml-module-load.sh',(HERE.parent/'android/vendor/bin/a9ml-module-load.sh').read_bytes(),0o755)
    prop=stage/'build.prop'
    if prop.is_symlink():raise ValueError('build.prop symlink rejected')
    with prop.open('a') as f:
        f.write('\n# Optional offline Wi-Fi draft; services and modem stay off by default.\n')
        f.write('ro.a9ml.wifi=1\nro.a9ml.wifi.pd_mapper='+mode+'\n')
    # build-vendor adds wifi/fs-config.txt: pack-vendor otherwise ignores
    # source modes. No MAC plaintext is staged into vendor.
    write('etc/a9-wifi/build-manifest.json',json.dumps({'pd_mapper':mode,'userspace':build,'installed':installed},indent=2).encode()+b'\n',0o400)
    print('Staged reviewed Wi-Fi draft assets; persistent property unset; no MPSS start action.')

if __name__=='__main__':main()
