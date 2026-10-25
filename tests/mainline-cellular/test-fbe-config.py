#!/usr/bin/env python3
"""Host-only config controls: preserve wrapped policy, reject unsafe inputs."""
from pathlib import Path
import importlib.util,unittest
R=Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('fbe_config',R/'scripts/mainline/reboot/fbe-config.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
F='''# inherited first-stage mounts stay unchanged
system /system ext4 ro wait,logical,first_stage_mount
/dev/block/by-name/metadata /metadata ext4 noatime,nosuid,nodev,discard check,formattable
/dev/block/by-name/userdata /data f2fs noatime,nosuid,nodev,discard,inlinecrypt latemount,check,formattable,fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized+wrappedkey_v0,metadata_encryption=aes-256-xts:wrappedkey_v0,keydirectory=/metadata/vold/metadata_encryption,checkpoint=fs
'''
P='ro.product.first_api_level=30\nro.vendor.build.security_patch=2022-04-05\n'
class Config(unittest.TestCase):
 def test_only_wait_and_explicit_options_version_change(self):
  f,p=m.render(F,P)
  self.assertEqual(f.replace('wait,check,formattable','check,formattable').replace('wait,latemount','latemount'),F)
  self.assertEqual(p,P+m.FORMAT+'=2\n')
  self.assertEqual(m.render(f,p),(f,p))
 def test_reject_ram_other_keydir_or_cipher(self):
  for f in (F.replace('/dev/block/by-name/userdata','/dev/block/ram0'),F.replace('wrappedkey_v0','wrappedkey_v1'),F.replace('aes-256-cts','aes-256-hctr2'),F.replace('/metadata/vold/metadata_encryption','/metadata/other')):
   with self.assertRaises(ValueError):m.render(f,P)
 def test_reject_duplicate_or_missing_mounts(self):
  for f in (F+F.splitlines()[2]+'\n','\n'.join(l for l in F.splitlines() if ' /metadata ' not in l)):
   with self.assertRaises(ValueError):m.render(f,P)
 def test_conflicting_options_version_is_not_silently_overridden(self):
  for p in (P+m.FORMAT+'=1\n',P+m.FORMAT+'=2\n'+m.FORMAT+'=2\n'):
   with self.assertRaises(ValueError):m.render(F,p)
if __name__=='__main__':unittest.main(verbosity=2)
