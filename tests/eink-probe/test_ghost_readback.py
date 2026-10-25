"""Captured Qualcomm readback replay. Private files/shell only; no transport."""
import ast
import hashlib
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import types
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/eink-probe/sessions'))
import ghost_prepare as P
FIX=Path(__file__).with_name('fixtures')/'ghost-readback'
OLD='256f5e09a7b1bab82fb2ac380092c0c87fd86f0f'


class CapturedReadback(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.capture=(FIX/'preparation-before.txt').read_text()
        self.gpio=(FIX/'hardware-gpio.txt').read_text()
        self.status=self.capture[self.capture.index('enable 0'):self.capture.index('SAFE3_ROOT_RC')]
        self.gate=''
        for name,value in re.findall(r'^SAFE3_BOOL ([\w/-]+) ([YN01])$',self.capture,re.M):
            p=self.root/'sys/module'/name;p.parent.mkdir(parents=True);p.write_text(value+'\n')
        p=self.root/'proc/mounts';p.parent.mkdir();p.write_text('/dev/block/ram0 /data ext4 rw 0 0\n')
        self.gpiofile=self.root/'sys/kernel/debug/gpio';self.gpiofile.parent.mkdir(parents=True);self.gpiofile.write_text(self.gpio)
        self.statusfile=self.root/'sys/class/drm/card3-DSI-1/eink/status';self.statusfile.parent.mkdir(parents=True);self.statusfile.write_text(self.status)
        self.oldsrc=subprocess.check_output(['git','show',OLD+':scripts/eink-probe/sessions/ghost_prepare.py'],cwd=ROOT,text=True)
        self.legacy=types.ModuleType('legacy_ghost');exec(compile(self.oldsrc,'legacy_ghost','exec'),self.legacy.__dict__)

    def shell(self,script,filename):
        script=re.sub(r'/sys/|/proc/',lambda m:str(self.root)+m[0],script)
        pre='''getprop() { case "$1" in
ro.boot.slot_suffix) echo _b;; sys.boot_completed) echo 1;;
vendor.a9ml.eink.card) echo card3;; vendor.a9ml.eink.gate) printf '%s\\n' '''+shlex.quote(self.gate)+''';;
*) return 1;; esac; }\n'''
        result=subprocess.run(['bash','-c',pre+script],capture_output=True,text=True,timeout=3)
        (self.root/filename).write_text(result.stdout+result.stderr)
        return result.returncode,result.stdout

    def runner(self,module=P):
        obj=module.Preparation.__new__(module.Preparation);obj.root=self.shell;obj.out=self.root
        # Exercise the production quarantine path on invalid physical evidence.
        from ghost_attempt import init
        if not (self.root/'attempt.json').exists():init(self.root,self.root,probe=True)
        return obj

    def test_captured_failure_verbatim_then_fixed_shell_accepts_real_gpio_and_empty_request(self):
        self.assertEqual(hashlib.sha256(self.capture.encode()).hexdigest(),'850e20c6fe3e5c380b9a1d69dc7213f1236380246e472ee54de8997aa1a24deb')
        self.assertEqual(hashlib.sha256(self.gpio.encode()).hexdigest(),'e4ccb3295924c79da6ed30c47384acf5578e9e9b50be36cb68299b0396f5ed18')
        with self.assertRaises(self.legacy.Stop):self.runner(self.legacy).closed_preparation('old.txt')
        self.assertEqual((self.root/'old.txt').read_text(),self.capture)
        txt=self.runner().closed_preparation('fixed.txt')
        self.assertEqual(txt,self.capture.replace('SAFE3_ROOT_RC 1\n',self.gpio+'SAFE3_ROOT_RC 0\n'))
        self.gate='closed';self.runner().closed_preparation('explicit-closed.txt')
        self.assertFalse((self.root/'gate-state.json').exists())

    def test_closed_readback_negative_matrix(self):
        cases={
            'gate-open':('gate','open'),'gate-faulted':('gate','faulted'),'gate-refused':('gate','refused'),
            'missing-vcom':('gpio',re.sub(r'^.*gpio36\s*:.*\n','',self.gpio,flags=re.M)),
            'missing-pwrup':('gpio',re.sub(r'^.*gpio86\s*:.*\n','',self.gpio,flags=re.M)),
            'vcom-high':('gpio',self.gpio.replace('gpio36  : out low','gpio36  : out high')),
            'pwrup-high':('gpio',self.gpio.replace('gpio86  : out low','gpio86  : out high')),
            'vcom-input':('gpio',self.gpio.replace('gpio36  : out low','gpio36  : in  low')),
            'wrong-mux':('gpio',self.gpio.replace('gpio36  : out low  func0','gpio36  : out low  func1')),
            'duplicate-pin':('gpio',self.gpio.replace(' gpio36  :',' gpio36 : out low func0\n gpio36  :')),
            'wrong-chip':('gpio',self.gpio.replace('500000.pinctrl','other.pinctrl')),
            'headerless':('gpio',self.gpio[self.gpio.index('\n')+1:]),
            'hv-on':('status',self.status.replace('hv_on 0','hv_on 1')),
            'driver-enabled':('status',self.status.replace('enable 0','enable 1',1)),
            'module-open':('module','Y\n'),'module-unreadable':('module',None),'gpio-unreadable':('gpio',None),
        }
        first=self.root/'sys/module/a9_eink/parameters/enable'
        for name,(kind,value) in cases.items():
            with self.subTest(case=name):
                self.gate='';self.gpiofile.write_text(self.gpio);self.statusfile.write_text(self.status);first.write_text('N\n')
                if kind=='gate':self.gate=value
                else:
                    p={'gpio':self.gpiofile,'status':self.statusfile,'module':first}[kind]
                    if value is None:p.unlink()
                    else:p.write_text(value)
                with self.assertRaises(P.Stop):self.runner().closed_preparation(name+'.txt')

    def test_explicit_root_result_and_unique_named_modules_required(self):
        good=self.runner().closed_preparation('baseline.txt')
        for tail in ('','SAFE3_ROOT_RC 1\n','SAFE3_ROOT_RC 0\nSAFE3_ROOT_RC 0\n'):
            r=self.runner();r.root=lambda *_:(0,good.replace('SAFE3_ROOT_RC 0\n',tail))
            with self.subTest(marker=tail),self.assertRaises(P.Stop):r.closed_preparation('marker.txt')
        for malformed in (good.replace('SAFE3_BOOL fp9929a/parameters/allow_power_on N','SAFE3_BOOL a9_eink/parameters/enable N'),good+self.status):
            with self.assertRaises(P.Stop):P.require_closed_readback(malformed)

    def test_only_readback_code_changed_in_production_helper(self):
        before=ast.parse(self.oldsrc);after=ast.parse(Path(P.__file__).read_text())
        def clean(tree):
            tree.body=[n for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name!='require_closed_readback']
            for node in tree.body:
                if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PREP_CLOSED' for t in node.targets):node.value=ast.Constant('READBACK')
                if isinstance(node,ast.ClassDef) and node.name=='Preparation':
                    node.body=[n for n in node.body if not isinstance(n,ast.FunctionDef) or n.name!='closed_preparation']
            return ast.dump(tree,include_attributes=False)
        self.assertEqual(clean(before),clean(after))


if __name__=='__main__':unittest.main()
