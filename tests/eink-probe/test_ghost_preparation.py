"""Real safe3 pipe primitives and production ghost route; no device calls."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
BOOT=ROOT/'scripts/mainline/android/android-boot.sh'
sys.path.insert(0,str(ROOT/'scripts/eink-probe/sessions'))
import ghost_prepare as P


def function(text,name):
    a=text.index(name+'() {');return text[a:text.index('\n}\n',a)+3]


class PreparationBarrier(unittest.TestCase):
    def test_safe3_barrier_is_exact(self):
        barrier=function(BOOT.read_text(),'latency_prepared')
        self.assertEqual(hashlib.sha256(barrier.encode()).hexdigest(),
                         '6a5e46645e1c5b8854a908fbfa84627aae4cb61136feca94423577071d089e35')

    def test_ghost_write_route_opts_in_before_gate_request(self):
        boot=BOOT.read_text();runner=(ROOT/'scripts/eink-probe/sessions/r0-ghost.sh').read_text()
        session=function(boot,'eink_session')
        self.assertLess(session.index('then latency_prepared; fi'),session.index('setprop vendor.a9ml.eink.gate open'))
        self.assertIn('--require-preparation',runner)
        self.assertIn('"$DEPLOY/ghost_prepare.py"',runner)
        self.assertIn('--latency-prepared-fd',Path(P.__file__).read_text())
        self.assertNotIn("adb shell 'settings put",runner)

    def test_pipe_blocks_then_accepts_only_exact_record_or_refuses_eof(self):
        barrier=function(BOOT.read_text(),'latency_prepared')
        self.assertIn('read -r -t 600 record',barrier)
        for record in (b'PREPARED_LAUNCHER3_VERIFIED_V1\n',b'wrong\n',None):
            with self.subTest(record=record),tempfile.TemporaryDirectory() as d:
                read,write=os.pipe();mark=Path(d,'admitted')
                text='set -u\nLATENCY_PREP_FD=$1\nfail() { exit 3; }\n'+barrier+'\nlatency_prepared\necho yes > "$2"\n'
                child=subprocess.Popen(['bash','-c',text,'fixture',str(read),str(mark)],pass_fds=(read,),stdout=subprocess.PIPE)
                os.close(read)
                self.assertIn(b'PREPARATION BARRIER',child.stdout.readline())
                self.assertIsNone(child.poll());self.assertFalse(mark.exists())
                if record is not None:os.write(write,record)
                os.close(write)
                child.communicate(timeout=2)
                self.assertEqual(child.returncode==0,record==b'PREPARED_LAUNCHER3_VERIFIED_V1\n')
                self.assertEqual(mark.exists(),child.returncode==0)

    def test_required_write_refuses_missing_or_nonpipe_fd_before_boot_work(self):
        with tempfile.TemporaryFile() as regular:
            for fd in (None,regular.fileno()):
                cmd=['bash',str(BOOT),'--write','--open-gates','--require-preparation']
                if fd is not None:cmd += ['--latency-prepared-fd',str(fd)]
                child=subprocess.run(cmd,pass_fds=(() if fd is None else (fd,)),capture_output=True,text=True,timeout=2)
                self.assertNotEqual(child.returncode,0)
                self.assertIn('safe3',child.stderr)

    def test_production_deadlines_cannot_be_extended(self):
        for args in (['--hold-s','601'],['--command-s','31'],['--arrival-s','3001'],['--setup-wait','6']):
            child=subprocess.run([sys.executable,'-B',str(Path(P.__file__)),'--out','unused','--boot-script','unused',*args,'--','--write','--open-gates'],capture_output=True,text=True)
            self.assertNotEqual(child.returncode,0)
            self.assertIn('reviewed bounds',child.stderr)

    def test_closed_probe_cannot_be_combined_with_open_gates(self):
        read,write=os.pipe()
        try:
            child=subprocess.run(['bash',str(BOOT),'--write','--eink','--waveform','unused','--template','unused',
                '--open-gates','--ghost-closed-probe','--latency-prepared-fd',str(read)],pass_fds=(read,),capture_output=True,text=True,timeout=2)
            self.assertNotEqual(child.returncode,0);self.assertIn('closed probe forbids',child.stderr)
        finally:os.close(read);os.close(write)



if __name__=='__main__':unittest.main()
