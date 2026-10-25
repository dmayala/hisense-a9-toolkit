#!/usr/bin/env python3
"""Host-only APEX signing; private keys are read from the external release store."""
from pathlib import Path
import hashlib,json,os,re,subprocess as sp,tempfile,zipfile
ROOT=Path('/home/user/A9-build/mainline');OUT=ROOT/'row27-release-20261022'
SOURCE=ROOT/'plain-integ-audiofix4-20261022/vendor.img'
KEYS=Path('/home/user/.local/share/a9-mainline/keys/mainline-release-20261022')
assert Path('/home/user/a9-assets/keys/mainline-release-20261022').resolve()==KEYS.resolve()
OTA=ROOT/'android/dl/cf_arm64_x/otatools'
ENV=dict(os.environ,PATH=':'.join([str(OTA/'bin'),'/home/user/A9-build/tools/jdk21/bin','/nix/store/nn4b544v4sanvsf22mjiw3qh8mwh6pzh-openssl-3.5.1-bin/bin',*json.loads((ROOT/'demo-integrated-20261019/source.json').read_text())['tools'],os.environ['PATH']]))
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def run(args,name):
 p=sp.run(list(map(str,args)),env=ENV,stdout=sp.PIPE,stderr=sp.PIPE)
 (OUT/(name+'.log')).write_bytes(p.stdout+p.stderr)
 if p.returncode:raise RuntimeError((name,p.returncode))
 return p.stdout
os.umask(0o077)
manifest=json.loads((KEYS/'MANIFEST.json').read_text())
(OUT/'public-key-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(OUT/'key-modes.json').write_text(json.dumps({str(p.relative_to(KEYS)):oct(p.stat().st_mode&0o777) for p in KEYS.rglob('*')},indent=2)+'\n')
assert all(p.stat().st_mode&0o777==0o600 for p in KEYS.rglob('*') if p.is_file())
inventory=json.loads((OUT/'vendor-inventory.json').read_text())
assert not [n for n in inventory if n.endswith('.apk')]
results={};destdir=OUT/'vendor-overlay/apex';destdir.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='apex-sign-',dir=OUT) as temp:
 ENV['TMPDIR']=temp
 for path in sorted(n for n in inventory if n.endswith('.apex')):
  name=Path(path).stem;original=Path(temp)/Path(path).name;target=destdir/Path(path).name
  original.write_bytes(sp.check_output(['debugfs','-R','cat /'+path,str(SOURCE)],stderr=sp.DEVNULL))
  listing=run([OTA/'bin/deapexer','--debugfs_path',OTA/'bin/debugfs_static','list',original],'apex-members-'+name).decode()
  assert '.apk' not in listing,'nested APKs need explicit signer mapping'
  key=KEYS/'apex'/(name+'.pem')
  print('Signing '+name,flush=True)
  run([OTA/'bin/sign_apex','-p',OTA,'--container_key',KEYS/'releasekey','--payload_key',key,'--avbtool',OTA/'bin/avbtool',original,target],'sign-'+name)
  checked=run(['bash',OTA/'bin/apksigner','verify','--print-certs',target],'verify-container-'+name).decode()
  certs=re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]+)$',checked,re.M)
  assert certs==[manifest['container_certificates']['releasekey']['certificate_sha256']]
  with zipfile.ZipFile(original) as z:before=z.read('apex_payload.img')
  with zipfile.ZipFile(target) as z:
   after=z.read('apex_payload.img');assert z.read('apex_pubkey')==(KEYS/'apex'/(name+'.avbpubkey')).read_bytes()
   # Container manifest and other unsigned entries are unchanged.
   with zipfile.ZipFile(original) as old:
    for member in old.namelist():
     if member in ['apex_payload.img','apex_pubkey'] or member.startswith('META-INF/'):continue
     assert z.read(member)==old.read(member),(name,member)
  payload=Path(temp)/'payload.img';payload.write_bytes(before)
  info=run([OTA/'bin/avbtool','info_image','--image',payload],'original-payload-info-'+name).decode()
  size=int(re.search(r'Original image size:\s+(\d+)',info).group(1))
  assert before[:size]==after[:size]
  payload.write_bytes(after)
  run([OTA/'bin/avbtool','verify_image','--image',payload,'--key',key],'verify-payload-'+name)
  results[path]={'original_sha256':sha(original),'signed_sha256':sha(target),'container_certificate_sha256':certs[0],'payload_public_key_sha256':sha(KEYS/'apex'/(name+'.avbpubkey')),'filesystem_bytes_unchanged':size,'embedded_apks':0,'container_signature_verified':True,'payload_signature_and_hashtree_verified':True}
(OUT/'apex-signatures.json').write_text(json.dumps(results,indent=2)+'\n')
assert len(results)==11
print('All11 APEX payload and container signatures verified; filesystems unchanged.',flush=True)
