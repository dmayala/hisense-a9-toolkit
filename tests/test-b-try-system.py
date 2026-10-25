#!/usr/bin/env python3
"""SYSTEM delivery: real checks, launcher and owner, entirely fake transports."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess as sp
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
base=load('system_lifecycle_fixture',ROOT/'tests/test-b-try-format.py')
builder=load('system_delivery_builder',ROOT/'scripts/mainline/build-integrated.py')

class Tests(unittest.TestCase):
    def phone(self,case):return base.FormatPath().run_phone(system_case=case)

    def test_1_fresh_and_ram_data_write_only_b_and_verify_before_format(self):
        for case in ('pass','ram'):
            with self.subTest(case=case):
                p,state,calls=self.phone(case)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertEqual(state['written'],['system_b','vendor_b','boot_b'])
                self.assertTrue(state['system_verified'])
                self.assertEqual((state['slot'],state['mode']),('a','device'))
                self.assertIn('RESULT: COMPATIBLE',state['system_check'])
                commands=[c[1:] for c in calls]
                flash=next(i for i,c in enumerate(commands) if c[:3]==['fastboot','flash','system_b'])
                readback=next(i for i,c in enumerate(commands) if c[:2]==['adb','shell'] and 'head -c ' in c[-1] and 'mapper/system_b' in c[-1])
                vendor=next(i for i,c in enumerate(commands) if c[:2]==['adb','shell'] and c[-1].startswith('blockdev --setrw'))
                self.assertLess(flash,readback);self.assertLess(readback,vendor)
                self.assertFalse(any(c[0]=='fastboot' and c[1:2]==['flash'] and c[2].endswith('_a') for c in commands))

    def test_2_hash_key_space_and_initial_slot_refuse_before_mutation(self):
        for case in ('sha-mismatch','key-mismatch','super-full','initial-b'):
            with self.subTest(case=case):
                p,state,calls=self.phone(case)
                self.assertEqual(p.returncode,2,p.stdout+p.stderr)
                self.assertIn('detail=system_check_failed',p.stdout)
                self.assertFalse(state.get('written'))
                self.assertFalse(state.get('formatted'))
                self.assertFalse(any(c[1]=='adb' and c[2:3]==['reboot'] or c[1]=='fastboot' and c[2:3] in (['flash'],['set_active'],['reboot']) for c in calls))

    def test_3_flash_slot_and_readback_failure_return_a_without_booting_b(self):
        for case,detail in (('flash-failed','system_flash_failed'),('fastbootd-slot','system_fastbootd_slot'),
                            ('readback-failed','system_readback_failed'),('mismatch','system_mismatch')):
            with self.subTest(case=case):
                p,state,calls=self.phone(case)
                self.assertEqual(p.returncode,2,p.stdout+p.stderr)
                self.assertIn('detail='+detail,p.stdout)
                self.assertEqual((state['slot'],state['mode']),('a','device'))
                self.assertFalse(state.get('flashed'))
                self.assertFalse(state.get('formatted'))
                self.assertFalse(state.get('hook'))
                self.assertTrue(set(state.get('written',[])) <= {'system_b'})

    def test_4_production_launchers_select_only_the_built_system(self):
        tree=ast.parse((ROOT/'scripts/mainline/reboot/build14.py').read_text())
        loop=next(n for n in ast.walk(tree) if isinstance(n,ast.For) and isinstance(n.target,ast.Tuple)
                  and [getattr(e,'id','') for e in n.target.elts]==['name','target','hold'])
        code=compile(ast.Module(body=[loop],type_ignores=[]),'<production launchers>','exec')
        with tempfile.TemporaryDirectory() as tmp:
            for included in (False,True):
                out=Path(tmp)/str(included);out.mkdir()
                if included:(out/'system.img').write_bytes(b'synthetic system')
                def run(command,name):sp.run(list(map(str,command)),check=True,capture_output=True,timeout=3)
                exec(code,dict(out=out,hook=out/'onb.py',args=SimpleNamespace(release=False),run=run))
                owner=out/'runtime/boot-deadline/b-try.sh';owner.parent.mkdir(parents=True)
                owner.write_text('#!/bin/sh\nprintf "%s\\n" "${A9_BTRY_SYSTEM:-none}"\n');owner.chmod(0o700)
                for name in ('run.sh','run-fingerprint.sh'):
                    p=sp.run(['bash',out/name],env=dict(os.environ,A9_BTRY_SYSTEM='/stale/other-system.img'),capture_output=True,text=True,timeout=3)
                    self.assertEqual(p.returncode,0,p.stderr)
                    self.assertEqual(p.stdout.strip(),str(out/'system.img') if included else 'none')

        # The source-tree sweep entry point also consumes packaged SYSTEM,
        # clears stale inherited images, and refuses malformed manifests.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); launcher=root/'acceptance/run.sh'
            launcher.parent.mkdir(); launcher.write_bytes((ROOT/'scripts/mainline/acceptance/run.sh').read_bytes())
            owner=root/'boot-deadline/b-try.sh'; owner.parent.mkdir()
            owner.write_text('#!/bin/sh\nprintf "%s\\n" "${A9_BTRY_SYSTEM:-none}"\n');owner.chmod(0o700)
            image=root/'image';image.mkdir();manifest=image/'integrated-build.json'
            for contents,expected in ((None,'none'), ({'sha256':{}},'none'),
                                      ({'sha256':{'system.img':'fixture'}},str(image/'system.img')),
                                      ('invalid',None), ('directory',None)):
                if contents is None:
                    if manifest.exists():manifest.unlink()
                elif contents == 'directory':
                    manifest.unlink();manifest.mkdir()
                else:manifest.write_text(json.dumps(contents) if isinstance(contents,dict) else contents)
                p=sp.run(['bash',launcher,image],env=dict(os.environ,A9_BTRY_SYSTEM='/stale/other-system.img'),capture_output=True,text=True,timeout=3)
                if expected is None:
                    self.assertEqual(p.returncode,2,p.stderr);self.assertEqual(p.stdout,'')
                else:
                    self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout.strip(),expected)

    def test_5_environment_alias_and_frozen_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.dict(os.environ,A9_INTEG_SYSTEM=str(root/'not-a-gsi')):
                self.assertEqual(builder.integ_system_default(),root/'not-a-gsi')
                for path in ('build-integrated.py','reboot/build14.py'):
                    p=sp.run(['python3','-B',ROOT/'scripts/mainline'/path,'--out',root/'not-created'],capture_output=True,text=True,timeout=5)
                    self.assertEqual(p.returncode,2,p.stderr)
                    self.assertIn('A9_INTEG_SYSTEM must name an input GSI',p.stderr)
                    self.assertFalse((root/'not-created').exists())
            pins=builder.bundle_system_delivery(root/'runtime')
            for name,digest in pins.items():
                target=root/'runtime/system-delivery'/Path(name).name
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),digest)
                self.assertEqual(target.read_bytes(),(ROOT/name).read_bytes())
            # Execute the current builder's manifest/sidecar statements, so
            # an optional SYSTEM cannot be produced but omitted from shipping.
            tree=ast.parse((ROOT/'scripts/mainline/reboot/build14.py').read_text())
            body=next(n.body for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
            hashes=next(n for n in body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='hashes' for t in n.targets))
            sums=next(n for n in body if isinstance(n,ast.Expr) and 'SHA256SUMS' in ast.unparse(n))
            sidecars=next(n for n in body if isinstance(n,ast.For) and ast.unparse(n.iter)=="('vendor.img', 'system.img')")
            for name in ('boot.img','vendor.img','system.img'):(root/name).write_bytes(name.encode())
            ctx=dict(out=root,artifacts=['boot.img'],sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest())
            exec(compile(ast.Module(body=[hashes,sums,sidecars],type_ignores=[]),'<production image hashes>','exec'),ctx)
            self.assertEqual(set(ctx['hashes']),{'boot.img','vendor.img','system.img'})
            expected=hashlib.sha256(b'system.img').hexdigest()+'  system.img\n'
            self.assertEqual((root/'system.img.sha256').read_text(),expected)
            self.assertIn(expected,(root/'SHA256SUMS').read_text())

if __name__=='__main__':unittest.main(verbosity=2)
