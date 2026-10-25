#!/usr/bin/env python3
"""Five host-only PIN groups: fake ADB/framework, real cached Android mksh.

Never invokes adb, phone scripts, or a connected device. Secrets are synthetic.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('pin_sweep', HERE/'onb.py')
sweep = importlib.util.module_from_spec(spec); spec.loader.exec_module(sweep)
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'

FAKE = '''#!PYTHON
import json, os, pathlib, sys
p = pathlib.Path(os.environ['PIN_TEST_STATE'])
s = json.loads(p.read_text()); a = sys.argv[1:]; name = pathlib.Path(sys.argv[0]).name
def save(): p.write_text(json.dumps(s))
if name == 'getprop': print('_b'); sys.exit(0)
if name == 'locksettings':
    command = a[0]
    if command == 'help':
        print('set-pin verify clear --old --user' if s['mode'] != 'unsupported' else 'help'); sys.exit(0)
    s['calls'].append(command)
    if command == 'set-pin':
        s.update(pin=a[-1], secure=1); save()
        print('Pin set to '+a[-1]+' PRIVATE_TEST_TOKEN')
        sys.exit(255 if s['mode'] == 'uncertain' else 0)
    old = a[a.index('--old')+1]
    if old != s.get('pin'): sys.exit(1)
    if command == 'verify':
        save()
        if s['mode'] == 'verify-fail': print('Credential did not match PRIVATE_TEST_TOKEN'); sys.exit(0)
        print('Lock credential verified successfully'); sys.exit(0)
    if command == 'clear':
        if s['mode'] == 'clear-fail': save(); sys.exit(1)
        s.update(secure=0, locked=0); s.pop('pin', None); save(); sys.exit(0)
if name == 'input':
    if a == ['keyevent', 'KEYCODE_SLEEP']: s['locked'] = 1
    elif a[0] == 'text': s['typed'] = a[1]
    elif a == ['keyevent', 'KEYCODE_ENTER']:
        if s.pop('typed', '') == s.get('pin'): s['locked'] = 0
    save(); sys.exit(0)
sys.exit(99)
'''


class Pin(unittest.TestCase):
    def replay(self, mode='ok', opt_in=True, secure=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); commands = root/'bin'; commands.mkdir()
            state_path = root/'state'
            state_path.write_text(json.dumps(dict(mode=mode, secure=secure, locked=0, calls=[])))
            for name in ('getprop', 'locksettings', 'input'):
                path = commands/name
                path.write_text(FAKE.replace('PYTHON', sys.executable)); path.chmod(0o700)
            adb = commands/'fake-transport'
            adb.write_text('#!'+sys.executable+'\nimport os, sys\n'
                           'assert sys.argv[1:3] == ["shell", "-T"]\n'
                           'assert "731946" not in " ".join(sys.argv)\n'
                           'os.execv('+repr(QEMU)+', '+repr([QEMU, str(ANDROID/'linker64'),
                               str(ANDROID/'system/bin/sh'), '-c'])+'+sys.argv[3:])\n')
            adb.chmod(0o700)
            env = dict(os.environ, ADB=str(adb), A9_BTRY_LOGS=str(root),
                       PATH=str(commands)+':'+os.environ['PATH'], PIN_TEST_STATE=str(state_path),
                       LD_LIBRARY_PATH=str(ANDROID/'lib64'),
                       A9_SWEEP_PIN_LIFECYCLE='fresh-disposable-formatted-b' if opt_in else '')

            class Local(sweep.Sweep):
                def keyguard_state(self):
                    state = json.loads(state_path.read_text())
                    return dict(device_secure=state['secure'], device_locked=state['locked'],
                                keyguard_locked=state['locked'])
                def run(self, argv, data=None, *args, **kwargs):
                    if mode == 'interrupt' and sweep.PIN_VERIFY in argv:
                        raise InterruptedError('PRIVATE_TEST_TOKEN')
                    return super().run(argv, data, *args, **kwargs)

            output = io.StringIO()
            with patch.dict(os.environ, env), patch.object(sweep.secrets, 'randbelow', return_value=631946), \
                    patch.object(sweep.time, 'sleep'), contextlib.redirect_stdout(output):
                s = Local(); s.results['29_wrapped_fbe']['status'] = 'PASS'
                # Prevent a failed cleanup-state poll from spending wall time.
                original_wait = s.pin_state_wait
                def state_wait(want_secure, locked=None):
                    if mode == 'clear-fail' and want_secure == 0: return False
                    return original_wait(want_secure, locked)
                s.pin_state_wait = state_wait
                s.pin_lifecycle()
                result = s.results['29_pin_lifecycle']
                saved = (root/'acceptance.json').read_text()
            for secret in ('731946', 'PRIVATE_TEST_TOKEN'):
                self.assertNotIn(secret, saved+output.getvalue())
            sweep.numeric_tree(result['evidence'])
            final = json.loads(state_path.read_text())
            return result, final, s.pin_cleanup_ok

    def test_1_lifecycle_and_android_shell(self):
        r, state, clean = self.replay()
        self.assertEqual(r['status'], 'PASS')
        self.assertEqual(state['calls'], ['set-pin', 'verify', 'clear'])
        self.assertEqual(state['secure'], 0); self.assertTrue(clean)
        self.assertTrue(all(r['evidence'][k] == 1 for k in ('verified', 'locked', 'unlocked', 'removed')))

    def test_2_opt_in_and_existing_credential_refusal(self):
        for kwargs in (dict(opt_in=False), dict(secure=1), dict(mode='unsupported')):
            r, state, clean = self.replay(**kwargs)
            self.assertNotEqual(r['status'], 'PASS')
            self.assertEqual(state['calls'], []); self.assertTrue(clean)

    def test_3_verify_failure_and_interruption_cleanup(self):
        for mode in ('verify-fail', 'interrupt'):
            r, state, clean = self.replay(mode)
            self.assertEqual(r['status'], 'FAIL')
            self.assertEqual(state['calls'][-1], 'clear')
            self.assertEqual(state['secure'], 0); self.assertTrue(clean)
            self.assertEqual(r['evidence']['unlocked'], 0)

    def test_4_uncertain_set_cleanup_and_cleanup_failure(self):
        r, state, clean = self.replay('uncertain')
        self.assertEqual(r['status'], 'FAIL'); self.assertFalse(clean)
        self.assertEqual(state['calls'], ['set-pin', 'clear'])
        self.assertEqual(state['secure'], 0)
        self.assertEqual(r['evidence']['set_completed'], 0)
        r, state, clean = self.replay('clear-fail')
        self.assertEqual(r['status'], 'FAIL'); self.assertFalse(clean)
        self.assertEqual(r['evidence']['cleanup_ok'], 0)
        self.assertEqual(state['secure'], 1)

    def test_5_probe_never_dismisses_keyguard_and_cleanup_propagates(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'A9_BTRY_LOGS': tmp}):
            s = sweep.Sweep(); commands = []
            def shell(source, **kwargs):
                commands.append(source)
                if source.startswith('run-as ') and 'cat files/result.json' in source:
                    return 0, '{"completed":1,"device_secure":1,"device_locked":1,"keyguard_locked":1}'
                return 0, ''
            s.shell = shell
            self.assertEqual(s.keyguard_state()['device_locked'], 1)
            self.assertFalse(any('dismiss-keyguard' in c for c in commands))
            s.touched = True; s.pin_cleanup_ok = False
            with contextlib.redirect_stdout(io.StringIO()): s.cleanup()
            self.assertEqual(s.results['cleanup']['status'], 'FAIL')
            self.assertEqual(s.results['cleanup']['evidence']['pin_cleanup_ok'], 0)


if __name__ == '__main__': unittest.main(verbosity=2)
