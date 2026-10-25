#!/usr/bin/env python3
"""Focused opt-in smoke: inert transport, actual Android mksh property scripts."""
import ast
import importlib.util
import os
from pathlib import Path
import subprocess as sp
import tempfile
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('qualification_test',HERE/'test-host.py')
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
SHELL=[
    '/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64',
    '-L','/home/user/A9-build/mainline/adsp/d2-v8-rehearsal/manifest-sysroot',
    '-E','LD_LIBRARY_PATH=/home/user/A9-build/mainline/adsp/d2-v8-rehearsal/manifest-sysroot/system/lib64',
    '/home/user/A9-build/mainline/adsp/d2-v8-rehearsal/manifest-sysroot/system/bin/sh']

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=self.root/'value';self.store.write_text('')
        # No property service: these two host stubs only access this temp file.
        for name,body in (
            ('getprop','IFS= read -r value < "$HVENC_TEST_STORE" || :\nprintf "%s\\n" "$value"'),
            ('setprop','printf "%s\\n" "$2" > "$HVENC_TEST_STORE"')):
            p=self.root/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o700)
        env=dict(os.environ,A9_BTRY_IN_HOOK='1',HVENC_TEST_STORE=str(self.store),PATH=str(self.root)+':'+os.environ['PATH'])
        env.pop('A9_SWEEP_HVENC_CPU_READ',None)
        self.env=patch.dict(os.environ,env,clear=True);self.env.start();self.addCleanup(self.env.stop)
        self.verify=patch.object(t.runner.v,'verify_bundle',return_value={});self.verify.start();self.addCleanup(self.verify.stop)
        self.inspect=patch.object(t.runner.v,'inspect',return_value={'host_mp4_ok':1});self.inspect.start();self.addCleanup(self.inspect.stop)

    def sweep(self,interrupt=False):
        test=self
        class Sweep:
            def __init__(s):
                s.out=test.root;s.assets=test.root/'assets';s.adb='INERT'
                s.hvenc_qualify=s.touched=True;s.calls=[];s.native_calls=[];s.property_calls=[]
                s.qualification_end=time.monotonic()+300;s.command_end=s.qualification_end
                s.results={'A_stability':dict(status='PASS',evidence={})}
            def put(s,key,ok,**evidence):s.results[key]=dict(status='PASS' if ok else 'FAIL',evidence=evidence)
            def native(s,script,timeout):
                raise AssertionError('framework property command in native root context')
            def shell(s,cmd,timeout=20,**kw):
                s.calls.append(cmd)
                if 'debug.stagefright.a9.enc_cpu_read' in cmd:
                    s.property_calls.append(cmd);test.assertEqual(timeout,3)
                    p=sp.run(SHELL+['-c',cmd],capture_output=True,text=True,timeout=5)
                    return p.returncode,p.stdout
                if cmd.startswith('getprop'):return 0,'_b\n1\n'
                if cmd.startswith('mktemp'):return 0,'/data/local/tmp/a9-hvenc.ABCDEF\n'
                if cmd.startswith('timeout'):
                    if os.environ.get('A9_SWEEP_HVENC_CPU_READ') in ('0','1'):
                        test.assertEqual(test.store.read_text().strip(),os.environ['A9_SWEEP_HVENC_CPU_READ'])
                    if interrupt:raise InterruptedError()
                    return 0,t.RESULT
                if cmd.startswith('stat'):return 0,'123\n'
                if cmd.startswith(('chmod','rm -r -- ')):return 0,''
                raise AssertionError(cmd)
            def run(s,args,timeout):
                test.assertEqual(args[0],'INERT')
                if args[1]=='pull':Path(args[-1]).write_bytes(b'x'*123)
                else:test.assertEqual(args[1],'push')
                return 0,''
        return Sweep()

    def test_default_component_policy_keeps_property_untouched(self):
        s=self.sweep();t.runner.run(s)
        self.assertEqual(s.native_calls,[])
        self.assertEqual(s.results['18_hardware_encoder']['status'],'PASS')

    def test_ab_override_precedes_codec_and_restores_empty_or_true(self):
        for original,value in (('','0'),('true','1')):
            os.environ['A9_SWEEP_HVENC_CPU_READ']=value
            self.store.write_text(original);s=self.sweep();end=s.qualification_end
            t.runner.run(s);t.runner.run(s)
            row=s.results['18_hardware_encoder'];self.assertEqual(row['status'],'PASS')
            for key in ('cpu_read_requested','cpu_read_applied','cpu_read_restored'):self.assertEqual(row['evidence'][key],1)
            self.assertEqual(row['evidence']['cpu_read_value'],int(value))
            self.assertEqual(self.store.read_text().strip(),original)
            self.assertEqual(len(s.property_calls),3);self.assertEqual(s.qualification_end,end)
            self.assertLessEqual(s.command_end,end)
            (self.root/'hvenc-output.mp4').unlink()

    def test_interruption_restores_without_another_attempt(self):
        os.environ['A9_SWEEP_HVENC_CPU_READ']='1';self.store.write_text('0')
        s=self.sweep(interrupt=True)
        with self.assertRaises(InterruptedError):t.runner.run(s)
        self.assertEqual(self.store.read_text().strip(),'0')
        self.assertEqual(s.results['18_hardware_encoder']['status'],'FAIL')
        self.assertEqual(s.results['18_hardware_encoder']['evidence']['cpu_read_restored'],1)
        t.runner.run(s);self.assertEqual(len(s.property_calls),3)
        self.assertEqual(sum(c.startswith('timeout ') for c in s.calls),1)

    def test_generated_launcher_exports_default_and_opt_in(self):
        # Evaluate only the production runner's text expression, never build/main.
        tree=ast.parse((HERE.parents[1]/'reboot/build14.py').read_text())
        expr=next(n.value.args[0] for n in ast.walk(tree) if isinstance(n,ast.Expr)
            and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute)
            and isinstance(n.value.func.value,ast.Name) and n.value.func.value.id=='runner'
            and n.value.func.attr=='write_text')
        runner=self.root/'run.sh'
        runner.write_text(eval(compile(ast.Expression(expr),'<production runner>','eval'),
            dict(target='inert-hook',hold='3600',out=self.root)))
        fake=self.root/'runtime/boot-deadline/b-try.sh';fake.parent.mkdir(parents=True)
        fake.write_text('#!/bin/sh\nprintf "CPU_READ %s\\n" "$A9_SWEEP_HVENC_CPU_READ"\n');fake.chmod(0o700)
        for value in ('','0','1'):
            with patch.dict(os.environ,A9_SWEEP_HVENC_CPU_READ=value):
                p=sp.run(['bash',runner],capture_output=True,text=True,check=True,timeout=3)
            self.assertEqual(p.stdout,'CPU_READ '+value+'\n')

if __name__=='__main__':unittest.main(verbosity=2)
