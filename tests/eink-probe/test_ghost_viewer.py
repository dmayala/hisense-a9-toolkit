"""Viewer admission, private-file rollback and identifier-safe replies; offline."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/eink-probe/sessions'))
import ghost_matched as M
import ghost_recovery as R
import ghost_stock_runner as H
import ghost_viewer as V
import matched_source as F
from probe_output import public_output
import test_ghost_matched_core as C
from test_ghost_stock_matched import FakeStock, FakeMatched, args
from test_ghost_mainline_matched import FakeGhost

VIEWER = ['am', 'start', '-W', '-n', V.COMPONENT, '-a', 'android.intent.action.VIEW', '-t', 'image/png', '-d', '{uri}']
SECRET = 'private-unit-device'


class Transport(C.Transport):
    def __init__(self, out):
        super().__init__(out, home=C.OLD_HOME)
        self.foreground = V.COMPONENT
        self.current = 'dark'
        self.launch_rc = 0
        self.launch_error = False
        self.fail_cache = False
        self.fail_cache_remove = False

    def __call__(self, argv, **kw):
        a = list(argv)[1:]
        if a == ['exec-out', 'screencap', '-p']:
            self.calls.append(a)
            return subprocess.CompletedProcess(argv, 0, F.source_png(self.current), b'')
        if a[:1] == ['shell']:
            command = a[1]
            words = shlex.split(command)
            if words[:3] == ['cmd', 'package', 'resolve-activity'] and '-n' in words:
                self.calls.append(a)
                return subprocess.CompletedProcess(argv, 0, V.COMPONENT, '')
            if command.startswith('if [ "$(id -u)" = 0 ]; then sh -c '):
                self.calls.append(a)
                script = words[words.index('-c')+1].rstrip(';')
                private = next((p for p in V.paths(json.loads((self.out/'matched-source.json').read_text())['token'])
                                if p.startswith(V.CACHE) and p in script), None)
                if private is None:
                    raise AssertionError('root command has no exact owned cache path')
                if script.startswith('set -eu;'):
                    marker = self.out/'pending.json'
                    if marker.exists():
                        journal = json.loads(marker.read_text())
                        assert private in journal['pending_paths'], 'cache write before durable journal'
                    else:
                        assert private in json.loads((self.out/'matched-source.json').read_text())['owned_device_paths']
                    assert private not in self.files
                    sid = 'dark' if private.endswith('-dark.png') else 'white'
                    source = next(p for p in self.files if p.startswith('/data/local/tmp/ghost-matched-') and p.endswith('-'+sid+'.png'))
                    self.files[private] = self.files[source]
                    rc = 7 if self.fail_cache else 0
                    stdout = F.source_metadata(sid)['sha256'] + '  ' + private
                elif script.startswith('rm -f -- '):
                    if not self.fail_cache_remove:
                        self.files.pop(private, None)
                    rc, stdout = 0, ''
                elif script.startswith('if [ -e '):
                    rc = 0
                    stdout = ('PRESENT' if private in self.files else 'REMOVED') if 'echo REMOVED' in script else ('EXISTS' if private in self.files else 'ABSENT')
                else:
                    raise AssertionError('unknown root operation')
                return subprocess.CompletedProcess(argv, rc, stdout, '')
            if words[:2] == ['am', 'start']:
                self.calls.append(a)
                assert words[words.index('-n')+1] == V.COMPONENT
                assert 'SingleItemOnly' not in words and '-S' not in words
                self.current = 'dark' if '-dark.png' in command else 'white'
                stdout = ('Error type 3\nError: Activity class does not exist' if self.launch_error else 'Status: ok\nActivity: '+V.COMPONENT)
                return subprocess.CompletedProcess(argv, self.launch_rc, (stdout+'\n'+SECRET+' 192.0.2.3').encode(), (SECRET+' aa:bb:cc:dd:ee:ff').encode())
        return super().__call__(argv, **kw)


class Viewer(unittest.TestCase):
    def make(self, directory):
        a = C.arguments(directory)
        a.source_viewer = VIEWER
        run = M.MatchedRun(a, ['fake-matched-transport'], directory, lambda _: None, stock=True)
        return run, Transport(directory)

    def test_probe_both_sources_retains_sanitized_replies_and_private_pixels(self):
        with tempfile.TemporaryDirectory() as d:
            run, phone = self.make(d)
            with mock.patch.object(M.subprocess, 'run', side_effect=phone), mock.patch.object(M.time, 'sleep'), mock.patch.dict(os.environ, A9_SERIAL=SECRET):
                run.start()
                self.assertEqual(len(run.owned), 4)
                self.assertTrue(all(p.startswith(V.CACHE) for p in run.paths.values()))
                run.probe_viewer()
                for sid in ('dark', 'white'):
                    record = json.loads((Path(d)/'runs'/('probe-viewer-'+sid)/'probe/launch.json').read_text())
                    self.assertTrue(record['verified'])
                    self.assertTrue(record['source']['verified'])
                    self.assertIn('Status: ok', record['stdout'])
                    self.assertIn('[redacted]', record['stderr'])
                    text = json.dumps(record)
                    for secret in (SECRET, '192.0.2.3', 'aa:bb:cc:dd:ee:ff'):
                        self.assertNotIn(secret, text)
                self.assertEqual(run.close(), [])
            self.assertEqual(phone.home, C.OLD_HOME)
            self.assertEqual(phone.files, {'/data/local/tmp/keep-unrelated.png': b'keep'})
            self.assertFalse((Path(d)/'pending.json').exists())

    def test_rc1_and_rc0_error_and_wrong_foreground_refuse_with_evidence_and_restore(self):
        for mode in ('rc1', 'error', 'foreground'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                run, phone = self.make(d)
                phone.launch_rc = int(mode == 'rc1')
                phone.launch_error = mode == 'error'
                if mode == 'foreground':
                    phone.foreground = C.NEW_HOME
                with mock.patch.object(M.subprocess, 'run', side_effect=phone), mock.patch.object(M.time, 'sleep'), mock.patch.dict(os.environ, A9_SERIAL=SECRET):
                    run.start()
                    with self.assertRaisesRegex(RuntimeError, 'viewer probe failed'):
                        run.probe_viewer()
                    record = json.loads((Path(d)/'runs/probe-viewer-dark/probe/launch.json').read_text())
                    self.assertFalse(record['verified'])
                    self.assertIn('error', record)
                    self.assertEqual(run.close(), [])
                self.assertEqual(phone.home, C.OLD_HOME)

    def test_partial_cache_copy_recovers_and_failed_removal_keeps_marker(self):
        for removal_fails in (False, True):
            with self.subTest(removal_fails=removal_fails), tempfile.TemporaryDirectory() as d:
                run, phone = self.make(d)
                phone.fail_cache = True
                phone.fail_cache_remove = removal_fails
                with mock.patch.object(M.subprocess, 'run', side_effect=phone):
                    with self.assertRaisesRegex(RuntimeError, 'transport command failed'):
                        run.start()
                    failures = run.close()
                self.assertEqual(bool(failures), removal_fails)
                self.assertEqual((Path(d)/'pending.json').exists(), removal_fails)
                self.assertEqual(phone.home, C.OLD_HOME)
                if removal_fails:
                    phone.fail_cache_remove = False
                    with mock.patch.object(M.subprocess, 'run', side_effect=phone), R.HomeRecovery(Path(d)/'pending.json', ['fake-matched-transport'], lambda _:None) as restart:
                        self.assertEqual(restart.recover_pending(), [])
                    self.assertFalse((Path(d)/'pending.json').exists())

    def test_private_journal_never_accepts_other_app_or_token_or_directory(self):
        with tempfile.TemporaryDirectory() as d:
            recovery = R.HomeRecovery(Path(d)/'pending.json', ['unused'])
            recovery.begin(C.OLD_HOME, C.NEW_HOME, 'a'*32)
            try:
                for p in (V.CACHE, V.CACHE+'/ghost-matched-'+('b'*32)+'-dark.png', '/data/user/0/other/cache/a.png'):
                    with self.assertRaisesRegex(RuntimeError, 'unowned'):
                        recovery.remember(p)
            finally:
                recovery.release()

    def test_probe_failure_precedes_all_stock_and_mainline_baselines_and_reps(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(M, 'MatchedRun', FakeMatched):
                stock = FakeStock(args(d))
            for session in (stock, FakeGhost(d)):
                session.matched.probe_viewer = mock.Mock(side_effect=RuntimeError('viewer unavailable'))
                try:
                    with self.assertRaisesRegex(RuntimeError, 'viewer unavailable'):
                        (session.run if isinstance(session, FakeStock) else session.run_all)()
                    self.assertEqual(session.phases, [])
                    self.assertEqual(session.reps, [])
                finally:
                    if isinstance(session, FakeStock): session.logf.close()

    def test_standalone_probe_uses_full_viewer_then_recovers_without_reps_or_clear(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(C, 'OLD_HOME', 'bitpit.launcher/.Home'):
            root = Path(d)
            phone = Transport(d)
            t = SimpleNamespace(marker=root/'pending.json', profile=dict(viewer_argv=VIEWER,
                launcher_component=C.NEW_HOME,source_roi='32,100,792,1560',wallpaper_roi='32,100,792,1560',source_wait_ms=4000))
            with mock.patch.object(M.subprocess, 'run', side_effect=phone), mock.patch.object(M.time, 'sleep'), mock.patch.dict(os.environ, A9_SERIAL=SECRET):
                self.assertEqual(H.home_probe(t, ['fake-matched-transport'], root), 0)
            record = json.loads((root/'probe.json').read_text())
            self.assertTrue(record['viewer_verified'] and record['home_restored'])
            self.assertEqual(record['reps'], 0)
            self.assertFalse(record['opening_consumed'])
            self.assertFalse(any('force_clear' in str(call) for call in phone.calls))

    def test_identifier_redaction_handles_transport_only_serial_and_timeouts(self):
        self.assertEqual(public_output(SECRET.encode(), ['fake', '-s', SECRET]), '[redacted]')
        with tempfile.TemporaryDirectory() as d:
            run, _ = self.make(d)
            run.paths = {s:'/data/local/tmp/ghost-'+s+'.png' for s in ('dark','white')}
            with mock.patch.object(M.subprocess,'run',side_effect=subprocess.TimeoutExpired(['fake','-s',SECRET],60,output=SECRET.encode(),stderr=SECRET.encode())), mock.patch.dict(os.environ,A9_SERIAL=SECRET):
                with self.assertRaisesRegex(RuntimeError,'timed out') as error:
                    run.probe_viewer()
            self.assertNotIn(SECRET,str(error.exception))
            self.assertNotIn(SECRET,(Path(d)/'runs/probe-viewer-dark/probe/launch.json').read_text())

    def test_mainline_private_cache_cleanup_does_not_set_home(self):
        with tempfile.TemporaryDirectory() as d:
            a = C.arguments(d); a.source_viewer = VIEWER
            run = M.MatchedRun(a, ['fake-matched-transport'], d, lambda _: None, stock=False)
            phone = Transport(d); phone.home = C.NEW_HOME
            with mock.patch.object(M.subprocess, 'run', side_effect=phone), mock.patch.object(M.time, 'sleep'):
                run.start(); run.probe_viewer()
                self.assertEqual(run.close(), [])
            self.assertEqual(phone.sets, [])
            self.assertEqual(phone.files, {'/data/local/tmp/keep-unrelated.png': b'keep'})

    def test_cache_stage_shell_preserves_argv_and_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); cache = root/'cache'; cache.mkdir()
            source = root/'source.png'; source.write_bytes(b'fixture')
            tools = root/'bin'; tools.mkdir()
            # Replay real noclobber/cat/chown/chmod/hash shell operations, with
            # only SELinux metadata calls replaced on the non-SELinux host.
            stat = tools/'stat'
            stat.write_text('#!/bin/sh\ncase "$2" in %C) echo u:object_r:app_data_file:s0:c1,c2;; *) exec '+shlex.quote(subprocess.check_output(['which','stat'],text=True).strip())+' "$@";; esac\n')
            stat.chmod(0o700)
            chcon = tools/'chcon'; chcon.write_text('#!/bin/sh\nexit 0\n'); chcon.chmod(0o700)
            identity = tools/'id'; identity.write_text('#!/bin/sh\necho "$FAKE_UID"\n'); identity.chmod(0o700)
            su = tools/'su'; su.write_text('#!/bin/sh\n[ "$#" = 2 ] && [ "$1" = -c ] || exit 91\nexec sh -c "$2"\n'); su.chmod(0o700)
            for uid in ('0', '2000'):
                target = cache/('new-'+uid+'.png'); source.write_bytes(b'fixture')
                env = dict(os.environ, PATH=str(tools)+':'+os.environ['PATH'], FAKE_UID=uid)
                with mock.patch.object(V, 'CACHE', str(cache)):
                    command = V.stage(str(source), str(target))
                result = subprocess.run(['sh','-c',command],env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(target.read_bytes(), b'fixture')
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                source.write_bytes(b'replacement')
                result = subprocess.run(['sh','-c',command],env=env,capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_bytes(), b'fixture')


if __name__ == '__main__': unittest.main()
