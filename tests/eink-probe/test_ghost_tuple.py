"""Complete source seal and paired-reference admission, no device commands."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/eink-probe/sessions'))
import ghost_tuple as T
import ghost_matched as M
from test_ghost_matched_core import NEW_HOME, VIEWER


class Seal(unittest.TestCase):
    def test_actual_boot_rule_and_runner_use_effective_1500_second_cap(self):
        import re,shlex
        source=Path(__file__).resolve().parents[2]
        runner=(source/'scripts/eink-probe/sessions/r0-ghost.sh').read_text()
        args=shlex.split(re.search(r'^ARGS="(.*)"$',runner,re.M).group(1))
        hold=args[args.index('--hold')+1];gate=args[args.index('--gate-secs')+1]
        boot=(source/'scripts/mainline/android/android-boot.sh').read_text()
        rule,=[s for s in boot.splitlines() if s.startswith('[ "$HOLD" -gt 0 ]') and 'GATE_SECS=$HOLD' in s]
        def effective(h):
            return subprocess.check_output(['bash','-c','HOLD="$1"; GATE_SECS="$2"; OPEN_GATES=1\n'+rule+'\nprintf "%s" "$GATE_SECS"','fixture',h,gate],text=True)
        self.assertEqual(effective(hold),'1500')
        self.assertEqual(effective('1800'),'1800')

    def test_device_binding_is_digest_only_and_fails_without_transport(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.make(d);t.d['device_serial_sha256']=__import__('hashlib').sha256(b'fixture-device').hexdigest()
            t.verify_device('fixture-device')
            with self.assertRaisesRegex(RuntimeError,'device binding'):t.verify_device('different-device')

    def make(self, root):
        root=Path(root);wt=root/'wt';wt.mkdir(parents=True)
        files=['scripts/eink-probe/sessions/ghost_matched.py','scripts/eink-probe/sessions/ghost_recovery.py',
               'scripts/eink-probe/matched_source.py']
        source=Path(__file__).resolve().parents[2]
        for f in files:
            p=wt/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((source/f).read_bytes())
        def git(*args):
            return subprocess.check_output(['git','-c','commit.gpgsign=false','-c','gc.auto=0',
                '-c','user.name=Fixture','-c','user.email=fixture@example.invalid',*args],cwd=wt,text=True).strip()
        git('init','-q');git('add','.');git('commit','-qm','fixture')
        artifact=root/'payload';artifact.write_text('fixed fake artifact; no tool execution')
        p=dict(launcher_component=NEW_HOME,viewer_argv=VIEWER,source_roi='32,100,792,1560',
               wallpaper_roi='32,100,792,1560',source_wait_ms=4000,reps=12,pg_settle_ms=100,settings_start='warm')
        config=dict(protocol='matched-ghost-v2',commit=git('rev-parse','HEAD'),worktree=str(wt),
            series_dir=str(root/'series'),recovery_marker=str(root/'pending.json'),profile=p,
            source_files={f:T.sha(wt/f) for f in files},
            artifacts={k:{'path':str(artifact),'sha256':T.sha(artifact)} for k in ('boot','vendor','module','calibration','waveform','template','python')},
            decision_rules='89k-descriptive-by-source-no-pooled-P2')
        path=root/'execution.json';path.write_text(json.dumps(config))
        return T.ExecutionTuple(path).verify()

    def reference(self,t):
        out=t.series/'stock';(out/'run').mkdir(parents=True)
        roi=list(map(int,t.profile['source_roi'].split(',')))
        receipt=dict(protocol='matched-source-v1',home_restored=True,cleanup_failures=[],token='a'*32,
                     requested_home=NEW_HOME,viewer_profile=VIEWER,source_roi=roi,wallpaper_roi=roi)
        reps=[]
        for k,sid in enumerate(['launcher','dark','white','white','dark','launcher']*2,1):
            s=dict(id=sid,verified=True,launcher_component=NEW_HOME,roi=roi,wallpaper_roi=roi)
            if sid!='launcher':s.update(fixture_sha256=M.F.source_metadata(sid)['sha256'],viewer_profile=VIEWER)
            reps.append(dict(rep=k,source_id=sid,clean=True,source=s))
        (out/'run/matched-source.json').write_text(json.dumps(receipt))
        (out/'run/stock-reps.json').write_text(json.dumps({'reps':reps}))
        (out/'run/source-baselines.json').write_text('{}')
        (out/'run/home-recovery-audit.json').write_text(json.dumps(dict(state='recovered',home_restored=True,pending_paths=[],token='a'*32)))
        return out

    def test_complete_source_and_profile_verified_and_common_flags_frozen(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.make(d)
            a=t.args()
            self.assertEqual(a[a.index('--ghost-reps')+1],'12')
            self.assertEqual(a[a.index('--source-viewer')+1],json.dumps(VIEWER,separators=(',',':')))
            self.assertEqual(t.environment()['REPS'],'12')
            self.assertEqual(t.environment()['CAP'],'60')

    def test_untracked_source_or_modified_artifact_refused(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.make(d);(t.wt/'unexpected').write_text('unreviewed')
            with self.assertRaisesRegex(RuntimeError,'not clean'):t.verify()
            (t.wt/'unexpected').unlink()
            Path(t.d['artifacts']['boot']['path']).write_text('changed')
            with self.assertRaisesRegex(RuntimeError,'artifact hash'):t.verify()

    def test_incomplete_manifest_and_arm_change_refused(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.make(d);t.d['source_files'].pop(next(iter(t.d['source_files'])))
            with self.assertRaisesRegex(RuntimeError,'incomplete'):t.verify()
            t=self.make(Path(d,'second'));t.profile['pg_settle_ms']=0
            with self.assertRaisesRegex(RuntimeError,'reviewed arms'):t.verify()

    def test_valid_completion_is_bound_to_tuple_and_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.make(d);out=self.reference(t)
            (out/'done').write_text(json.dumps(t.completion(out)))
            t.stock_check()
            record=json.loads((out/'done').read_text());record['tuple_sha256']='0'*64
            (out/'done').write_text(json.dumps(record))
            with self.assertRaisesRegex(RuntimeError,'does not match'):t.stock_check()

    def test_later_home_probe_audit_does_not_invalidate_stock_capture(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.make(d);out=self.reference(t)
            (out/'done').write_text(json.dumps(t.completion(out)))
            Path(str(t.marker)+'.last.json').write_text(json.dumps({'token':'later HOME probe'}))
            t.stock_check()

    def test_legacy_done_and_truncated_stock_never_admit(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.make(d);out=self.reference(t);(out/'done').write_text('rc0 historical stock\n')
            with self.assertRaises(ValueError):t.stock_check()
            p=out/'run/stock-reps.json';r=json.loads(p.read_text());r['reps'].pop();p.write_text(json.dumps(r))
            with self.assertRaisesRegex(RuntimeError,'12-attempt'):t.completion(out)

    def test_wrong_viewer_fixture_and_missing_source_metadata_refused(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.make(d);out=self.reference(t);p=out/'run/stock-reps.json';initial=json.loads(p.read_text())
            for field,value in [('viewer_profile',['wrong']),('fixture_sha256','0'*64),('verified',False)]:
                r=copy.deepcopy(initial);r['reps'][1]['source'][field]=value;p.write_text(json.dumps(r))
                with self.assertRaisesRegex(RuntimeError,'mismatch'):t.completion(out)

    def test_home_failure_pending_marker_or_wrong_recovery_token_refused(self):
        with tempfile.TemporaryDirectory() as d:
            t=self.make(d);out=self.reference(t);p=out/'run/matched-source.json';r=json.loads(p.read_text())
            r['home_restored']=False;p.write_text(json.dumps(r))
            with self.assertRaisesRegex(RuntimeError,'not verified'):t.completion(out)
            r['home_restored']=True;p.write_text(json.dumps(r));t.marker.write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'pending HOME'):t.completion(out)
            t.marker.unlink();p=out/'run/home-recovery-audit.json';r=json.loads(p.read_text());r['token']='b'*32;p.write_text(json.dumps(r))
            with self.assertRaisesRegex(RuntimeError,'audit mismatch'):t.completion(out)


if __name__=='__main__':unittest.main()
