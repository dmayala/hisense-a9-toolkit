#!/usr/bin/env python3
"""Offline readelf closure for shipped vendor HALs; never execute target code.

Vendor dependencies resolve in the service's installed LD_LIBRARY_PATH and
vendor defaults. Only GSI LLNDK/Bionic runtime exports cross to the platform namespace.
Includes vendor APEX HALs. This is a static NEEDED check, not a full linker,
SELinux, dlopen, symbol-version, or TA qualification.
"""
import argparse,hashlib,io,json,os,posixpath,re,shlex,shutil,struct,subprocess as sp,zipfile
from pathlib import Path
READELF=Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf')
# HAL plugins and script-dispatched services inherit these actual init contexts.
ALIASES={
 '/vendor/bin/hw/android.hardware.wifi-service':'vendor.a9ml_wifi_hal',
 '/vendor/bin/hw/wpa_supplicant':'wpa_supplicant',
 '/vendor/bin/hw/hostapd':'hostapd',
 '/vendor/lib64/hw/camera.default.so':'vendor.camera-provider-a9',
 '/vendor/lib64/hw/android.hardware.camera.provider@2.4-impl.so':'vendor.camera-provider-a9',
 '/vendor/lib64/hw/fingerprint.default.so':'vendor.fps_hal',
}

def norm(p):return posixpath.normpath('/'+p.lstrip('/'))
def manifest_name(data):
 # apex_manifest.proto field1 is name. Skip other protobuf wire fields safely.
 def varint(i):
  n=0
  for shift in range(0,70,7):
   b=data[i];i+=1;n|=(b&127)<<shift
   if not b&128:return n,i
  raise ValueError('bad APEX manifest varint')
 i=0
 while i<len(data):
  tag,i=varint(i);field,wire=tag>>3,tag&7
  if wire==2:
   size,i=varint(i);value=data[i:i+size];i+=size
   if field==1:return value.decode()
  elif wire==0:_,i=varint(i)
  elif wire==1:i+=8
  elif wire==5:i+=4
  else:raise ValueError('unsupported APEX manifest wire type')
 raise ValueError('APEX name missing')

class Tree:
 def __init__(self,root):self.root=Path(root)
 def info(self,p):
  f=self.root/p.lstrip('/')
  try:
   if f.is_symlink():return 'link',os.readlink(f)
   if f.is_dir():return 'dir',None
   if f.is_file():return 'file',None
  except OSError:pass
  return None,None
 def read(self,p):return (self.root/p.lstrip('/')).read_bytes()
 def names(self,p):
  d=self.root/p.lstrip('/')
  return sorted(f.name for f in d.iterdir()) if d.is_dir() else []

class Image:
 def __init__(self,path):self.path=Path(path);self.cache={}
 def debug(self,command):return sp.check_output(['debugfs','-R',command,str(self.path)],stderr=sp.DEVNULL)
 def info(self,p):
  if p not in self.cache:
   t=self.debug('stat '+p).decode(errors='replace');m=re.search(r'Type:\s+(\w+)',t)
   kind={'regular':'file','directory':'dir','symlink':'link'}.get(m[1]) if m else None
   dest=None
   if kind=='link':
    x=re.search(r'Fast link dest: "(.*)"',t);dest=x[1] if x else self.debug('cat '+p).decode()
   self.cache[p]=(kind,dest)
  return self.cache[p]
 def read(self,p):return self.debug('cat '+p)
 def names(self,p):
  t=self.debug('ls -p '+p).decode(errors='replace')
  return sorted(r[5] for line in t.splitlines() if len(r:=line.split('/'))>=7 and r[5] not in ('.','..'))

