from pathlib import Path
import subprocess as sp,json,hashlib,gzip,re,sys
assert len(sys.argv)>=4, 'kernel-root label vendor.img required'
O=Path(sys.argv[1]).resolve()
kernel=Path(sys.argv[1]) if len(sys.argv)>1 else O.parent/'plain-wrapped-fix3-20261022'
label=sys.argv[2] if len(sys.argv)>2 else 'fix3'
exports={a[1]:int(a[0],16) for l in (kernel/'vmlinux.symvers').read_text().splitlines() if len(a:=l.split())>=4}
version=re.search(rb'Linux version ([^ ]+)',gzip.decompress((kernel/'Image.gz').read_bytes())).group(1).decode()
out={'kernel':str(kernel),'release':version,'vendors':{}}
images=[Path(p) for p in sys.argv[3:]] if len(sys.argv)>3 else [O.parent/n/'vendor.img' for n in ['plain-integ-20261022','plain-wrapped-fix2-20261022']]
for image in images:
 name=image.parent.name
 root=O/'module-audit'/name
 root.mkdir(parents=True,exist_ok=True)
 if not list(root.rglob('*.ko')):
  sp.run(['debugfs','-R',f'rdump /lib/modules {root}',str(image)],stdout=sp.DEVNULL,stderr=sp.DEVNULL,check=True)
 # rdump copies source directory itself under the destination.
 mods=sorted(root.rglob('*.ko'))
 assert mods,name
 providers={}
 data={}
 for p in mods:
  result=sp.run(['modprobe','--show-exports',str(p)],stdout=sp.PIPE,stderr=sp.PIPE,text=True)
  if result.returncode and 'No data available' not in result.stderr:raise RuntimeError(result.stderr)
  e=result.stdout
  for l in e.splitlines():
   a=l.split()
   if len(a)==2:providers[a[1]]=(int(a[0],16),p.name)
  v=sp.check_output(['modinfo','-F','vermagic',str(p)],text=True).strip()
  imports=[]
  for l in sp.check_output(['modprobe','--dump-modversions',str(p)],text=True).splitlines():
   a=l.split()
   if len(a)==2:imports.append((a[1],int(a[0],16)))
  data[str(p.relative_to(root))]={'vermagic':v,'release_match':v.split()[0]==version,'imports':imports}
 mismatches=[];checks=0
 for p,d in data.items():
  bad=[]
  for n,crc in d['imports']:
   checks+=1
   actual=exports.get(n)
   provider='vmlinux'
   if actual is None and n in providers:actual,provider=providers[n]
   if actual!=crc:bad.append({'symbol':n,'module_crc':hex(crc),'kernel_or_provider_crc':None if actual is None else hex(actual),'provider':provider})
  d['mismatches']=bad
  d['import_count']=len(d.pop('imports'))
  if bad or not d['release_match']:mismatches.append(p)
 out['vendors'][name]={'modules':len(mods),'imports_checked':checks,'failing_modules':len(mismatches),'crc_mismatches':sum(len(v['mismatches']) for v in data.values()),'unique_bad_symbols':len({v['symbol'] for d in data.values() for v in d['mismatches']}),'vermagic_mismatches':sum(not d['release_match'] for d in data.values()),'details':data}
path=O/('module-audit-'+label+'.json');path.write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'kernel':str(kernel),'release':version,'vendors':{n:{k:v for k,v in d.items() if k!='details'} for n,d in out['vendors'].items()}},indent=2),flush=True)
