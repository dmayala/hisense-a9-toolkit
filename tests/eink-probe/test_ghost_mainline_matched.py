"""Mainline matched history rehearsal: fake phase recorder; no phone/camera."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/eink-probe/sessions'))
import ghost_ab as G
from test_ghost_stock_matched import FakeMatched


class FakeGhost(G.GhostSession):
    def __init__(self, out):
        self.a = argparse.Namespace(ghost_reps=6, ghost_arms='100', rep_est_s=45,
                                   settings_start='warm', clean_retries=2, post_s=3.5)
        self.out = out
        self.matched = FakeMatched(None, None, None, None, stock=True)
        self.source_refs, self.reps, self.phases, self.refs, self.arms = {}, [], [], [], []
        self.ref_home = None
        self.plain = mock.Mock()
        self.messages, self.clean_results = [], []

    def log(self, text):
        self.messages.append(text)

    def set_settle(self, arm):
        self.arms.append(arm)

    def time_left(self):
        return 1200

    def check_clean(self, tag):
        self.refs.append(self.ref_home)
        return {'clean': self.clean_results.pop(0) if self.clean_results else True, 'score': 0}

    def phase(self, rep, tag, steps):
        self.phases.append((tag, steps))
        (Path(self.out) / 'runs' / tag).mkdir(parents=True, exist_ok=True)
        clear = not tag.endswith('-B')
        return {'tag': tag, 'wf_end_low': 0, 'settle_ok': 0, 'reread_high': 0,
                'updates': [{'mode': '0x203' if clear else '0x206', 'clear': int(clear),
                             'bin': 9, 'rect': '0,0-1648,824', 'sent': 38 if clear else 8}]}


class MainlineMatched(unittest.TestCase):
    def test_pending_stock_journal_refuses_direct_mainline_before_session(self):
        with tempfile.TemporaryDirectory() as d:
            marker=Path(d,'pending.json');marker.write_text('{}')
            with mock.patch.object(G,'GhostSession',side_effect=AssertionError('no device action')):
                with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as exc:
                    G.main(['--dry-run','--adb','fake','--out',d,'--recovery-marker',str(marker)])
            self.assertEqual(exc.exception.code,2)

    def test_three_distinct_baselines_and_source_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            s = FakeGhost(d)
            s.run_all()
            self.assertEqual([p[0] for p in s.phases[:3]], ['00-launcher-A', '00-dark-A', '00-white-A'])
            self.assertEqual([r['source_id'] for r in s.reps], ['launcher','dark','white','white','dark','launcher'])
            self.assertTrue(all(r['source']['id'] == r['source_id'] for r in s.reps))
            self.assertEqual(set(s.arms), {100})
            self.assertEqual(len(s.refs), 6)
            for r, ref in zip(s.reps, s.refs):
                self.assertIn('00-'+r['source_id']+'-A', ref)
            for tag, steps in s.phases:
                if tag.endswith('-B'):
                    self.assertEqual(steps[0]['adb'], ['shell','am','start','-a','android.settings.SETTINGS'])
                elif tag.endswith('-C'):
                    self.assertEqual(len(steps), 1)
                    self.assertIn('force_clear', steps[0]['adb'][1])

    def test_retry_does_not_relaunch_source(self):
        with tempfile.TemporaryDirectory() as d:
            s = FakeGhost(d);s.clean_results = [False, True]
            s.run_all()
            retry = dict(s.phases)['01-A1']
            self.assertEqual(len(retry), 1)
            self.assertEqual(retry[0]['name'], 'clear_home')

    def test_source_validation_stops_before_settings(self):
        with tempfile.TemporaryDirectory() as d:
            s = FakeGhost(d)
            original = s.matched.capture_source
            def capture(tag, sid):
                if tag == '01-A':
                    raise RuntimeError('wrong source')
                return original(tag, sid)
            s.matched.capture_source = capture
            with self.assertRaisesRegex(RuntimeError, 'wrong source'):
                s.run_all()
            self.assertNotIn('01-B', [tag for tag, _ in s.phases])

    def test_cleanup_failures_reach_existing_teardown(self):
        with tempfile.TemporaryDirectory() as d:
            s = FakeGhost(d);s.matched.cleanup_failures = ['source cleanup failed']
            with mock.patch.object(G.R.RailSession, 'teardown', return_value=(['existing failure'], None)):
                failures, _ = s.teardown()
            self.assertTrue(s.matched.closed)
            self.assertEqual(failures, ['source cleanup failed','existing failure'])

    def test_plan_is_offline_and_requires_fixed_settle(self):
        argv = ['--plan','--matched-source','--ghost-reps','12','--launcher-component',
                'com.android.launcher3/.Launcher','--source-viewer',
                json.dumps(['am','start','-n','com.example.viewer/.View','-d','{uri}'])]
        with mock.patch.object(G, 'GhostSession', side_effect=AssertionError('no session in plan')):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                G.main(argv)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(G.main(argv + ['--ghost-arms','100']), 0)
            self.assertIn('launcher/dark/white', output.getvalue())


if __name__ == '__main__':
    unittest.main()
