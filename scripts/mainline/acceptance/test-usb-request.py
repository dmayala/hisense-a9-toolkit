#!/usr/bin/env python3
"""Real Android mksh/toybox, fake svc only; no USB or device operations."""
import importlib.util
import os
from pathlib import Path
import shlex
import signal
import subprocess as sp
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('usb_sweep', HERE/'onb.py')
sweep = importlib.util.module_from_spec(spec); spec.loader.exec_module(sweep)
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


class Usb(unittest.TestCase):
    def replay(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); commands = root/'bin'; commands.mkdir()
            prefix = ('#!/bin/sh\nexec env LD_LIBRARY_PATH='+shlex.quote(str(ANDROID/'lib64'))+' '+QEMU+' '+
                      str(ANDROID/'linker64')+' '+str(ANDROID/'system/bin')+'/')
            for name in ('sh', 'nohup', 'setsid', 'timeout', 'mkdir', 'cat', 'mv', 'rm'):
                p = commands/name
                p.write_text(prefix+('sh' if name == 'sh' else 'toybox '+name)+' "$@"\n'); p.chmod(0o700)
            svc = commands/'svc'
            svc.write_text('#!/bin/sh\n[ "$*" = "usb setFunctions mtp" ] || exit 99\n'
                           'echo call >>"$USB_TEST_CALLS"\n'
                           + ('sleep 3\n' if mode == 'timeout' else 'sleep .1\n')
                           + 'echo "setCurrentFunctions opId:1 PRIVATE_TEST_TOKEN"\n'
                           + ('echo "SecurityException: Permission Denial PRIVATE_TEST_TOKEN" >&2\nexit 1\n'
                              if mode == 'denied' else 'exit 0\n'))
            svc.chmod(0o700)
            env = dict(os.environ, PATH=str(commands)+':'+os.environ['PATH'],
                       USB_TEST_CALLS=str(root/'calls'), A9_BTRY_LOGS=str(root))

            class Local(sweep.Sweep):
                launches = 0
                def shell(self, source, data=None, timeout=20, capture_stderr=False):
                    source = source.replace('/data/local/tmp/', str(root)+'/')
                    # Shorten only the fake svc's timeout. All launcher/session
                    # mechanics and the production result parser are exercised.
                    source = source.replace('timeout -k 2 20 svc', 'timeout -k .1 .3 svc')
                    if source.startswith('umask 077; mkdir '):
                        self.launches += 1
                        p = sp.Popen(['/bin/sh', '-c', '"$@"; sleep 10', 'usb-test', commands/'sh', '-c', source], env=env,
                                     stdout=sp.PIPE, stderr=sp.PIPE, start_new_session=True)
                        try:
                            ack = p.stdout.readline()
                            # Model adbd destroying its shell process group as
                            # soon as the transport closes. Detached svc survives.
                            os.killpg(p.pid, signal.SIGTERM)
                            p.wait(timeout=3)
                            if not ack: raise AssertionError(p.stderr.read().decode())
                            return 255, ack.decode()+'error: closed PRIVATE_TEST_TOKEN\n'
                        finally:
                            if p.poll() is None:
                                os.killpg(p.pid, signal.SIGKILL); p.wait()
                            p.stdout.close()
                            p.stderr.close()
                    r = sp.run([commands/'sh', '-c', source], env=env, capture_output=True,
                               text=True, timeout=timeout)
                    return r.returncode, r.stdout
                def wait_restart(self, seconds, predicate):
                    stop = time.monotonic()+5
                    while time.monotonic() < stop:
                        if predicate(): return True
                        time.sleep(.02)
                    return False

            with patch.dict(os.environ, env):
                diagnostic = Local(); result = diagnostic.usb_set_mtp()
            self.assertEqual(diagnostic.launches, 1)
            self.assertEqual((root/'calls').read_text().splitlines(), ['call'])
            self.assertEqual(result['set_rc'], 255)
            self.assertEqual(result['set_completed'], 1)
            self.assertEqual(result['set_result_readable'], 1)
            self.assertEqual(result['set_receipt_cleaned'], 1)
            self.assertFalse(list(root.glob('a9-sweep-usb-*')))
            evidence = (root/'usb-set-evidence.json').read_text()
            self.assertNotIn('PRIVATE_TEST_TOKEN', evidence)
            sweep.numeric_tree(result)
            return result

    def test_survives_initiating_group_teardown(self):
        result = self.replay('ok')
        self.assertEqual(result['set_command_rc'], 0)
        self.assertEqual(result['set_request_logged'], 1)
        self.assertEqual(result['set_permission_denied'], 0)

    def test_permission_refusal_is_retained(self):
        result = self.replay('denied')
        self.assertEqual(result['set_command_rc'], 1)
        self.assertEqual(result['set_exception'], 1)
        self.assertEqual(result['set_permission_denied'], 1)

    def test_bounded_setter_timeout(self):
        result = self.replay('timeout')
        self.assertEqual(result['set_command_rc'], 124)
        self.assertEqual(result['set_request_logged'], 0)


if __name__ == '__main__': unittest.main(verbosity=2)
