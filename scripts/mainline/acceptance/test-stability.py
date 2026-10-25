#!/usr/bin/env python3
"""Five focused stability cases, including the actual Android trace parser."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess as sp
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
s = importlib.util.spec_from_file_location('stability_fake', HERE/'selftest.py')
fake = importlib.util.module_from_spec(s); s.loader.exec_module(fake)
m = fake.sweep
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


class Stability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = patch.dict(os.environ, {'A9_BTRY_LOGS':self.tmp.name}); p.start(); self.addCleanup(p.stop)
        self.output = io.StringIO(); p = contextlib.redirect_stdout(self.output)
        p.__enter__(); self.addCleanup(p.__exit__, None, None, None)

    def test_events_keep_names_without_ids_and_do_not_repeat_snapshots(self):
        sweep = fake.Fake()
        lines = ['123.4 567 890 I am_anr : [0,111,com.android.phone,999,PRIVATE_TOKEN]',
                 '124.4 567 890 I am_crash : [0,222,com.example.app,999,PRIVATE_TOKEN]']
        sweep.record_crash_events(lines); sweep.record_crash_events(lines)
        sweep.record_crash_events([lines[0].replace('123.4','125.4')])
        text = (self.root/'stability-events.jsonl').read_text()
        rows = [json.loads(l) for l in text.splitlines()]
        self.assertEqual(len(rows),3)
        self.assertEqual({r['process'] for r in rows}, {'com.android.phone','com.example.app'})
        self.assertNotIn('PRIVATE_TOKEN',text); self.assertNotIn('111',text); self.assertNotIn('123.4',text)
        self.assertEqual((self.root/'stability-events.jsonl').stat().st_mode & 0o777,0o600)

    def test_activity_and_traces_keep_only_names_and_numeric_categories(self):
        sweep = fake.Fake()
        with patch.object(sweep,'shell',return_value=(0,'ANR in com.android.phone\nProcess: com.android.phone\nPID: 12345\nPRIVATE_TOKEN\ngetRadioProxy\nfailed to complete startup\n')), \
             patch.object(sweep,'native',return_value=(1,'trace_process com.android.phone 1 1\ntrace_files 65\ntrace_errors 1\ntrace_truncated 0\n')):
            sweep.collect_anr_evidence(force=True)
        text = (self.root/'stability-evidence.json').read_text(); data = json.loads(text)
        self.assertNotIn('PRIVATE_TOKEN',text); self.assertNotIn('12345',text)
        self.assertEqual(data['last']['trace_rc'],1)
        self.assertEqual(data['processes']['com.android.phone']['radio_wait'],1)
        self.assertEqual(data['processes']['com.android.phone']['startup_timeout'],1)

    def test_android_bounded_trace_reader_skips_symlinks_and_command_arguments(self):
        traces = self.root/'anr'; traces.mkdir()
        (traces/'trace').write_text('Cmd line: com.android.phone PRIVATE_TOKEN\ngetRadioProxy\n'+'x'*70000+'\nPRIVATE_TOKEN\n')
        (self.root/'other').write_text('Cmd line: com.private.other\n')
        (traces/'link').symlink_to(self.root/'other')
        # Android head/awk are the cached toybox applets; no device tools.
        bindir = self.root/'bin'; bindir.mkdir()
        for name in ('head','awk'):
            tool = bindir/name
            command = str(ANDROID/'system/bin/awk') if name == 'awk' else str(ANDROID/'system/bin/toybox')+' head'
            tool.write_text('#!/bin/sh\nexec '+QEMU+' '+str(ANDROID/'linker64')+' '+command+' "$@"\n')
            tool.chmod(0o700)
        env = dict(os.environ,PATH=str(bindir)+':'+os.environ['PATH'],LD_LIBRARY_PATH=str(ANDROID/'lib64'))
        r = sp.run([QEMU,ANDROID/'linker64',ANDROID/'system/bin/sh'],
                   input=m.ANR_TRACES.replace('/data/anr',str(traces)),text=True,capture_output=True,env=env,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('trace_process com.android.phone 1 0',r.stdout)
        self.assertIn('trace_files 1',r.stdout)
        self.assertNotIn('PRIVATE_TOKEN',r.stdout); self.assertNotIn('com.private.other',r.stdout)

    def test_retained_count_does_not_hide_new_anrs(self):
        sweep = fake.Fake()
        def snapshot(n): return dict(snapshot_ok=1,anrs=65,crash_events=65,native_rc=0,events_rc=0,
                                     anrs_signature=n,crash_signature=n,tombstones_signature=0)
        with patch.object(sweep,'snapshot',side_effect=[snapshot(1),snapshot(2),snapshot(3)]):
            for _ in range(3): sweep.monitor(force=True)
        self.assertEqual(sweep.signature_changes['anrs'],2)
        self.assertEqual(sweep.signature_changes['crash_events'],2)
        self.assertGreater(sweep.crashes,0)

    def test_lost_observation_reports_source_and_never_passes_as_empty(self):
        sweep = fake.Fake()
        with patch.object(sweep,'snapshot',return_value=dict(snapshot_ok=0,native_rc=1,events_rc=0)):
            sweep.monitor(force=True)
        self.assertEqual(sweep.lost,1); self.assertEqual(sweep.losses['native'],1)
        self.assertEqual(sweep.losses['events'],0)
        self.assertIn('STABILITY_LOSS',self.output.getvalue())
        original = sweep.shell
        def partial(source, data=None, timeout=20):
            if source == m.EVENTS:
                return 1, 'event 123.4 567 890 I am_anr : [0,111,com.android.phone,999,PRIVATE_TOKEN]\ncrash_events 1\n'
            return original(source,data,timeout)
        with patch.object(sweep,'shell',side_effect=partial): observation = sweep.snapshot()
        self.assertEqual(observation['snapshot_ok'],0)
        text = (self.root/'stability-events.jsonl').read_text()
        self.assertIn('com.android.phone',text); self.assertNotIn('PRIVATE_TOKEN',text)


if __name__ == '__main__': unittest.main(verbosity=2)
