#!/usr/bin/env python3
"""Host-only numeric privacy fixtures, using the actual Android shell/find."""
import importlib.util
import os
from pathlib import Path
import subprocess as sp
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fp_privacy_fake', HERE/'selftest.py')
fake = importlib.util.module_from_spec(spec); spec.loader.exec_module(fake)
m = fake.sweep
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
HOST_PATH = os.environ.get('PATH', os.defpath)
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


class Privacy(unittest.TestCase):
    setUp = fake.Tests.setUp

    def test_android_inventory_empty_nested_files_symlink_and_error(self):
        root = Path(self.tmp.name)/'fsfp_dump'
        bin = Path(self.tmp.name)/'bin'; bin.mkdir()
        for name in ('find', 'wc'):
            p = bin/name
            p.write_text('#!/bin/sh\nexec '+QEMU+' '+str(ANDROID/'linker64')+' '+str(ANDROID/'system/bin/toybox')+' '+name+' "$@"\n')
            p.chmod(0o700)
        script = 'PATH='+str(bin)+':'+HOST_PATH+'\n'+m.FP_NO_DUMPS
        def scan(path):
            return sp.run([QEMU, ANDROID/'linker64', ANDROID/'system/bin/sh', '-c', script, 'fixture', path],
                          env=dict(os.environ, LD_LIBRARY_PATH=str(ANDROID/'lib64')), text=True,
                          capture_output=True, timeout=15)
        self.assertEqual(scan(root).stdout, 'completed 1\nfiles 0\n')
        (root/'templet').mkdir(parents=True)
        self.assertEqual(scan(root).stdout, 'completed 1\nfiles 0\n')
        (root/'templet'/'PRIVATE.bmp').write_text('PRIVATE_BIOMETRIC_PAYLOAD')
        result = scan(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'completed 1\nfiles 1\n')
        link = root.parent/'PRIVATE_LINK'; link.symlink_to(root)
        self.assertEqual(scan(link).stdout, 'completed 1\nfiles 1\n')
        result = scan(root.parent/'missing'/'root')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('PRIVATE', result.stdout)
        (bin/'find').write_text('#!/bin/sh\nexit 1\n')
        self.assertNotEqual(scan(root).returncode, 0)

    def test_failure_is_sticky_and_does_not_gate_features(self):
        s = fake.Fake()
        with patch.object(s, 'native', return_value=(0, 'completed 1\nfiles 1\n')):
            s.fingerprint_privacy()
        with patch.object(s, 'native', return_value=(0, 'completed 1\nfiles 0\n')):
            s.fingerprint_privacy()
        self.assertEqual(s.results['30_fp_no_dumps']['status'], 'FAIL')
        self.assertEqual(s.results['30_fp_no_dumps']['evidence'], dict(files=1, scan_errors=0, scans=2))
        s.execute()
        self.assertEqual(s.results['30_fp_no_dumps']['status'], 'FAIL')
        for row in ('08_22_sensors', '12_wifi', '18_hardware_encoder', 'cleanup'):
            self.assertEqual(s.results[row]['status'], 'PASS')

    def test_scan_failure_is_not_empty(self):
        s = fake.Fake()
        with patch.object(s, 'native', return_value=(1, '')):
            s.fingerprint_privacy()
        self.assertEqual(s.results['30_fp_no_dumps']['status'], 'FAIL')
        self.assertEqual(s.results['30_fp_no_dumps']['evidence']['scan_errors'], 1)


if __name__ == '__main__': unittest.main(verbosity=2)
