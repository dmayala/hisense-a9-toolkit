#!/usr/bin/env python3
"""Build the pinned jack HAL correction and framework input overlay; host only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess as sp

HERE=Path(__file__).resolve().parent
ROOT=Path('/home/user/A9-build/mainline')
NDK=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64')
TOOLS=Path('/nix/store/iqyx3zkviyc1ik9snyhhw77aanc4b3zx-android-sdk-build-tools-36.0.0/libexec/android-sdk/build-tools/36.0.0')
JAVA=Path('/nix/store/fnshd55b29s6z88mfl3ilmdqfb7l3dqi-openjdk-21.0.7+6')
SDK=Path('/nix/store/ynv47k6r22z1x1062cyqvxyvxwmbv42h-android-sdk-platforms-36/libexec/android-sdk/platforms/android-36/android.jar')
DONOR=ROOT/'android/dl/cf_arm64_x/apex/com.android.hardware.audio/root/bin/hw/android.hardware.audio.service-aidl.example'
SIGNING_KEY=ROOT/'agent-headphone-20261024/overlay.p12'
DONOR_SHA='2fb5385e20cf8a585c568225d8275e3a1189ef25d5c193c7346a8a6f0a17bf8f'
THRESHOLDS=(75000,76000,280000,450000,460000,460000,460000,460000)
DT_NODE='/a9-wcd-codec'
APK='overlay/A9Jack.apk'
HAL='bin/hw/android.hardware.audio.service-aidl.a9'
POLICY='etc/primary_audio_policy_configuration.xml'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def sections(b):
    off=struct.unpack_from('<Q',b,40)[0];size,n,names=struct.unpack_from('<HHH',b,58)
    ss=[struct.unpack_from('<IIQQQQIIQQ',b,off+i*size) for i in range(n)]
    strings=b[ss[names][4]:ss[names][4]+ss[names][5]]
    return {strings[s[0]:].split(b'\0',1)[0].decode():s for s in ss}

def file_offset(b,va):
    for s in sections(b).values():
        if s[1]!=8 and s[3]<=va<s[3]+s[5]:return s[4]+va-s[3]
    raise ValueError(hex(va))

def branch(source,target):
    delta=target-source;assert delta%4==0 and -(1<<27)<=delta<(1<<27)
    return struct.pack('<I',0x14000000|((delta//4)&0x3ffffff))

def dt_properties():
    return {'qcom,mbhc-buttons-vthreshold-microvolt':THRESHOLDS,
            'qcom,mbhc-headset-vthreshold-microvolt':(1700000,)}

def update_dtb(dtb):
    dtc=Path('/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin')
    assert sp.check_output([dtc/'fdtget',dtb,DT_NODE,'compatible'],text=True).strip()=='hisense,a9-wcd937x'
    for prop,values in dt_properties().items():
        sp.run([dtc/'fdtput','-t','i',dtb,DT_NODE,prop,*map(str,values)],check=True)
        assert tuple(map(int,sp.check_output([dtc/'fdtget','-t','i',dtb,DT_NODE,prop],text=True).split()))==values

def build(out):
    out=out.resolve();assert not out.exists(),'preserve prior output'
    assert shutil.disk_usage(out.parent).free>15*1024**3+20*1024**2
    out.mkdir();env=dict(os.environ,JAVA_HOME=str(JAVA),PATH=str(JAVA/'bin')+':'+os.environ['PATH'])
    def run(args,name):
        with (out/(name+'.log')).open('w') as f:sp.run(list(map(str,args)),env=env,stdout=f,stderr=sp.STDOUT,check=True)
    donor=DONOR.read_bytes();assert sha(DONOR)==DONOR_SHA
    cc=NDK/'bin/aarch64-linux-android30-clang'
    run([cc,'-O2','-ffreestanding','-fno-stack-protector','-fno-unwind-tables','-fno-asynchronous-unwind-tables','-c',HERE/'hal.c','-o',out/'hal.o'],'compile-c')
    run([cc,'-c',HERE/'hal-entry.S','-o',out/'entry.o'],'compile-entry')
    # Reuse only the original base implementation body. Other functions and
    # all ELF layout, dynamic relocations/vtables remain unchanged.
    (out/'patch.ld').write_text('''SECTIONS {
      . = 0x72320;
      .jack : { *(.text.port_entry) *(.text.port_predicate) *(.text.wired_select) }
      /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
    }
    AStatus_newOk = 0x10b120;
    AStatus_fromExceptionCode = 0x10b270;
    a9_store_continue = 0xc767c;
    ''')
    run([NDK/'bin/ld.lld','-T',out/'patch.ld',out/'entry.o',out/'hal.o','-o',out/'patch.elf'],'link')
    patch_elf=(out/'patch.elf').read_bytes();sec=sections(patch_elf)['.jack'];code=patch_elf[sec[4]:sec[4]+sec[5]]
    assert sec[3]==0x72320 and 0<len(code)<=664
    # Recover the local helper address from its ELF symbol table.
    syms=sections(patch_elf)['.symtab'];strings=sections(patch_elf)['.strtab'];names=patch_elf[strings[4]:strings[4]+strings[5]]
    helper=None
    for i in range(syms[4],syms[4]+syms[5],24):
        n,_,_,_,va,_=struct.unpack_from('<IBBHQQ',patch_elf,i)
        if names[n:].split(b'\0',1)[0]==b'a9_wired_select':helper=va
    assert helper is not None
    data=bytearray(donor);start=file_offset(data,0x72320);store=file_offset(data,0xc7678)
    assert data[start:start+4]==bytes.fromhex('ffc301d1')
    assert data[store:store+4]==bytes.fromhex('b7e200f9') # str x23,[x21,#0x1c0]
    data[start:start+len(code)]=code
    data[store:store+4]=branch(0xc7678,helper)
    changed={i for i,(a,b) in enumerate(zip(donor,data)) if a!=b}
    assert changed and all(start<=i<start+len(code) or store<=i<store+4 for i in changed)
    hal=out/'audio-hal';hal.write_bytes(data);hal.chmod(0o755)
    run([TOOLS/'aapt2','compile','--dir',HERE/'overlay/res','-o',out/'resources.zip'],'overlay-compile')
    run([TOOLS/'aapt2','link','-I',SDK,'--manifest',HERE/'overlay/AndroidManifest.xml','-o',out/'unsigned.apk',out/'resources.zip'],'overlay-link')
    if not SIGNING_KEY.exists():
        SIGNING_KEY.parent.mkdir(parents=True,exist_ok=True)
        run([JAVA/'bin/keytool','-genkeypair','-keystore',SIGNING_KEY,'-storepass','android','-keypass','android','-alias','overlay','-keyalg','RSA','-keysize','2048','-validity','3650','-dname','CN=A9 Jack Overlay'],'overlay-key')
    run([TOOLS/'apksigner','sign','--ks',SIGNING_KEY,'--ks-pass','pass:android','--ks-key-alias','overlay','--out',out/'A9Jack.apk',out/'unsigned.apk'],'overlay-sign')
    run([TOOLS/'apksigner','verify',out/'A9Jack.apk'],'overlay-verify')
    record={'donor_sha256':DONOR_SHA,'hal_sha256':sha(hal),'hal_helper_va':hex(helper),'code_bytes':len(code),
            'source_sha256':{str(p.relative_to(HERE)):sha(p) for p in [HERE/'build.py',HERE/'hal.c',HERE/'hal-entry.S',*sorted((HERE/'overlay').rglob('*.xml'))]},
            'apk_sha256':sha(out/'A9Jack.apk'),'host_only':True,'playback_executed':False,'hardware_tested':False}
    (out/'build.json').write_text(json.dumps(record,indent=2)+'\n')
    return {HAL:hal,APK:out/'A9Jack.apk',POLICY:HERE.parent/'output/primary_audio_policy_configuration.xml'},record

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();_,record=build(args.out);print(json.dumps(record,indent=2))
