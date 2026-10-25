#!/usr/bin/env python3
"""OFFLINE S2 release preparation: gated vendor delta, bundle, driver and hash seal.
Never contacts a device, reads 1Password/token, or writes a9-assets. Original images stay intact.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
AW=Path('/home/user/A9-build/mainline/wifi-android')
BASE_VENDOR=AW/'out/vendor_b-eink-wifi-android-session.img'
BASE_VENDOR_SHA='88286d560af8e32cca0b3273627217da65a89aba582ba2183b12556f8dfa4a22'
REVIEWED_VENDOR_SHA='2955a1afaa0145c0238346044379c5cdf29a052a55d69e4ba1226c740c51f670'

def tool_paths():
    tools={}
    for name in ('python3','bash','adb','fastboot','op','grep','shred'):
        target=Path(shutil.which(name)).resolve()
        # Multicall applets dispatch on argv[0]. Keep the immutable store alias,
        # then bind both the alias resolution and the binary bytes in the seal.
        if target.name in ('coreutils','busybox','toybox'):
            applet=target.parent/name
            if not applet.is_file() or applet.resolve()!=target:raise ValueError('tool applet missing')
            tools[name]=str(applet)
        else:tools[name]=str(target)
    return tools

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()

def canonical_image(path):
    # android-boot/lp-write use stat without -L for size and readback. Pass a
    # regular image pathname, never the release's reuse symlink. Bind the
    # sidecar actually consumed through that canonical pathname as well.
    image=Path(path).resolve(strict=True)
    if not image.is_file() or image.stat().st_size<=0 or image.stat().st_size%512:
        raise ValueError('image must be a nonempty sector-aligned regular file')
    sidecar=Path(str(image)+'.sha256')
    fields=sidecar.read_text().split() if sidecar.is_file() else []
    if not fields or fields[0]!=sha(image):
        raise ValueError('canonical image sidecar missing or mismatched')
    return image

def debug(image,cmd,write=False):
    r=subprocess.run(['debugfs',*(['-w'] if write else []),'-R',cmd,str(image)],capture_output=True,check=True)
    return r.stdout

def image_replace(image,name,data,mode,work):
    source=work/'replacement';source.write_bytes(data)
    label=work/'label';label.unlink(missing_ok=True)
    debug(image,f'ea_get -f {label} /{name} security.selinux')
    if not label.is_file() or not label.read_bytes().endswith(b'\0'):raise ValueError('image label missing')
    debug(image,'rm /'+name,True)
    debug(image,f'write {source} /{name}',True)
    debug(image,f'set_inode_field /{name} mode 0{(0o100000|mode):o}',True)
    for field in ('uid','gid'):debug(image,f'set_inode_field /{name} {field} 0',True)
    debug(image,f'ea_set -f {label} /{name} security.selinux',True)
    if debug(image,'cat /'+name)!=data:raise ValueError('image readback')
    check=work/'check-label';debug(image,f'ea_get -f {check} /{name} security.selinux')
    if check.read_bytes()!=label.read_bytes():raise ValueError('image label changed')

def inventory(image,parent):
    with tempfile.TemporaryDirectory(prefix='s2-inventory-',dir=parent) as t:
        debug(image,f'rdump / {t}')
        result={}
        for p in Path(t).rglob('*'):
            name=str(p.relative_to(t))
            if p.is_symlink():result[name]=['symlink',os.readlink(p)]
            elif p.is_file():result[name]=['file',sha(p)]
            elif p.is_dir():result[name]=['directory']
            else:raise ValueError('unexpected image node')
        if len(result)<100:raise ValueError('incomplete image inventory')
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--reuse-vendor',type=Path,help='reference the hash-reviewed gated image without copying or changing image bytes')
    a=p.parse_args();out=a.out.absolute()
    if out.exists() or not re.fullmatch('/home/user/A9-build/[A-Za-z0-9_./-]+',str(out)) or any(x.is_symlink() for x in (out,*out.parents)):
        p.error('new plain directory under A9-build required')
    for suffix in ('.used','.used.pending'):
        marker=out.parent/('s2-87k-attempt1'+suffix)
        if marker.exists() or marker.is_symlink():
            p.error('87k attempt paths already exist')
    reserve=15*1024**3+32*1024**2 if a.reuse_vendor else 16*1024**3
    if shutil.disk_usage(out.parent).free<reserve:p.error('insufficient free space above the 15 GiB reserve')
    if sha(BASE_VENDOR)!=BASE_VENDOR_SHA:p.error('S1 source image changed')
    os.umask(0o077);out.mkdir(mode=0o700)
    rc=(HERE/'s2-session.rc').read_bytes();old_rc=(HERE.parents[1]/'session/init.a9ml-wifi-session.rc').read_bytes()
    if debug(BASE_VENDOR,'cat /etc/init/init.a9ml-wifi-session.rc')!=old_rc:raise ValueError('S1 rc binding')
    source_inventory=None if a.reuse_vendor else inventory(BASE_VENDOR,out)
    vendor=out/'vendor_b-s2-gated.img'
    if a.reuse_vendor:
        if sha(a.reuse_vendor)!=REVIEWED_VENDOR_SHA:raise ValueError('reviewed vendor hash')
        vendor.symlink_to(a.reuse_vendor.resolve())
        if sha(vendor)!=REVIEWED_VENDOR_SHA:raise ValueError('vendor copy hash')
        manifest=json.loads(debug(vendor,'cat /etc/a9-wifi/session/manifest.json'))
        if debug(vendor,'cat /etc/init/init.a9ml-wifi-session.rc')!=rc or manifest['files']['etc/init/init.a9ml-wifi-session.rc']!=hashlib.sha256(rc).hexdigest():
            raise ValueError('reviewed vendor source binding')
    else:
        shutil.copyfile(BASE_VENDOR,vendor)
        with tempfile.TemporaryDirectory(prefix='s2-image-',dir=out) as t:
            temp=Path(t)
            image_replace(vendor,'etc/init/init.a9ml-wifi-session.rc',rc,0o644,temp)
            manifest=json.loads(debug(vendor,'cat /etc/a9-wifi/session/manifest.json'))
            manifest['files']['etc/init/init.a9ml-wifi-session.rc']=hashlib.sha256(rc).hexdigest()
            manifest['s2_start_gate']='vendor.a9ml.wifi.s2.start=1; slot-B store PRE before start'
            image_replace(vendor,'etc/a9-wifi/session/manifest.json',(json.dumps(manifest,indent=2)+'\n').encode(),0o400,temp)
    if not a.reuse_vendor:vendor.chmod(0o400)
    result=subprocess.run(['e2fsck','-fn',str(vendor)],capture_output=True)
    (out/'vendor-fsck.log').write_bytes(result.stdout+result.stderr)
    if result.returncode:raise ValueError('vendor fsck')
    if a.reuse_vendor:
        # Exact image bytes already had a complete 562-entry inventory in 87c/87d.
        # Reusing its pinned hash needs no second extraction near the disk reserve.
        changed=['etc/init/init.a9ml-wifi-session.rc','etc/a9-wifi/session/manifest.json']
        entries=562
    else:
        final_inventory=inventory(vendor,out)
        if source_inventory.keys()!=final_inventory.keys():raise ValueError('image path delta')
        changed=[n for n in source_inventory if source_inventory[n]!=final_inventory[n]]
        entries=len(final_inventory)
    if set(changed)!={'etc/init/init.a9ml-wifi-session.rc','etc/a9-wifi/session/manifest.json'}:raise ValueError('unexpected image data delta')
    (out/'vendor-delta.json').write_text(json.dumps({'source_sha256':BASE_VENDOR_SHA,'vendor_sha256':sha(vendor),'changed_files':sorted(changed),'inventory_entries':entries,'inventory_reused_by_exact_hash':bool(a.reuse_vendor)},indent=2)+'\n')
    (out/'vendor_b-s2-gated.img.sha256').write_text(sha(vendor)+'  vendor_b-s2-gated.img\n')
    subprocess.run(['python3','-I',str(HERE/'build.py'),'--sdk','/home/user/.cache/gen1recomp-a9/sdk','--jdk','/home/user/A9-build/tools/jdk21','--out',str(out/'bundle')],check=True)
    shutil.copyfile(HERE/'driver.py',out/'driver.py');(out/'driver.py').chmod(0o400)
    tools=tool_paths()
    golden=Path('/home/user/a9-assets/Backups/edl_2026-10-14_110759')
    gsi=Path('/home/user/a9-assets/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8.img')
    config={'schema':2,'attempt':1,'repo':str(REPO),'bundle':str(out/'bundle'),'boot':str(AW/'out/boot-demo-wifi.img'),
            'vendor':str(canonical_image(vendor)),'session_rc_sha256':hashlib.sha256(rc).hexdigest(),'token_file':'/run/agenix/op-token','session_seconds':manifest['seconds'],
            'permit':str(out.parent/'s2-87k-attempt1.used'),'runs_root':str(out.parent/'s2-87k-runs'),
            'probes_root':str(out.parent/'s2-87k-probes'),'device_json':'/home/user/A9/local/device.json','tools':tools,
            'env':{'A9_ANDROID':str(AW),'A9_WIFI_SNAPSHOT':str(AW/'kernel-snap-wifi'),
                   'A9_WIFI_BINARIES':'/home/user/A9-build/mainline/sol-wifi/run14-fix/android',
                   'A9_DATA':'/home/user/a9-assets','A9_BACKUPS':'/home/user/a9-assets/Backups','A9_GOLDEN':str(golden),
                   'A9_GSI_IMG':str(gsi),
                   'PYTHONPATH':'/nix/store/g85cbzcan6ja3y749ycizqw6cbbzahbq-python3.13-unicorn-2.1.4/lib/python3.13/site-packages',
                   'PATH':str(Path(tools['python3']).parent)+':'+os.environ['PATH']}}
    names=[Path(x) for x in subprocess.check_output(['git','-C',str(REPO),'ls-files','scripts'],text=True).splitlines()]
    pins=[REPO/n for n in names if (REPO/n).is_file() and not (REPO/n).is_symlink()]
    pins.extend(x for x in HERE.iterdir() if x.is_file())
    pins.extend(x for x in (out/'bundle').iterdir() if x.is_file())
    pins.extend(x for x in (AW/'kernel-snap-wifi').rglob('*') if x.is_file() and not x.is_symlink())
    pins.extend(Path(x) for x in tools.values())
    pins.extend(x for x in Path(config['env']['PYTHONPATH']).rglob('*') if x.is_file() and not x.is_symlink())
    pins.append(Path('/home/user/a9-assets/Backups/edl_2026-10-17_102417/lun4/abl_a.bin'))
    pins.extend([out/'driver.py',vendor,Path(str(vendor)+'.sha256'),Path(config['vendor']+'.sha256'),Path(config['boot']),Path(config['boot']+'.sha256'),
                 AW/'golden/vendor_b.img',AW/'golden/vendor_b.img.sha256',AW/'golden/super-lpdump.txt',
                 golden/'SHA256SUMS',golden/'lun4/boot_b.bin',golden/'lun4/dtbo_b.bin',gsi,Path(config['device_json'])])
    config['aliases']={str(n):str(n.resolve()) for n in pins if n!=n.resolve()}
    config['pins']={str(n.resolve()):sha(n) for n in sorted(set(pins))}
    release=out/'release.json';release.write_text(json.dumps(config,indent=2,sort_keys=True)+'\n');release.chmod(0o400)
    launcher=f'''#!{tools['bash']}
set +x
set -euo pipefail
exec {tools['python3']} -I {out}/driver.py --release {release} --seal {sha(release)} "$@"
'''
    (out/'driver.sh').write_text(launcher);(out/'driver.sh').chmod(0o500)
    (out/'review-seal.json').write_text(json.dumps({'status':'author-tested-plumbing-87k','launcher_sha256':sha(out/'driver.sh'),
        'driver_sha256':sha(out/'driver.py'),'release_sha256':sha(release),'bundle_manifest_sha256':sha(out/'bundle/SHA256SUMS'),
        'vendor_sha256':sha(vendor),'GO_deployed':False,'phone_contact':False,'token_read':False,
        'source_commit':subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
        'captured_regression_sha256':sha(REPO/'tests/mainline-wifi/fixtures/android-s2/s2-87i-provenance.json')},indent=2,sort_keys=True)+'\n')
    print('S2_FREEZE prepared=yes phone_contact=no token_read=no approval=author-tests-required-87k')

if __name__=='__main__':main()
