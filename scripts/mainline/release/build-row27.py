#!/usr/bin/env python3
"""Package row27 vendor release around the existing signed fence-fix artifacts."""
from pathlib import Path
import difflib,gzip,hashlib,importlib.util,json,os,re,shutil,struct,subprocess as sp
HERE=Path(__file__).resolve().parent;WT=HERE.parents[2]
ROOT=Path('/home/user/A9-build/mainline');O=ROOT/'row27-release-20261022'
BASE=ROOT/'plain-integ-audiofix4-20261022/vendor.img'
SYSTEM=Path('/home/user/A9-build/roms/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8.img')
FENCE=ROOT/'plain-integ-fencefix-20261022';DEADLINE=ROOT/'plain-integ-fencefix-deadline-logfix1-wdt-20261022'
OTA=ROOT/'android/dl/cf_arm64_x/otatools'
ENV=dict(os.environ,PATH='/nix/store/z0dbybxar9l1xy2cvzwi7kkvllrasmay-python3-3.13.7-env/bin:'+':'.join(json.loads((ROOT/'demo-integrated-20261019/source.json').read_text())['tools'])+':'+os.environ['PATH'])
def mod(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
meta=mod('meta',WT/'scripts/mainline/android/refresh-integrated-vendor.py')
def release_rc(text):
    blocks = re.split(r'(?=^(?:service |on |import ))', text, flags=re.M)
    result = []
    for block in blocks:
        lines = block.splitlines(keepends=True)
        if not lines:
            continue
        if lines[0].startswith('service a9ml_rootsh ') or (
                lines[0].startswith('on ') and 'property:vendor.a9ml.rootsh=' in lines[0]):
            continue
        lines = [line for line in lines if line.lstrip().startswith('#') or not any(
            token in line for token in (
                'start a9ml_rootsh', 'stop a9ml_rootsh', '/securize_disable',
                '/adb_keys', '/acm.gs9', '/ncm.gs8', 'a9ml_test_awake',
                'exec_background u:r:a9ml_rootsh:s0',
            ))]
        if lines[0].startswith('on ') and not any(
                line.strip() and not line.lstrip().startswith('#') for line in lines[1:]):
            continue
        result.extend(lines)
    return ''.join(result)



def binary_permissives(data):
    # policydb header then policycaps and permissive ebitmaps, Linux format v30.
    magic, length = struct.unpack_from('<II', data)
    assert magic == 0xf97cff8c and data[8:8+length] == b'SE Linux'
    pos = 8 + length
    version, config, sym_num, ocon_num = struct.unpack_from('<IIII', data, pos)
    assert version == 30
    pos += 16
    maps = []
    for _ in range(2):
        width, highbit, count = struct.unpack_from('<III', data, pos)
        assert width == 64 and count <= len(data)//12
        pos += 12
        bits = []
        for _ in range(count):
            start, bitmap = struct.unpack_from('<IQ', data, pos)
            pos += 12
            bits += [start+i for i in range(64) if bitmap & (1 << i)]
        maps.append(bits)
    return dict(version=version, policycaps=maps[0], permissive_bits=maps[1], permissive_count=len(maps[1]))



def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def read(image,path):return sp.check_output(['debugfs','-R','cat /'+path,str(image)],stderr=sp.DEVNULL)
def run(args,name):
 print(name,flush=True)
 (O/(name+'.command.json')).write_text(json.dumps(list(map(str,args)))+'\n')
 with (O/(name+'.log')).open('w') as f:r=sp.run(list(map(str,args)),env=ENV,stdout=f,stderr=sp.STDOUT)
 (O/(name+'.rc')).write_text(str(r.returncode)+'\n')
 if r.returncode:raise RuntimeError((name,r.returncode))
def save(path,data):
 f=O/'vendor-overlay'/path;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(data);return f
def install(changes,removed):
 target=O/'vendor.img';assert not target.exists()
 run(['cp','--sparse=always',BASE,target],'vendor-copy')
 commands=[];metadata={};delta={}
 for i,(path,f) in enumerate(changes.items()):
  mt=meta.metadata(BASE,path);metadata[path]=mt
  lab=O/('label-'+str(i));lab.write_bytes(mt['label'].encode()+b'\0')
  commands += ['rm /'+path,f'write {f} /{path}',f'sif /{path} mode 010{mt["mode"]}',f'sif /{path} uid {mt["uid"]}',f'sif /{path} gid {mt["gid"]}',f'ea_set -f {lab} /{path} security.selinux']
  for t in ['atime','mtime','ctime','crtime']:commands.append(f'sif /{path} {t} @{int(mt[t],16)}')
  delta[path]={'before':hashlib.sha256(read(BASE,path)).hexdigest(),'after':sha(f)}
 commands += ['rm /'+p for p in removed]
 batch=O/'install.debugfs';batch.write_text('\n'.join(commands)+'\n');run(['debugfs','-w','-f',batch,target],'vendor-install')
 for path,f in changes.items():
  assert read(target,path)==f.read_bytes(),path
  assert meta.metadata(target,path)==metadata[path],path
 for path in removed:assert not read(target,path),path
 (O/'vendor-delta.json').write_text(json.dumps({'changed':delta,'removed':removed,'metadata_preserved':True},indent=2)+'\n')
 run(['e2fsck','-fn',target],'vendor-fsck')
def main():
 os.umask(0o077)
 inv=json.loads((O/'vendor-inventory.json').read_text())
 assert sha(BASE)=='3050225ce4e7026186b50c495aaa2c0d57c6714a39214da7048c031729f24618'
 assert shutil.disk_usage(O).free>3*1024**3
 changes={p:O/'vendor-overlay'/p for p in json.loads((O/'apex-signatures.json').read_text())}
 diff=[]
 for path in inv:
  if path.endswith('.rc') and path.startswith('etc/init/'):
   old=read(BASE,path).decode();new=release_rc(old)
   new='\n'.join(l for l in new.splitlines() if not(l.lstrip().startswith('#') and any(t in l for t in ['rootsh','root shell','permissive','adb_keys','securize_disable','2324','ttyGS0','ACM','NCM','test_awake'])))+'\n'
   if path=='etc/init/hw/init.hlte556n.rc':
    new+='\n'+(WT/'scripts/mainline/boot-deadline/init.a9-boot-deadline.rc').read_text()
   if new!=old:
    changes[path]=save(path,new.encode());diff.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='audiofix4/'+path,tofile='release/'+path))
 old=read(BASE,'build.prop').decode()
 props={'ro.vendor.build.type':'user','ro.vendor.build.tags':'release-keys','ro.vendor.build.id':'A9ML.20261022','ro.vendor.build.version.incremental':'20261022','ro.vendor.build.fingerprint':'Hisense/hlte556n_mainline/HLTE556N:16/A9ML.20261022/20261022:user/release-keys','ro.build.type':'user','ro.debuggable':'0','ro.secure':'1','ro.adb.secure':'1','ro.force.debuggable':'0','ro.vendor.a9ml.release':'1','persist.sys.usb.config':'adb'}
 lines=[l for l in old.splitlines() if l.split('=',1)[0].strip() not in props]
 new='\n'.join(lines)+'\n\n# Mainline release vendor; USB debugging remains key-authenticated.\n'+'\n'.join(k+'='+v for k,v in props.items())+'\n'
 changes['build.prop']=save('build.prop',new.encode())
 diff.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='audiofix4/build.prop',tofile='release/build.prop'))
 changes['bin/a9ml-usb.sh']=save('bin/a9ml-usb.sh',(HERE/'a9ml-usb-release.sh').read_bytes())
 run(['bash','-n',changes['bin/a9ml-usb.sh']],'usb-shell-syntax')
 # Link original GSI split policy with an enforcing vendor-owned precompiled
 # policy. Original GSI bytes and its signer are never changed.
 version=read(BASE,'etc/selinux/plat_sepolicy_vers.txt').decode().strip()
 sourcepaths=['system/etc/selinux/plat_sepolicy.cil',f'system/etc/selinux/mapping/{version}.cil','system/system_ext/etc/selinux/system_ext_sepolicy.cil',f'system/system_ext/etc/selinux/mapping/{version}.cil','system/product/etc/selinux/product_sepolicy.cil',f'system/product/etc/selinux/mapping/{version}.cil']
 files=[];platform_receipt={};stripnotes={}
 policy=O/'policy-inputs';policy.mkdir(exist_ok=True)
 for i,path in enumerate(sourcepaths):
  original=read(SYSTEM,path);text=original.decode()
  removed=re.findall(r'\(typepermissive\s+[^)]+\)',text)
  text=re.sub(r'\(typepermissive\s+[^)]+\)','',text)
  f=policy/(str(i)+'-'+Path(path).name);f.write_text(text);files.append(f)
  platform_receipt[path]={'original_sha256':hashlib.sha256(original).hexdigest(),'compiled_input_sha256':sha(f),'removed_permissive_declarations':removed}
 public=policy/'plat_pub_versioned.cil';public.write_bytes(read(BASE,'etc/selinux/plat_pub_versioned.cil'));files.append(public)
 vendor=read(BASE,'etc/selinux/vendor_sepolicy.cil').decode()
 stripnotes['vendor']=re.findall(r'\(typepermissive\s+[^)]+\)',vendor)
 vendor=re.sub(r'\(typepermissive\s+[^)]+\)','',vendor)
 prepare=mod('prepare',WT/'scripts/mainline/boot-deadline/prepare-vendor.py')
 additions=[l for l in prepare.RULES.decode().splitlines() if l.startswith('(') and l not in vendor.splitlines()]
 vendor+='\n; Mainline release boot defaults must override inherited GSI debug defaults.\n'
 vendor+='(allow vendor_init_202504 build_prop_202504 (property_service (set)))\n'
 vendor+='(allow vendor_init_202504 userdebug_or_eng_prop_202504 (property_service (set)))\n'
 vendor+='\n; Existing deadline/logger handoff.\n'+'\n'.join(additions)+'\n'
 changes['etc/selinux/vendor_sepolicy.cil']=save('etc/selinux/vendor_sepolicy.cil',vendor.encode());files.append(changes['etc/selinux/vendor_sepolicy.cil'])
 binary=save('etc/selinux/precompiled_sepolicy',b'')
 run([OTA/'bin/secilc','-m','-M','true','-G','-N','-c','30',*files,'-o',binary,'-f','/dev/null'],'policy-link')
 assert binary_permissives(binary.read_bytes())['permissive_count']==0
 changes['etc/selinux/precompiled_sepolicy']=binary
 # Sidecar selection must match the unchanged GSI, or init would recompile
 # from its still-present development declarations.
 selectors={}
 for prefix,dir in [('plat','system/etc/selinux'),('system_ext','system/system_ext/etc/selinux'),('product','system/product/etc/selinux')]:
  name=prefix+'_sepolicy_and_mapping.sha256';remote='etc/selinux/precompiled_sepolicy.'+name
  actual=read(SYSTEM,dir+'/'+name)
  assert actual,dir
  selectors[remote]=actual.decode().strip()
  if read(BASE,remote)!=actual:changes[remote]=save(remote,actual)
 for path in ['lib/modules/kernel-image.sha256','etc/thermal-a9.kernel-image.sha256']:
  changes[path]=save(path,(sha(DEADLINE/'Image.gz')+'\n').encode())
 removed=[p for p in ['etc/adb_keys','bin/a9ml-ctl.sh'] if p in inv]
 (O/'payload.diff').write_text(''.join(diff))
 (O/'policy-receipt.json').write_text(json.dumps({'platform_inputs':platform_receipt,'vendor_removed_permissives':stripnotes,'precompiled_policy':binary_permissives(binary.read_bytes()),'unchanged_system_hash_selectors':selectors,'new_property_set_grants':['vendor_init -> build_prop','vendor_init -> userdebug_or_eng_prop'],'system_image_modified':False},indent=2)+'\n')
 install(changes,removed)
 # Keep exact plain fencefix available; b-try's default needs real deadline code.
 for src,dest in [(DEADLINE/'boot.img','boot.img'),(FENCE/'boot.img','boot-plain.img'),(DEADLINE/'Image.gz','Image.gz'),(DEADLINE/'recovery_dtbo.img','recovery_dtbo.img'),(FENCE/'vmlinux.symvers','vmlinux.symvers'),(FENCE/'Module.symvers','Module.symvers'),(FENCE/'kernel.config','kernel.config')]:shutil.copyfile(src,O/dest)
 for n in ['boot.img','boot-plain.img','vendor.img']:(O/(n+'.sha256')).write_text(sha(O/n)+'  '+n+'\n')
 (O/'properties-audit.json').write_text(json.dumps(props,indent=2)+'\n')
 (O/'build-inputs.json').write_text(json.dumps({'audiofix4_vendor':str(BASE),'audiofix4_sha256':sha(BASE),'default_deadline_boot':str(DEADLINE/'boot.img'),'exact_plain_boot':str(FENCE/'boot.img'),'system':str(SYSTEM),'system_opened_readonly':True,'system_modified':False,'vendor_apexes':11,'vendor_apks':0,'changed_paths':list(changes),'removed':removed},indent=2)+'\n')
 print('Release vendor assembled with unchanged audiofix4 modules; secure props and zero permissive domains.',flush=True)
if __name__=='__main__':main()
