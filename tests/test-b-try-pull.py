#!/usr/bin/env python3
"""Production recovery-stability/archive helper against fake transport and time."""
import io,json,os,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'scripts/mainline/boot-deadline/b-try.sh').read_text()
HELPER=SOURCE[SOURCE.index('wait_pull_ready() {'):SOURCE.index('pull_logs() {')]
class Pull(unittest.TestCase):
 def replay(self,responses,kind='direct',states=None,available=True):
  with tempfile.TemporaryDirectory() as tmp:
   r=Path(tmp);b=r/'bin';b.mkdir();buf=io.BytesIO()
   with tarfile.open(fileobj=buf,mode='w') as t:
    p=tarfile.TarInfo('boot-fixture');p.type=tarfile.DIRTYPE;t.addfile(p)
   (r/'good.tar').write_bytes(buf.getvalue());(r/'clock').write_text('0')
   (b/'timeout').write_text('#!/bin/sh\nwhile [ "${1#--}" != "$1" ]; do shift; done\nshift\nexec "$@"\n')
   fake='''#!PYTHON
import json,os,sys
from pathlib import Path
r=Path(os.environ['FIXTURE']);now=int((r/'clock').read_text())
with (r/'calls').open('a') as f:f.write(json.dumps([sys.argv[1],now])+'\\n')
if sys.argv[1]=='get-state':
 p=r/'states-count';n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1))
 states=os.environ['STATES'].split(',');s=states[min(n,len(states)-1)]
 if s=='offline':sys.exit(1)
 print(s+'\\r');sys.exit(0)
assert sys.argv[1]=='exec-out'
assert sys.argv[2]==os.environ['EXPECTED_COMMAND']
p=r/'count';n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1))
a=os.environ['RESPONSES'].split(',')[n]
if a=='good':sys.stdout.buffer.write((r/'good.tar').read_bytes())
else:sys.stdout.buffer.write(b'partial')
sys.exit(1 if a=='fail' else 0)
'''.replace('PYTHON',sys.executable)
   adb=b/'fake-adb';adb.write_text(fake)
   for p in b.iterdir():p.chmod(0o700)
   script=HELPER+'''\nunset SECONDS
SECONDS=0
sleep() {
 local n
 read -r n < "$FIXTURE/clock" || true
 n=$((n+$1)); printf '%s\\n' "$n" > "$FIXTURE/clock"; SECONDS=$n
 printf '%s\\n' "$1" >> "$FIXTURE/sleeps"
}
shell_kind=$KIND
pull_state=$EXPECTED_STATE
pull_archive /tmp/fixture.tar "$FIXTURE/result.tar"
'''
   wanted='device' if kind=='su' else 'recovery'
   env=dict(os.environ,FIXTURE=str(r),KIND=kind,RESPONSES=','.join(responses),STATES=','.join(states or [wanted]),ADB=str(adb),EXPECTED_STATE=wanted,EXPECTED_COMMAND="su -c 'cat /tmp/fixture.tar'" if kind=='su' else 'cat /tmp/fixture.tar',PATH=str(b)+':'+os.environ['PATH'])
   p=subprocess.run(['bash','-eu','-c',script],env=env,capture_output=True,timeout=25)
   count=int((r/'count').read_text()) if (r/'count').exists() else 0
   self.assertEqual(count,len(responses));calls=[json.loads(l) for l in (r/'calls').read_text().splitlines()]
   transfers=[t for name,t in calls if name=='exec-out']
   if transfers:self.assertGreaterEqual(transfers[0],10)
   for earlier,later in zip(transfers,transfers[1:]):self.assertGreaterEqual(later-earlier,20)
   sleeps=list(map(int,(r/'sleeps').read_text().splitlines()))
   attempts=len(responses) if available else 3
   self.assertEqual(sleeps.count(10),attempts-1)
   self.assertTrue(all(name in ('get-state','exec-out') for name,t in calls))
   good=p.returncode==0
   if good:self.assertEqual((r/'result.tar').read_bytes(),buf.getvalue());self.assertFalse((r/'result.tar.part').exists())
   else:self.assertFalse((r/'result.tar').exists())
   return good,(r/'result.tar.pull.log').read_text(),transfers,int((r/'clock').read_text())
 def test_direct_first_success(self):self.assertTrue(self.replay(['good'])[0])
 def test_su_disconnect_then_success(self):self.assertTrue(self.replay(['fail','good'],'su')[0])
 def test_three_transport_failures_stop(self):self.assertFalse(self.replay(['fail','fail','fail'])[0])
 def test_third_attempt_succeeds(self):self.assertTrue(self.replay(['fail','fail','good'])[0])
 def test_zero_status_truncated_tar_retried(self):self.assertTrue(self.replay(['bad','good'])[0])
 def test_third_bad_tar_still_fails(self):self.assertFalse(self.replay(['fail','bad','bad'])[0])
 def test_reenumeration_restarts_stability_window(self):
  ok,_,times,_=self.replay(['good'],states=['recovery']*5+['offline','device','recovery'])
  self.assertTrue(ok);self.assertGreaterEqual(times[0],17)
 def test_never_recovery_is_bounded_without_transfer(self):
  ok,log,times,elapsed=self.replay([],states=['device'],available=False)
  self.assertFalse(ok);self.assertEqual(times,[]);self.assertEqual(log.count('attempt='),3)
  self.assertGreaterEqual(elapsed,380);self.assertLess(elapsed,390)
if __name__=='__main__':unittest.main(verbosity=2)
