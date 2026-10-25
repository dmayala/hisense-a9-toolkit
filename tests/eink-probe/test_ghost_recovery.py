import json
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_ghost_matched_core as Core
from test_ghost_matched_core import Transport, M, OLD_HOME, NEW_HOME
from rehearse_recovery import run_crash_replay, fixture, OLD
import ghost_recovery as R
import ghost_stock_runner as H


class PersistentRecovery(unittest.TestCase):
    def test_unexpected_initial_home_fails_before_setting_or_staging(self):
        with tempfile.TemporaryDirectory() as d:
            a=Core.arguments(d);a.expected_stock_home_package='bitpit.launcher'
            transport=Transport(d)
            run=M.MatchedRun(a,['fake-matched-transport'],d,lambda _s:None,stock=True)
            with mock.patch.object(M.subprocess,'run',side_effect=transport):
                with self.assertRaisesRegex(RuntimeError,'not the expected package'):run.start()
                self.assertEqual(run.close(),[])
            self.assertEqual(transport.sets,[]);self.assertEqual(run.owned,[])
            self.assertFalse(run.journal.marker.exists())

    def start(self, directory):
        run, transport, logs = Core.Lifecycle().make(directory)
        patch = mock.patch.object(M.subprocess,'run',side_effect=transport)
        patch.start();self.addCleanup(patch.stop)
        run.start()
        return run,transport,logs

    def test_journal_and_receipt_contain_exact_paths_before_push(self):
        with tempfile.TemporaryDirectory() as d:
            run,transport,logs = Core.Lifecycle().make(d)
            def call(argv,**kw):
                if argv[1]=='push':
                    marker=json.loads(run.journal.marker.read_text())
                    receipt=json.loads(Path(d,'matched-source.json').read_text())
                    self.assertIn(argv[3],marker['pending_paths'])
                    self.assertIn(argv[3],receipt['owned_device_paths'])
                    self.assertEqual(marker['token'],receipt['token'])
                return transport(argv,**kw)
            with mock.patch.object(M.subprocess,'run',side_effect=call):
                run.start();self.assertEqual(run.close(),[])
            self.assertFalse(run.journal.marker.exists())

    def test_failed_setter_cannot_be_success_even_if_readback_matches(self):
        with tempfile.TemporaryDirectory() as d:
            run,transport,logs=self.start(d)
            original=transport.__call__
            def call(argv,**kw):
                p=original(argv,**kw)
                if len(argv)>2 and argv[1]=='shell' and shlex.split(argv[2])==['cmd','package','set-home-activity',OLD_HOME]:
                    p.returncode=7
                return p
            with mock.patch.object(M.subprocess,'run',side_effect=call):
                failures=run.close()
            self.assertTrue(failures);self.assertEqual(transport.home,OLD_HOME)
            self.assertFalse(json.loads(Path(d,'matched-source.json').read_text())['home_restored'])
            self.assertTrue(run.journal.marker.exists());self.assertIn('home restored: no',logs)
            with R.HomeRecovery(run.journal.marker,run.adb,logs.append) as recovery:
                self.assertEqual(recovery.recover_pending(),[])
            self.assertFalse(run.journal.marker.exists())

    def test_unreadable_home_keeps_marker_and_fails(self):
        with tempfile.TemporaryDirectory() as d:
            run,transport,logs=self.start(d)
            def call(argv,**kw):
                p=transport(argv,**kw)
                if argv[1:]==['shell',M.HOME_QUERY]:p.stdout=''
                return p
            with mock.patch.object(M.subprocess,'run',side_effect=call):
                self.assertTrue(run.close())
            self.assertTrue(run.journal.marker.exists());self.assertIn('home restored: no',logs)

    def test_pending_transport_outage_blocks_before_stock_constructor(self):
        with tempfile.TemporaryDirectory() as d:
            run,transport,logs=self.start(d)
            run.journal.release()  # model the kernel releasing a crashed owner's flock
            def lost(argv,**kw):
                transport.calls.append(argv[1:]);return subprocess.CompletedProcess(argv,7,'','lost')
            with mock.patch.object(M.subprocess,'run',side_effect=lost),mock.patch.object(M.Recovery,'print',create=True), \
                    mock.patch.object(__import__('ghost_stock'),'Stock',side_effect=AssertionError('must not start')):
                rc=__import__('ghost_stock').main(['--adb','fake-matched-transport','--out',str(Path(d,'fresh')),
                    '--calib','unused','--dry-run','--recovery-marker',str(run.journal.marker)])
            self.assertEqual(rc,3);self.assertTrue(run.journal.marker.exists())
            self.assertFalse(Path(d,'fresh').exists())
            self.assertEqual(run.close(),[])

    def test_wrong_slot_and_wrong_transport_never_mutate(self):
        with tempfile.TemporaryDirectory() as d:
            run,transport,logs=self.start(d);run.journal.release()
            before=len(transport.calls)
            with R.HomeRecovery(run.journal.marker,['different-transport'],logs.append) as r:
                with self.assertRaisesRegex(RuntimeError,'different transport'):r.recover_pending()
            self.assertEqual(len(transport.calls),before)
            def wrong(argv,**kw):
                p=transport(argv,**kw)
                if argv[1:]==['shell','getprop ro.boot.slot_suffix; getprop sys.boot_completed']:p.stdout='_b\n1'
                return p
            with mock.patch.object(M.subprocess,'run',side_effect=wrong),R.HomeRecovery(run.journal.marker,run.adb,logs.append) as r:
                self.assertTrue(r.recover_pending())
            self.assertEqual(transport.home,NEW_HOME);self.assertEqual(len(transport.calls),before+1)
            self.assertEqual(run.close(),[])

    def test_corrupt_paths_and_active_session_lock_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            run,transport,logs=self.start(d)
            other=R.HomeRecovery(run.journal.marker,run.adb,logs.append)
            with self.assertRaisesRegex(RuntimeError,'already active'):other.acquire()
            record=json.loads(run.journal.marker.read_text());record['pending_paths'].append('/data/local/tmp/unrelated.png')
            R.atomic_json(run.journal.marker,record)
            before=len(transport.calls)
            with self.assertRaisesRegex(RuntimeError,'invalid owned'):run.journal.recover_pending()
            self.assertEqual(len(transport.calls),before)
            record['pending_paths'].pop();R.atomic_json(run.journal.marker,record)
            self.assertEqual(run.close(),[])

    def test_actual_sigkill_and_next_entry_recover_before_any_other_action(self):
        with tempfile.TemporaryDirectory() as d:
            result=run_crash_replay(Path(d,'replay'))
            self.assertTrue(result['recovery_preceded_stock_constructor'])

    def test_supervisor_recovers_sigkilled_child_but_never_marks_done(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d,'supervised');adb=fixture(root)
            wt=root/'wt';(wt/'scripts/eink-probe/sessions').mkdir(parents=True)
            (wt/'scripts/a9-health.sh').write_text("echo '{\"slot\":\"_a\",\"boot_completed\":\"1\"}'\n")
            (wt/'scripts/eink-probe/sessions/ghost-stock.sh').write_text('exec '+shlex.join([sys.executable,'-B',str(root/'worker.py')])+' "$@"\n')
            class Tuple:
                marker=root/'pending.json';series=root/'series'
                d={'artifacts':{'calibration':{'path':'unused'}}}
                def args(self):return ['--matched-source','--ghost-reps','12','--recovery-marker',str(self.marker)]
                def completion(self,_out):raise AssertionError('crash cannot be complete')
            t=Tuple();t.wt=wt
            rc=H.run_stock(t,adb,root/'usb.lock',lock_s=1,health_s=1)
            self.assertEqual(rc,3);self.assertFalse((t.series/'stock/done').exists())
            self.assertFalse(t.marker.exists())
            self.assertEqual(json.loads((root/'state.json').read_text()),{'home':OLD,'files':{'/data/local/tmp/unrelated.png':'keep'}})


if __name__=='__main__':unittest.main()
