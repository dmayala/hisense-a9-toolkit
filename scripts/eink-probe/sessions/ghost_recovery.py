"""Persistent slot-A HOME/owned-file rollback. No transport call without a marker.

The journal is outside per-run output directories. Hold its flock while a stock
session owns HOME. SIGKILL releases the lock, not the journal; the next runner
must recover it before any other device action. All marker updates are fsynced.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import tempfile
import ghost_viewer as Viewer

DEFAULT_MARKER = str(Path.home() / 'A9-build/mainline/eink-settle-f6/r0-ghost/recovery/stock-home.json')
HOME_QUERY = 'cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN'
COMPONENT = re.compile(r'[A-Za-z][A-Za-z0-9_.]*/[A-Za-z0-9_.$]+\Z')


def canonical(component):
    if not isinstance(component, str) or not COMPONENT.fullmatch(component):
        raise RuntimeError('invalid HOME component')
    p,a = component.split('/',1)
    return p+'/'+(p+a if a.startswith('.') else a)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd,name = tempfile.mkstemp(prefix=path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(name,path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def shield_cleanup(record_signal=None):
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
    try:
        for s in previous:
            signal.signal(s,record_signal if record_signal is not None else signal.SIG_IGN)
        yield
    finally:
        for s,handler in previous.items():
            signal.signal(s,handler)


class HomeRecovery:
    def __init__(self, marker, adb, log=print):
        self.marker, self.adb, self.log = Path(marker).absolute(), list(adb), log
        self.digest = hashlib.sha256(json.dumps(self.adb,separators=(',',':')).encode()).hexdigest()
        self.lock = None
        self.last_home_restored = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_args):
        self.release()

    def acquire(self):
        if self.lock is not None:
            return
        self.marker.parent.mkdir(parents=True,exist_ok=True)
        lock = open(str(self.marker)+'.lock','a')
        try:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('HOME recovery/session already active; lock not stolen')
        self.lock = lock

    def release(self):
        if self.lock is not None:
            self.lock.close(); self.lock = None

    def shell(self, text):
        try:
            p = subprocess.run(self.adb+['shell',text],capture_output=True,text=True,
                               stdin=subprocess.DEVNULL,timeout=30)
        except subprocess.TimeoutExpired:
            raise RuntimeError('recovery transport command timed out after 30 seconds') from None
        if p.returncode:
            raise RuntimeError('recovery transport command failed (rc %d)' % p.returncode)
        return p.stdout.strip()

    def resolve_home(self):
        text = self.shell(HOME_QUERY)
        lines = [s.strip() for s in text.splitlines() if COMPONENT.fullmatch(s.strip())]
        if len(lines) != 1:
            raise RuntimeError('recovery HOME readback did not resolve exactly one component')
        canonical(lines[0])
        return lines[0]

    def read(self):
        if self.lock is None:
            raise RuntimeError('HOME journal lock is not held')
        if not self.marker.exists():
            return None
        d = json.loads(self.marker.read_text())
        if d.get('version') != 1 or d.get('state') != 'pending-recovery' or d.get('transport_digest') != self.digest:
            raise RuntimeError('invalid HOME journal or different transport; recovery required')
        canonical(d.get('original_home')); canonical(d.get('requested_home'))
        token = d.get('token','')
        if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token):
            raise RuntimeError('invalid HOME recovery token')
        allowed = Viewer.paths(token)
        for key in ('owned_paths','pending_paths'):
            if not isinstance(d.get(key),list) or len(set(d[key])) != len(d[key]) or not set(d[key]) <= allowed:
                raise RuntimeError('invalid owned recovery paths; nothing removed')
        if not set(d['pending_paths']) <= set(d['owned_paths']):
            raise RuntimeError('unowned path in recovery journal')
        return d

    def begin(self, original, requested, token):
        self.acquire()
        if self.marker.exists():
            raise RuntimeError('pending HOME recovery blocks a new session')
        canonical(original); canonical(requested)
        if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token):
            raise RuntimeError('invalid HOME recovery token')
        atomic_json(self.marker,dict(version=1,state='pending-recovery',transport_digest=self.digest,
                    token=token,original_home=original,requested_home=requested,
                    owned_paths=[],pending_paths=[],home_restored=False,cleanup_failures=[]))

    def remember(self, path):
        d = self.read()
        if d is None or path not in Viewer.paths(d['token']):
            raise RuntimeError('cannot journal an unowned source path')
        if path in d['owned_paths']:
            raise RuntimeError('source path is already journaled')
        d['owned_paths'].append(path); d['pending_paths'].append(path)
        atomic_json(self.marker,d)  # MUST complete before the first byte of push.

    def recover_pending(self):
        self.acquire()
        d = self.read()
        if d is None:
            return []
        failures, restored = [], False
        with shield_cleanup(getattr(self,'cleanup_signal_handler',None)):
            try:
                if self.shell('getprop ro.boot.slot_suffix; getprop sys.boot_completed').splitlines() != ['_a','1']:
                    raise RuntimeError('slot A is not booted; no HOME/file mutation attempted')
            except Exception as e:
                failures.append('slot-A recovery unavailable: '+str(e))
            if not failures:
                setter_ok = True
                try:
                    self.shell('cmd package set-home-activity '+shlex.quote(d['original_home']))
                except Exception as e:
                    setter_ok = False; failures.append('preferred HOME restore: '+str(e))
                # Always read back, including after a failed setter. Neither an
                # exit-zero setter nor coincidental HOME equality masks failure.
                try:
                    equal = canonical(self.resolve_home()) == canonical(d['original_home'])
                    if not equal:
                        failures.append('preferred HOME verification mismatch')
                    restored = setter_ok and equal
                except Exception as e:
                    failures.append('preferred HOME readback: '+str(e))
                for path in list(d['pending_paths']):
                    try:
                        if path.startswith(Viewer.CACHE + '/'):
                            self.shell(Viewer.remove(path))
                            missing = self.shell(Viewer.absent(path, 'REMOVED', 'PRESENT'))
                        else:
                            q = shlex.quote(path)
                            self.shell('rm -f -- %s; if [ -e %s ]; then echo PRESENT; else echo REMOVED; fi' % (q,q))
                            missing = self.shell('if [ -e %s ]; then echo PRESENT; else echo REMOVED; fi' % q)
                        if missing != 'REMOVED':
                            raise RuntimeError('temporary source still present')
                        d['pending_paths'].remove(path)
                    except Exception as e:
                        failures.append('source cleanup: '+str(e))
            self.last_home_restored = restored
            d.update(home_restored=restored,cleanup_failures=failures)
            atomic_json(self.marker,d)
            self.log('home restored: '+('yes' if restored else 'no'))
            if not failures and restored and not d['pending_paths']:
                audit = dict(d,state='recovered')
                atomic_json(str(self.marker)+'.last.json',audit)
                self.marker.unlink(); sync_directory(self.marker.parent)
                self.log('pending HOME/file recovery: complete')
            else:
                self.log('RECOVERY PENDING: new sessions forbidden: '+'; '.join(failures))
        return failures


def main():
    ap = argparse.ArgumentParser(description='Recover pending stock HOME/files before any other device action')
    ap.add_argument('--adb',required=True)
    ap.add_argument('--marker',default=DEFAULT_MARKER)
    a = ap.parse_args()
    r = HomeRecovery(a.marker,shlex.split(a.adb))
    try:
        return 3 if r.recover_pending() else 0
    except Exception as e:
        print('RECOVERY PENDING: '+str(e)); return 3
    finally:
        r.release()


if __name__ == '__main__':
    raise SystemExit(main())
