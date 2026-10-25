#!/usr/bin/env python3
"""Five host groups with real readelf/ELFs and a synthetic APEX; no target execution."""
import importlib.util,json,os,shutil,subprocess as sp,sys,tempfile,unittest,zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('needed',HERE/'check-hal-needed.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
CC='/nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin/cc'
class Needed(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.r=Path(self.tmp.name);self.v=self.r/'vendor';self.system=self.r/'system';self.out=self.r/'audit'
  for p in [self.v/'bin/hw',self.v/'lib64',self.system/'lib64',self.system/'etc',self.out]:p.mkdir(parents=True,exist_ok=True)
  (self.system/'etc/llndk.libraries.txt').write_text('')
 def elf(self,p,needed=(),runpath=None):
  p.parent.mkdir(parents=True,exist_ok=True);c=self.r/'fixture.c';c.write_text('int fixture(void) { return 7; }\n')
  cmd=[CC,'-shared','-nostdlib','-fPIC',c,'-Wl,-soname,'+p.name,'-o',p]
  for q in needed:cmd+=['-Wl,--no-as-needed','-L'+str(q.parent),'-l:'+q.name]
  if runpath:cmd+=['-Wl,-rpath,'+runpath]
  sp.run(list(map(str,cmd)),check=True,capture_output=True);return p
 def audit(self,llndk=()):
  fs=m.Files(self.out);fs.mount('/vendor',m.Tree(self.v));fs.mount('/system',m.Tree(self.system));return m.Audit(fs,self.out,llndk)
 def test_1_hidden_system_interface_and_vendor_fix(self):
  interface=self.elf(self.system/'lib64/libinterface.so');self.elf(self.v/'bin/hw/service',[interface]);a=self.audit()
  self.assertEqual(a.check('/vendor/bin/hw/service',['/vendor/lib64/private'])['missing'],1)
  self.assertEqual(self.audit(['libinterface.so']).check('/vendor/bin/hw/service',[])['missing'],0)
  p=self.v/'lib64/private/libinterface.so';p.parent.mkdir();shutil.copyfile(interface,p)
  self.assertEqual(self.audit().check('/vendor/bin/hw/service',['/vendor/lib64/private'])['missing'],0)
 def test_2_transitive_missing_and_origin_runpath(self):
  leaf=self.elf(self.r/'build/libleaf.so');mid=self.elf(self.v/'lib64/private/libmid.so',[leaf],runpath='$ORIGIN');self.elf(self.v/'bin/hw/service',[mid],runpath='$ORIGIN/../../lib64/private')
  self.assertEqual(self.audit().check('/vendor/bin/hw/service',[])['missing'],1)
  shutil.copyfile(leaf,mid.parent/leaf.name)
  self.assertEqual(self.audit().check('/vendor/bin/hw/service',[])['missing'],0)
 def test_3_inventory_and_vendor_apex_missing_dependency(self):
  self.elf(self.v/'bin/hw/ordinary');leaf=self.elf(self.r/'build/libleaf.so');tree=self.r/'apex-tree';self.elf(tree/'bin/hw/apex-hal',[leaf]);(tree/'lib64').mkdir()
  pkg='com.test.hal';image=self.r/'apex.img';apex=self.v/'apex/test.apex';apex.parent.mkdir()
  def pack():
   if image.exists():image.unlink()
   sp.run(['mke2fs','-q','-t','ext4','-F','-d',str(tree),str(image),'8192'],check=True,capture_output=True)
   with zipfile.ZipFile(apex,'w',zipfile.ZIP_DEFLATED) as z:
    z.writestr('apex_manifest.pb',b'\x0a'+bytes([len(pkg)])+pkg.encode());z.write(image,'apex_payload.img')
  pack()
  def check(name):
   out=self.r/name;r=sp.run([sys.executable,str(HERE/'check-hal-needed.py'),'--vendor-root',str(self.v),'--system-root',str(self.system),'--out',str(out)],capture_output=True,text=True)
   return r,json.loads((out/'hal-needed.json').read_text())
  r,j=check('first');self.assertEqual(r.returncode,1,r.stdout+r.stderr);self.assertEqual(len(j['roots']),2);self.assertTrue(any('/apex/com.test.hal/' in v['root'] for v in j['failures']))
  shutil.copyfile(leaf,tree/'lib64/libleaf.so');pack();r,j=check('second');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
 def test_4_architecture_and_namespace_symlink_fail_closed(self):
  leaf=self.elf(self.v/'lib64/libleaf.so');self.elf(self.v/'bin/hw/service',[leaf]);b=bytearray(leaf.read_bytes());b[18:20]=(40).to_bytes(2,'little');leaf.write_bytes(b)
  self.assertGreater(self.audit().check('/vendor/bin/hw/service',[])['missing'],0)
  leaf.unlink();system=self.elf(self.system/'lib64/libleaf.so');leaf.symlink_to('/system/lib64/libleaf.so')
  self.assertGreater(self.audit().check('/vendor/bin/hw/service',[])['missing'],0)
 def test_5_cycles_contexts_and_init_first_definition(self):
  b=self.elf(self.v/'lib64/private/libb.so');a=self.elf(self.v/'lib64/liba.so',[b]);self.elf(b,[a]);self.elf(self.v/'bin/hw/good',[a]);self.elf(self.v/'bin/hw/bad',[a])
  audit=self.audit();self.assertEqual(audit.check('/vendor/bin/hw/good',['/vendor/lib64/private'])['missing'],0);self.assertEqual(audit.check('/vendor/bin/hw/bad',[])['missing'],1)
  rc=self.v/'etc/init/a.rc';rc.parent.mkdir(parents=True);rc.write_text('service same /vendor/bin/hw/good\n    setenv LD_LIBRARY_PATH /vendor/lib64/private\nservice same /vendor/bin/hw/bad\n')
  self.assertEqual(m.services(audit.fs,['/vendor/etc/init/a.rc'])['same']['exe'],'/vendor/bin/hw/good')
  rc.write_text(rc.read_text()+'    override\n');self.assertEqual(m.services(audit.fs,['/vendor/etc/init/a.rc'])['same']['exe'],'/vendor/bin/hw/bad')
 def test_6_every_hal_plugin_and_no_service_environment_leak(self):
  leaf=self.elf(self.v/'lib64/private/libhidden.so')
  self.elf(self.v/'bin/hw/service',[leaf])
  plugin=self.v/'lib64/hw/android.hardware.gatekeeper@1.0-impl-qti.so'
  self.elf(plugin,[leaf]);self.elf(self.v/'lib/hw/synthetic32.so')
  # Real ARM32 ELF, even though host compilation above uses x86-64 fixtures.
  ndk=m.READELF.with_name('clang');sp.run([ndk,'--target=armv7a-linux-androideabi30','-shared','-nostdlib','-fPIC',self.r/'fixture.c','-o',self.v/'lib/hw/synthetic32.so'],check=True,capture_output=True)
  rc=self.v/'etc/init/a.rc';rc.parent.mkdir(parents=True)
  rc.write_text('service vendor.gatekeeper-1-0 /vendor/bin/hw/service\n    setenv LD_LIBRARY_PATH /vendor/lib64/private\n')
  r=sp.run([sys.executable,HERE/'check-hal-needed.py','--vendor-root',self.v,'--system-root',self.system,'--out',self.out],capture_output=True,text=True)
  j=json.loads((self.out/'hal-needed.json').read_text());self.assertEqual(r.returncode,1,r.stdout+r.stderr)
  self.assertIn('/vendor/lib/hw/synthetic32.so',j['inventory']['lib_hw'])
  self.assertIn('/vendor/lib64/hw/'+plugin.name,j['inventory']['lib64_hw'])
  self.assertTrue(any(f['root'].endswith(plugin.name) and f['needed']=='libhidden.so' for f in j['failures']))
  self.assertFalse(any(f['root']=='/vendor/bin/hw/service' for f in j['failures']))
 def test_7_only_selected_vndk_exports_cross_namespace(self):
  lib=self.elf(self.r/'vndk/lib64/libvndk.so');self.elf(self.v/'bin/hw/service',[lib]);a=self.audit();a.fs.mount('/apex/com.android.vndk.v30',m.Tree(self.r/'vndk'))
  self.assertEqual(a.check('/vendor/bin/hw/service',[])['missing'],1)
  a.vndk=dict(prefix='/apex/com.android.vndk.v30',exports={'libvndk.so'})
  self.assertEqual(a.check('/vendor/bin/hw/service',[])['missing'],0)
  a.vndk['exports']=set();self.assertEqual(a.check('/vendor/bin/hw/service',[])['missing'],1)
if __name__=='__main__':unittest.main(verbosity=2)
