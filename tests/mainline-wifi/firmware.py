#!/usr/bin/env python3
"""Exercise the actual firmware-prep shell with only synthetic host paths."""
from pathlib import Path
import os,re,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2]
class Firmware(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(prefix='a9-wifi-firmware-');self.addCleanup(self.t.cleanup)
  self.r=Path(self.t.name);self.fake=self.r/'fs';self.tools=self.r/'bin';self.tools.mkdir()
  for p in ('mnt/a9-wifi/firmware','data/local/tmp/a9fw/hisense','sys/module/firmware_class/parameters','vendor/firmware/qcom/sm6115/hisense/hlte556n','vendor/firmware/ath10k/WCN3990/hw1.0/hisense/hlte556n'):(self.fake/p).mkdir(parents=True,exist_ok=True)
  (self.fake/'mnt/a9-wifi').chmod(0o700)
  for p in ('qcom/sm6115/hisense/hlte556n/modem.mdt','ath10k/WCN3990/hw1.0/hisense/hlte556n/board.bin','regulatory.db','regulatory.db.p7s'):(self.fake/'vendor/firmware'/p).write_bytes(b'dummy-'+p.encode())
  self.display=self.fake/'data/local/tmp/a9fw/hisense/a9-waveform.bin';self.display.write_bytes(b'dummy-display')
  self.path=self.fake/'sys/module/firmware_class/parameters/path';self.path.write_text(str(self.fake/'mnt/a9-wifi/firmware')+'\n')
  text=(ROOT/'scripts/mainline/wifi/a9ml-wifi-firmware.sh').read_text()
  # Substitute only the known absolute host roots; production has no test flag.
  text=re.sub(r'/(mnt|data|sys|vendor)(?=/|\s|[\"\'])',lambda m:str(self.fake)+m[0],text)
  self.script=self.r/'prep.sh';self.script.write_text(text)
  (self.tools/'getprop').write_text("#!/bin/sh\nprintf '1\\n'\n")
  realstat=subprocess.check_output(['which','stat'],text=True).strip()
  (self.tools/'stat').write_text('#!/bin/sh\nif [ "$1" = -c ] && [ "$2" = %u ]; then printf "0\\n"; else exec '+realstat+' "$@"; fi\n')
  for p in self.tools.iterdir():p.chmod(0o755)
 def run_prep(self):
  return subprocess.run([os.environ.get('A9_WIFI_TEST_SHELL','/bin/sh'),str(self.script)],env=os.environ|{'PATH':str(self.tools)+':'+os.environ['PATH']},capture_output=True,text=True)
 def test_bridge_resolves_direct_loader_names_and_preserves_display(self):
  inode=self.display.stat().st_ino
  for _ in range(2):
   p=self.run_prep();self.assertEqual(p.returncode,0,p.stderr)
  fw=self.fake/'mnt/a9-wifi/firmware'
  for name in ('qcom/sm6115/hisense/hlte556n/modem.mdt','ath10k/WCN3990/hw1.0/hisense/hlte556n/board.bin','regulatory.db','regulatory.db.p7s'):
   self.assertEqual((fw/name).read_bytes(),(self.fake/'vendor/firmware'/name).read_bytes())
  self.assertEqual((fw/'hisense/a9-waveform.bin').read_bytes(),b'dummy-display')
  self.assertEqual(self.display.stat().st_ino,inode)
 def test_wrong_kernel_path_stops_before_consumers(self):
  self.path.write_text('/vendor/firmware\n');p=self.run_prep();self.assertNotEqual(p.returncode,0)
  self.assertIn('kernel-path',p.stderr);self.assertFalse((self.fake/'mnt/a9-wifi/firmware/qcom').exists())
 def test_unreviewed_existing_tree_is_not_overwritten(self):
  dst=self.fake/'mnt/a9-wifi/firmware/qcom';dst.mkdir();(dst/'keep').write_text('sentinel')
  p=self.run_prep();self.assertNotEqual(p.returncode,0);self.assertEqual((dst/'keep').read_text(),'sentinel')
 def test_writable_anchor_rejected(self):
  (self.fake/'mnt/a9-wifi').chmod(0o777);p=self.run_prep();self.assertNotEqual(p.returncode,0);self.assertIn('parent-mode',p.stderr)
 def test_bridge_init_precedes_both_consumers_without_starting_radio(self):
  rc=(ROOT/'scripts/mainline/wifi/a9-wifi.rc').read_text()
  action=rc.split('on post-fs-data && property:ro.a9ml.wifi=1\n',1)[1].split('\nservice ',1)[0]
  self.assertIn('    exec_start a9ml_wifi_firmware\n',action)
  self.assertLess(action.index('restorecon_recursive /mnt/a9-wifi'),action.index('exec_start a9ml_wifi_firmware'))
  commands=[line.strip() for line in action.splitlines() if line.strip() and not line.strip().startswith('#')]
  self.assertEqual([c for c in commands if c.startswith(('start ','exec_start '))],['exec_start a9ml_wifi_firmware'])
  service=rc.split('service a9ml_wifi_firmware ',1)[1].split('\n\n',1)[0]
  self.assertTrue(service.startswith('/system/bin/sh /vendor/bin/a9ml-wifi-firmware.sh\n'))
  for setting in ('disabled','oneshot','timeout_period 10'):self.assertIn('    '+setting,service)
  eink=(ROOT/'scripts/mainline/android/vendor/etc/init/init.a9ml-eink.rc').read_text()
  self.assertIn('on early-boot && property:ro.boot.a9ml.eink=1',eink)
  # A synchronous post-fs-data action runs before early-boot. Exercise its
  # actual helper and prove display request lookup before radio module prep.
  result=self.run_prep();self.assertEqual(result.returncode,0,result.stderr)
  self.assertEqual((self.fake/'mnt/a9-wifi/firmware/hisense/a9-waveform.bin').read_bytes(),self.display.read_bytes())
 def test_real_boot_cmdline_guard(self):
  script=(ROOT/'scripts/mainline/android/pack-boot.sh').read_text()
  body=re.search(r"<<'PYWIFI'.*?\n(.*?)\nPYWIFI",script,re.S).group(1)
  good='firmware_class.path=/mnt/a9-wifi/firmware'
  for arg,ok in ((good,True),(good+' '+good,False),('firmware_class.path=/data/local/tmp/a9fw',False),(good+' '+'x'*512,False)):
   p=subprocess.run(['python3','-I','-',arg],input=body,text=True,capture_output=True)
   self.assertEqual(p.returncode==0,ok,p.stderr)
if __name__=='__main__':unittest.main(verbosity=2)
