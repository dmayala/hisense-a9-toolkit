#!/usr/bin/env python3
"""Actual Android shell cap replay with a fake iw; no socket/device access."""
import importlib.util
import os
from pathlib import Path
import subprocess as sp
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
s = importlib.util.spec_from_file_location('play_cap', HERE.parent/'play/onb.py')
play = importlib.util.module_from_spec(s); s.loader.exec_module(play)
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


class Cap(unittest.TestCase):
    def replay(self, code, message):
        with tempfile.TemporaryDirectory() as tmp:
            iw = Path(tmp)/'iw'
            iw.write_text('#!/bin/sh\n[ "$*" = "dev wlan0 set txpower limit 2000" ] || exit 99\n'
                          'printf "%s\\n" "$CAP_TEST_ERROR" >&2\nexit "$CAP_TEST_RC"\n')
            iw.chmod(0o700)
            source = play.CAP.replace('/mnt/a9-wifi/tools/iw', str(iw))
            env = dict(os.environ, LD_LIBRARY_PATH=str(ANDROID/'lib64'),
                       CAP_TEST_RC=str(code), CAP_TEST_ERROR=message+' PRIVATE_TEST_TOKEN')
            r = sp.run([QEMU,ANDROID/'linker64',ANDROID/'system/bin/sh'],
                       input='set -e\n'+source,env=env,text=True,capture_output=True,timeout=10)
            self.assertNotIn('PRIVATE_TEST_TOKEN',r.stdout+r.stderr)
            self.assertIn('cap_command_rc '+str(code)+'\n', r.stdout)
            self.assertEqual(r.returncode, code)
            self.assertEqual('READY\n' in r.stdout, code == 0)
            return r.stdout

    def test_success(self): self.replay(0,'')
    def test_netlink_refusal(self):
        self.assertIn('cap_netlink_error 1\n',self.replay(1,'Failed to connect to generic netlink.'))
    def test_permission_refusal(self):
        self.assertIn('cap_permission_error 1\n',self.replay(1,'Operation not permitted'))
    def test_unsupported(self):
        self.assertIn('cap_unsupported 1\n',self.replay(95,'Operation not supported'))
    def test_timeout_or_missing(self):
        for code in (124,127): self.replay(code,'command unavailable')


if __name__ == '__main__': unittest.main(verbosity=2)