class Files:
 def __init__(self,out):self.mounts={};self.apexes=[];self.platform_apexes=[];self.out=out
 def mount(self,p,source):self.mounts[norm(p)]=source
 def source(self,p):
  for prefix in sorted(self.mounts,key=len,reverse=True):
   if p==prefix or p.startswith(prefix+'/'):
    src=self.mounts[prefix]
    if callable(src):src=src();self.mounts[prefix]=src
    return src,p[len(prefix):] or '/'
  return None,None
 def locate(self,p):
  p=norm(p)
  for _ in range(32):
   parts=p.strip('/').split('/');changed=False
   for i in range(1,len(parts)+1):
    candidate='/'+('/'.join(parts[:i]));src,local=self.source(candidate)
    if src is None:continue
    kind,link=src.info(local)
    if kind=='link':
     target=link if link.startswith('/') else posixpath.join(posixpath.dirname(candidate),link)
     p=norm(posixpath.join(target,*parts[i:]));changed=True;break
   if changed:continue
   src,local=self.source(p)
   if src is None:return None
   kind,_=src.info(local)
   return (p,src,local,kind) if kind else None
  raise ValueError('symlink loop: '+p)
 def read(self,p):
  q=self.locate(p)
  return q[1].read(q[2]) if q and q[3]=='file' else None
 def walk(self,p,seen=None):
  if seen is None:seen=set()
  q=self.locate(p)
  if not q:return
  if q[3]=='file':yield p;return
  if q[3]!='dir':return
  if q[0] in seen:raise ValueError('directory symlink cycle: '+p)
  branch=seen|{q[0]}
  for name in q[1].names(q[2]):yield from self.walk(p.rstrip('/')+'/'+name,branch)
 def apex(self,data,origin,platform=False):
  z=zipfile.ZipFile(io.BytesIO(data))
  if 'original_apex' in z.namelist():z=zipfile.ZipFile(io.BytesIO(z.read('original_apex')))
  name=manifest_name(z.read('apex_manifest.pb'))
  if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):raise ValueError('bad APEX name')
  prefix='/apex/'+name
  if prefix in self.mounts:return prefix
  def materialize():
   info=z.getinfo('apex_payload.img')
   if shutil.disk_usage(self.out).free < 15*1024**3+info.file_size:raise ValueError('APEX scratch budget: '+origin)
   p=self.out/(name+'.img')
   with z.open(info) as source,p.open('wb') as dest:shutil.copyfileobj(source,dest)
   return Image(p)
  self.mount(prefix,materialize);self.apexes.append(prefix)
  if platform:self.platform_apexes.append(prefix)
  return prefix

class Audit:
 def __init__(self,files,out,llndk,readelf=READELF,vndk=None):
  self.fs=files;self.out=out;self.llndk=set(llndk);self.readelf=readelf;self.cache={};self.failures=[];self.edges=[];self.vndk=vndk
 def elf(self,path):
  q=self.fs.locate(path)
  if not q or q[3]!='file':return None
  path=q[0]
  if path in self.cache:return self.cache[path]
  b=q[1].read(q[2])
  if b[:4]!=b'\x7fELF':self.cache[path]=None;return None
  if len(b)<20 or b[5]!=1:raise ValueError('unsupported ELF header: '+path)
  temp=self.out/'inspect.elf';temp.write_bytes(b)
  d=sp.check_output([str(self.readelf),'-d','--wide',str(temp)],text=True,stderr=sp.STDOUT)
  result=dict(path=path,abi=(b[4],struct.unpack_from('<H',b,18)[0]),needed=re.findall(r'\(NEEDED\).*?\[([^]]+)\]',d),runpath=[],sha256=hashlib.sha256(b).hexdigest())
  for value in re.findall(r'\(RUNPATH\).*?\[([^]]*)\]',d):result['runpath']+=value.split(':')
  self.cache[path]=result;return result
 def find(self,name,paths):
  for directory in paths:
   p=norm(directory+'/'+name)
   if self.fs.locate(p):return p
  return None
 def platform(self,name,lib):
  paths=['/system/'+lib,'/system_ext/'+lib,'/product/'+lib,'/apex/com.android.runtime/'+lib+'/bionic']
  p=self.find(name,paths)
  if p:return p
  if name=='ld-android.so':
   p='/apex/com.android.runtime/bin/'+('linker64' if lib=='lib64' else 'linker')
   if self.fs.locate(p):return p
  for apex in self.fs.platform_apexes:
   p=self.find(name,[apex+'/'+lib,apex+'/'+lib+'/bionic'])
   if p:return p
  return None
 def check(self,root,ldpaths):
  initial=self.elf(root)
  if initial is None:raise ValueError('HAL root is not ELF: '+root)
  lib='lib64' if initial['abi'][0]==2 else 'lib';seen=set();errors=[]
  paths=[norm(p.replace('${LIB}',lib)) for p in ldpaths]
  paths=[p for p in paths if p.startswith(('/vendor/','/odm/'))]
  if root.startswith('/apex/'):paths.insert(0,'/'.join(root.split('/')[:3])+'/'+lib)
  paths+=['/odm/'+lib,'/vendor/'+lib,'/vendor/'+lib+'/hw','/vendor/'+lib+'/egl']
  def visit(path,context):
   row=self.elf(path)
   if row is None:errors.append(dict(root=root,parent=path,error='not_elf'));return
   if row['abi']!=initial['abi']:errors.append(dict(root=root,parent=path,error='wrong_elf_abi'));return
   key=(row['path'],context)
   if key in seen:return
   seen.add(key)
   runpaths=[p.replace('${ORIGIN}',posixpath.dirname(row['path'])).replace('$ORIGIN',posixpath.dirname(row['path'])) for p in row['runpath']]
   if any('$' in p or not p.startswith('/') for p in runpaths):raise ValueError('unsupported RUNPATH: '+path)
   if context=='vendor' and not row['path'].startswith(('/vendor/','/odm/','/apex/')):
    errors.append(dict(root=root,parent=row['path'],error='outside_vendor_namespace'));return
   runpaths=[norm(p) for p in runpaths]
   if context=='vendor':
    local=paths+[p for p in runpaths if p.startswith(('/vendor/','/odm/','/apex/'))]
   elif context=='vndk':
    local=[self.vndk['prefix']+'/'+lib]
   else:
    local=runpaths+['/system/'+lib,'/system_ext/'+lib,'/product/'+lib]
    if row['path'].startswith('/apex/'):local.insert(0,'/'.join(row['path'].split('/')[:3])+'/'+lib)
   for name in row['needed']:
    if '/' in name:raise ValueError('absolute/slashed NEEDED requires explicit handling: '+name)
    target=self.find(name,local);next_context=context
    if target is None and context=='vendor' and self.vndk and name in self.vndk['exports']:
     target=self.find(name,[self.vndk['prefix']+'/'+lib]);next_context='vndk'
    if target is None and (context=='platform' or name in self.llndk or name in ('libdl_android.so','ld-android.so')):target=self.platform(name,lib);next_context='platform'
    if target is None:errors.append(dict(root=root,parent=row['path'],needed=name,error='missing_in_namespace',namespace=context));continue
    self.edges.append(dict(root=root,parent=row['path'],needed=name,resolved=target,namespace=next_context));visit(target,next_context)
  visit(root,'vendor');self.failures+=errors
  return dict(root=root,ld_library_path=ldpaths,objects=len(seen),missing=len(errors))

