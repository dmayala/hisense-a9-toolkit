"""Captured silent stock failure and remote quoting, entirely offline."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/eink-probe/sessions'))
import ghost_stock as S
import ghost_stock_runner as H
import ghost_recovery as R
import test_ghost_stock_runner as fixtures

FIX=Path(__file__).with_name('fixtures')/'ghost-stock-refusal'
OLD=Path('/home/user/A9-wt/eink-ghost-run4/scripts/eink-probe/sessions/ghost_stock_runner.py')


class Refusal(unittest.TestCase):
    def test_actual_old_silent_rc3_and_new_stop_summary_with_cleanup_intact(self):
        spec=importlib.util.spec_from_file_location('old_stock_supervisor',OLD)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        for module in (old,H):
            with self.subTest(old=module is old),tempfile.TemporaryDirectory() as d:
                t=fixtures.Supervisor().fixture(d)
                worker=Path(d)/'session.py'
                worker.write_text('import sys\nprint('+repr((FIX/'stock-stop.txt').read_text())+')\nsys.exit(3)\n')
                stdout,stderr=io.StringIO(),io.StringIO()
                with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                    self.assertEqual(module.run_stock(t,['fake-unused-transport'],Path(d)/'usb.lock',0,0),3)
                self.assertFalse((t.series/'stock/done').exists())
                self.assertFalse(t.marker.exists())
                if module is old:self.assertEqual(stdout.getvalue()+stderr.getvalue(),'')
                else:
                    self.assertIn('source did not resume the requested launcher/viewer activity',stderr.getvalue())
                    self.assertIn('capture child rc=3',stderr.getvalue())
                    self.assertIn('stock.log',stderr.getvalue())
                    self.assertIn('phase=capture',stdout.getvalue())

    def test_failed_silent_child_still_has_reason_and_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            t=fixtures.Supervisor().fixture(d);(Path(d)/'session.py').write_text('raise SystemExit(7)\n')
            stderr=io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(H.run_stock(t,['fake-unused-transport'],Path(d)/'usb.lock',0,0),3)
            self.assertIn('capture child rc=7',stderr.getvalue())
            self.assertIn('no STOP reason retained',stderr.getvalue())

    def test_timeout_diagnostic_never_prints_argv_or_device_identifier(self):
        secret='private-device-fixture'
        reason=H.public_reason(subprocess.TimeoutExpired(['forbidden-client','-s',secret],30))
        self.assertIn('timed out',reason);self.assertNotIn(secret,reason)
        with patch.dict(os.environ,A9_SERIAL=secret):
            self.assertNotIn(secret,H.public_reason('failure '+secret))

    def test_recovery_timeout_reason_has_no_command_vector(self):
        with tempfile.TemporaryDirectory() as d:
            recovery=R.HomeRecovery(Path(d)/'pending.json',['fake-transport','-s','private-fixture'])
            with patch.object(R.subprocess,'run',side_effect=subprocess.TimeoutExpired(recovery.adb,30)):
                with self.assertRaisesRegex(RuntimeError,'timed out after 30 seconds') as result:
                    recovery.shell('fixture')
            self.assertNotIn('private-fixture',str(result.exception))

    def test_original_clear_redirection_is_outside_su_and_new_command_is_inside(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);target=root/'force_clear';bin=root/'bin';bin.mkdir()
            su=bin/'su';su.write_text('#!/bin/sh\nexit 17\n');su.chmod(0o700)
            old=' '.join(['su','-c','echo 1 > '+str(target)])
            env=dict(os.environ,PATH=str(bin)+':'+os.environ['PATH'])
            result=subprocess.run(['sh','-c',old],capture_output=True,env=env)
            self.assertEqual(result.returncode,17);self.assertTrue(target.exists())
            target.unlink()
            session=S.Stock.__new__(S.Stock);session.a=SimpleNamespace(fc_path=str(target))
            step=session.clear_step('clear_source')
            result=subprocess.run(['sh','-c',' '.join(step['adb'][1:])],capture_output=True,env=env)
            self.assertEqual(result.returncode,17);self.assertFalse(target.exists())
            # The exact remote parse gives su one complete root command.
            self.assertEqual(shlex.split(step['adb'][1]),['su','-c','echo 1 > '+str(target)])

    def phase(self, root, actions, steps, process_rc=0):
        session=S.Stock.__new__(S.Stock)
        session.a=SimpleNamespace(probe='fake-probe',adb='fake-transport',preroll_ms=0,url='unused',calib='unused',run_timeout_s=1)
        session.out=str(root);session.check_stop=lambda:None
        path=root/'runs/00-dark-A/probe';path.mkdir(parents=True,exist_ok=True)
        (path/'session.json').write_text(json.dumps({'actions':actions}))
        with patch.object(S.subprocess,'run',return_value=SimpleNamespace(returncode=process_rc)):
            return session.phase('00-dark-A',steps)

    def test_captured_action_failures_are_refused_despite_successful_probe_process(self):
        for record in json.loads((FIX/'actions.json').read_text()):
            with self.subTest(source=record['source']),tempfile.TemporaryDirectory() as d:
                actions=record['actions'];steps=[{'name':a['name'],'adb':['shell','fixture']} for a in actions]
                failed=next(a for a in actions if a['rc'])
                with self.assertRaisesRegex(S.Stop,failed['name']+' failed rc=1'):
                    self.phase(Path(d),actions,steps)

    def test_successful_actions_allow_camera_warning_but_missing_or_wrong_receipts_refuse(self):
        step={'name':'source_dark','adb':['shell','fixture']}
        for actions,valid in [([{'name':'source_dark','rc':0}],True),([],False),
                ([{'name':'other','rc':0}],False),([{'name':'source_dark','rc':True}],False)]:
            with self.subTest(actions=actions),tempfile.TemporaryDirectory() as d:
                if valid:
                    self.assertEqual(self.phase(Path(d),actions,[step]),0)
                    self.assertEqual(self.phase(Path(d),actions,[step],process_rc=4),4)
                else:
                    with self.assertRaisesRegex(S.Stop,'results missing or invalid'):self.phase(Path(d),actions,[step])


if __name__=='__main__':unittest.main()
