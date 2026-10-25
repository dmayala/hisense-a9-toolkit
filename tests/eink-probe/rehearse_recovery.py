"""Actual SIGKILL, fake filesystem-backed transport; never calls device tools."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/eink-probe/sessions'))
import ghost_stock as S

OLD = 'bitpit.launcher/.ui.HomeActivity'
NEW = 'com.android.launcher3/.Launcher'
FAKE = r'''import json,os,pathlib,shlex,signal,sys
r=pathlib.Path(__file__).resolve().parent
s=json.loads((r/'state.json').read_text());a=sys.argv[1:]
with (r/'calls.jsonl').open('a') as f:f.write(json.dumps(a)+'\n')
def save():
 p=r/'state.tmp';p.write_text(json.dumps(s));os.replace(p,r/'state.json')
if a[0]=='push':
 d=json.loads((r/'pending.json').read_text())
 assert a[2] in d['pending_paths'] and a[2] in d['owned_paths']
 s['files'][a[2]]='partial PNG';save()
 os.kill(os.getppid(),signal.SIGKILL)
 sys.exit(7)
assert a[0]=='shell'
c=a[1];w=shlex.split(c)
if c=='getprop ro.boot.slot_suffix; getprop sys.boot_completed':print('_a\n1')
elif 'resolve-activity' in c:print(s['home'])
elif w[:3]==['cmd','package','set-home-activity']:s['home']=w[3];save()
elif w[:3]==['rm','-f','--']:s['files'].pop(w[3].rstrip(';'),None);save();print('REMOVED')
elif w[:3]==['if','[','-e']:
 present=w[3] in s['files']
 print(('EXISTS' if present else 'ABSENT') if 'echo ABSENT' in c else ('PRESENT' if present else 'REMOVED'))
else:raise AssertionError('outside fake allowlist')
'''


def fixture(root):
    root = Path(root)
    root.mkdir(parents=True)
    (root/'state.json').write_text(json.dumps({'home':OLD,'files':{'/data/local/tmp/unrelated.png':'keep'}}))
    (root/'fake-transport.py').write_text(FAKE)
    sessions = str(Path(S.__file__).parent)
    worker = """import argparse,sys
from pathlib import Path
sys.path.insert(0,%r)
import ghost_matched as M
r=Path(__file__).resolve().parent
out=Path(sys.argv[sys.argv.index('--out')+1]) if '--out' in sys.argv else r/'crashed-run'
a=argparse.Namespace(matched_source=True,launcher_component=%r,
 source_viewer='["am","start","-n","verified.viewer/.Image","-d","{uri}"]',
 source_roi='32,100,792,1560',wallpaper_roi='32,100,792,1560',settings_start='warm',
 ghost_reps=12,source_wait_ms=4000,recovery_marker=str(r/'pending.json'))
M.MatchedRun(a,[sys.executable,str(r/'fake-transport.py')],out,print,stock=True).start()
raise AssertionError('worker should have been killed')
""" % (sessions,NEW)
    (root/'worker.py').write_text(worker)
    return [sys.executable,str(root/'fake-transport.py')]


def run_crash_replay(root):
    root = Path(root)
    adb = fixture(root)
    p = subprocess.run([sys.executable,'-B',str(root/'worker.py')],capture_output=True,text=True,timeout=15)
    assert p.returncode == -9
    before = json.loads((root/'state.json').read_text())
    marker = json.loads((root/'pending.json').read_text())
    assert before['home'] == NEW and len(marker['pending_paths']) == 1
    assert marker['pending_paths'][0] in before['files']
    before_calls = len((root/'calls.jsonl').read_text().splitlines())
    reached = []
    def construct(_args):
        state = json.loads((root/'state.json').read_text())
        assert state == {'home':OLD,'files':{'/data/local/tmp/unrelated.png':'keep'}}
        assert not (root/'pending.json').exists()
        reached.append(True)
        raise RuntimeError('rehearsal ends after verified recovery, before preflight')
    argv = ['--adb',__import__('shlex').join(adb),'--out',str(root/'new-run'),'--calib','unused',
            '--dry-run','--recovery-marker',str(root/'pending.json')]
    with mock.patch.object(S,'Stock',side_effect=construct):
        rc = S.main(argv)
    assert rc == 3 and reached == [True] and not (root/'new-run').exists()
    new_calls = [json.loads(s) for s in (root/'calls.jsonl').read_text().splitlines()[before_calls:]]
    assert new_calls[0] == ['shell','getprop ro.boot.slot_suffix; getprop sys.boot_completed']
    assert 'set-home-activity' in new_calls[1][1] and 'resolve-activity' in new_calls[2][1]
    audit = json.loads((root/'pending.json.last.json').read_text())
    assert audit['home_restored'] is True and audit['pending_paths'] == []
    result = dict(worker_rc=-9,restart_rc=rc,home_restored=True,unrelated_file_preserved=True,
                  partial_png_removed=True,recovery_preceded_stock_constructor=True,marker_cleared_after_verified_readback=True,
                  note='restart intentionally stopped before preflight; no new session started')
    (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('F2 SIGKILL/restart replay PASS: '+json.dumps(result))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser();p.add_argument('output')
    run_crash_replay(p.parse_args().output)
