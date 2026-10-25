"""Host-only execution seal and matched stock completion binding. No device IO."""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def sha(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1048576),b''):
            h.update(b)
    return h.hexdigest()


class ExecutionTuple:
    def __init__(self, path):
        self.path = Path(path).absolute()
        self.digest = sha(self.path)
        self.d = json.loads(self.path.read_text())
        if self.d.get('protocol') != 'matched-ghost-v2':
            raise RuntimeError('not a matched-ghost-v2 execution tuple')
        self.wt = Path(self.d['worktree'])
        self.series = Path(self.d['series_dir'])
        self.profile = self.d['profile']
        self.marker = Path(self.d['recovery_marker'])

    def verify(self):
        env = dict(os.environ,GIT_OPTIONAL_LOCKS='0')
        def git(*args):
            return subprocess.check_output(['git',*args],cwd=self.wt,env=env,text=True).strip()
        if git('rev-parse','HEAD') != self.d['commit'] or git('status','--porcelain','--untracked-files=all'):
            raise RuntimeError('execution worktree is not clean at its pinned commit')
        files = self.d['source_files']
        if set(git('ls-files').splitlines()) != set(files):
            raise RuntimeError('execution source manifest is incomplete')
        for rel,digest in files.items():
            p = Path(rel)
            if p.is_absolute() or '..' in p.parts or sha(self.wt/p) != digest:
                raise RuntimeError('execution source hash mismatch: '+rel)
        for name,a in self.d['artifacts'].items():
            if sha(a['path']) != a['sha256']:
                raise RuntimeError('execution artifact hash mismatch: '+name)
        # Validate exactly the CLI that both sides will receive. This imports no
        # camera library and performs no transport/file staging.
        sys.path.insert(0,str(self.wt/'scripts/eink-probe/sessions'))
        import ghost_matched as M
        import argparse as A
        p = self.profile
        if p['reps'] != 12 or p['pg_settle_ms'] != 100 or p['settings_start'] != 'warm':
            raise RuntimeError('execution tuple changed the reviewed arms')
        a = A.Namespace(matched_source=True,launcher_component=p['launcher_component'],
                        source_viewer=json.dumps(p['viewer_argv']),source_roi=p['source_roi'],
                        wallpaper_roi=p['wallpaper_roi'],source_wait_ms=p['source_wait_ms'],
                        settings_start=p['settings_start'],ghost_reps=p['reps'],ghost_arms='100',pg_settle_ms=100)
        M.validate(a)
        if self.d.get('decision_rules') != '89k-descriptive-by-source-no-pooled-P2':
            raise RuntimeError('missing pre-capture descriptive decision rules')
        return self

    def args(self):
        p = self.profile
        return ['--matched-source','--launcher-component',p['launcher_component'],
                '--source-viewer',json.dumps(p['viewer_argv'],separators=(',',':')),
                '--source-roi',p['source_roi'],'--wallpaper-roi',p['wallpaper_roi'],
                '--source-wait-ms',str(p['source_wait_ms']),'--recovery-marker',str(self.marker),
                '--ghost-reps','12','--settings-start','warm','--expected-stock-home-package','bitpit.launcher']

    def verify_device(self, serial):
        if not serial or hashlib.sha256(serial.encode()).hexdigest() != self.d.get('device_serial_sha256'):
            raise RuntimeError('device binding mismatch; no discovery or transport permitted')

    def environment(self):
        a = self.d['artifacts']; p = self.profile
        return dict(RUNWT=str(self.wt),BOOTWT=str(self.wt),A9DIR=str(self.wt),SER=str(self.series),
                    BOOT=a['boot']['path'],BOOT_SHA=a['boot']['sha256'],VEND=a['vendor']['path'],
                    VEND_SHA=a['vendor']['sha256'],MOD_SHA=a['module']['sha256'],CALIB=a['calibration']['path'],
                    F=str(Path(a['waveform']['path']).parent),RA=str(self.wt/'scripts/eink-probe/sessions/ghost-rootadb.sh'),
                    GHOST_MATCHED_SOURCE='1',GHOST_LAUNCHER_COMPONENT=p['launcher_component'],
                    GHOST_SOURCE_VIEWER_JSON=json.dumps(p['viewer_argv'],separators=(',',':')),
                    GHOST_SOURCE_ROI=p['source_roi'],GHOST_WALLPAPER_ROI=p['wallpaper_roi'],
                    GHOST_SOURCE_WAIT_MS=str(p['source_wait_ms']),GHOST_RECOVERY_MARKER=str(self.marker),
                    GHOST_TUPLE_SHA=self.digest,REPS='12',GHOST_PYTHON=self.d['artifacts']['python']['path'],
                    GHOST_TOOL_PATH=self.d.get('tool_path',str(Path(self.d['artifacts']['python']['path']).parent)),
                    GHOST_ADB=self.d['artifacts'].get('adb',{}).get('path','adb'),
                    GHOST_FASTBOOT=self.d['artifacts'].get('fastboot',{}).get('path','fastboot'),
                    A9_GOLDEN=self.d.get('golden_dir','/home/user/a9-assets/Backups/edl_2026-10-17_102417'),
                    A9_ANDROID='/home/user/A9-build/mainline/android',A9_MAINLINE='/home/user/A9-build/mainline',
                    A9_DATA='/home/user/a9-assets',A9_BACKUPS='/home/user/a9-assets/Backups',A9_FW='/home/user/a9-assets/HLTE556N',
                    T_HEALTH='1800',T_REBOOT='900',T_BOOTB='1500',T_HOLD='600',T_LOCK='900',T_CMD='30',T_RESTORE='900',
                    POLL='3',GOAL='1000',CAP='60',GHOST_SETUP_WAIT='5',
                    GHOST_CAMERA_URL=p.get('camera_url','http://127.0.0.1:8093/stream'))

    def completion(self, out):
        out = Path(out)
        if self.marker.exists():
            raise RuntimeError('pending HOME recovery forbids stock completion')
        receipt = json.loads((out/'run/matched-source.json').read_text())
        reps = json.loads((out/'run/stock-reps.json').read_text())['reps']
        expected = ['launcher','dark','white','white','dark','launcher']*2
        if receipt.get('home_restored') is not True or receipt.get('cleanup_failures') != []:
            raise RuntimeError('stock HOME restoration/cleanup is not verified')
        if receipt.get('protocol') != 'matched-source-v1' or receipt.get('viewer_profile') != self.profile['viewer_argv']:
            raise RuntimeError('stock reference protocol/viewer mismatch')
        if receipt.get('requested_home') != self.profile['launcher_component'] or receipt.get('source_roi') != list(map(int,self.profile['source_roi'].split(','))) or receipt.get('wallpaper_roi') != list(map(int,self.profile['wallpaper_roi'].split(','))):
            raise RuntimeError('stock reference launcher/ROI mismatch')
        if [r.get('source_id') for r in reps] != expected:
            raise RuntimeError('stock reference is not the complete matched 12-attempt protocol')
        for r in reps:
            s = r.get('source',{})
            sid = r['source_id']
            if s.get('verified') is not True or s.get('id') != sid or s.get('launcher_component') != self.profile['launcher_component'] or s.get('roi') != receipt.get('source_roi') or s.get('wallpaper_roi') != receipt.get('wallpaper_roi'):
                raise RuntimeError('stock repetition source evidence mismatch')
            if sid != 'launcher':
                import matched_source as F
                if s.get('fixture_sha256') != F.source_metadata(sid)['sha256'] or s.get('viewer_profile') != self.profile['viewer_argv']:
                    raise RuntimeError('stock repetition fixture/viewer mismatch')
        valid = collections.Counter(r['source_id'] for r in reps if r.get('clean') and r.get('source',{}).get('verified') is True)
        if not all(valid[s] for s in ('launcher','dark','white')):
            raise RuntimeError('stock reference lacks a usable matched source arm')
        # Bind receipts and the authoritative recovery audit to this exact
        # source commit/profile; a historical stock/done string is insufficient.
        paths = [out/'run/stock-reps.json',out/'run/matched-source.json',out/'run/source-baselines.json',
                 out/'run/home-recovery-audit.json']
        audit = json.loads(paths[-1].read_text())
        if audit.get('state') != 'recovered' or audit.get('home_restored') is not True or audit.get('pending_paths') != [] or audit.get('token') != receipt.get('token'):
            raise RuntimeError('stock completion recovery audit mismatch')
        return dict(protocol='matched-ghost-stock-v2',tuple_sha256=self.digest,commit=self.d['commit'],
                    profile=self.profile,receipts={str(p):sha(p) for p in paths},clean_start_by_source=dict(valid))

    def stock_check(self):
        out = self.series/'stock'
        actual = json.loads((out/'done').read_text())
        if actual != self.completion(out):
            raise RuntimeError('stock completion does not match this execution tuple/receipts')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action',choices=('env','check','stock-check','device-check'))
    p.add_argument('--tuple',required=True)
    a = p.parse_args()
    try:
        t = ExecutionTuple(a.tuple).verify()
        if a.action == 'env':
            for k,v in t.environment().items():
                print(k+'='+shlex.quote(v))
        elif a.action == 'stock-check':
            t.stock_check()
        elif a.action == 'device-check':
            t.verify_device(os.environ.get('A9_SERIAL'))
        else:
            print('execution tuple verified: '+t.digest)
        return 0
    except Exception as e:
        print('EXECUTION REFUSED: '+str(e),file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
