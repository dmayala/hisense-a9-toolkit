#!/usr/bin/env python3
"""Five host groups: real wrapper/launcher, fake transport and native endpoint."""
import ast
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess as sp
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

MAIN = Path(__file__).resolve().parents[1]


class Tests(unittest.TestCase):
    def fixture(self, root):
        runtime = root/'runtime'
        for name in ('fingerprint/with-display.py', 'play/onb.py'):
            p = runtime/name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(MAIN/name, p)
        for name in ('a9-waveform.bin', 'a9-ctrl-template.bin'):
            p = root/'acceptance-assets/firmware'/name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('synthetic display firmware')
        def script(p, text):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('#!'+sys.executable+'\n'+text)
            p.chmod(0o700)
        script(root/'fake-adb', '''import os, pathlib, shutil, sys
r=pathlib.Path(os.environ['FIXTURE'])
assert sys.argv[1]=='push'
shutil.copyfile(sys.argv[2],r/pathlib.Path(sys.argv[3]).name)
with (r/'events').open('a') as f: f.write('PUSH '+pathlib.Path(sys.argv[2]).name+'\\n')
''')
        script(runtime/'boot-deadline/onb-root.py', '''import os, pathlib, sys
r=pathlib.Path(os.environ['FIXTURE']); source=sys.stdin.read()
opening="printf 'EINK_OPENED" in source
event='OPEN' if opening else 'CLOSE'
with (r/'events').open('a') as f: f.write(event+'\\n')
if opening:
    assert all((r/('row28-'+n)).is_file() for n in ('a9-waveform.bin','a9-ctrl-template.bin'))
(r/'state').write_text(event)
if os.environ.get('FAIL_'+event)=='1': sys.exit(1)
print('EINK_OPENED' if opening else 'EINK_CLOSED')
''')
        script(root/'outer.py', '''import os, pathlib, time
r=pathlib.Path(os.environ['FIXTURE'])
assert (r/'state').read_text()=='OPEN'
with (r/'events').open('a') as f: f.write('REGISTRATION_RETURNED\\n')
assert (r/'state').read_text()=='OPEN'
with (r/'events').open('a') as f: f.write('ENROLL_UNLOCK\\n')
if os.environ.get('WAIT_CHILD')=='1': time.sleep(60)
raise SystemExit(int(os.environ.get('CHILD_RC','0')))
''')
        env = dict(os.environ, FIXTURE=str(root), ADB=str(root/'fake-adb'),
                   A9_BTRY_IN_HOOK='1', A9_BTRY_BOOT=str(root/'boot.img'),
                   A9_BTRY_LOGS=str(root/'logs'), A9_FINGERPRINT_ONB=str(root/'outer.py'))
        env.pop('A9_SWEEP_ASSETS', None)
        for k in ('FAIL_OPEN', 'FAIL_CLOSE', 'WAIT_CHILD', 'CHILD_RC'):
            env.pop(k, None)
        return [sys.executable, '-B', str(runtime/'fingerprint/with-display.py')], env

    def test_1_display_spans_registration_and_outer_hook(self):
        for child_rc in (0, 7):
            with self.subTest(child_rc=child_rc), tempfile.TemporaryDirectory() as tmp:
                r=Path(tmp); cmd, env=self.fixture(r); env['CHILD_RC']=str(child_rc)
                if child_rc == 0:
                    default=r/'runtime/fingerprint/onb.py'
                    shutil.copyfile(r/'outer.py', default); default.chmod(0o700)
                    env.pop('A9_FINGERPRINT_ONB')
                p=sp.run(cmd,env=env,capture_output=True,text=True,timeout=5)
                self.assertEqual(p.returncode,child_rc,p.stdout+p.stderr)
                self.assertEqual((r/'events').read_text().splitlines(), [
                    'PUSH a9-waveform.bin', 'PUSH a9-ctrl-template.bin', 'OPEN',
                    'REGISTRATION_RETURNED', 'ENROLL_UNLOCK', 'CLOSE'])
                self.assertIn('FP_DISPLAY CLOSED',p.stdout)

    def test_2_partial_open_closes_without_running_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp); cmd, env=self.fixture(r); env['FAIL_OPEN']='1'
            p=sp.run(cmd,env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(p.returncode,1)
            events=(r/'events').read_text()
            self.assertNotIn('ENROLL',events)
            self.assertTrue(events.endswith('OPEN\nCLOSE\n'))

    def test_3_close_failure_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp); cmd, env=self.fixture(r); env['FAIL_CLOSE']='1'
            p=sp.run(cmd,env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(p.returncode,1)
            self.assertIn('FP_DISPLAY CLOSE_FAILED',p.stdout)

    def test_4_interruption_closes_and_reaps_owned_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp); cmd, env=self.fixture(r); env['WAIT_CHILD']='1'
            p=sp.Popen(cmd,env=env,stdout=sp.PIPE,stderr=sp.PIPE,text=True)
            try:
                end=time.monotonic()+5
                while time.monotonic()<end:
                    if (r/'events').exists() and 'ENROLL_UNLOCK' in (r/'events').read_text(): break
                    time.sleep(0.02)
                else: self.fail('inert outer hook never entered')
                p.send_signal(signal.SIGTERM)
                stdout,stderr=p.communicate(timeout=5)
                self.assertEqual(p.returncode,1,stdout+stderr)
                self.assertTrue((r/'events').read_text().endswith('CLOSE\n'))
            finally:
                if p.poll() is None: p.kill(); p.wait()

    def test_5_production_launcher_wraps_custom_outer_and_keeps_hold(self):
        tree=ast.parse((MAIN/'reboot/build14.py').read_text())
        loop=next(n for n in ast.walk(tree) if isinstance(n,ast.For)
            and isinstance(n.target,ast.Tuple)
            and [getattr(e,'id','') for e in n.target.elts]==['name','target','hold'])
        code=compile(ast.Module(body=[loop],type_ignores=[]),'<production launchers>','exec')
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp)
            def run(argv,name): sp.run(list(map(str,argv)),check=True,capture_output=True,timeout=3)
            exec(code,dict(out=r,hook=r/'sweep.py',args=SimpleNamespace(release=False),run=run))
            owner=r/'runtime/boot-deadline/b-try.sh'; owner.parent.mkdir(parents=True)
            owner.write_text('#!'+sys.executable+'\nimport json,os\nprint(json.dumps({k:os.environ[k] for k in ("A9_BTRY_ONB","A9_BTRY_HOLD","A9_FINGERPRINT_ONB")}))\n')
            owner.chmod(0o700)
            env=dict(os.environ,A9_BTRY_HOLD='1800',A9_FINGERPRINT_ONB=str(r/'outer.py'))
            p=sp.run(['bash',r/'run-fingerprint.sh'],env=env,capture_output=True,text=True,timeout=3)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(json.loads(p.stdout),dict(A9_BTRY_ONB=str(r/'runtime/fingerprint/with-display.py'),
                A9_BTRY_HOLD='1800',A9_FINGERPRINT_ONB=str(r/'outer.py')))
            # Execute the production explicit-copy loop: new files cannot be
            # picked up by the preceding refresh of an older runtime tree.
            copy_loop=next(n for n in ast.walk(tree) if isinstance(n,ast.For)
                and isinstance(n.target,ast.Name) and n.target.id=='name'
                and isinstance(n.iter,ast.Tuple)
                and any(isinstance(e,ast.Constant) and e.value=='fingerprint/with-display.py' for e in n.iter.elts))
            for e in copy_loop.iter.elts: (r/'runtime'/Path(e.value).parent).mkdir(parents=True,exist_ok=True)
            build_main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
            chmod=build_main.body[build_main.body.index(copy_loop)+1]
            exec(compile(ast.Module(body=[copy_loop,chmod],type_ignores=[]),'<runtime copy>','exec'),
                 dict(MAIN=MAIN,runtime=r/'runtime',shutil=shutil))
            self.assertEqual((r/'runtime/fingerprint/with-display.py').read_bytes(),(MAIN/'fingerprint/with-display.py').read_bytes())
            self.assertTrue(os.access(r/'runtime/fingerprint/with-display.py',os.X_OK))


if __name__ == '__main__':
    unittest.main(verbosity=2)
