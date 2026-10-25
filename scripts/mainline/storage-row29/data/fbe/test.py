#!/usr/bin/env python3
"""Bounded host checks: native ARM64, Android mksh and synthetic boundaries."""
import argparse,base64,importlib.util,os,resource,subprocess,tempfile,unittest
from pathlib import Path
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
HERE=Path(__file__).resolve().parent
def load(path,name):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
stage=load(HERE.parent/'integrate.py','stage');build=load(HERE.parent/'build-map.py','build')
QEMU='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
SHELL='/home/user/A9-build/mainline/integrated-20261021n/r4-tests/android-sh'
class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='row29-fbe-test-');cls.root=Path(cls.tmp.name);cls.arm=cls.root/'functions'
        subprocess.run([build.CC,'-static','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function','-o',str(cls.arm),str(HERE/'test-functions.c')],check=True)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def native(self,*args,ok=True):
        r=subprocess.run([QEMU,str(self.arm),*map(str,args)],capture_output=True,timeout=15)
        self.assertEqual(r.returncode==0,ok,(r.stdout,r.stderr))
    def test_1_superblocks_and_no_prekey_traversal(self):
        self.native('super');self.native('no-encrypt',ok=False)
        root=self.root/'data';root.mkdir();(root/'local').symlink_to('/not-a-real-phone-key-unavailable')
        self.native('root',root);self.assertTrue((root/'local').is_symlink())
    def test_2_public_markers_and_cli_refusals(self):
        root=self.root/'proof';root.mkdir()
        self.native('create',root);self.native('verify',root);self.native('create',root,ok=False)
        p=root/'row29-fbe-proof';p.write_text('tamper');self.native('verify',root,ok=False);p.unlink()
        p.symlink_to(root/'elsewhere');self.native('create',root,ok=False);self.native('verify',root,ok=False)
        self.native('--selftest');self.native('/dev/block/sde',ok=False)
    def test_3_fbe_render_and_rollback(self):
        image=OUT/'../image';fstab=(image/'fstab.hlte556n').read_text();rc=(image/'init.hlte556n.rc').read_text()
        self.assertEqual(stage.render(fstab,rc,'row29-fbe'),(fstab,rc))
        self.assertIn(stage.FBE,fstab);self.assertNotIn('inlinecrypt',fstab);self.assertNotIn('wrappedkey',fstab);self.assertNotIn('metadata_encryption',fstab)
        self.assertNotIn('\non post-fs-data\n',rc)
        self.assertLess(rc.index('mkdir /dev/a9-row29-hold'),rc.index('start a9ml_deadman'))
        for mode in ('row29','ram'):
            pair=stage.render(fstab,rc,mode);self.assertNotIn('fileencryption=',pair[0]);self.assertNotIn('mkdir /dev/a9-row29-hold',pair[1]);self.assertIn('\non post-fs-data\n',pair[1])
            self.assertEqual(stage.render(*pair,mode),pair)
    def test_4_embedded_tools_android_shell_and_approval(self):
        for script,helper,tag in [('check-fbe.sh','row29-fbe-policy-check','CHECK'),('row29-fbe-reformat.sh','row29-fbe-reformat-helper','REFORMAT')]:
            p=OUT/script;text=p.read_text();encoded=text.split("<<'ROW29_FBE_"+tag+"_HELPER'\n")[1].split('\nROW29_FBE_'+tag+'_HELPER')[0]
            self.assertEqual(base64.b64decode(encoded),(OUT/helper).read_bytes())
            subprocess.run([SHELL,'-n',str(p)],check=True,capture_output=True,timeout=15)
            r=subprocess.run([QEMU,str(OUT/helper),'--selftest'],capture_output=True,timeout=15);self.assertEqual(r.returncode,0)
        r=subprocess.run([SHELL,str(OUT/'row29-fbe-reformat.sh')],capture_output=True,timeout=15)
        self.assertNotEqual(r.returncode,0);self.assertIn(b'--write-approved required',r.stderr)
    def test_5_checker_framing_and_state_refusals(self):
        # Exercise actual Android mksh with the native-policy boundary stubbed;
        # native validators above test the separate C boundary.
        text=(OUT/'check-fbe.sh').read_text()
        text=text[:text.index('node=/dev/block/mapper/')]+"echo 'native/map boundary reached'\n"
        line=next(l for l in text.splitlines() if l.startswith("awk "))
        text=text.replace(line,': # fstab boundary checked separately')
        stubs='''id() { echo 0; }
getprop() { case "$1" in ro.boot.slot_suffix) echo "${FIXTURE_SLOT:-_b}";;sys.boot_completed) echo 1;;ro.crypto.state) echo "${FIXTURE_CRYPTO:-encrypted}";;ro.crypto.type) echo file;;init.svc.*) echo running;;esac; }
getenforce() { echo Enforcing; }
cmd() { echo "${FIXTURE_USER:-RUNNING_UNLOCKED}"; }
'''
        p=self.root/'check.sh';p.write_text(text.replace('set -eu','set -eu\n'+stubs))
        for extra,ok in [({},True),({'FIXTURE_SLOT':'_a'},False),({'FIXTURE_CRYPTO':'unsupported'},False),({'FIXTURE_USER':'RUNNING_LOCKED'},False)]:
            r=subprocess.run([SHELL,str(p),'check'],env={**os.environ,**extra},capture_output=True,timeout=15);self.assertEqual(r.returncode==0,ok,(r.stdout,r.stderr))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a,rest=p.parse_known_args();OUT=a.out
    unittest.main(argv=[__file__,*rest],verbosity=2)
