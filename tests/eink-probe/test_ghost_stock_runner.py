import fcntl
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/eink-probe/sessions'))
import ghost_stock_runner as H


class Supervisor(unittest.TestCase):
    def test_default_recovery_only_does_not_create_output_or_probe_health(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.fixture(d)
            self.assertEqual(H.run_stock(t,['fake-unused-transport'],Path(d,'usb.lock'),0,0,start_session=False),0)
            self.assertFalse(t.series.exists());self.assertFalse(Path(d,'health.calls').exists())

    def fixture(self, root, slot='_a'):
        root=Path(root);wt=root/'wt';(wt/'scripts/eink-probe/sessions').mkdir(parents=True)
        (wt/'scripts/a9-health.sh').write_text('echo health >> '+shlex.quote(str(root/'health.calls'))+"\necho '{\"slot\":\""+slot+"\",\"boot_completed\":\"1\"}'\n")
        worker=root/'session.py'
        worker.write_text("import json,pathlib,sys\nr=pathlib.Path(__file__).parent\na=sys.argv[1:]\n(r/'session-args.json').write_text(json.dumps(a))\np=pathlib.Path(a[a.index('--out')+1]);p.mkdir();(p/'stock-reps.json').write_text('{}')\n")
        (wt/'scripts/eink-probe/sessions/ghost-stock.sh').write_text('exec '+shlex.join([sys.executable,str(worker)])+' "$@"\n')
        class Tuple:
            marker=root/'pending.json';series=root/'series'
            d={'artifacts':{'calibration':{'path':'unused'}}}
            def args(self):return ['--matched-source','--launcher-component','com.android.launcher3/.Launcher',
                '--source-viewer','["am","start","-n","viewer/.Image","-d","{uri}"]',
                '--source-roi','32,100,792,1560','--wallpaper-roi','32,100,792,1560',
                '--recovery-marker',str(self.marker),'--ghost-reps','12','--settings-start','warm']
            def completion(self,out):
                assert (out/'run/stock-reps.json').exists()
                return {'fixture':'completion checked separately by tuple tests'}
        t=Tuple();t.wt=wt
        return t

    def test_success_forwards_every_matched_flag_and_writes_bound_completion(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.fixture(d)
            self.assertEqual(H.run_stock(t,['fake-unused-transport'],Path(d,'usb.lock'),1,1),0)
            args=json.loads(Path(d,'session-args.json').read_text())
            self.assertEqual(args[-len(t.args()):],t.args())
            self.assertEqual(json.loads((t.series/'stock/done').read_text()),t.completion(t.series/'stock'))

    def test_usb_lock_holder_survives_and_no_health_or_session_runs(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.fixture(d);lock=Path(d,'usb.lock')
            with lock.open('a') as holder:
                fcntl.flock(holder,fcntl.LOCK_EX)
                with self.assertRaisesRegex(RuntimeError,'USB lock held'):
                    H.run_stock(t,['fake-unused-transport'],lock,0,0)
                self.assertFalse(holder.closed)
            self.assertFalse(Path(d,'health.calls').exists());self.assertFalse(t.series.exists())

    def test_wrong_slot_never_starts_child_or_marks_done(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.fixture(d,'_b')
            with self.assertRaisesRegex(RuntimeError,'not healthy'):
                H.run_stock(t,['fake-unused-transport'],Path(d,'usb.lock'),0,0)
            self.assertFalse(Path(d,'session-args.json').exists());self.assertFalse((t.series/'stock/done').exists())

    def test_existing_output_is_refused_before_health_without_changing_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.fixture(d);out=t.series/'stock';out.mkdir(parents=True)
            (out/'stock.log').write_text('preserve crashed evidence')
            with self.assertRaisesRegex(RuntimeError,'already ran'):
                H.run_stock(t,['fake-unused-transport'],Path(d,'usb.lock'),0,0)
            self.assertEqual((out/'stock.log').read_text(),'preserve crashed evidence')
            self.assertFalse(Path(d,'health.calls').exists())


if __name__=='__main__':unittest.main()
