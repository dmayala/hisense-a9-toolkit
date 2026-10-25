#!/usr/bin/env python3
"""Offline staging of exact Android 16 CF HAL/supplicant artifacts. No device IO.

Libraries remain in separate process-local search directories: the supplicant
APEX libcrypto differs from base vendor libcrypto and must never replace it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct

HERE = Path(__file__).resolve().parent
PUBLIC = {'libbinder_ndk.so','libc.so','libdl.so','liblog.so','libm.so','libvndksupport.so'}

def sha(data): return hashlib.sha256(data).hexdigest()

def safe_stage(stage):
    if any(p.is_symlink() for p in (stage.absolute(),*stage.absolute().parents)):
        raise ValueError('output symlink rejected')
    stage=stage.resolve()
    for forbidden in (HERE.parents[3],Path('/home/user/A9'),Path('/home/user/a9-assets').resolve(),Path('/srv/nas/A9')):
        if stage==forbidden or forbidden in stage.parents: raise ValueError('unsafe stage path')
    if not stage.is_dir() or not (stage/'build.prop').is_file(): raise ValueError('existing vendor stage required')
    return stage

def needed(data):
    """ELF64 little-endian AArch64 DT_NEEDED; do not execute target binaries."""
    if len(data)<64 or data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183:
        raise ValueError('expected ARM64 ELF')
    off=struct.unpack_from('<Q',data,32)[0]; size,count=struct.unpack_from('<HH',data,54)
    if size<56 or off+size*count>len(data): raise ValueError('invalid ELF headers')
    loads=[]; dyn=None
    for i in range(count):
        typ,flags,start,va,pa,fs,ms,align=struct.unpack_from('<IIQQQQQQ',data,off+i*size)
        if start+fs>len(data): raise ValueError('invalid ELF segment')
        if typ==1: loads.append((va,start,fs))
        if typ==2: dyn=(start,fs)
    if dyn is None: raise ValueError('expected dynamic ELF')
    strings=None; entries=[]
    for i in range(dyn[0],dyn[0]+dyn[1],16):
        tag,val=struct.unpack_from('<QQ',data,i)
        if tag==0: break
        if tag==5: strings=val
        if tag==1: entries.append(val)
    for va,start,length in loads:
        if strings is not None and va<=strings<va+length:
            base=start+strings-va; end=start+length
            names=[]
            for n in entries:
                stop=data.find(b'\0',base+n,end)
                if base+n>=end or stop<0: raise ValueError('invalid needed string')
                names.append(data[base+n:stop].decode('ascii'))
            return names
    raise ValueError('unmapped dynamic string table')

def stage_prebuilts(stage, cf):
    stage=safe_stage(stage); cf=cf.resolve()
    prop=stage/'build.prop'
    if prop.is_symlink() or prop.stat().st_nlink!=1: raise ValueError('invalid build.prop')
    lock=json.loads((HERE/'prebuilts.lock.json').read_text()); pending={}; records=[]; groups={}
    for row in lock['files']:
        rel=Path(row['source']); source=cf/rel
        if source.is_symlink() or not source.is_file() or cf not in source.resolve().parents:
            raise ValueError('invalid prebuilt source')
        data=source.read_bytes()
        if len(data)!=row['bytes'] or sha(data)!=row['sha256']: raise ValueError('pinned prebuilt changed: '+str(rel))
        package=rel.parts[1]; path=Path(*rel.parts[3:]); group='hal' if package=='com.android.hardware.wifi' else 'supplicant'
        if path.parts[0]=='lib64': target=Path('lib64/a9-wifi')/group/path.name
        elif path.parts[0]=='bin': target=path
        elif 'vintf' in path.parts: target=Path('etc/vintf/manifest')/path.name.removesuffix('.prebuilt')
        else: raise ValueError('unreviewed prebuilt member')
        deps=needed(data) if path.parts[0] in ('bin','lib64') else []
        groups.setdefault(group,{})[path.name]=deps
        if target in pending: raise ValueError('duplicate payload destination')
        pending[target]=(data,0o755 if path.parts[0]=='bin' else 0o644)
        records.append(dict(row,target=str(target)))
    for group,libs in groups.items():
        for name,deps in libs.items():
            if set(deps)-set(libs)-PUBLIC: raise ValueError('missing process-local dependency: '+name)
    # Read every input and validate every destination before copying anything.
    for name in ('wpa_supplicant.conf','wpa_supplicant_overlay.conf','p2p_supplicant.conf','p2p_supplicant_overlay.conf','hostapd.conf'):
        pending[Path('etc/wifi')/name]=((HERE/'config'/name).read_bytes(),0o644)
    pending[Path('etc/init/init.a9ml-wifi-android.rc')]=((HERE/'a9-wifi-android.rc').read_bytes(),0o644)
    pending[Path('bin/a9ml-wifi-android-start.sh')]=((HERE/'a9ml-wifi-android-start.sh').read_bytes(),0o755)
    manifest_rel=Path('etc/a9-wifi/android-bundle.json')
    for rel in (*pending,manifest_rel):
        target=stage/rel
        if any(n.is_symlink() for n in (target,*target.parents)): raise ValueError('destination symlink rejected')
        if target.exists() and not (str(rel).startswith('etc/wifi/') and target.is_file()):
            raise ValueError('destination exists: '+str(rel))
    for rel,(data,mode) in pending.items():
        target=stage/rel;target.parent.mkdir(parents=True,exist_ok=True)
        # Replace Cuttlefish templates atomically, never follow a hardlink.
        temporary=target.with_name(target.name+'.a9-new')
        with temporary.open('xb') as f: f.write(data)
        temporary.chmod(mode);os.replace(temporary,target)
    manifest={'build':lock['build'],'mode':'experimental-cf-prebuilt','files':records,
              'caveats':['country HAL is a no-op','emulator APF is not hardware offload','5 GHz/channel reporting incomplete'],
              'activation':'vendor.a9ml.wifi.android.ready=1 only after coordinator driver checks'}
    dest=stage/manifest_rel;dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('x') as f: json.dump(manifest,f,indent=2);f.write('\n')
    dest.chmod(0o400)
    with prop.open('a') as f:f.write('\nro.a9ml.wifi.android=1\nwifi.interface=wlan0\nwifi.direct.interface=p2p-dev-wlan0\n')
    print('Staged pinned Android 16 CF Wi-Fi prebuilts; explicit runtime gate remains unset.')

def stage_policy(stage):
    """Use existing compiled HAL types and new radio types; no phhsu labels."""
    stage=safe_stage(stage)
    files={'vendor_sepolicy.cil':'a9-wifi.cil','vendor_file_contexts':'file_contexts'}
    pending=[]
    for dest,src in files.items():
        target=stage/'etc/selinux'/dest
        if any(n.is_symlink() for n in (target,*target.parents)) or target.stat().st_nlink!=1: raise ValueError('invalid policy output')
        text=target.read_text(); marker='# A9 Wi-Fi labels' if dest.endswith('contexts') else '; A9 Wi-Fi labels'
        if marker in text: raise ValueError('Wi-Fi policy already installed')
        pending.append((target,text+'\n'+marker+'\n'+(HERE/'sepolicy'/src).read_text()))
    for target,text in pending:target.write_text(text)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--cf',type=Path);p.add_argument('--policy-only',action='store_true');a=p.parse_args()
    if a.policy_only: stage_policy(a.stage)
    elif a.cf is None:p.error('--cf required for prebuilts')
    else:stage_prebuilts(a.stage,a.cf)

if __name__=='__main__':main()
