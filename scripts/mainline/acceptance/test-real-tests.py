#!/usr/bin/env python3
"""One host regression: real PIN/qualifier control flow, inert transports only."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('real_rows_fixture', HERE.parent/'video/qualification/test-host.py')
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
fake = fixture.fake
m = fake.sweep


class RealTests(unittest.TestCase):
    setUp = fake.Tests.setUp

    def test_pin_and_encoder_run_before_usb_despite_diagnostic_failures(self):
        made = []

        class Sweep(fake.Fake):
            def __init__(s):
                super().__init__(); s.order = []; s.secure = s.locked = 0; s.disconnected = False
            def native(s, source, timeout=78):
                if source in (m.SNAPSHOT, m.ANR_TRACES):
                    return 1, 'A9_ONB_TRANSPORT phase=1 rc=1\nA9_ONB_ERROR kind=1\n'
                return super().native(source, timeout)
            def basic(s):
                super().basic()
                # Failed diagnostics/restart health must not veto a live B test.
                s.put('29_wrapped_fbe', False, diagnostic=1)
                s.restart_attempted = True
                s.put('01_restart', False, baseline=0)
            def cleanup(s):
                super().cleanup(); s.cleanup_ok = False
                s.put('cleanup', False, diagnostic=1, pin_cleanup_ok=int(s.pin_cleanup_ok))
            def keyguard_state(s):
                return dict(device_secure=s.secure, device_locked=s.locked, keyguard_locked=s.locked)
            def shell(s, source, data=None, **kw):
                if s.disconnected: return 255, ''
                if source == 'getprop ro.boot.slot_suffix': return 0, '_b\n'
                if source == 'getprop ro.boot.slot_suffix; getprop sys.boot_completed': return 0, '_b\r\n1\r\n'
                if source.startswith('locksettings help'): return 0, 'set-pin verify clear --old --user\n'
                if source == m.PIN_SET:
                    s.order.append('pin-set'); s.secure = 1; return 0, ''
                if source == m.PIN_VERIFY: s.order.append('pin-verify'); return 0, ''
                if source == m.PIN_CLEAR:
                    s.order.append('pin-clear'); s.secure = s.locked = 0; return 0, ''
                if source == m.PIN_UNLOCK: s.locked = 0; return 0, ''
                if source.startswith('input keyevent KEYCODE_SLEEP'): s.locked = 1; return 0, ''
                if source.startswith('mktemp -d /data/local/tmp/a9-hvenc.'): return 0, '/data/local/tmp/a9-hvenc.ABCDEF\n'
                if source.startswith('timeout -k 2 55 '):
                    s.order.append('hvenc'); return 0, fixture.RESULT
                if source.startswith('stat -c %s /data/local/tmp/a9-hvenc.'): return 0, '123\n'
                if source.startswith(('chmod 700 /data/local/tmp/a9-hvenc.', 'rm -r -- /data/local/tmp/a9-hvenc.')): return 0, ''
                return super().shell(source, data=data, **kw)
            def run(s, argv, **kw):
                if len(argv) > 1 and argv[1] == 'push' and len(argv) == 6: return 0, ''
                if len(argv) > 1 and argv[1] == 'pull':
                    Path(argv[-1]).write_bytes(b'x'*123); return 0, ''
                return super().run(argv, **kw)
            def usb_restart(s):
                # Model the retained MTP re-enumeration failure. This phase must
                # not have run while either real row still needed adb.
                s.order.append('usb'); s.disconnected = True
                s.put('11_adb_mtp', False, request_rc=255)
                s.put('01_restart', False, baseline=0)

        def factory():
            s = Sweep(); made.append(s); return s

        original_spec = importlib.util.spec_from_file_location
        def load(name, path, *args, **kw):
            spec = original_spec(name, path, *args, **kw)
            if name == 'last_hvenc':
                execute = spec.loader.exec_module
                def execute_fake_transport(module):
                    execute(module)
                    module.v.verify_bundle = lambda *a: None
                    module.v.inspect = lambda *a: dict(host_mp4_ok=1)
                spec.loader.exec_module = execute_fake_transport
            return spec

        with patch.dict(os.environ, ADB='INERT', A9_BTRY_IN_HOOK='1', A9_SWEEP_HVENC_QUALIFY='1',
                        A9_SWEEP_PIN_LIFECYCLE='fresh-disposable-formatted-b'), \
             patch.object(m, 'Sweep', side_effect=factory), patch.object(m.signal, 'signal'), \
             patch.object(m.sp, 'run', side_effect=AssertionError('real subprocess forbidden')), \
             patch.object(importlib.util, 'spec_from_file_location', side_effect=load):
            self.assertEqual(m.main(), 1)  # Failed diagnostic/cleanup rows remain failed.
        s = made[0]
        for row in ('18_hardware_encoder', '29_pin_lifecycle'):
            self.assertEqual(s.results[row]['status'], 'PASS', s.results[row])
            self.assertEqual(s.results[row]['evidence']['attempted'], 1)
        self.assertEqual(s.order, ['hvenc', 'pin-set', 'pin-verify', 'pin-clear', 'usb'])
        self.assertEqual(s.secure, 0); self.assertTrue(s.pin_cleanup_ok)
        self.assertEqual(s.results['A_stability']['status'], 'FAIL')
        self.assertEqual(s.results['11_adb_mtp']['evidence']['request_rc'], 255)
        self.assertEqual(s.results['01_restart']['evidence']['baseline'], 0)
        json.loads((s.out/'acceptance.json').read_text())


if __name__ == '__main__': unittest.main(verbosity=2)
