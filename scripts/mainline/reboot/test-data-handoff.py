#!/usr/bin/env python3
"""Actual Android mksh, fake mount/vold/property commands; no device access."""
from pathlib import Path
import os, re, shutil, subprocess as sp, tempfile, unittest
HERE=Path(__file__).resolve().parent
SH='/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sh'

class Handoff(unittest.TestCase):
 def case(self,mode,initial='tmpfs'):
  with tempfile.TemporaryDirectory(prefix='wrapped-fixture-') as tmp:
   d=Path(tmp); commands=d/'commands';commands.mkdir();(d/'props').mkdir();(d/'data').mkdir()
   (d/'metadata/vold/metadata_encryption/key').mkdir(parents=True)
   (d/'keys').write_text('SYNTHETIC_TEST_AUTH\n')
   mounts=d/'mounts';mounts.write_text(f'device {d}/metadata ext4 rw 0 0\n'+(f'bootstrap {d}/data {initial} rw 0 0\n' if initial else ''))
   bodies={
    'getprop':'cat "$TEST_PROPS/$1" 2>/dev/null || :',
    'setprop':'[ "$TEST_MODE" != prop_failed ] || [ "$1" != ro.crypto.state ] || exit 1\nprintf "%s" "$2" >"$TEST_PROPS/$1"',
    'umount':'printf "detach\\n" >>"$TEST_CALLS"\nawk -v data="$TEST_DATA" \'$2!=data\' "$TEST_MOUNTS" >"$TEST_MOUNTS.next"\nmv "$TEST_MOUNTS.next" "$TEST_MOUNTS"',
    'mount':'printf "fallback\\n" >>"$TEST_CALLS"\nprintf "bootstrap %s tmpfs rw 0 0\\n" "$TEST_DATA" >>"$TEST_MOUNTS"',
    'vdc':'printf "vold %s\\n" "$2" >>"$TEST_CALLS"\n[ "$TEST_MODE" != vold_failed ] || exit 25\nprintf "dm %s f2fs rw 0 0\\n" "$TEST_DATA" >>"$TEST_MOUNTS"\n[ "$TEST_MODE" != partial ] || exit 25',
    'restorecon':'exit 0','chown':'exit 0',
    'logcat':'printf "trace\\n" >>"$TEST_CALLS"\nexit 0'}
   for name,body in bodies.items():
    p=commands/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o755)
   source=(HERE/'vendor/bin/a9ml-wrapped-start.sh').read_text()
   for src,dst in [('/metadata',str(d/'metadata')),('/data',str(d/'data')),('/proc/mounts',str(mounts)),('/dev/kmsg',str(d/'kmsg')),('/vendor/etc/adb_keys',str(d/'keys'))]:source=re.sub(re.escape(src)+r'(?![A-Za-z0-9_])',lambda _:dst,source)
   self.assertNotIn('/dev/kmsg',source);self.assertNotIn('/proc/mounts',source)
   for name in ('mount','umount','vdc','restorecon','logcat'):source=source.replace('/system/bin/'+name,str(commands/name))
   source=source.replace('/system/bin/timeout',shutil.which('timeout'))
   env=dict(os.environ,PATH=str(commands)+':'+os.environ['PATH'],TEST_MODE=mode,TEST_PROPS=str(d/'props'),TEST_DATA=str(d/'data'),TEST_MOUNTS=str(mounts),TEST_CALLS=str(d/'calls'))
   r=sp.run([SH,'-c',source,'data-fixture','data'],env=env,text=True,capture_output=True,timeout=20)
   r.stderr += '\nfixture_kmsg='+((d/'kmsg').read_text() if (d/'kmsg').exists() else 'absent')
   calls=(d/'calls').read_text() if (d/'calls').exists() else ''
   types=[line.split()[2] for line in mounts.read_text().splitlines() if line.split()[1]==str(d/'data')]
   ready=d/'props/vendor.a9.data.ready'
   if mode=='ok':
    self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(types,['f2fs']);self.assertEqual(ready.read_text(),'1')
    self.assertEqual(calls,('detach\n' if initial else '')+'vold mountFstab\n')
    # Init already owns the persistent ADB setup. The mounted-data helper
    # must not duplicate credential copies or their failing relabel operation.
    self.assertFalse((d/'data/misc/adb').exists())
   elif initial=='f2fs':
    self.assertNotEqual(r.returncode,0);self.assertEqual(calls,'');self.assertEqual(types,['f2fs']);self.assertEqual(ready.read_text(),'0')
   elif mode=='vold_failed':
    self.assertEqual(r.returncode,25,r.stderr);self.assertEqual(types,['tmpfs']);self.assertEqual(calls,'detach\nvold mountFstab\ntrace\nfallback\n');self.assertEqual(ready.read_text(),'0')
   elif mode=='partial':
    self.assertEqual(r.returncode,25,r.stderr);self.assertEqual(types,['f2fs']);self.assertNotIn('fallback',calls);self.assertEqual(ready.read_text(),'0')
   else:
    self.assertNotEqual(r.returncode,0);self.assertEqual(types,['f2fs']);self.assertEqual(ready.read_text(),'0')
 def test_bootstrap_retires_before_real_mount(self):self.case('ok')
 def test_clean_mount_and_existing_mount_refusal(self):
  self.case('ok','');self.case('refuse','f2fs')
 def test_error_restores_only_ram_fallback(self):self.case('vold_failed')
 def test_partial_mount_is_never_covered(self):self.case('partial')
 def test_property_failure_cannot_publish_readiness(self):self.case('prop_failed')

if __name__=='__main__':unittest.main(verbosity=2)
