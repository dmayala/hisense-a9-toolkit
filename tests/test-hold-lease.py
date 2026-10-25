#!/usr/bin/env python3
"""Actual lease writer and deadman predicates, fake clock/sysfs, Android shell."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lease', ROOT/'scripts/mainline/boot-deadline/hold-lease.py')
lease = importlib.util.module_from_spec(spec); spec.loader.exec_module(lease)
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit/bin')
DEADMAN = (ROOT/'scripts/mainline/android/vendor/bin/a9ml-deadman.sh').read_text()
PREDICATES = DEADMAN[DEADMAN.index('cap() {'):DEADMAN.index('\n# phase 1')]


class Lease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.stay = self.root/'stay'
        self.script = self.root/'deadman'; self.script.write_text('STAY=/dev/a9-row29-hold/stay\n')
        self.up = self.root/'uptime'; self.prop = self.root/'maxstay'
        self.host = 0; self.phone = 200; self.calls = 0; self.slot = '_b'

    def shell(self, source):
        self.up.write_text(str(self.phone)+'.00 0\n')
        source = source.replace('/vendor/bin/a9ml-deadman.sh', str(self.script)).replace('/proc/uptime', str(self.up))
        source = source.replace('touch "$stay"', 'touch '+str(self.stay))
        stubs = ('getprop() { case "$1" in ro.boot.slot_suffix) echo '+self.slot+';; '
                 'vendor.a9ml.maxstay) cat '+str(self.prop)+';; esac; }\n'
                 'setprop() { [ "$1" = vendor.a9ml.maxstay ] || exit 90; echo "$2" >'+str(self.prop)+'; }\n')
        return subprocess.run([str(ANDROID/'sh'), '-c', stubs+source], capture_output=True,
                              env=dict(os.environ, PATH=str(ANDROID)+':'+os.environ['PATH']), timeout=5)

    def transport(self, argv, **kwargs):
        self.calls += 1
        self.assertEqual(argv[:3], ['FAKE', 'shell', '-T'])
        result = self.shell(argv[-1])
        if result.returncode == 0:
            os.utime(self.stay, (self.phone, self.phone))
        return result

    def held(self):
        source = ('STAY='+str(self.stay)+'\nMAXSTAY=7200\nFRESH=600\n'
                  'up() { echo '+str(self.phone)+'; }\n'
                  'date() { echo '+str(self.phone)+'; }\n'+PREDICATES+'\nheld\n')
        return self.shell(source).returncode == 0

    def test_restart_renews_volatile_lease_without_restarting_owner_deadline(self):
        self.assertTrue(lease.renew('FAKE', 7200, now=lambda:self.host, run=self.transport))
        self.assertEqual(int(self.prop.read_text()), 7460)
        # 01_restart clears /dev and properties. The same host deadline remains.
        self.stay.unlink(); self.prop.unlink(); self.host = 1000; self.phone = 110
        self.assertTrue(lease.renew('FAKE', 7200, now=lambda:self.host, run=self.transport))
        self.assertEqual(int(self.prop.read_text()), 6370)
        # Replay r5's 5400s Android hold expiration after the restart.
        self.host += 5403; self.phone += 5403
        self.assertTrue(lease.renew('FAKE', 7200, now=lambda:self.host, run=self.transport))
        self.assertTrue(self.held())
        self.assertEqual(int(self.prop.read_text()), 6370)
        # Losing the owner does not disable the original 600s freshness bound.
        self.phone += 601
        self.assertFalse(self.held())
        self.host = 7200; before = self.calls
        self.assertFalse(lease.renew('FAKE', 7200, now=lambda:self.host, run=self.transport))
        self.assertEqual(self.calls, before)

    def test_no_renewal_on_a_and_original_uptime_cap_remains(self):
        self.slot = '_a'
        self.assertFalse(lease.renew('FAKE', 7200, now=lambda:0, run=self.transport))
        self.assertFalse(self.stay.exists()); self.assertFalse(self.prop.exists())
        self.slot = '_b'
        self.assertTrue(lease.renew('FAKE', 86400, now=lambda:0, run=self.transport))
        self.assertEqual(int(self.prop.read_text()), 21600)
        self.phone = 21600
        self.assertFalse(self.held())

    def test_keeper_exits_when_its_host_owner_exits(self):
        marker = self.root/'renewed'
        fake = self.root/'fake-transport'
        fake.write_text('#!'+sys.executable+'\nfrom pathlib import Path\nPath('+repr(str(marker))+').touch()\n')
        fake.chmod(0o700)
        helper = ROOT/'scripts/mainline/boot-deadline/hold-lease.py'
        parent = ('import subprocess,sys,time\nfrom pathlib import Path\n'
                  'p=subprocess.Popen([sys.executable,"-B",'+repr(str(helper))+',"--deadline",str(time.monotonic()+60)],'
                  'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nprint(p.pid,flush=True)\n'
                  'end=time.monotonic()+5\n'
                  'while not Path('+repr(str(marker))+').exists() and time.monotonic()<end: time.sleep(.02)\n')
        result = subprocess.run([sys.executable, '-c', parent], env=dict(os.environ, ADB=str(fake)),
                                capture_output=True, text=True, check=True, timeout=8)
        self.assertTrue(marker.exists())
        pid = int(result.stdout)
        end = time.monotonic()+3
        while time.monotonic() < end:
            try:
                stat = Path('/proc/'+str(pid)+'/stat').read_text()
                if stat[stat.rfind(')')+2:].split()[0] in ('Z', 'X'): return
            except FileNotFoundError: return
            time.sleep(.05)
        self.fail('lease keeper survived its owner')


if __name__ == '__main__': unittest.main(verbosity=2)
