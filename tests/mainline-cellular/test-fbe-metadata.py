#!/usr/bin/env python3
"""Android mksh metadata-directory setup, with fake mounts/owners only."""
from pathlib import Path
import os,re,shutil,subprocess as sp,tempfile,unittest
R=Path(__file__).resolve().parents[2];SOURCE=R/'scripts/mainline/reboot/vendor/bin/a9ml-wrapped-start.sh'
SH=Path('/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sh')
class Metadata(unittest.TestCase):
 def run_case(self,mode):
  with tempfile.TemporaryDirectory() as tmp:
   d=Path(tmp);(d/'dev').mkdir();(d/'metadata').mkdir();(d/'commands').mkdir();(d/'mnt/vendor').mkdir(parents=True)
   (d/'mounts').write_text(('device '+str(d/'metadata')+' ext4 rw 0 0\n') if mode!='missing' else '')
   for name,body in {'mount':'exit '+('1' if mode=='mount_failed' else '0'),'restorecon':'exit 0','setprop':'exit 0','chown':'printf "%s\\n" "$*" >>"$TEST_CHOWN"'}.items():
    p=d/'commands'/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
   text=SOURCE.read_text()
   for path in ('/metadata','/mnt/vendor/persist','/dev/kmsg'):
    text=text.replace(path,str(d)+path)
   text=text.replace('/proc/mounts',str(d/'mounts'))
   for name in ('mount','restorecon'):
    text=text.replace('/system/bin/'+name,str(d/'commands'/name))
   text=text.replace('/system/bin/timeout',shutil.which('timeout'))
   env=dict(os.environ,PATH=str(d/'commands')+':'+os.environ['PATH'],TEST_CHOWN=str(d/'chown.log'))
   r=sp.run([SH,'-c',text,'metadata-fixture','metadata'],env=env,capture_output=True,text=True,timeout=20)
   root=d/'metadata/a9-tee-persist';data=root/'data'
   if mode=='ok':
    self.assertEqual(r.returncode,0,r.stderr)
    self.assertEqual(root.stat().st_mode&0o777,0o771);self.assertEqual(data.stat().st_mode&0o777,0o700)
    calls=(d/'chown.log').read_text().splitlines()
    self.assertEqual(calls,['0:1000 '+str(root),'1000:1000 '+str(data)])
   else:
    self.assertNotEqual(r.returncode,0)
    self.assertFalse(root.exists());self.assertFalse((d/'chown.log').exists())
 def test_stock_modes_on_existing_metadata_namespace(self):self.run_case('ok')
 def test_no_setup_after_missing_real_metadata(self):self.run_case('missing')
 def test_no_setup_after_mount_failure(self):self.run_case('mount_failed')
if __name__=='__main__':unittest.main(verbosity=2)
