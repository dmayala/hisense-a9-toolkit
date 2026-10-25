#!/usr/bin/env python3
"""Five focused groups on the actual shared guard under cached Android mksh."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SHELL='/home/user/A9-build/mainline/integrated-20261021n/r4-tests/android-sh'
GUARD=ROOT/'scripts/mainline/android/vendor/bin/a9ml-test-data.sh'

class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.proc=self.root/'proc'; self.sys=self.root/'sys'
        (self.proc/'self').mkdir(parents=True)
        self.block=self.sys/'dev/block/253:63'
        for p,value in [('dm/name','a9-row29-data'),('dm/uuid','A9-ROW29-DATA-20261021'),('size','6191304'),('slaves/sde/dev','8:64')]:
            f=self.block/p;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(value+'\n')
        f=self.sys/'class/block/sde/dev';f.parent.mkdir(parents=True);f.write_text('8:64\n')

    def check(self,expected,device='253:63',fs='f2fs',source='/dev/block/dm-63',root='/',opts='rw,nosuid,nodev',extra=''):
        (self.proc/'self/mountinfo').write_text(f'32 1 {device} {root} /data {opts} - {fs} {source} rw\n'+extra)
        p=subprocess.run([SHELL,'-c','. "$1"; a9ml_test_data "$2" "$3" /data','test',str(GUARD),str(self.proc),str(self.sys)],capture_output=True,text=True)
        self.assertEqual(p.returncode==0,expected,p.stderr)

    def test_ram(self):
        for source in ('/dev/block/ram0','/dev/ram0'):
            self.check(True,'1:0','ext4',source)
        self.check(False,'1:1','ext4','/dev/block/ram0')
        self.check(False,'1:0','f2fs','/dev/block/ram0')

    def test_row29(self):
        self.check(True)
        self.check(True,source='/dev/block/mapper/a9-row29-data')

    def test_userdata(self):
        self.check(False,source='/dev/block/by-name/userdata')
        (self.block/'dm/name').write_text('userdata\n');self.check(False)
        (self.block/'dm/name').write_text('a9-row29-data\n')
        (self.block/'dm/uuid').write_text('userdata\n');self.check(False)

    def test_geometry(self):
        self.check(False,fs='ext4')
        (self.block/'size').write_text('6191305\n');self.check(False)
        (self.block/'size').write_text('6191304\n')
        (self.block/'slaves/sde/dev').write_text('8:0\n');self.check(False)
        (self.block/'slaves/sde/dev').write_text('8:64\n')
        (self.block/'slaves/sda').mkdir();self.check(False)

    def test_mount_and_callers(self):
        self.check(False,root='/subdir')
        self.check(False,opts='ro')
        self.check(False,extra='33 1 1:0 / /data rw - ext4 /dev/block/ram0 rw\n')
        sensors=(ROOT/'scripts/mainline/android/vendor/bin/a9-sensors.sh').read_text()
        wifi=(ROOT/'scripts/mainline/wifi/coordinator-target.sh').read_text()
        self.assertIn('a9ml_test_data "$PROC" "$SYS" "$DATA" || fail unsupported-data',sensors)
        self.assertIn("a9ml_test_data || die 'RAM ext4 or checked ROW29 f2fs /data required'",wifi)
        self.assertNotIn('fail nonram-data',sensors)
        self.assertNotIn("stat -L -c '%t:%T' /dev/block/ram0",wifi)

if __name__=='__main__':unittest.main(verbosity=2)
