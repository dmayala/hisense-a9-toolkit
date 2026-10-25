#!/usr/bin/env python3
"""Offline wrapper aliases must not redirect writes into another tree."""
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
M = SourceFileLoader('s1_offline_test', str(ROOT/'scripts/mainline/sensors/s1-offline-check.py')).load_module()


class WrapperTests(unittest.TestCase):
    def test_valid_reuse_and_result_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); snapshot = out/'snapshot'
            M.prepare_wrapper(out, snapshot)
            M.prepare_wrapper(out, snapshot)
            log, receipt = M.result_paths(out)
            log.write_text('retained failure\n')
            self.assertEqual(M.result_paths(out)[0].name, 'offline-check.1.log')
            self.assertEqual(log.read_text(), 'retained failure\n')

    def test_directory_aliases_leave_outside_unchanged(self):
        for relative in ('wrapper', 'wrapper/android', 'wrapper/mainline', 'wrapper/mainline/out-android'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)/'out'; out.mkdir()
                outside = Path(tmp)/'outside'; outside.mkdir()
                canary = outside/'canary'; canary.write_text('unchanged')
                alias = out/relative; alias.parent.mkdir(parents=True, exist_ok=True)
                alias.symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    M.prepare_wrapper(out, out/'snapshot')
                self.assertEqual([p.name for p in outside.iterdir()], ['canary'])
                self.assertEqual(canary.read_text(), 'unchanged')

    def test_result_aliases_refused(self):
        for name in ('offline-check.log', 'offline-check.json'):
            for dangling in (False, True):
                with self.subTest(name=name, dangling=dangling), tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp); outside = out/'outside'
                    if not dangling:
                        outside.write_text('unchanged')
                    (out/name).symlink_to(outside)
                    with self.assertRaises(ValueError):
                        M.result_paths(out)
                    if not dangling:
                        self.assertEqual(outside.read_text(), 'unchanged')

    def test_existing_file_is_not_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); (out/'wrapper').write_text('unchanged')
            with self.assertRaises(ValueError):
                M.prepare_wrapper(out, out/'snapshot')


if __name__ == '__main__':
    unittest.main()
