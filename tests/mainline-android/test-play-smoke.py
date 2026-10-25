#!/usr/bin/env python3
"""Host-only Play/check-in regression tests; never execute a hook subprocess."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'play_smoke', REPO / 'scripts/mainline/play/onb.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class Clock:
    value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class PlaySmoke(unittest.TestCase):
    def test_screen_classes_and_error_precedence(self):
        activity = 'mResumedActivity: ActivityRecord{ com.android.vending/.MainActivity}'
        for text, expected in (
            ('Sign in', ('SIGN_IN', 'SIGN_IN_BUTTON')),
            ('Games', ('HOME', 'GAMES_TAB')),
            ('Apps', ('HOME', 'APPS_TAB')),
            ('Sign in: Server error', ('ERROR', 'SERVER_ERROR')),
            ('Check your connection', ('ERROR', 'CHECK_YOUR_CONNECTION')),
            ('Something went wrong', ('ERROR', 'SOMETHING_WENT_WRONG')),
            ('Try again', ('ERROR', 'TRY_AGAIN')),
            ('unrecognized content', ('UNKNOWN', 'NO_MATCH')),
        ):
            xml = ('<hierarchy><node package="com.android.vending" text="' +
                   text + '"/></hierarchy>')
            self.assertEqual(smoke.classify_play(xml, activity), expected)
        self.assertEqual(smoke.classify_play('', activity), ('UNKNOWN', 'NO_XML'))
        focus = 'mCurrentFocus=Window{ Application Error: com.android.bluetooth}'
        self.assertEqual(smoke.crash_dialog('', focus), 'BLUETOOTH_CRASH')
        self.assertIsNone(smoke.crash_dialog('', 'old Application Error: com.android.bluetooth'))
        hook, output = smoke.Hook(), io.StringIO()
        windows = iter([focus, 'mCurrentFocus=Window{ com.android.vending/.MainActivity}'])

        def shell(source, **kwargs):
            if source.startswith('dumpsys window'):
                return 0, next(windows)
            if source.startswith('uiautomator'):
                return 0, '<hierarchy><node package="com.android.vending" text="Sign in"/></hierarchy>'
            if source.startswith('dumpsys activity'):
                return 0, activity
            return 0, ''

        hook.shell = shell
        hook.prepare_eink = lambda: True
        hook.camera_frame = lambda: None
        hook.until = lambda seconds, predicate: predicate() or predicate()
        with patch.object(smoke.sp, 'run', side_effect=AssertionError('hook process forbidden')), \
             contextlib.redirect_stderr(output):
            self.assertTrue(hook.play())
        self.assertFalse(hook.results['NO_SYSTEM_CRASH_DIALOG'])
        self.assertFalse(hook.results['BLUETOOTH_NO_CRASH'])
        self.assertIn('WARN PLAY_SYSTEM_CRASH_DIALOG label=BLUETOOTH_CRASH', output.getvalue())
        self.assertIn('PLAY_SCREEN class=SIGN_IN label=SIGN_IN_BUTTON', output.getvalue())
        self.assertNotIn('<hierarchy', output.getvalue())

    def test_completion_predicate_rejects_scheduling_and_binding(self):
        program = smoke.CHECKIN.split("| awk '", 1)[1].rsplit("'", 1)[0]
        for record, expected in (
            ('CheckinRequestProcessor Checkin succeeded:', 'CHECKIN 1'),
            ('CheckinOperation Checkin Operation finished with result: SUCCESS', 'CHECKIN 1'),
            ('CheckinOperation Checkin Operation finished with result: RESCHEDULED', 'CHECKIN 0'),
            ('CheckinOperation Checkin Operation finished with result: FAILURE', 'CHECKIN 0'),
            ('CheckinOperation Checkin Operation finished with result: SUCCESSFUL_BINDING', 'CHECKIN 0'),
            ('Checkin scheduled with schedule delay: 7137124', 'CHECKIN 0'),
        ):
            # Only host awk runs; no logcat, shell hook or phone tool is invoked.
            result = subprocess.run(['awk', program], input=record, text=True,
                                    capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), expected)

    def test_force_once_after_validation_before_play(self):
        hook, events, requests = smoke.Hook(), [], []

        def shell(source, *args, **kwargs):
            if source == smoke.CHECKIN_REQUEST:
                requests.append(source)
                events.append('force')
                return 0, 'Broadcast completed: result=0'
            if source == smoke.CHECKIN:
                return 0, 'CHECKIN 1'
            if source == 'getprop vendor.a9ml.wifi.session.state':
                return 0, 'serving'
            return 0, ''

        def internet():
            events.append('validated')
            return True

        def play():
            events.append('play')
            return True

        hook.shell = shell
        hook.native = lambda *args, **kwargs: (0, 'READY')
        hook.credentials = lambda: ('', '')  # No secret or network fixture.
        hook.saved = lambda: set()
        hook.status = lambda: True
        hook.internet = internet
        hook.play = play
        hook.until = lambda seconds, predicate, **kwargs: predicate()
        hook.start_bluetooth = lambda: None
        with patch.object(smoke.sp, 'run', side_effect=AssertionError('hook process forbidden')), \
             contextlib.redirect_stderr(io.StringIO()):
            hook.execute()
            self.assertTrue(hook.checkin())
        self.assertEqual(events, ['validated', 'force', 'play'])
        self.assertEqual(requests, [smoke.CHECKIN_REQUEST])
        self.assertIn('-p com.google.android.gms', requests[0])
        self.assertIn('--ez checkin_source_force true', requests[0])
        self.assertIn('--es checkin_source_package com.android.shell', requests[0])
        # A later clean snapshot must not erase a BT crash seen in the window.
        hook = smoke.Hook()
        hook.bt_active, hook.bt_proof = True, True
        hook.bt_previous = (1, 0, '0' * 64)
        snapshots = iter([(1, 0, '0' * 64), (1, 0, '1' * 64), (1, 0, '1' * 64)])
        hook.bluetooth_snapshot = lambda: next(snapshots)
        with contextlib.redirect_stderr(io.StringIO()):
            hook.monitor_bluetooth(force=True)
            self.assertTrue(hook.results['BLUETOOTH_ENABLED'])
            self.assertTrue(hook.results['BLUETOOTH_NO_CRASH'])
            hook.monitor_bluetooth(force=True)
            hook.monitor_bluetooth(force=True)
        self.assertTrue(hook.results['BLUETOOTH_ENABLED'])
        self.assertFalse(hook.results['BLUETOOTH_NO_CRASH'])

    def test_timeout_and_length_evidence_keep_output_fixed(self):
        for length, expected in (('0', False), ('17', True)):
            hook, clock, output = smoke.Hook(), Clock(), io.StringIO()

            def shell(source, **kwargs):
                if source == smoke.CHECKIN_REQUEST:
                    return 1, 'SecurityException SUPPRESSED_FIXTURE'
                return 0, 'CHECKIN 0'

            hook.shell = shell
            hook.native = lambda source: (0, 'GSF_LEN ' + length)
            with patch.object(smoke.sp, 'run', side_effect=AssertionError('hook process forbidden')), \
                 patch.object(smoke.time, 'monotonic', clock.now), \
                 patch.object(smoke.time, 'sleep', clock.sleep), \
                 contextlib.redirect_stderr(output):
                self.assertEqual(hook.checkin(), expected)
            self.assertNotIn('SUPPRESSED_FIXTURE', output.getvalue())
            if expected:
                self.assertIn('evidence=GSF_LENGTH', output.getvalue())
            else:
                self.assertEqual(clock.value, 900)
                self.assertIn('TIMEOUT elapsed_s=900', output.getvalue())

    def test_missing_owner_context_refuses_before_subprocess(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(smoke.sp, 'run', side_effect=AssertionError('hook process forbidden')), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(smoke.main(), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
