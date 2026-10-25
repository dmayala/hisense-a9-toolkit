#!/usr/bin/env python3
"""Host/QEMU production-byte replay; never enters HAL main or opens audio."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess as sp
import xml.etree.ElementTree as ET
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('jack_build',HERE/'build.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--stage',type=Path,required=True);a=ap.parse_args();out=a.stage
cc=m.NDK/'bin/aarch64-linux-android30-clang'
sp.run([cc,'-O2','-Wall','-Wextra','-Werror',HERE/'test.c','-o',out/'test-hal'],check=True)
# Verify the donor's text VA equals file offset for the copied intervals.
b=m.DONOR.read_bytes();assert all(m.file_offset(b,v)==v for v in (0x72320,0xc7678))
sysroot=out/'sysroot';(sysroot/'system/bin').mkdir(parents=True,exist_ok=True)
shutil.copyfile(m.ROOT/'android/dl/cf_arm64_x/tf/VENDOR_BOOT/RAMDISK/system/bin/linker64',sysroot/'system/bin/linker64');(sysroot/'system/bin/linker64').chmod(0o755)
q='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
system=m.ROOT/'adsp/d2-v8-20261017/system-image/system'
libs=':'.join(map(str,[system/'lib64/bootstrap',system/'lib64']))
cmd=[q,'-L',str(sysroot),'-E','LD_LIBRARY_PATH='+libs]
for donor,flag,name in [(m.DONOR,'0','before'),(out/'audio-hal','1','after')]:
 r=sp.run([*cmd,str(out/'test-hal'),str(donor),flag],capture_output=True,text=True,timeout=30)
 (out/('test-'+name+'.log')).write_text(r.stdout+r.stderr);print(name,r.stdout+r.stderr);assert r.returncode==0
# Real production DTB: apply only the two calibrated properties.
dtb=out/'jack.dtb';shutil.copyfile(m.ROOT/'integ-20261024-26/integ.dtb',dtb);m.update_dtb(dtb)
# Current policy exposes outputs as external and retains manual endpoint ownership.
p=ET.parse(HERE.parent/'output/primary_audio_policy_configuration.xml').getroot();attached={e.text for e in p.find('attachedDevices')}
for n in ('Wired Headphones','Wired Headset'):
 assert n not in attached
 dev=next(x for x in p.find('devicePorts') if x.get('tagName')==n)
 assert not dev.get('address') and dev.find('profile').get('samplingRates')=='48000'
 assert any(x.get('sink')==n and x.get('sources')=='primary output' for x in p.find('routes'))
src=m.ROOT/'linux-7.2.10-gpu/sound/soc/codecs/wcd-mbhc-v2.c'
text=src.read_text();assert 'cfg->btn_high[i] = cfg->btn_high[i]/1000' in text
assert tuple(v//1000 for v in m.THRESHOLDS)==(75,76,280,450,460,460,460,460)
r=sp.run([m.TOOLS/'aapt2','dump','resources',out/'A9Jack.apk'],capture_output=True,text=True,check=True)
assert 'config_useDevInputEventForAudioJack' in r.stdout and 'true' in r.stdout
(out/'overlay-resources.txt').write_text(r.stdout)
idmap=system/'bin/idmap2'
# Default idmap2 create enforces overlayable/policy; never pass --ignore-overlayable.
r=sp.run([*cmd,str(idmap),'create','--target-apk-path',str(system/'framework/framework-res.apk'),
          '--overlay-apk-path',str(out/'A9Jack.apk'),'--idmap-path',str(out/'jack.idmap'),'--policy','vendor'],capture_output=True,text=True)
(out/'idmap-create.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
r=sp.run([*cmd,str(idmap),'lookup','--idmap-path',str(out/'jack.idmap'),'--config','','--resid','android:bool/config_useDevInputEventForAudioJack'],capture_output=True,text=True)
(out/'idmap-lookup.log').write_text(r.stdout+r.stderr);assert r.returncode==0 and 'value=true' in r.stdout,r.stderr
print('PASS production DT calibration, external wired policy, and compiled input overlay')
(out/'tests.json').write_text(json.dumps({'real_android_arm64_qemu':True,'production_byte_replay_before_after':True,'dtb_calibration':True,'policy_and_overlay':True,'phone_access':False,'playback_executed':False,'hardware_tested':False},indent=2)+'\n')
