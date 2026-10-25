#!/usr/bin/env python3
"""Row27 property verdict only: synthetic shell replies, no transport."""
import importlib.util
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
s = importlib.util.spec_from_file_location('sweep', HERE/'onb.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)


class Release(unittest.TestCase):
    def check(self, reply, rc=0):
        obj = object.__new__(m.Sweep); obj.results = {}
        obj.persist = lambda: None
        obj.row_errors = set()
        obj.shell = lambda _: (rc, reply)
        obj._basic_release()
        return obj.results['27_release']

    def test_dev_keeps_informational_not_run(self):
        r = self.check('userdebug\nrelease-keys\n0\n0\n1\nuserdebug\ntest-keys\n')
        self.assertEqual(r['status'], 'NOT_RUN')
        self.assertEqual(r['evidence']['release_configuration'], 0)

    def test_secure_release_passes(self):
        r = self.check('user\nrelease-keys\n1\n1\n0\nuser\nrelease-keys\n1\n')
        self.assertEqual(r['status'], 'PASS')
        self.assertEqual(r['evidence']['release_configuration'], 1)

    def test_release_insecure_or_unreadable_fails(self):
        good = 'user\nrelease-keys\n1\n1\n0\nuser\nrelease-keys\n1\n'
        for index, bad in ((0,'userdebug'),(1,'test-keys'),(2,'0'),(3,'0'),
                           (4,'1'),(5,'userdebug'),(6,'test-keys')):
            p = good.splitlines(); p[index] = bad
            self.assertEqual(self.check('\n'.join(p)+'\n')['status'], 'FAIL')
        self.assertEqual(self.check(good, 1)['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main(verbosity=2)
