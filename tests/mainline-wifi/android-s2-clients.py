#!/usr/bin/env python3
"""Real cached Android mksh/awk/Toybox under QEMU; synthetic files, no phone/root."""
import contextlib,hashlib,importlib.util,io,json,os,pathlib,shutil,subprocess,tempfile,unittest
R=pathlib.Path(__file__).resolve().parents[2];S=R/'scripts/mainline/wifi/android/s2'
spec=importlib.util.spec_from_file_location('driver',S/'driver.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
A=pathlib.Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
Q='/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'

class RealClients(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='s2-real-clients-');self.addCleanup(self.tmp.cleanup);self.t=pathlib.Path(self.tmp.name)
  self.bin=self.t/'bin';self.bin.mkdir()
  for name in ('base64','tar','chmod','mkdir','sha256sum','timeout','grep','tr','cat','seq'):
   p=self.bin/name
   p.write_text('#!/bin/sh\nexec env LD_LIBRARY_PATH='+str(A/'lib64')+' '+Q+' '+str(A/'linker64')+' '+str(A/'system/bin/toybox')+' '+name+' "$@"\n');p.chmod(0o700)
  shutil.copyfile(A/'bin/awk',self.bin/'awk');(self.bin/'awk').chmod(0o700)
 def shell(self,text):
  return subprocess.run([str(A/'bin/sh')],input=text,text=True,capture_output=True,timeout=40,env=dict(os.environ,PATH=str(self.bin)+':'+os.environ['PATH']))
 def test_exact_five_store_stdin_hash_commands_on_android_clients(self):
  block=self.t/'blocks';block.mkdir()
  for n in m.STORES:(block/n).write_bytes(('synthetic-'+n).encode())
  text='set -eu\n'+m.AUDIT_HELPERS+m.HASH_STORES.replace('/dev/block/by-name',str(block))
  r=self.shell(text);self.assertEqual(r.returncode,0,'Android hash command failed')
  self.assertEqual(m.store_rows(r.stdout.encode()),{n:hashlib.sha256((block/n).read_bytes()).hexdigest() for n in m.STORES})
 def mpss_tree(self):
  root=self.t/'phone';root.mkdir()
  device=root/'sys/bus/platform/devices/modem/of_node';device.mkdir(parents=True)
  (device/'compatible').write_bytes(b'qcom,sm6115-mpss-pas\0')
  return root
 def proof(self,root,prestart):
  text=m.AUDIT_HELPERS+(m.PRE_MPSS if prestart else m.OFFLINE_MPSS)
  text=text.replace('/sys/',str(root)+'/sys/')
  return self.shell('set -eu\n'+text)
 def test_87d_sequencing_defect_and_unbound_prestart_proof(self):
  root=self.mpss_tree()
  # Derived from the captured awaiting-audit chronology + pinned module/init
  # manifests, not a claim that hardware sysfs was captured in 87d.
  spec=importlib.util.spec_from_file_location('old_driver',pathlib.Path('/home/user/A9-build/mainline/wifi-android/s2-87d/driver.py'))
  old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
  r=self.shell('set -eu\n'+old.OFFLINE_MPSS.replace('/sys/',str(root)+'/sys/'))
  self.assertNotEqual(r.returncode,0,'old check incorrectly accepted zero remoteprocs')
  self.assertEqual(self.proof(root,True).returncode,0)
  strict=self.proof(root,False);self.assertNotEqual(strict.returncode,0)
  self.assertIn('gate=mpss-count',strict.stdout)
 def test_unbound_proof_rejects_missing_duplicate_bound_or_loaded_device(self):
  root=self.mpss_tree();device=root/'sys/bus/platform/devices/modem'
  module=root/'sys/module/qcom_q6v5_pas';module.mkdir(parents=True)
  self.assertNotEqual(self.proof(root,True).returncode,0);module.rmdir()
  (device/'driver').symlink_to(self.t/'nonexistent')
  self.assertNotEqual(self.proof(root,True).returncode,0);(device/'driver').unlink()
  second=device.parent/'second/of_node';second.mkdir(parents=True);(second/'compatible').write_bytes(b'qcom,sm6115-mpss-pas\0')
  self.assertNotEqual(self.proof(root,True).returncode,0);(second/'compatible').unlink()
  (device/'of_node/compatible').unlink();self.assertNotEqual(self.proof(root,True).returncode,0)
 def test_registered_remoteproc_must_be_unique_and_offline_even_before_start(self):
  root=self.mpss_tree();r=root/'sys/class/remoteproc/remoteproc0';(r/'device/of_node').mkdir(parents=True)
  (r/'device/of_node/compatible').write_bytes(b'qcom,sm6115-mpss-pas\0');(r/'state').write_text('offline\n')
  self.assertEqual(self.proof(root,True).returncode,0);self.assertEqual(self.proof(root,False).returncode,0)
  (r/'state').write_text('running\n');self.assertNotEqual(self.proof(root,True).returncode,0);self.assertNotEqual(self.proof(root,False).returncode,0)
  (r/'state').write_text('offline\n');second=r.parent/'remoteproc1';(second/'device/of_node').mkdir(parents=True)
  (second/'device/of_node/compatible').write_bytes(b'qcom,sm6115-mpss-pas\0');(second/'state').write_text('offline\n')
  self.assertNotEqual(self.proof(root,True).returncode,0);self.assertNotEqual(self.proof(root,False).returncode,0)
 def test_full_root_pre_audit_and_idle_cleanup_before_module_registration(self):
  root=self.mpss_tree();(root/'proc').mkdir();(root/'proc/mounts').write_text('/dev/block/ram0 /data ext4 rw 0 0\n')
  for name in ('a9_eink/parameters/enable','panel_hisense_hlte556n_eink/parameters/enable','fp9929a/parameters/allow_power_on'):
   p=root/'sys/module'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('N\n')
  block=root/'dev/block/by-name';block.mkdir(parents=True)
  for name in m.STORES:(block/name).write_text('synthetic-'+name)
  d=m.Driver({'evidence':str(self.t/'evidence'),'private':str(self.t/'private'),'tools':{'adb':'fake-boundary'}});d.e.mkdir(mode=0o700)
  def call(argv,**kw):
   script=kw['input'].decode()
   for path in ('/proc/mounts','/sys/','/dev/block/by-name','/mnt/a9-wifi/session'):script=script.replace(path,str(root)+path)
   # Model admission only. The real complete root envelope, shell, awk, tr,
   # grep, timeout and sha256sum execute against the synthetic filesystem.
   pre='''getprop() { case "$1" in ro.boot.slot_suffix) echo _b;; ro.a9ml.wifi.session) echo 1;; vendor.a9ml.wifi.session.state) echo awaiting-s2-audit;; esac; }; uname() { echo 7.synthetic; }; id() { echo 0; };\n'''
   r=self.shell(pre+script);kw['status'](r.returncode,r.stdout.encode(),False)
   if r.returncode:raise m.Refused('command')
   return r.stdout.encode()
  d.call=call;d.phase='store-pre';before=d.live_stores(True)
  with contextlib.redirect_stdout(io.StringIO()):d.stop()
  self.assertEqual(d.live_stores(False),before)
  events=[json.loads(x) for x in (d.e/'root-status.jsonl').read_text().splitlines()]
  self.assertIn('S2_AUDIT gate=session-service state=unpublished',events[0]['audit'])
  self.assertIn('S2_AUDIT gate=mpss branch=unbound',events[0]['audit'])
  self.assertEqual(sum('gate=store-read' in x for x in events[0]['audit']),5)
  d.started_at=0
  with self.assertRaises(m.Refused):d.live_stores(False)
  d.started_at=None;(block/'persist').unlink()
  with self.assertRaises(m.Refused):d.live_stores(True)
  last=json.loads((d.e/'root-status.jsonl').read_text().splitlines()[-1])
  self.assertIn('S2_AUDIT gate=store-open-persist result=failed',last['audit'])
 def test_disabled_never_started_service_matrix_with_real_android_clients(self):
  root=self.mpss_tree();props=root/'props';props.mkdir();(root/'proc').mkdir()
  (root/'proc/mounts').write_text('/dev/block/ram0 /data ext4 rw 0 0\n')
  for name in ('a9_eink/parameters/enable','panel_hisense_hlte556n_eink/parameters/enable','fp9929a/parameters/allow_power_on'):
   p=root/'sys/module'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('N\n')
  pre='''getprop() {
 if [ "$1" = "$(cat ROOT/props/fail)" ]; then return 1; fi
 if [ -f "ROOT/props/$1" ]; then cat "ROOT/props/$1"; fi
}; uname() { echo 7.synthetic; }; id() { echo 0; };\n'''.replace('ROOT',str(root))
  base={'ro.boot.slot_suffix':'_b','ro.a9ml.wifi.session':'1','vendor.a9ml.wifi.session.state':'awaiting-s2-audit','fail':'none'}
  key='init.svc.a9ml_wifi_session';pid='init.svc_debug_pid.a9ml_wifi_session';history='ro.boottime.a9ml_wifi_session';start='vendor.a9ml.wifi.s2.start'
  cases=[('unpublished',{},None),('stopped',{key:'stopped'},None),
   *[(value,{key:value},'session-service') for value in ('running','stopping','restarting','untrusted-value')],
   ('has-pid',{pid:'321'},'session-service-pid'),('has-history',{history:'123456'},'session-service-history'),
   ('requested',{start:'1'},'session-start-request'),('stopped-requested',{key:'stopped',start:'1'},'session-start-request'),
   *[('unreadable-'+name,{'fail':value},'session-service-read') for name,value in (('state',key),('pid',pid),('history',history),('start',start))]]
  for name,changes,reason in cases:
   with self.subTest(case=name):
    for p in props.iterdir():p.unlink()
    for k,v in {**base,**changes}.items():(props/k).write_text(v+'\n')
    text=m.RAM_SLOT+m.PRE+m.PRE_MPSS+'echo S2_IDLE_OK\n'
    for path in ('/proc/mounts','/sys/','/mnt/a9-wifi/session'):text=text.replace(path,str(root)+path)
    result=self.shell('set -eu\n'+pre+m.AUDIT_HELPERS+text)
    self.assertEqual(result.returncode==0,reason is None,result.stdout)
    if reason:self.assertIn('S2_AUDIT gate='+reason+' result=failed',result.stdout)
    else:self.assertIn('S2_IDLE_OK',result.stdout)
  # The empty state fixture is derived from the pinned disabled/never-started
  # lifecycle; 87f captured only the failed predicate, not the raw property.
  for p in props.iterdir():p.unlink()
  for k,v in base.items():(props/k).write_text(v+'\n')
  spec=importlib.util.spec_from_file_location('driver87f',pathlib.Path('/home/user/A9-build/mainline/wifi-android/s2-87f/driver.py'))
  old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
  text=(old.RAM_SLOT+old.PRE).replace('/proc/mounts',str(root)+'/proc/mounts').replace('/sys/',str(root)+'/sys/').replace('/mnt/a9-wifi/session',str(root)+'/mnt/a9-wifi/session')
  self.assertNotEqual(self.shell('set -eu\n'+pre+old.AUDIT_HELPERS+text).returncode,0)
 def test_real_android_decodes_and_installs_the_exact_archive(self):
  bundle=self.t/'bundle';bundle.mkdir()
  for n in m.BUNDLE_FILES:(bundle/n).write_text('synthetic-'+n)
  (bundle/'SHA256SUMS').write_text(''.join(hashlib.sha256((bundle/n).read_bytes()).hexdigest()+'  '+n+'\n' for n in m.BUNDLE_FILES))
  config={'bundle':str(bundle),'evidence':str(self.t/'e'),'private':str(self.t/'p')};d=m.Driver(config)
  captured=[]
  d.root=lambda text,seconds:captured.append(text) or b'S2_INSTALL ready=yes\n'
  d.install();text=captured[0]
  # Only device admission and root ownership are modeled; archive bytes, here-doc,
  # pipeline, base64/tar/chmod commands execute with the real Android clients.
  text=text.replace(m.RAM_SLOT,'').replace('/data/local/tmp',str(self.t/'phone'))
  (self.t/'phone').mkdir()
  text=text.replace('chown -R 0:0 "$B"',': # ordinary host user; root ownership checked by the worker tests')
  r=self.shell('set -eu\n'+text);self.assertEqual(r.returncode,0,'Android archive command failed')
  target=self.t/'phone/a9-wifi-s2';self.assertEqual(target.stat().st_mode&0o777,0o700)
  for n in m.BUNDLE_FILES:
   self.assertEqual((target/n).read_bytes(),(bundle/n).read_bytes());self.assertEqual((target/n).stat().st_mode&0o777,0o400)
  self.assertTrue((target/'GO').read_text().startswith('I_AUTHORIZE_REVIEWED_ANDROID_S2\n'))

if __name__=='__main__':unittest.main(verbosity=2)
