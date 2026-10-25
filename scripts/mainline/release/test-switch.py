#!/usr/bin/env python3
"""Focused current integ switch/launcher smoke; all executed transports are inert."""
import ast
import os
from pathlib import Path
import subprocess as sp
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MAIN=Path(__file__).resolve().parents[1]

class Tests(unittest.TestCase):
    def test_both_builders_refuse_incomplete_release_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'must-not-exist'
            for source in (MAIN/'build-integrated.py',MAIN/'reboot/build14.py'):
                with patch.dict(os.environ,A9_RELEASE='1'):
                    p=sp.run(['python3','-I',source,'--out',out],capture_output=True,text=True,timeout=5)
                self.assertEqual(p.returncode,2)
                self.assertIn('requires --release-system',p.stderr)
                self.assertFalse(out.exists())

    def test_production_dev_launchers_run_inert_owner_release_launchers_refuse(self):
        tree=ast.parse((MAIN/'reboot/build14.py').read_text())
        loop=next(n for n in ast.walk(tree) if isinstance(n,ast.For)
            and isinstance(n.target,ast.Tuple)
            and [getattr(e,'id','') for e in n.target.elts]==['name','target','hold'])
        code=compile(ast.Module(body=[loop],type_ignores=[]),'<production launchers>','exec')
        with tempfile.TemporaryDirectory() as tmp:
            for release in (False,True):
                out=Path(tmp)/str(int(release));out.mkdir()
                def run(argv,name):
                    sp.run(list(map(str,argv)),check=True,capture_output=True,timeout=3)
                exec(code,dict(out=out,hook=out/'inert-hook.py',args=SimpleNamespace(release=release),run=run))
                owner=out/'runtime/boot-deadline/b-try.sh';owner.parent.mkdir(parents=True)
                owner.write_text('#!/bin/sh\nprintf "INERT_DEVELOPMENT_OWNER\\n"\n');owner.chmod(0o700)
                for name in ('run.sh','run-fingerprint.sh'):
                    p=sp.run(['bash',out/name],capture_output=True,text=True,timeout=3)
                    self.assertEqual(p.returncode,2 if release else 0)
                    if release:
                        self.assertEqual(p.stdout,'');self.assertIn('root sweep is unavailable',p.stderr)
                    else:self.assertEqual(p.stdout,'INERT_DEVELOPMENT_OWNER\n')

if __name__=='__main__':unittest.main(verbosity=2)
