#!/usr/bin/env python3
"""Real manual script through fake adb and Android mksh. No phone access."""
import importlib.util
import json
from pathlib import Path
import subprocess as sp
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('manual_fixture', HERE/'test-host.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
FFMPEG = '/nix/store/j1b3q6akcmxjamapv9qac6ibpd4g1d8b-ffmpeg-headless-7.1.1-bin/bin/ffmpeg'


class Linear(fixture.Manual):
    # Inherit only setup/teardown/helpers, not the unrelated drain test cases.
    def setUp(self):
        super().setUp()
        r = self.r
        (r/'scripts/hvenc-linear.sh').write_text((HERE/'hvenc-linear.sh').read_text())
        fake = fixture.FAKE.replace("sys.stdout.buffer.write(result.stdout.replace(str(r/'proc').encode(),b'/proc'))",
            "sys.stdout.buffer.write(result.stdout.replace(str(r/'proc').encode(),b'/proc').replace(str(r/'device').encode(),b'/data/local/tmp'))")
        fake = fake.replace("if args[0]=='push':", """if args[0]=='push':
 args[-1]=args[-1].replace('/data/local/tmp/',str(r/'device')+'/')""")
        fake = fake.replace("text=payload or args[-1]", """if os.environ.get('DROP_LINEAR')=='1' and args[-1]=='timeout 45 nc 127.0.0.1 2324':
 print('[ 1.000] synthetic partial kernel tail');sys.exit(255)
text=payload or args[-1]""")
        (r/'fake-adb').write_text(fake)
        logcat=r/'bin/logcat';logcat.write_text('#!/bin/sh\necho fixture-logcat\n');logcat.chmod(0o755)
        native=r/'image/linear'
        native.write_text('''#!/bin/sh
if [ "${DENY_SYSTEM:-0}" = 1 ]; then
 echo 'HVENC_LINEAR op=system.open phase=1 rc=-13'; exit 1
fi
cp "$FIXTURE/one.h264" "$2"
echo 'HVENC_LINEAR_RESULT rc=0 vcl=1 input_done=1 bytes=100'
''')
        native.chmod(0o755)
        sp.run([FFMPEG,'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=10',
                '-frames:v','1','-an','-c:v','libx264','-threads','1','-bf','0','-f','h264',r/'one.h264'],check=True,timeout=15)

    def invoke(self, name, mode='mmap', **env):
        return sp.run(['bash',self.r/'scripts/hvenc-linear.sh',self.r/'image',self.r/name,mode],
            env=dict(self.env,**env),text=True,capture_output=True,timeout=20)

    def test_manual_mmap_one_attempt_host_decode_and_restore(self):
        p=self.invoke('mmap-run');self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual((self.r/'mmap-run/decoded.yuv').stat().st_size,115200)
        self.assertTrue(all(p.read_text().strip()=='N' for p in (self.r/'params').iterdir()))
        calls=[json.loads(l) for l in (self.r/'calls').read_text().splitlines()]
        live=[c for c in calls if c['args'][-1]=='timeout 45 nc 127.0.0.1 2324']
        self.assertEqual(len(live),1)
        self.assertIn('timeout 40 dmesg -w',live[0]['payload'])
        self.assertIn('linear mmap',live[0]['payload'])

    def test_manual_heap_denial_clean_restore_and_transport_loss_no_retry(self):
        p=self.invoke('denied','system',DENY_SYSTEM='1')
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertEqual((self.r/'denied/native.rc').read_text().strip(),'1')
        self.assertTrue(all(p.read_text().strip()=='N' for p in (self.r/'params').iterdir()))
        p=self.invoke('lost',DROP_LINEAR='1')
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('synthetic partial kernel tail',(self.r/'lost/kernel-live.log').read_text())
        self.assertIn('restoration unconfirmed',(self.r/'lost/reset.log').read_text())
        calls=[json.loads(l) for l in (self.r/'calls').read_text().splitlines()]
        self.assertEqual(sum(c['args'][-1]=='timeout 45 nc 127.0.0.1 2324' for c in calls),2)


if __name__ == '__main__':
    suite=unittest.TestSuite(Linear(name) for name in (
        'test_manual_mmap_one_attempt_host_decode_and_restore',
        'test_manual_heap_denial_clean_restore_and_transport_loss_no_retry'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
