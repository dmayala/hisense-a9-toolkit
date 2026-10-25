"""Sealed stock supervisor: persistent recovery precedes every device action."""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import uuid

from ghost_recovery import HomeRecovery, atomic_json, shield_cleanup, canonical
from ghost_tuple import ExecutionTuple


def public_reason(error):
    # Timeout/CalledProcessError reprs include argv (and the device identifier).
    if isinstance(error,subprocess.TimeoutExpired):
        return 'command timed out after %s seconds' % error.timeout
    if isinstance(error,subprocess.CalledProcessError):
        return 'command failed with rc=%s' % error.returncode
    text=str(error)
    for name in ('A9_SERIAL','ANDROID_SERIAL'):
        value=os.environ.get(name)
        if value:text=text.replace(value,'[redacted]')
    text=re.sub(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|\b(?:\d{1,3}\.){3}\d{1,3}\b','[redacted]',text)
    return ' '.join(text.split())[:600] or type(error).__name__


def refused(reason, log=None):
    message='STOCK REFUSED/FAILED: '+public_reason(reason)
    if log is not None:message+='; evidence='+str(log)
    print(message,file=sys.stderr,flush=True)


def child_failure(log, child_rc):
    # Forward bounded STOP reasons, never dump command vectors or raw replies.
    reasons=[]
    try:
        with Path(log).open('rb') as stream:
            stream.seek(max(0,Path(log).stat().st_size-65536))
            for line in stream.read().decode(errors='replace').splitlines():
                for marker in ('STOP: ','RECOVERY/SESSION FAILED: ','refusing: '):
                    if marker in line:
                        reasons.append(line.split(marker,1)[1]);break
    except OSError:
        pass
    detail='; '.join(reasons[-4:]) if reasons else 'no STOP reason retained; inspect child evidence'
    refused('capture child rc=%s: %s' % (child_rc,detail),log)


def home_probe(t,adb,out):
    rc,original,changed=3,None,False
    matched=None
    cancelled=False
    old={}
    def record_signal(signum,_frame):
        nonlocal rc,cancelled
        rc=3;cancelled=True
    def interrupted(signum,_frame):
        record_signal(signum,_frame)
        raise RuntimeError('stock HOME probe signal '+str(signum))
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):old[sig]=signal.signal(sig,interrupted)
    try:
        with HomeRecovery(t.marker,adb) as recovery:
            recovery.cleanup_signal_handler=record_signal
            try:
                if recovery.recover_pending():raise RuntimeError('pending recovery')
                if cancelled:raise RuntimeError('probe cancelled after recovery')
                if recovery.shell('getprop ro.boot.slot_suffix; getprop sys.boot_completed').splitlines()!=['_a','1']:
                    raise RuntimeError('stock HOME probe requires booted slot A')
                if 'viewer_argv' in t.profile:
                    # Sealed --probe exercises the same staging/launch/pixel
                    # admission as capture, with no reps or force-clear calls.
                    import ghost_matched as M
                    p=t.profile
                    a=argparse.Namespace(matched_source=True,launcher_component=p['launcher_component'],
                        source_viewer=p['viewer_argv'],source_roi=p['source_roi'],wallpaper_roi=p['wallpaper_roi'],
                        source_wait_ms=p['source_wait_ms'],settings_start='warm',ghost_reps=12,
                        recovery_marker=str(t.marker),expected_stock_home_package='bitpit.launcher',_home_recovery=recovery)
                    matched=M.MatchedRun(a,adb,out,lambda message:print(message,flush=True),stock=True)
                    matched.start()
                    matched.probe_viewer()
                else:
                    original=recovery.resolve_home()
                    if original.split('/',1)[0]!='bitpit.launcher':raise RuntimeError('stock HOME probe expected Niagara')
                    component=t.profile['launcher_component']
                    recovery.begin(original,component,uuid.uuid4().hex)
                    changed=True
                    recovery.shell('cmd package set-home-activity '+shlex.quote(component))
                    if canonical(recovery.resolve_home())!=canonical(component):raise RuntimeError('probe HOME readback mismatch')
                    recovery.shell('input keyevent KEYCODE_HOME')
                rc=3 if cancelled else 0
            except Exception as e:
                rc=3
                refused('HOME probe: '+public_reason(e),out/'probe.json')
            finally:
                with shield_cleanup(record_signal):
                    if matched is not None:
                        original,changed=matched.original_home,matched.home_touched
                        if matched.close():rc=3
                    if recovery.recover_pending():rc=3
                    restored=recovery.last_home_restored is True if changed else None
                    if changed and not restored:rc=3
                    atomic_json(out/'probe.json',dict(protocol='stock-home-probe-v1',rc=rc,original_home=original,
                                home_restored=restored,reps=0,opening_consumed=False,
                                viewer_verified=matched is not None and rc==0))
    finally:
        for sig,handler in old.items():signal.signal(sig,handler)
    if rc:refused('HOME probe failed or was interrupted; HOME recovery was still attempted',out/'probe.json')
    return rc