def services(fs,rcs):
 result={}
 for p in rcs:
  b=fs.read(p)
  if b is None:continue
  current=None;name=None
  def commit():
   if current is not None and (name not in result or current['override']):result[name]=current
  for line in b.decode(errors='replace').splitlines():
   if not line or line.lstrip().startswith('#'):continue
   if not line[0].isspace():
    commit();a=shlex.split(line);current=None;name=None
    if a[:1]==['service'] and len(a)>=3:
     name=a[1];current=dict(exe=a[2],paths=[],rc=p,override=False)
   elif current:
    a=shlex.split(line)
    if a==['override']:current['override']=True
    if a[:2]==['setenv','LD_LIBRARY_PATH']:
     if len(a)!=3:raise ValueError('invalid LD_LIBRARY_PATH: '+p)
     current['paths']=a[2].split(':')
  commit()
 return result

def main():
 ap=argparse.ArgumentParser(description=__doc__);v=ap.add_mutually_exclusive_group(required=True);v.add_argument('--vendor-image',type=Path);v.add_argument('--vendor-root',type=Path)
 ap.add_argument('--standalone-plugins',action='store_true',help='also diagnose every plugin without its explicitly deployed private context');ap.add_argument('--system-root',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--readelf',type=Path,default=READELF)
 a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);os.chmod(a.out,0o700)
 fs=Files(a.out);fs.mount('/vendor',Image(a.vendor_image) if a.vendor_image else Tree(a.vendor_root));fs.mount('/system',Tree(a.system_root))
 for name in ['system_ext','product']:
  if (a.system_root/name).is_dir():fs.mount('/'+name,Tree(a.system_root/name))
 vendor_apex=[]
 for p in fs.walk('/vendor/apex'):
  if p.endswith(('.apex','.capex')):vendor_apex.append(fs.apex(fs.read(p),p))
 for directory in [a.system_root/'apex',a.system_root/'system_ext/apex',a.system_root/'product/apex']:
  if directory.is_dir():
   for p in sorted(directory.glob('*')):
    if p.is_file() and p.suffix in ('.apex','.capex'):fs.apex(p.read_bytes(),str(p),platform=True)
 rcs=list(fs.walk('/vendor/etc/init'))
 for p in vendor_apex:rcs+=list(fs.walk(p+'/etc'))
 configs=services(fs,[p for p in rcs if p.endswith('.rc')])
 llndk=(a.system_root/'etc/llndk.libraries.txt').read_text().splitlines()
 props={}
 for line in (fs.read('/vendor/build.prop') or b'').decode().splitlines():
  if '=' in line and not line.startswith('#'):
   key,value=line.split('=',1);props[key.strip()]=value.strip()
 version=props.get('ro.vndk.version',props.get('ro.vendor.vndk.version',''));vndk=None
 if version:
  if not re.fullmatch(r'[0-9]+',version):raise ValueError('invalid VNDK version: '+version)
  prefix='/apex/com.android.vndk.v'+version
  if prefix not in fs.platform_apexes:raise ValueError('selected VNDK APEX not provided: '+prefix)
  exports=set()
  for kind in ('vndkcore','vndksp'):
   data=fs.read(prefix+'/etc/'+kind+'.libraries.'+version+'.txt')
   if data is None:raise ValueError('selected VNDK export list absent: '+kind)
   exports.update(s.strip() for s in data.decode().splitlines() if s.strip() and not s.startswith('#'))
  vndk=dict(version=version,prefix=prefix,exports=exports)
 audit=Audit(fs,a.out,[s.strip() for s in llndk if s.strip() and not s.startswith('#')],a.readelf,vndk)
 roots=set(fs.walk('/vendor/bin/hw'))|set(fs.walk('/vendor/lib64/hw'))|set(fs.walk('/vendor/lib/hw'))
 for cfg in configs.values():
  if cfg['exe'].startswith(('/vendor/','/apex/')):roots.add(cfg['exe'])
 for p in vendor_apex:roots.update(fs.walk(p+'/bin'));roots.update(fs.walk(p+'/lib64/hw'));roots.update(fs.walk(p+'/lib/hw'))
 private_contexts={}
 for cfg in configs.values():
  for directory in cfg['paths']:
   if directory.startswith(('/vendor/lib64/','/vendor/lib/')) and not directory.endswith('/hw'):
    for path in fs.walk(directory):
     roots.add(path);private_contexts.setdefault(path,[]).append(cfg)
 rows=[]
 for root in sorted(roots):
  if audit.elf(root) is None:
   if re.fullmatch(r'/vendor/lib(?:64)?/hw/[^/]+\.so',root):audit.failures.append(dict(root=root,parent=root,error='plugin_not_elf'))
   continue
  candidates=[c for c in configs.values() if c['exe']==root] or private_contexts.get(root,[])
  if root in ALIASES:
   if ALIASES[root] not in configs:raise ValueError('missing actual init context: '+root)
   candidates=[configs[ALIASES[root]]]
  unique={tuple(c['paths']):c for c in candidates or [dict(paths=[])]}
  # Extra bare-namespace diagnostic, without deployment-specific contexts.
  # Unmapped plugins (including Gatekeeper) always receive that context.
  if a.standalone_plugins and re.fullmatch(r'/vendor/lib(?:64)?/hw/[^/]+\.so',root):unique[()]=dict(paths=[])
  for cfg in unique.values():rows.append(audit.check(root,cfg['paths']))
 if not rows:raise ValueError('no vendor HAL ELF roots found')
 result=dict(status='FAIL' if audit.failures else 'PASS',host_only=True,executed_target=False,roots=rows,objects=len([r for r in audit.cache.values() if r]),edges=audit.edges,failures=audit.failures,vendor_apexes=vendor_apex,runtime_exports=['libdl_android.so','ld-android.so'],llndk=sorted(audit.llndk),limitations='Static NEEDED/search-path gate; does not qualify dlopen, symbols, SELinux, TA loading or full generated linker namespace behavior.')
 result['vndk']=dict(version=version,enabled=bool(vndk),exports=sorted(vndk['exports']) if vndk else [])
 result['inventory']={name:sorted(p for p in roots if p.startswith(prefix) and audit.elf(p)) for name,prefix in [('bin_hw','/vendor/bin/hw/'),('lib64_hw','/vendor/lib64/hw/'),('lib_hw','/vendor/lib/hw/')]}
 (a.out/'hal-needed.json').write_text(json.dumps(result,indent=2)+'\n')
 # Payload images are only offline scratch; the immutable packages and
 # complete resolution report remain. No mounted filesystem is involved.
 for source in fs.mounts.values():
  if isinstance(source,Image) and source.path.parent==a.out:source.path.unlink()
 print('HAL_NEEDED',result['status'],'roots='+str(len(rows)),'objects='+str(result['objects']),'missing='+str(len(audit.failures)))
 for f in audit.failures:print(json.dumps(f,sort_keys=True))
 return int(bool(audit.failures))
if __name__=='__main__':
 try:raise SystemExit(main())
 except (ValueError,sp.CalledProcessError,OSError,zipfile.BadZipFile) as e:raise SystemExit('HAL_NEEDED ERROR: '+str(e))
