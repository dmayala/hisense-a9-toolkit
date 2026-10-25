#!/usr/bin/env python3
"""End-to-end offline coordinator regression: real subprocesses, fake adb/op/boot/health.
No phone, real token, 1Password, real USB lock or real store is accessed.
"""
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode=True
REPO=Path(__file__).resolve().parents[2]
SRC=REPO/'scripts/mainline/wifi/android/s2'
spec=importlib.util.spec_from_file_location('s2driver',SRC/'driver.py');driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
spec=importlib.util.spec_from_file_location('s2freeze',SRC/'freeze.py');freeze=importlib.util.module_from_spec(spec);spec.loader.exec_module(freeze)

def sealed_driver():
 release=Path(os.environ.get('A9_S2_SEALED_RELEASE','/home/user/A9-build/mainline/wifi-android/s2-87i/release.json'))
 seal=json.loads((release.parent/'review-seal.json').read_text())
 spec=importlib.util.spec_from_file_location('sealed_s2driver',release.parent/'driver.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 config=module.verify(release,seal['release_sha256'])
 return module,config

FAKE=r'''import json,sys,os,pathlib,signal,time,base64,tarfile,io,subprocess
root=pathlib.Path(__file__).parent.parent
cfg=json.loads((root/'fixture.json').read_text())
def log(x):
 with (root/'calls').open('a') as f:f.write(x+'\n')
name=pathlib.Path(sys.argv[0]).name
if name=='adb':
 if sys.argv[1:] == ['start-server']:log('adb-server');sys.exit(0)
 if 'su -c' in sys.argv[-1]:
  log('stores-a')
  for n in ('modemst1','modemst2','fsg','fsc','persist'):print('S2_STORE',n,'aa'*32)
  sys.exit(0)
 text=sys.stdin.read()
 if cfg.get('stage_refusal') and not (root/'boot-ready').exists():sys.exit(1)
 if cfg.get('root_sleep'):time.sleep(1)
 if cfg.get('root_transport_fail'):
  sys.stderr.write(cfg['ssid']);sys.exit(1)
 if 'S2_FRAMEWORK_CLEANUP' in text:
  log('framework-cleanup')
  if cfg.get('framework_cleanup_fail'):print('S2_ROOT_RC 1');sys.exit(0)
  print('S2_BRIDGE cleanup=forgotten');print('S2_FRAMEWORK_CLEANUP disabled=yes forgotten=yes')
 elif 'S2_ROOT_READY' in text:
  if cfg.get('root_not_ready'):print('S2_ROOT_RC 1');sys.exit(0)
  log('root-ready');print('S2_ROOT_READY uid=0')
 elif 'S2_INSTALL' in text:
  log('install');print('S2_INSTALL ready=yes')
  data=text.split("S2_PUBLIC_BUNDLE' | tar -xf - -C \"$B\"\n")[1].split('\nS2_PUBLIC_BUNDLE')[0]
  with tarfile.open(fileobj=io.BytesIO(base64.b64decode(data))) as tar:
   assert len(tar.getmembers())==8 and all(m.mode==0o400 and '/' not in m.name for m in tar.getmembers())
 elif 'S2_STORE' in text:
  before='awaiting-s2-audit' in text
  log('stores-pre' if before else 'stores-post')
  if before and cfg.get('store_pre_fail'):
   print('S2_AUDIT gate=store-open-persist result=failed');print('S2_ROOT_RC 1');sys.exit(0)
  if before:
   (root/'pre-taken').touch();print('S2_AUDIT gate=session-service state=unpublished')
  for n in ('modemst1','modemst2','fsg','fsc','persist'):
   print('S2_STORE',n,('bb' if cfg.get('store_diff') and not before and n=='persist' else 'aa')*32)
 elif 'setprop vendor.a9ml.wifi.s2.start 1' in text:
  assert (root/'pre-taken').exists();log('start');print('S2_AUDIT gate=session-service state=unpublished');print('S2_START requested=yes')
 elif 'S2_LOGD' in text:
  log('logd-stop');(root/'logd-stopped').touch();print('S2_LOGD stopped=yes')
 elif 's2-target.sh probe' in text:
  assert (root/'logd-stopped').exists();log('probe');(root/'probe-done').touch()
  if cfg.get('probe_fail'):print('S2_ROOT_RC 1');sys.exit(0)
  print('S2_BRIDGE wifi=enabled verified=yes')
  print('global=US phy=CN mode=normal syntax=ok')
  print('channel=accepted frequency=2427 max_dbm=20.0')
  print('S2_GATE phase=power-before rc=0 branch=unreported dbm=none')
  print('S2_GATE phase=channel public_preflight=accepted')
 elif 's2-target.sh receive' in text:
  assert (root/'probe-done').exists();log('receive')
  assert 'passphrase_b64=' in text
  assert not pathlib.Path(cfg['permit']).exists()
  assert pathlib.Path(cfg['permit']+'.pending').is_file()
  if cfg.get('delivery_lost'):sys.exit(1)
  if cfg.get('leak_first'):
   (pathlib.Path(cfg['evidence'])/'injected.private.log').write_text(cfg['passphrase'])
  print('S2_GATE phase=receive input=ready')
 elif 's2-target.sh run' in text:
  log('network')
  if cfg.get('network_selection_fail'):
   print('S2_BRIDGE select=age-rejected rows=3 parsed=3 named=1 frequency=1 security=1 fresh=0')
   print('S2_BRIDGE result=failed phase=scan-select')
   print('S2_GATE phase=framework-connect result=failed');print('S2_ROOT_RC 1');sys.exit(0)
  if cfg.get('network_fail'):
   print('S2_GATE phase=power-after result=failed');print('S2_ROOT_RC 1');sys.exit(0)
  if cfg.get('leak'):
   values=[cfg['ssid'],cfg['ssid'].encode().hex(),cfg['passphrase'],base64.b64encode(cfg['passphrase'].encode()).decode()]
   (pathlib.Path(cfg['evidence'])/'injected.private.log').write_text(values[cfg['leak']-1])
  print('S2_GATE phase=done result=success');print('S2_GATE phase=cleanup framework_cleanup_rc=0')
 elif 'S2_STOP' in text:
  log('stop')
  if 'awaiting-s2-audit' in text:print('S2_AUDIT gate=session-service state=unpublished')
  print('S2_STOP offline=yes')
 elif 'getprop vendor.a9ml.wifi.session.state' in text:print('serving')
 else:raise RuntimeError('unexpected public request')
 print('S2_ROOT_RC 0')
elif name=='op':
 assert not pathlib.Path(cfg['permit']).exists()
 assert os.environ.get('OP_SERVICE_ACCOUNT_TOKEN')=='synthetic-token'
 assert cfg['ssid'] not in ' '.join(sys.argv) and cfg['passphrase'] not in ' '.join(sys.argv)
 log('op-read')
 sys.stdout.write(cfg['ssid'] if sys.argv[-1].endswith('/network name') else cfg['passphrase'])
elif name=='booter':
 assert os.environ['A9_ANDROID']==cfg['android']
 for key in ('A9_WIFI_SNAPSHOT','A9_WIFI_BINARIES','A9_GOLDEN'):assert os.environ.get(key)
 assert '--boot' in sys.argv and '--vendor' in sys.argv and '--s2-quiet' in sys.argv
 if '--restore' in sys.argv:
  log('restore')
  sys.exit(1 if cfg.get('restore_fail') else 0)
 vendor=sys.argv[sys.argv.index('--vendor')+1]
 boot=sys.argv[sys.argv.index('--boot')+1]
 assert pathlib.Path(boot).is_file()
 assert '--open-gates' not in sys.argv and sys.argv[sys.argv.index('--hold')+1]=='5400'
 if '--write' not in sys.argv:
  log('boot-check')
  if cfg.get('boot_check_fail'):sys.exit(1)
  print('CHECK: PASS. Approval code for exactly these images: abcd1234')
 else:
  assert sys.argv[sys.argv.index('--code')+1]=='abcd1234'
  log('stage-check')
  # Execute ONLY the real lp-write local size/hash guard, not the phone script.
  check=subprocess.run(['bash',str(root/'image-preflight.sh'),vendor])
  if check.returncode:
   print('lp-write check failed\nstaging failed',flush=True);sys.exit(1)
  log('stage-passed');(root/'boot-ready').touch()
  log('boot');print('S2_QUIET_READY',flush=True)
  signal.signal(signal.SIGTERM,lambda s,f:sys.exit(0))
  while True:time.sleep(.05)
elif name=='health':
 log('health')
 if cfg.get('health_start_fail') and (root/'calls').read_text().splitlines().count('health')==1:sys.exit(1)
 print(json.dumps(dict(mode='android',slot='_a',boot_completed='1')))
else:
 log('DEVICE-TRIPWIRE');sys.exit(97)
'''

class Coordinator(unittest.TestCase):
 def test_87i_capture_is_scan_failure_after_root_probe_and_delivery(self):
  fix=REPO/'tests/mainline-wifi/fixtures/android-s2'
  rows=[json.loads(x) for x in (fix/'s2-87i-root-status.jsonl').read_text().splitlines()]
  self.assertEqual([(x['phase'],x['transport_rc'],x['root_rc']) for x in rows],
      [('root-ready',0,0),('probe',0,0),('delivery',0,0),('network',0,1),('framework-cleanup',0,0)])
  raw=(fix/'s2-87i-run.txt').read_bytes()
  self.assertIn(b'phase=input',raw);self.assertIn(b'result=failed phase=scan',raw)
  self.assertNotIn(b'phase=connect-dispatch',raw)
  self.assertIn(b'command=bridge-cleanup rc=0',raw)
  d=driver.Driver(self.c);d.e.mkdir();d.phase='network'
  def call(*a,**kw):kw['status'](0,raw,False);return raw
  d.call=call
  with contextlib.redirect_stdout(io.StringIO()) as output:
   with self.assertRaises(driver.Refused):d.root('public fixture')
  self.assertIn('root_response=rejected',output.getvalue())
  self.assertEqual(json.loads((d.e/'root-status.jsonl').read_text())['root_rc'],1)

 def test_87j_keeps_private_path_and_root_connect(self):
  old=Path('/home/user/A9-build/mainline/wifi-android/s2-87i')
  self.assertEqual((SRC/'s2-encode.py').read_bytes(),(old/'bundle/s2-encode.py').read_bytes())
  previous=subprocess.check_output(['git','show','4b05f5229014751f23ab041e834e447d3769a72f:scripts/mainline/wifi/android/s2/Protocol.java'],cwd=REPO,text=True)
  current=(SRC/'Protocol.java').read_text()
  self.assertEqual(current.split('    // Shipped printf fields',1)[0],previous.split('    // Shipped printf fields',1)[0])
  self.assertEqual(current.split('    public static String[] connectArgs',1)[1],previous.split('    public static String[] connectArgs',1)[1])
  text=(SRC/'Bridge.java').read_text()
  read_input=text.split('private static Protocol.Input readInput()',1)[1].split('private static ConnectivityManager',1)[0]
  baseline=subprocess.check_output(['git','show','HEAD:scripts/mainline/wifi/android/s2/Bridge.java'],cwd=REPO,text=True)
  self.assertEqual(read_input,baseline.split('private static Protocol.Input readInput()',1)[1].split('private static ConnectivityManager',1)[0])
  connect=text.split('private static void connect()',1)[1].split('private static void web()',1)[0]
  self.assertNotIn('publicScan()',connect);self.assertNotIn('start-scan',connect)
  self.assertIn('Protocol.connectArgs(in,target)',connect)
  self.assertIn('if(android.os.Process.myUid()!=0)',text)
  self.assertIn('case "scan": publicScan();break;',text)
  worker=(SRC/'s2-target.sh').read_text();previous=(old/'bundle/s2-target.sh').read_text()
  self.assertEqual(worker.split('if [ "$mode" = receive ]; then\n    phase=receive',1)[1].split('# Permanent one-attempt',1)[0],
      previous.split('if [ "$mode" = receive ]; then\n    phase=receive',1)[1].split('# Permanent one-attempt',1)[0])

 def test_87j_capture_names_the_actual_rejecting_check(self):
  raw=(REPO/'tests/mainline-wifi/fixtures/android-s2/s2-87j-run.txt').read_bytes()
  lines,reason=driver.Driver.root_diagnostics(raw)
  self.assertEqual(reason,'bridge-scan-select')
  self.assertIn('S2_BRIDGE result=failed phase=scan-select',lines)
  self.assertNotIn(b'phase=connect-dispatch',raw)
  d=driver.Driver(self.c);d.e.mkdir();d.phase='network'
  def call(*a,**kw):kw['status'](0,raw,False);return raw
  d.call=call
  with contextlib.redirect_stdout(io.StringIO()) as out,self.assertRaises(driver.Refused):d.root('fixture')
  self.assertIn('root_response=rejected reason=bridge-scan-select',out.getvalue())
  e=json.loads((d.e/'root-status.jsonl').read_text())
  self.assertEqual(e['rejection_reason'],'bridge-scan-select')
  self.assertEqual(e['diagnostics'],lines)

 def test_redacted_selection_reasons_preserve_first_failure_before_cleanup(self):
  for reason in ('no-rows','row-format','ssid-absent','frequency-mismatch','security-mismatch','age-rejected','ambiguous'):
   raw=('S2_BRIDGE select='+reason+' rows=3 parsed=2 named=1 frequency=1 security=1 fresh=0\n'
       'S2_BRIDGE result=failed phase=scan-select\nS2_BRIDGE phase=forget\nS2_ROOT_RC 1\n').encode()
   lines,label=driver.Driver.root_diagnostics(raw)
   self.assertEqual(label,'scan-select-'+reason);self.assertEqual(len(lines),3)
  raw=b'S2_BRIDGE command=list-scan-results result=failed branch=security rc=-1\nS2_BRIDGE result=failed phase=scan-results\n'
  self.assertEqual(driver.Driver.root_diagnostics(raw)[1],'command-list-scan-results-security')
  for raw in (b'S2_BRIDGE result=failed phase=private-marker\n',
      b'S2_BRIDGE select=ssid-absent rows=1 parsed=1 named=0 frequency=0 security=0 fresh=0 private-marker\n',
      b'S2_BRIDGE command=private-marker result=failed branch=exception\n',
      b'S2_BRIDGE result=failed phase=scan-select private-marker\n',
      b'private-marker SecurityException: private-marker\n'):
   lines,reason=driver.Driver.root_diagnostics(raw)
   self.assertEqual(lines,[]);self.assertNotIn('private-marker',reason)

 def test_captured_87h_rejection_is_worker_ram_gate_before_framework(self):
  fix=REPO/'tests/mainline-wifi/fixtures/android-s2'
  rows=[json.loads(x) for x in (fix/'s2-87h-root-status.jsonl').read_text().splitlines()]
  self.assertEqual([(x['phase'],x['transport_rc'],x['root_rc']) for x in rows],
                   [('root-ready',0,0),('probe',0,1),('framework-cleanup',0,0)])
  raw=(fix/'s2-87h-worker.txt').read_bytes()
  d=driver.Driver(self.c);d.e.mkdir();d.phase='probe'
  def call(*a,**kw):kw['status'](0,raw,False);return raw
  d.call=call
  with contextlib.redirect_stdout(io.StringIO()) as output:
   with self.assertRaises(driver.Refused):d.root('public fixture')
  self.assertIn('root_response=rejected',output.getvalue())
  self.assertNotIn(b'S2_BRIDGE',raw)
  self.assertEqual(json.loads((d.e/'root-status.jsonl').read_text())['root_rc'],1)

 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='s2-driver-');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.old_signals={n:signal.getsignal(n) for n in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
  self.addCleanup(lambda:[signal.signal(n,v) for n,v in self.old_signals.items()])
  (self.root/'bin').mkdir();(self.root/'runtime').mkdir();(self.root/'bundle').mkdir()
  self.env=patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(self.root/'runtime'),'PATH':str(self.root/'bin')+':'+os.environ['PATH']});self.env.start();self.addCleanup(self.env.stop)
  (self.root/'fake.py').write_text(FAKE)
  for n in ('adb','op','booter','health','fastboot','edl','nc','lsusb'):
   p=self.root/'bin'/n;p.write_text('#!'+sys.executable+'\n'+FAKE);p.chmod(0o700)
  repo=self.root/'repo';(repo/'scripts/mainline/android').mkdir(parents=True)
  (repo/'scripts/mainline/android/android-boot.sh').write_text('exec '+str(self.root/'bin/booter')+' "$@"\n')
  (repo/'scripts/a9-health.sh').write_text('exec '+str(self.root/'bin/health')+' "$@"\n')
  (repo/'scripts/a9_devices.py').write_text('print("fixture-device")\n')
  # Extract the unchanged local guard and stop before any phone function.
  text=(REPO/'scripts/mainline/android/lp-write.sh').read_text()
  guard=text.split('[ -f "$IMG" ] || fail',1)[1].split('\n# extents',1)[0]
  (self.root/'image-preflight.sh').write_text('set -uo pipefail\nIMG=$1\nfail() { echo "FAIL: $*"; exit 1; }\n[ -f "$IMG" ] || fail'+guard+'\n')
  for n in ('boot','vendor'):
   image=self.root/(n+'.img');image.write_bytes(b'fixture-image'.ljust(1024,b'\0'))
   Path(str(image)+'.sha256').write_text(hashlib.sha256(image.read_bytes()).hexdigest()+'  '+image.name+'\n')
  for n in driver.BUNDLE_FILES:
   p=self.root/'bundle'/n
   if n=='s2-encode.py':shutil.copyfile(SRC/n,p)
   else:p.write_text('fixture')
  (self.root/'bundle/SHA256SUMS').write_text(''.join(hashlib.sha256((self.root/'bundle'/n).read_bytes()).hexdigest()+'  '+n+'\n' for n in driver.BUNDLE_FILES))
  (self.root/'token').write_text('synthetic-token');(self.root/'token').chmod(0o600)
  self.c={'schema':2,'attempt':1,'repo':str(repo),'bundle':str(self.root/'bundle'),
   'boot':str(self.root/'boot.img'),'vendor':str(self.root/'vendor.img'),'session_rc_sha256':'ab'*32,
   'token_file':str(self.root/'token'),'session_seconds':3600,'evidence':str(self.root/'evidence'),'private':str(self.root/'private'),
   'permit':str(self.root/'permit'),'runs_root':str(self.root/'runs'),'probes_root':str(self.root/'probes'),'device_json':str(self.root/'device.json'),
   'tools':{n:(str(self.root/'bin'/n) if n in ('adb','op','fastboot') else shutil.which(n)) for n in ('adb','op','python3','bash','grep','shred','fastboot')},
   'env':{'A9_ANDROID':str(self.root/'android'),'A9_WIFI_SNAPSHOT':'fixture','A9_WIFI_BINARIES':'fixture','A9_GOLDEN':'fixture'},'pins':{}}
  self.fixture={'ssid':'fixture network','passphrase':'fixturePass!87','evidence':self.c['evidence'],'android':self.c['env']['A9_ANDROID']}
  self.save()
 def save(self):
  self.fixture['permit']=self.c['permit']
  (self.root/'fixture.json').write_text(json.dumps(self.fixture))
 def run_driver(self,module=driver,probe=False):
  d=module.Driver(self.c,probe=probe);output=io.StringIO()
  with contextlib.redirect_stdout(output):rc=d.run()
  for secret in (self.fixture['ssid'],self.fixture['passphrase'],'synthetic-token'):
   self.assertNotIn(secret,output.getvalue())
  self.assertFalse((self.root/'private/patterns').exists());return rc,d,output.getvalue()
 def test_frozen_mapping_removes_patterns_and_private_directory(self):
  module,sealed=sealed_driver()
  # Only device/credential boundaries are fake; every other tool path comes
  # from the real sealed release, including the actual shred applet alias.
  fake={n:self.c['tools'][n] for n in ('adb','op','fastboot')}
  self.c['tools']={**sealed['tools'],**fake}
  for failed in (False,True):
   with self.subTest(network_failure=failed):
    self.c.update(evidence=str(self.root/f'evidence-sealed-{failed}'),private=str(self.root/f'private-sealed-{failed}'),permit=str(self.root/f'permit-sealed-{failed}'))
    self.fixture.update(evidence=self.c['evidence'],network_fail=failed);self.save()
    rc,d,out=self.run_driver(module)
    self.assertEqual(rc,int(failed),out)
    self.assertFalse(d.pattern.exists(), 'sealed cleanup left patterns before fixture teardown')
    self.assertFalse(d.private.exists(), 'sealed cleanup left its private directory')
    self.assertNotIn('private_cleanup=failed',out)
 def test_bare_sealed_multicall_target_reproduces_retained_patterns(self):
  module,sealed=sealed_driver()
  # Negative control for F1: the exact old resolved path must fail cleanup.
  target=Path(sealed['tools']['shred']).resolve()
  self.assertEqual(target.name,'coreutils')
  self.c['tools']={**sealed['tools'],'shred':str(target)}
  d=module.Driver(self.c);d.e.mkdir(mode=0o700);d.private.mkdir(mode=0o700)
  d.pattern.write_text('synthetic-unused-pattern\n');d.pattern.chmod(0o600);d.pattern_ready=True
  with contextlib.redirect_stdout(io.StringIO()):d.finish()
  self.assertTrue(d.failed)
  self.assertTrue(d.pattern.is_file(), 'negative control did not reproduce F1')
  self.assertTrue(d.private.is_dir())
 def test_full_sequence_and_restore_environment(self):
  rc,d,out=self.run_driver();self.assertEqual(rc,0,out)
  calls=(self.root/'calls').read_text().splitlines()
  for first,second in [('stores-pre','start'),('probe','op-read'),('op-read','receive'),('stop','stores-post'),('stores-post','restore')]:self.assertLess(calls.index(first),calls.index(second))
  self.assertEqual(calls.count('op-read'),2);self.assertIn('stores_b=SAME count=5',out)
  self.assertIn('leak_check=1 hits=0',out);self.assertIn('leak_check=2 hits=0',out)
  self.assertIn('restore=passed',out);self.assertTrue((self.root/'permit').exists())
 def reused_vendor(self):
  # Match the captured 71-byte symlink; its target is a synthetic 1024-byte image.
  target=self.root/('v'*67+'.img');target.write_bytes(b'fixture-image'.ljust(1024,b'\0'))
  alias=self.root/'reused.img';alias.symlink_to(target.name)
  for image in (target,alias):Path(str(image)+'.sha256').write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+image.name+'\n')
  self.assertEqual(alias.lstat().st_size,71);return alias,target
 def test_captured_87e_symlink_passes_code_but_fails_actual_local_staging_guard(self):
  fix=REPO/'tests/mainline-wifi/fixtures/android-s2'
  captured=(fix/'s2-87e-boot-stage.txt').read_text()
  provenance=json.loads((fix/'s2-87e-provenance.json').read_text())
  self.assertEqual(hashlib.sha256((fix/'s2-87e-boot-stage.txt').read_bytes()).hexdigest(),provenance['stage_excerpt_sha256'])
  self.assertIn('new 71 B',captured);self.assertIn('FAIL: image size 71 is not a multiple of 512',captured)
  alias,target=self.reused_vendor();self.c['vendor']=str(alias);self.fixture['stage_refusal']=True;self.save()
  rc,d,out=self.run_driver();self.assertEqual(rc,1)
  self.assertIn('CHECK: PASS.',(d.e/'boot-check.log').read_text())
  self.assertIn('FAIL: image size 71 is not a multiple of 512',(d.e/'boot.log').read_text())
  for label in ('phase=boot result=failed','cleanup=stop result=failed','cleanup=boot-owner result=failed','restore=passed','slot_a=healthy','leak_check=2 result=not-delivered'):
   self.assertIn(label,out)
  calls=(self.root/'calls').read_text().splitlines()
  self.assertIn('stage-check',calls);self.assertNotIn('boot',calls);self.assertNotIn('op-read',calls)
  self.assertEqual(freeze.canonical_image(alias),target)
  check=subprocess.run(['bash',str(self.root/'image-preflight.sh'),str(freeze.canonical_image(alias))],capture_output=True)
  self.assertEqual(check.returncode,0)
 def test_canonical_image_refuses_bad_sidecar_and_bad_sector_size(self):
  alias,target=self.reused_vendor()
  sidecar=Path(str(target)+'.sha256')
  for content in ('00'*32+'  fixture\n',''):
   sidecar.write_text(content)
   with self.assertRaises(ValueError):freeze.canonical_image(alias)
  sidecar.unlink()
  with self.assertRaises(ValueError):freeze.canonical_image(alias)
  target.write_bytes(b'bad-size')
  with self.assertRaises(ValueError):freeze.canonical_image(alias)
 def test_whole_driver_cli_rehearsal_with_resolved_reuse_and_real_core_tools(self):
  alias,target=self.reused_vendor();self.c['vendor']=str(freeze.canonical_image(alias))
  # Use the reviewed immutable core-tool mapping, with device/op boundaries fake.
  # No method of Driver is patched or replaced in this case.
  release=Path(os.environ.get('A9_S2_SEALED_RELEASE','/home/user/A9-build/mainline/wifi-android/s2-87e/release.json'))
  sealed=json.loads(release.read_bytes())
  self.c['tools'].update({n:sealed['tools'][n] for n in ('python3','bash','grep','shred')})
  self.c['pins']={str(p):driver.digest(p) for p in (target,Path(str(target)+'.sha256'),self.root/'bundle/s2-encode.py')}
  config=self.root/'release.json';config.write_text(json.dumps(self.c))
  source=Path(os.environ.get('A9_S2_REHEARSAL_DRIVER',str(SRC/'driver.py')))
  argv=[self.c['tools']['python3'],'-I',str(source),'--release',str(config),'--seal',driver.digest(config),'--run','1']
  r=subprocess.run(argv,capture_output=True,timeout=30)
  self.assertEqual(r.returncode,0,r.stdout.decode())
  for secret in (self.fixture['ssid'],self.fixture['passphrase'],'synthetic-token'):
   self.assertNotIn(secret.encode(),r.stdout+r.stderr)
  calls=(self.root/'calls').read_text().splitlines()
  expected=['adb-server','health','stores-a','boot-check','stage-check','stage-passed','boot','root-ready','stores-pre','install','start','logd-stop','probe','op-read','op-read','receive','network','stop','stores-post','restore','health','stores-a']
  self.assertEqual(calls,expected)
  out=r.stdout.decode()
  for label in ('stores_b=SAME count=5','leak_check=1 hits=0','leak_check=2 hits=0','restore=passed','slot_a=healthy','result=success'):
   self.assertIn(label,out)
  actual_e=next((self.root/'runs').glob('*/evidence'))
  self.assertFalse((actual_e.parent/'private').exists());self.assertTrue(Path(self.c['permit']).exists())
  before=(self.root/'calls').read_bytes()
  again=subprocess.run(argv,capture_output=True,timeout=10)
  self.assertEqual(again.returncode,1);self.assertEqual((self.root/'calls').read_bytes(),before)
  if dest:=os.environ.get('A9_S2_REHEARSAL_OUT'):
   dest=Path(dest);dest.mkdir(parents=True,exist_ok=False)
   shutil.copytree(actual_e,dest/'evidence')
   (dest/'calls.txt').write_text('\n'.join(calls)+'\n');(dest/'console.txt').write_bytes(r.stdout+r.stderr)
   receipt={'host_only':True,'real_driver_sha256':driver.digest(source),'driver_methods_patched':False,
    'real_local_staging_guard_sha256':driver.digest(self.root/'image-preflight.sh'),'runtime_vendor_is_regular':not Path(self.c['vendor']).is_symlink(),
    'credential_boundary':'synthetic local token and fake op, no real token or account read','network_boundary':'fake target response, no network',
    'private_removed':True,'fake_permit_consumed_once':True,'second_attempt_no_contact':True,'result':'PASS'}
   (dest/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
 def test_captured_87d_prestart_failure_is_diagnosed_without_false_post_failure(self):
  captured=(REPO/'tests/mainline-wifi/fixtures/android-s2/s2-87d-console.txt').read_text()
  self.assertIn('phase=store-pre result=failed',captured)
  self.assertIn('cleanup=stop result=failed',captured)
  self.assertIn('cleanup=post result=failed',captured)
  # The capture did not retain the hardware subcheck. Inject a distinct public
  # cause to verify that 87e records it, rather than inventing a captured cause.
  self.fixture['store_pre_fail']=True;self.save();rc,d,out=self.run_driver()
  self.assertEqual(rc,1);self.assertIn('phase=store-pre result=failed',out)
  self.assertIn('cleanup=stop result=not-started verified=yes',out)
  self.assertIn('cleanup=post result=not-required reason=no-pre-no-start',out)
  events=[json.loads(x) for x in (d.e/'root-status.jsonl').read_text().splitlines()]
  pre=next(x for x in events if x['phase']=='store-pre')
  self.assertEqual(pre['transport_rc'],0);self.assertEqual(pre['root_rc'],1);self.assertFalse(pre['timeout'])
  self.assertIn('S2_AUDIT gate=store-open-persist result=failed',pre['audit'])
  self.assertNotIn('op-read',(self.root/'calls').read_text());self.assertIn('restore=passed',out)
 def test_root_readiness_is_bounded_and_never_reads_credentials(self):
  self.fixture['root_not_ready']=True;self.save()
  d=driver.Driver(self.c);d.e.mkdir(mode=0o700);d.setup_env();d.phase='root-ready'
  with self.assertRaises(driver.Refused):d.wait_root(.05)
  event=json.loads((d.e/'root-status.jsonl').read_text().splitlines()[-1])
  self.assertEqual(event['transport_rc'],0);self.assertEqual(event['root_rc'],1)
  self.assertNotIn('op-read',(self.root/'calls').read_text() if (self.root/'calls').exists() else '')
 def test_real_87f_capture_proves_root_channel_and_names_rejected_gate(self):
  fix=REPO/'tests/mainline-wifi/fixtures/android-s2'
  proof=json.loads((fix/'s2-87f-provenance.json').read_text())
  for name,h in proof['fixtures'].items():self.assertEqual(hashlib.sha256((fix/name).read_bytes()).hexdigest(),h)
  events=[json.loads(x) for x in (fix/'s2-87f-root-status.jsonl').read_text().splitlines()]
  self.assertEqual(events[0]['phase'],'root-ready');self.assertEqual(events[0]['transport_rc'],0);self.assertEqual(events[0]['root_rc'],0)
  for e in events[1:]:
   self.assertEqual(e['transport_rc'],0);self.assertEqual(e['root_rc'],1);self.assertFalse(e['timeout'])
   self.assertEqual(e['audit'],['S2_AUDIT gate=session-service result=failed'])
  self.assertIn('vendor '+proof['vendor_sha256'],(fix/'s2-87f-hardware-meta.txt').read_text())
  d=driver.Driver(self.c);d.e.mkdir(mode=0o700);d.phase='store-pre'
  # Derived protocol envelope from the retained status, not recovered raw stdout.
  def boundary(raw):
   def call(argv,**kw):kw['status'](0,raw,False);return raw
   return call
  d.call=boundary(b'S2_AUDIT gate=session-service result=failed\nS2_ROOT_RC 1\n')
  output=io.StringIO()
  with contextlib.redirect_stdout(output),self.assertRaises(driver.Refused):d.root('false')
  self.assertIn('root_response=rejected',output.getvalue());self.assertNotIn('invalid',output.getvalue())
  d.call=boundary(b'no remote sentinel\n');output=io.StringIO()
  with contextlib.redirect_stdout(output),self.assertRaises(driver.Refused):d.root('false')
  self.assertIn('root_response=invalid',output.getvalue())
 def test_service_diagnostic_is_retained_without_changing_exact_result_grammar(self):
  d=driver.Driver(self.c);d.e.mkdir(mode=0o700);d.phase='start'
  raw=b'S2_AUDIT gate=session-service state=unpublished\nS2_START requested=yes\nS2_ROOT_RC 0\n'
  def call(argv,**kw):kw['status'](0,raw,False);return raw
  d.call=call
  self.assertEqual(d.root('fixture'),b'S2_START requested=yes\n')
  event=json.loads((d.e/'root-status.jsonl').read_text())
  self.assertIn('S2_AUDIT gate=session-service state=unpublished',event['audit'])
 def test_root_transport_failure_and_timeout_have_fixed_private_diagnostics(self):
  for kind in ('root_transport_fail','root_sleep'):
   with self.subTest(kind=kind):
    self.fixture.pop('root_transport_fail',None);self.fixture.pop('root_sleep',None);self.fixture[kind]=True;self.save()
    d=driver.Driver(self.c);d.e=self.root/kind;d.e.mkdir(mode=0o700);d.setup_env();d.phase='root-ready'
    with self.assertRaises(driver.Refused):d.root('echo harmless',.03 if kind=='root_sleep' else 5)
    raw=(d.e/'root-status.jsonl').read_text();self.assertNotIn(self.fixture['ssid'],raw)
    event=json.loads(raw);self.assertIsNone(event['root_rc'])
    self.assertEqual(event['timeout'],kind=='root_sleep')
    self.assertEqual(event['transport_rc'],None if kind=='root_sleep' else 1)
 def test_restore_failure_fails_even_with_healthy_slot_a(self):
  self.fixture['restore_fail']=True;self.save();rc,d,out=self.run_driver()
  self.assertEqual(rc,1);self.assertIn('restore=failed',out);self.assertIn('slot_a=healthy',out)
 def test_five_store_difference_aborts_without_repair(self):
  self.fixture['store_diff']=True;self.save();rc,d,out=self.run_driver()
  self.assertEqual(rc,1);self.assertIn('stores_b=DIFFER abort=yes repair=no',out);self.assertIn('restore=passed',out)
 def test_public_probe_failure_never_reads_credential(self):
  self.fixture['probe_fail']=True;self.save();rc,d,out=self.run_driver()
  self.assertEqual(rc,1);self.assertNotIn('op-read',(self.root/'calls').read_text());self.assertIn('restore=passed',out)
 def test_all_four_leak_forms_fail_without_printing_matches(self):
  # Each case gets independent evidence/permit paths but the same exact coordinator code.
  for n in range(1,5):
   with self.subTest(form=n):
    self.c['evidence']=str(self.root/f'evidence-{n}');self.c['private']=str(self.root/f'private-{n}');self.c['permit']=str(self.root/f'permit-{n}')
    self.fixture.update(leak=n,evidence=self.c['evidence']);self.save();rc,d,out=self.run_driver()
    self.assertEqual(rc,1);self.assertIn('leak_check=2 result=failed',out)
    self.assertIn('restore=passed',out);self.assertFalse(d.pattern.exists())
 def test_full_fake_cli_prints_selection_cause_and_always_restores(self):
  self.fixture['network_selection_fail']=True;self.save()
  r=self.cli();self.assertEqual(r.returncode,1)
  out=r.stdout.decode();self.assertIn('root_response=rejected reason=scan-select-age-rejected',out)
  self.assertIn('restore=passed',out);self.assertIn('leak_check=2 hits=0',out)
  self.assertNotIn(self.fixture['ssid'],out);self.assertNotIn(self.fixture['passphrase'],out)
  actual_e=next((self.root/'runs').glob('*/evidence'))
  rows=[json.loads(x) for x in (actual_e/'root-status.jsonl').read_text().splitlines()]
  network=next(x for x in rows if x['phase']=='network')
  self.assertEqual(network['rejection_reason'],'scan-select-age-rejected')
  self.assertIn('S2_BRIDGE select=age-rejected rows=3 parsed=3 named=1 frequency=1 security=1 fresh=0',network['diagnostics'])
  self.assertTrue(Path(self.c['permit']).is_file());self.assertFalse((actual_e.parent/'private').exists())

 def test_failed_worker_keeps_exact_gate_diagnostics(self):
  self.fixture['network_fail']=True;self.save();rc,d,out=self.run_driver()
  self.assertEqual(rc,1);self.assertIn('phase=power-after result=failed',(d.e/'run.log').read_text())
  self.assertIn('restore=passed',out);self.assertIn('leak_check=2 hits=0',out)
  self.assertIn('root_response=rejected reason=gate-power-after',out)
 def test_delivery_leak_fails_before_network_and_still_restores(self):
  self.fixture['leak_first']=True;self.save();rc,d,out=self.run_driver()
  self.assertEqual(rc,1);self.assertNotIn('network',(self.root/'calls').read_text().splitlines())
  self.assertIn('restore=passed',out);self.assertIn('phase=delivery result=failed',out)
 def test_second_cli_attempt_refuses_without_new_contact(self):
  rc,d,out=self.run_driver();self.assertEqual(rc,0)
  p=self.root/'release.json';p.write_text(json.dumps(self.c));before=(self.root/'calls').read_bytes()
  r=subprocess.run([sys.executable,'-I',str(SRC/'driver.py'),'--release',str(p),'--seal',driver.digest(p),'--run','1'],capture_output=True)
  self.assertEqual(r.returncode,1);self.assertEqual((self.root/'calls').read_bytes(),before)
 def cli(self,mode='--run'):
  p=self.root/'release.json';p.write_text(json.dumps(self.c))
  source=Path(os.environ.get('A9_S2_REHEARSAL_DRIVER',str(SRC/'driver.py')))
  return subprocess.run([sys.executable,'-I',str(source),'--release',str(p),'--seal',driver.digest(p),mode,*(['1'] if mode=='--run' else [])],capture_output=True,timeout=35)
 def test_probe_is_repeatable_without_credentials_or_any_permit_mutation(self):
  Path(self.c['token_file']).unlink()
  for i in range(2):
   result=self.cli('--probe');self.assertEqual(result.returncode,0,result.stdout.decode())
   self.assertFalse(Path(self.c['permit']).exists());self.assertFalse(Path(self.c['permit']+'.pending').exists())
  # A previously consumed delivery permit does not prevent a public probe.
  Path(self.c['permit']).write_text('previously delivered\n');before=Path(self.c['permit']).read_bytes()
  result=self.cli('--probe');self.assertEqual(result.returncode,0,result.stdout.decode())
  self.assertEqual(Path(self.c['permit']).read_bytes(),before)
  calls=(self.root/'calls').read_text().splitlines()
  for forbidden in ('op-read','receive','network'):self.assertNotIn(forbidden,calls)
  self.assertEqual(calls.count('restore'),3);self.assertEqual(calls.count('framework-cleanup'),3)
  runs=sorted((self.root/'probes').glob('probe-*'));self.assertEqual(len(runs),3)
  for run in runs:
   self.assertFalse((run/'private').exists());self.assertFalse((run/'evidence/run.log').exists())
   out=(run/'evidence/driver.log').read_text()
   for label in ('probe=passed credential_access=no permit=unchanged','framework_cleanup=passed','stores_b=SAME count=5','restore=passed','slot_a=healthy','result=success'):self.assertIn(label,out)
  if dest:=os.environ.get('A9_S2_PROBE_REHEARSAL_OUT'):
   dest=Path(dest);dest.mkdir(parents=True)
   for i,run in enumerate(runs):shutil.copytree(run/'evidence',dest/('probe-'+str(i+1)))
   (dest/'calls.txt').write_text('\n'.join(calls)+'\n')
   (dest/'receipt.json').write_text(json.dumps(dict(result='PASS',probe_runs=3,real_token_present=False,credential_calls=0,permit_mutations=0,restores=3,driver_methods_patched=False),indent=2)+'\n')
 def test_probe_failures_restore_without_consuming_delivery_permit(self):
  for i,flag in enumerate(('probe_fail','store_pre_fail','boot_check_fail','health_start_fail','restore_fail','framework_cleanup_fail')):
   with self.subTest(failure=flag):
    self.fixture={k:v for k,v in self.fixture.items() if k not in ('probe_fail','store_pre_fail','boot_check_fail','health_start_fail','restore_fail','framework_cleanup_fail')}
    self.c.update(evidence=str(self.root/f'fail-e-{i}'),private=str(self.root/f'fail-p-{i}'))
    self.fixture.update({flag:True,'evidence':self.c['evidence']});self.save()
    (self.root/'calls').unlink(missing_ok=True)
    rc,d,out=self.run_driver(probe=True);self.assertEqual(rc,1)
    calls=(self.root/'calls').read_text().splitlines();self.assertIn('restore',calls)
    self.assertNotIn('op-read',calls);self.assertNotIn('receive',calls);self.assertNotIn('network',calls)
    self.assertFalse(Path(self.c['permit']).exists());self.assertFalse(d.delivery_fence().exists())
 def test_pre_delivery_failure_can_retry_same_seal_with_fresh_evidence(self):
  self.fixture['probe_fail']=True;self.save();first=self.cli()
  self.assertEqual(first.returncode,1);self.assertFalse(Path(self.c['permit']).exists())
  self.assertFalse(Path(self.c['permit']+'.pending').exists())
  seal=driver.digest(self.root/'release.json')
  self.fixture['probe_fail']=False;self.save();second=self.cli()
  self.assertEqual(second.returncode,0,second.stdout.decode());self.assertEqual(driver.digest(self.root/'release.json'),seal)
  self.assertEqual(len(list((self.root/'runs').glob('run-*'))),2)
  self.assertTrue(Path(self.c['permit']).exists());self.assertFalse(Path(self.c['permit']+'.pending').exists())
 def test_lost_delivery_receipt_keeps_uncertainty_fence_and_blocks_redelivery(self):
  self.fixture['delivery_lost']=True;self.save();rc,d,out=self.run_driver()
  self.assertEqual(rc,1);self.assertFalse(Path(self.c['permit']).exists());self.assertTrue(d.delivery_fence().is_file())
  before=(self.root/'calls').read_bytes();again=self.cli()
  self.assertEqual(again.returncode,1);self.assertEqual((self.root/'calls').read_bytes(),before)
  self.assertIn('restore=passed',out)
  # Public diagnostics remain available even when delivery outcome is uncertain.
  probe=self.cli('--probe');self.assertEqual(probe.returncode,0,probe.stdout.decode())
  self.assertTrue(d.delivery_fence().is_file());self.assertFalse(Path(self.c['permit']).exists())
 def test_probe_mode_cannot_call_credential_reader_or_delivery(self):
  d=driver.Driver(self.c,probe=True)
  with self.assertRaises(driver.Refused):d.credentials()
  with self.assertRaises(driver.Refused):d.deliver()
  self.assertFalse((self.root/'calls').exists())
 def test_sigterm_at_boot_runs_stop_restore_and_fails(self):
  p=self.root/'release.json';p.write_text(json.dumps(self.c))
  # This synthetic boot/restore test models sufficient capacity independently
  # of host free space. The production CLI's 15 GiB admission stays unchanged.
  wrapper="import runpy,sys,shutil,types;shutil.disk_usage=lambda p:types.SimpleNamespace(free=100*1024**3);sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
  proc=subprocess.Popen([sys.executable,'-I','-c',wrapper,str(SRC/'driver.py'),'--release',str(p),'--seal',driver.digest(p),'--run','1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  end=time.monotonic()+10
  while time.monotonic()<end:
   if (self.root/'calls').exists() and 'boot\n' in (self.root/'calls').read_text():break
   time.sleep(.05)
  proc.send_signal(signal.SIGTERM);out,err=proc.communicate(timeout=90)
  self.assertEqual(proc.returncode,1);self.assertIn('restore',(self.root/'calls').read_text())
  self.assertFalse(Path(self.c['permit']).exists())
  self.assertNotIn(self.fixture['passphrase'].encode(),out+err)
 def test_bundle_enable_precedes_input_and_clock_uses_relaxed_floor(self):
  text=(SRC/'Bridge.java').read_text()
  connect=text.split('private static void connect() throws Exception {',1)[1].split('private static void web()',1)[0]
  self.assertLess(connect.index('prepareWifi()'),connect.index('readInput()'))
  prepare=text.split('private static void prepareWifi() throws Exception {',1)[1].split('private static void connect()',1)[0]
  self.assertIn('Protocol.enableWifi',prepare);self.assertIn('wifi=enabled verified=yes',prepare)
  self.assertIn('User-Agent","A9-S2/1',(SRC/'Bridge.java').read_text())
 def test_bounded_lock_wait_does_not_kill_holder_or_consume_permit(self):
  lock=self.root/'runtime/a9-usb.lock'
  with lock.open('w') as holder:
   fcntl.flock(holder,fcntl.LOCK_EX|fcntl.LOCK_NB)
   d=driver.Driver(self.c);start=time.monotonic()
   try:
    with self.assertRaises(driver.Refused):d.acquire_lock(.03)
    self.assertLess(time.monotonic()-start,1);self.assertFalse(Path(self.c['permit']).exists())
    fcntl.flock(holder,fcntl.LOCK_EX|fcntl.LOCK_NB)
   finally:
    if d.lock_fd is not None:os.close(d.lock_fd)
 def test_offline_seal_verify_never_invokes_adb_or_op(self):
  p=self.root/'release.json';p.write_text(json.dumps(self.c));h=driver.digest(p)
  self.assertEqual(driver.verify(p,h)['attempt'],1)
  r=subprocess.run([sys.executable,'-I',str(SRC/'driver.py'),'--release',str(p),'--seal',h,'--verify'],capture_output=True)
  self.assertEqual(r.returncode,0);self.assertFalse((self.root/'calls').exists())
  p.write_text('changed')
  with self.assertRaises(driver.Refused):driver.verify(p,h)
 def test_store_parser_refuses_missing_duplicate_or_malformed(self):
  rows=''.join('S2_STORE '+n+' '+'aa'*32+'\n' for n in driver.STORES)
  self.assertEqual(len(driver.store_rows(rows.encode())),5)
  for bad in ('',rows+rows,rows.replace('persist','other'),rows.replace('aa','zz',1)):
   with self.assertRaises(driver.Refused):driver.store_rows(bad.encode())
 def test_s2_gate_has_no_autostart_and_existing_stop_body_is_unchanged(self):
  rc=(SRC/'s2-session.rc').read_text();old=(REPO/'scripts/mainline/wifi/session/init.a9ml-wifi-session.rc').read_text()
  entry=rc.split('on property:sys.boot_completed=1 && property:ro.a9ml.wifi.session=1\n',1)[1].split('\non ',1)[0]
  self.assertNotIn('start a9ml_wifi_session',entry);self.assertIn('awaiting-s2-audit',entry)
  self.assertEqual(rc[rc.index('# Ordered end'):],old[old.index('# Ordered end'):])
 def test_quiet_boot_refuses_open_gates_before_any_transport(self):
  script=REPO/'scripts/mainline/android/android-boot.sh'
  # Exercise only the exact guard; do not invoke the real boot script.
  guard=next(x for x in script.read_text().splitlines() if x.startswith('[ "$S2_QUIET" = 0 ]'))
  text='S2_QUIET=1; WIFI=1; EINK=1; OPEN_GATES=1; HOLD=1800\nfail() { echo "$*"; exit 1; }\n'+guard+'\n'
  r=subprocess.run(['bash','-c',text],capture_output=True)
  self.assertNotEqual(r.returncode,0);self.assertIn(b'--s2-quiet requires',r.stdout+r.stderr)
 def test_usb_lock_inheritance_uses_live_owner(self):
  text='export A9_SERIAL=fixture; source '+str(REPO/'scripts/a9-env.sh')+'; export A9_USB_LOCKED=1 A9_USB_LOCK_OWNER=$$; a9_usb_lock(){ exit 99; }; a9_usb_lock_inherit'
  r=subprocess.run(['bash','-c',text],capture_output=True);self.assertEqual(r.returncode,0)

if __name__=='__main__':unittest.main(verbosity=2)