def run_stock(t, adb, lock_path, lock_s=900, health_s=1800, start_session=True,probe=False):
    child, child_rc, rc = None, None, 3
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a') as lock:
        print('STOCK phase=usb-lock wait_seconds=%s' % lock_s,flush=True)
        end = time.monotonic()+lock_s
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise RuntimeError('USB lock held; no lock stealing or holder kill')
                time.sleep(min(1,max(0,end-time.monotonic())))
        # No health probe, wake, HOME, image staging or previous-output refusal
        # may precede this recovery gate. Empty marker means no device command.
        with HomeRecovery(t.marker,adb) as recovery:
            if recovery.recover_pending():
                refused('pending HOME/file recovery blocks capture',t.marker)
                return 3
        if probe:
            out=t.series/'probes'/('stock-'+uuid.uuid4().hex);out.mkdir(parents=True)
            return home_probe(t,adb,out)
        if not start_session:
            print('recovery gate complete; no new stock capture requested')
            return 0
        if isinstance(t,ExecutionTuple):
            t.verify()
        out = t.series/'stock'
        if out.exists():
            raise RuntimeError('stock reference already ran; output is never reused')
        out.mkdir(parents=True)
        print('STOCK phase=health evidence='+str(out),flush=True)
        end = time.monotonic()+health_s
        while True:
            p = subprocess.run(['bash',str(t.wt/'scripts/a9-health.sh'),'--json'],
                               capture_output=True,text=True,timeout=30,stdin=subprocess.DEVNULL,
                               env=dict(os.environ,A9_HOME=str(t.wt)))
            try:
                d = json.loads(p.stdout)
            except ValueError:
                d = {}
            if not p.returncode and d.get('slot') == '_a' and d.get('boot_completed') == '1':
                break
            if time.monotonic() >= end:
                raise RuntimeError('slot A is not healthy; stock not started')
            time.sleep(1)
        if isinstance(t,ExecutionTuple):
            t.verify()
        def interrupted(signum, _frame):
            raise RuntimeError('stock supervisor signal '+str(signum))
        old = {s:signal.signal(s,interrupted) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
        try:
            a = t.d['artifacts']
            cmd = ['bash',str(t.wt/'scripts/eink-probe/sessions/ghost-stock.sh'),'--go',
                   '--adb',shlex.join(adb),'--out',str(out/'run'),'--calib',a['calibration']['path'],
                   '--post-s','3.5','--clean-retries','2','--clean-threshold','0.02','--budget-s','900',
                   '--stop-file',str(out/'STOP'),*t.args()]
            with (out/'stock.log').open('w') as log:
                print('STOCK phase=capture evidence='+str(out/'stock.log'),flush=True)
                child = subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
                                         cwd=t.wt,env=dict(os.environ,A9_HOME=str(t.wt),PYTHONDONTWRITEBYTECODE='1'),
                                         start_new_session=True)
                rc = child_rc = child.wait(timeout=1100)
        except Exception as e:
            refused(e,out/'stock.log'); rc = 3
        finally:
            with shield_cleanup():
                if child is not None:
                    # Only our session's process group; never a USB lock owner.
                    try:
                        os.killpg(child.pid,signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        child.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        pass
                    try:
                        os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait(timeout=5)
                try:
                    with HomeRecovery(t.marker,adb) as recovery:
                        if recovery.recover_pending():
                            refused('post-capture HOME/file recovery remains pending',t.marker)
                            rc = 3
                except Exception as e:
                    refused('recovery pending: '+public_reason(e),t.marker); rc = 3
            for s,h in old.items():
                signal.signal(s,h)
        if rc:
            if child_rc:child_failure(out/'stock.log',child_rc)
            else:refused('supervision or cleanup failed; capture cannot be accepted',out/'stock.log')
            return 3  # A recovered crash is still a failed capture, never done.
        if isinstance(t,ExecutionTuple):
            # Probe/recovery audits may subsequently replace the global last
            # audit; completion binds an immutable copy for this stock capture.
            atomic_json(out/'run/home-recovery-audit.json',json.loads(Path(str(t.marker)+'.last.json').read_text()))
        completion = t.completion(out)
        atomic_json(out/'done',completion)
        return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--tuple',default=str(Path(__file__).with_name('execution.json')))
    p.add_argument('--go',action='store_true')
    p.add_argument('--plan',action='store_true')
    p.add_argument('--probe',action='store_true',help='verify HOME and both viewer fixtures, then restore; no reps or force-clears')
    a = p.parse_args()
    if a.go and a.probe:p.error('--probe and --go are mutually exclusive')
    try:
        t = ExecutionTuple(a.tuple).verify()
        if t.path.resolve() != Path(__file__).with_name('execution.json').resolve():
            raise RuntimeError('unsealed tuple override')
        os.environ['PATH'] = t.d.get('tool_path',str(Path(t.d['artifacts']['python']['path']).parent))+':'+os.environ['PATH']
        for key in ('A9_USB_LOCKED','A9_USB_LOCK_OWNER','A9_RUN','ADB','FB','EDL','A9_HOME'):
            os.environ.pop(key,None)
        os.environ.update({k:v for k,v in t.environment().items() if k.startswith('A9_')})
        if a.plan:
            print(('stock HOME/viewer probe plan' if a.probe else 'matched stock plan')+'; no device commands: '+json.dumps(t.profile,sort_keys=True)); return 0
        serial = os.environ.get('A9_SERIAL')
        if not serial:
            raise RuntimeError('A9_SERIAL is required; no discovery command is run')
        t.verify_device(serial)
        os.environ['ANDROID_SERIAL'] = serial
        adb=t.d['artifacts']['adb']['path']
        os.environ['ADB']=adb
        os.environ['FB']=t.d['artifacts']['fastboot']['path']
        rc=run_stock(t,[adb,'-s',serial],Path(os.environ.get('XDG_RUNTIME_DIR','/tmp'))/'a9-usb.lock',start_session=a.go,probe=a.probe)
        if rc:refused('stock stage returned rc=%s; mainline must not start' % rc,t.series)
        return rc
    except Exception as e:
        refused(e); return 3


if __name__ == '__main__':
    raise SystemExit(main())
