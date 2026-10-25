"""Stock HOME-only probe uses real persistent recovery on a fake transport."""
import json
import os
import signal
import subprocess
import time
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/eink-probe/sessions'))
import ghost_stock_runner as H
from rehearse_recovery import fixture,OLD,NEW


class HomeProbe(unittest.TestCase):
    def test_home_cycle_success_and_failed_partial_set_always_restore_no_reps(self):
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as d:
                root=Path(d)/'fixture';adb=fixture(root)
                path=root/'fake-transport.py';text=path.read_text()
                text=text.replace("else:raise AssertionError('outside fake allowlist')", "elif w[:3]==['input','keyevent','KEYCODE_HOME']:pass\nelse:raise AssertionError('outside fake allowlist')")
                if fail:
                    text=text.replace("s['home']=w[3];save()", "s['home']=w[3];save();sys.exit(7 if w[3]==%r else 0)"%NEW)
                path.write_text(text)
                t=SimpleNamespace(marker=root/'pending.json',series=root/'series',profile={'launcher_component':NEW})
                self.assertEqual(H.run_stock(t,adb,root/'usb.lock',lock_s=0,health_s=0,start_session=False,probe=True),3 if fail else 0)
                out=next((t.series/'probes').iterdir())
                self.assertFalse((t.series/'stock').exists())
                record=json.loads((out/'probe.json').read_text())
                self.assertTrue(record['home_restored']);self.assertEqual(record['reps'],0)
                self.assertFalse(record['opening_consumed']);self.assertFalse(t.marker.exists())
                state=json.loads((root/'state.json').read_text());self.assertEqual(state['home'],OLD)
                self.assertEqual(state['files'],{'/data/local/tmp/unrelated.png':'keep'})
                calls=[json.loads(x) for x in (root/'calls.jsonl').read_text().splitlines()]
                self.assertTrue(all(x[0]=='shell' for x in calls))

    def test_timed_sigterm_during_restore_is_failed_but_home_is_verified(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'fixture';adb=fixture(root)
            path=root/'fake-transport.py';text=path.read_text().replace('import json,os,pathlib,shlex,signal,sys','import json,os,pathlib,shlex,signal,sys,time')
            line="elif w[:3]==['cmd','package','set-home-activity']:s['home']=w[3];save()"
            replacement="elif w[:3]==['cmd','package','set-home-activity']:\n s['home']=w[3];save()\n if w[3]==%r:(r/'restore.active').touch();time.sleep(.8)"%OLD
            assert line in text;text=text.replace(line,replacement)
            text=text.replace("else:raise AssertionError('outside fake allowlist')", "elif w[:3]==['input','keyevent','KEYCODE_HOME']:pass\nelse:raise AssertionError('outside fake allowlist')")
            path.write_text(text)
            worker=root/'probe-worker.py'
            sessions=str(Path(H.__file__).parent)
            worker.write_text("import sys,json\nfrom pathlib import Path\nfrom types import SimpleNamespace\nsys.path.insert(0,%r)\nimport ghost_stock_runner as H\nr=Path(__file__).parent\nt=SimpleNamespace(marker=r/'pending.json',series=r/'series',profile={'launcher_component':%r})\nraise SystemExit(H.run_stock(t,%r,r/'usb.lock',lock_s=0,health_s=0,start_session=False,probe=True))\n"%(sessions,NEW,adb))
            with (root/'console').open('w') as log:
                child=subprocess.Popen([sys.executable,'-B',str(worker)],stdout=log,stderr=subprocess.STDOUT)
                end=time.monotonic()+8
                while not (root/'restore.active').exists() and time.monotonic()<end:
                    self.assertIsNone(child.poll());time.sleep(.01)
                self.assertTrue((root/'restore.active').exists())
                child.send_signal(signal.SIGTERM)
                self.assertEqual(child.wait(timeout=8),3)
            out=next((root/'series/probes').iterdir())
            result=json.loads((out/'probe.json').read_text())
            self.assertEqual(result['rc'],3);self.assertTrue(result['home_restored'])
            self.assertFalse((root/'pending.json').exists())
            self.assertEqual(json.loads((root/'state.json').read_text())['home'],OLD)


if __name__=='__main__':unittest.main()
