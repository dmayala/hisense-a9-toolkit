#!/usr/bin/env python3
"""Production listener-root stage in real phone mksh/toybox under QEMU.

Only Android uid and absolute paths/mount tables are fixtures. Copy, find,
readlink, hashing, modes and shell behavior execute the production operations.
No mount syscall, device transport, persist image or stock calibration is used.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess as sp
import tempfile

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'scripts/mainline/sensors/s1-listener-root.sh'
CASES=('pass','tuple','bundle-hash','nonram-data','nonram-persist','real-persist',
       'nested-persist','duplicate-persist','seed-alias','vendor-alias',
       'seed-link','vendor-link','seed-fifo','vendor-fifo','seed-hash',
       'existing-root','root-link','soc0-missing','soc0-link','soc0-extra','soc0-alias')
WHY={'tuple':'tuple-mismatch','bundle-hash':'bundle-hash','nonram-data':'nonram-data',
     'nonram-persist':'nonram-persist','real-persist':'real-persist','nested-persist':'nested-mount',
     'duplicate-persist':'mounts','seed-alias':'seed-alias','vendor-alias':'vendor-alias',
     'seed-link':'source-special','vendor-link':'source-special','seed-fifo':'source-special',
     'vendor-fifo':'source-special','seed-hash':'seed-hash','existing-root':'consumed-root','root-link':'consumed-root',
     'soc0-missing':'soc0-missing','soc0-link':'soc0-missing','soc0-extra':'soc0-extra','soc0-alias':'soc0-alias'}
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def inventory(p):
    return {str(f.relative_to(p)):(stat.S_IMODE(f.lstat().st_mode),
            ('link',str(f.readlink())) if f.is_symlink() else
            ('file',f.read_bytes()) if f.is_file() else
            ('dir',) if f.is_dir() else ('special',stat.S_IFMT(f.lstat().st_mode)))
            for f in sorted(p.rglob('*'))}
def run(sysroot,qemu,selected):
    with tempfile.TemporaryDirectory(prefix='s1-listener-root-phone-') as td:
        base=Path(td); x=base/'xbin'; x.mkdir()
        bash=shutil.which('bash'); q=str(qemu)
        linker=str(sysroot/'system/bin/linker64'); toy=str(sysroot/'system/bin/toybox')
        shell=str(sysroot/'system/bin/sh')
        for app in ('sha256sum','cut','readlink','find','mkdir','chmod','timeout','cp','wc'):
            p=x/app; p.write_text(f'#!{bash}\nexec {q} -L / {linker} {toy} {app} "$@"\n'); p.chmod(0o755)
        p=x/'id'; p.write_text(f'#!{bash}\nprintf "0\\n"\n'); p.chmod(0o755)
        for case in CASES:
            if selected and case not in selected: continue
            r=base/case; b=r/'data/local/tmp/sensors-s1'; b.mkdir(parents=True)
            persist=r/'mnt/vendor/persist'; seed=persist/'sensors'; seed.mkdir(parents=True)
            cfg=b/'vendor-config'; cfg.mkdir()
            (seed/'registry/registry').mkdir(parents=True)
            (seed/'registry/registry/synthetic').write_bytes(b'synthetic non-calibration registry\n')
            (seed/'settings').write_bytes(b'synthetic settings\n')
            (cfg/'config').mkdir(); (cfg/'config/synthetic.json').write_bytes(b'{"synthetic":true}\n')
            (cfg/'sns_reg_config').write_bytes(b'path=/persist/sensors/registry/registry\n')
            soc=b/'soc0'; soc.mkdir()
            for name,text in (('hw_platform','QRD'),('platform_subtype','Unknown'),('platform_subtype_id','0'),
                              ('platform_version','65536'),('revision','1.0'),('soc_id','444')):
                (soc/name).write_bytes(text.encode()+b'\n')
            for tree in (seed,cfg,soc):
                for f in tree.rglob('*'): f.chmod(0o700 if f.is_dir() else 0o600)
            original_seed=inventory(seed); original_cfg=inventory(cfg)
            expected_seed=inventory(seed); expected_cfg=inventory(cfg); expected_soc=inventory(soc)
            if case=='seed-alias':
                target=r/'seed-target'; seed.rename(target); seed.symlink_to(target,target_is_directory=True)
            if case=='vendor-alias':
                target=r/'vendor-target'; cfg.rename(target); cfg.symlink_to(target,target_is_directory=True)
            if case in ('seed-link','vendor-link'):
                target=seed if case=='seed-link' else cfg
                (target/'unexpected-link').symlink_to(r/'protected-file')
            if case in ('seed-fifo','vendor-fifo'):
                target=seed if case=='seed-fifo' else cfg; os.mkfifo(target/'unexpected-fifo')
            if case=='soc0-missing': (soc/'soc_id').unlink()
            if case=='soc0-link': (soc/'soc_id').unlink(); (soc/'soc_id').symlink_to(r/'protected-file')
            if case=='soc0-extra': (soc/'extra').write_bytes(b'x'); (soc/'extra').chmod(0o600)
            if case=='soc0-alias':
                target=r/'soc0-target'; soc.rename(target); soc.symlink_to(target,target_is_directory=True)
            listener=persist/'s1-listener-root'
            if case=='existing-root': listener.mkdir(); (listener/'canary').write_bytes(b'protected')
            if case=='root-link': listener.symlink_to(r/'nonexistent')
            (r/'proc').mkdir()
            data_dev='/dev/block/sda1' if case=='nonram-data' else '/dev/block/ram0'
            persist_dev='/dev/block/by-name/persist' if case=='real-persist' else 'tmpfs'
            persist_fs='ext4' if case=='nonram-persist' else 'tmpfs'
            mounts=f'{data_dev} /data ext4 rw 0 0\n{persist_dev} {persist} {persist_fs} rw 0 0\n'
            if case=='nested-persist': mounts+=f'tmpfs {persist}/sensors tmpfs rw 0 0\n'
            if case=='duplicate-persist': mounts+=f'tmpfs {persist} tmpfs rw 0 0\n'
            (r/'proc/mounts').write_text(mounts)
            (b/'registry.sha256').write_text(''.join((('0'*64) if case=='seed-hash' else sha(f))+'  '+str(f)+'\n'
                                                  for f in sorted(seed.rglob('*')) if f.is_file() and not f.is_symlink()))
            # Include only the ordinary input files, never intentional FIFO/link
            # negative controls; the production source scan must reject those.
            inputs=[b/'registry.sha256',*[f for f in cfg.rglob('*') if f.is_file() and not f.is_symlink()],
                    *[f for f in soc.rglob('*') if f.is_file() and not f.is_symlink()]]
            (b/'SHA256SUMS').write_text(''.join(sha(f)+'  '+str(f.relative_to(b))+'\n' for f in inputs))
            tuple_=sha(b/'SHA256SUMS') if case!='tuple' else '0'*64
            if case=='bundle-hash': (cfg/'sns_reg_config').write_bytes(b'tampered')
            seed_before=inventory(seed); cfg_before=inventory(cfg); soc_before=inventory(soc)
            body=SCRIPT.read_text()
            prefixes=('/data/local/tmp/sensors-s1','/proc/mounts','/mnt/vendor/persist')
            body=re.sub('|'.join(re.escape(p) for p in prefixes),lambda m:str(r)+m[0],body)
            script=r/'stage.sh'; script.write_text(body)
            env={**os.environ,'PATH':str(x),'LD_LIBRARY_PATH':str(sysroot/'system/lib64')}
            result=sp.run([q,'-L','/',linker,shell,str(script),'--prepare-root',tuple_],env=env,
                          stdout=sp.PIPE,stderr=sp.STDOUT,timeout=45)
            output=result.stdout.decode(errors='replace')
            assert (result.returncode==0)==(case=='pass'),(case,result.returncode,output)
            assert inventory(seed)==seed_before and inventory(cfg)==cfg_before and inventory(soc)==soc_before,(case,'source changed')
            if case=='pass':
                assert 'S1-LISTENER-ROOT-STAGED RAM-COPY; no attach or registry-delivery claim' in output,output
                assert stat.S_IMODE(listener.stat().st_mode)==0o700
                assert inventory(listener/'persist/sensors')==expected_seed
                assert inventory(listener/'vendor/etc/sensors')==expected_cfg
                assert inventory(listener/'sys/devices/soc0')==expected_soc and (listener/'sys/devices/soc0/soc_id').read_bytes()==b'444\n'
                # Subsequent listener writes must affect only the RAM copy.
                (listener/'persist/sensors/registry/registry/synthetic').write_bytes(b'updated RAM copy')
                assert inventory(seed)==original_seed and inventory(cfg)==original_cfg
            else:
                assert 'S1-LISTENER-ROOT-FAIL '+WHY[case] in output,(case,output)
                if case=='existing-root': assert (listener/'canary').read_bytes()==b'protected'
                elif case=='root-link': assert listener.is_symlink()
                else: assert not listener.exists() and not listener.is_symlink(),(case,'unexpected partial root')
            print('PASS phone-shell listener root:',case,flush=True)
    print('S1 LISTENER ROOT PHONE TEST PASS',flush=True)
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sysroot',required=True,type=Path); ap.add_argument('--qemu',required=True,type=Path)
    ap.add_argument('--case',action='append'); a=ap.parse_args()
    run(a.sysroot.resolve(),a.qemu.resolve(),a.case)
