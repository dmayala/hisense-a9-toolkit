"""Local-only process runner with a 15 GB floor for D2 preparation.

Only offline build tools use this runner. It never runs a device transport.
"""
import os
import json
from pathlib import Path
import shutil
import signal
import subprocess as sp
import time

FLOOR = 15_000_000_000
MINIMUM = None


def disk_gate(reserve=0):
    global MINIMUM
    free = shutil.disk_usage('/').free
    if MINIMUM is None or free < MINIMUM:
        MINIMUM = free
        log = os.environ.get('D2_DISK_LOG')
        if log:
            path = Path(log).resolve()
            assert path.is_relative_to(Path.home()/'A9-build'), 'disk log must stay private'
            with path.open('a') as f:
                f.write(json.dumps({'time':time.time(),'free':free,'floor':FLOOR})+'\n')
    if free < FLOOR + reserve:
        raise RuntimeError(f'disk admission refused: {free} free; need {FLOOR + reserve}')
    return free


def run(*args, **kw):
    disk_gate()
    argv = list(map(str, args))
    if Path(argv[0]).name in {'adb', 'fastboot', 'edl', 'nc', 'netcat'}:
        raise RuntimeError('device transport forbidden during D2 preparation')
    if any(Path(x).name == 'android-boot.sh' for x in argv) and '--offline' not in argv:
        raise RuntimeError('android-boot.sh requires --offline')
    print('+', *argv, flush=True)
    # A 1 GB early-stop margin leaves time for in-flight filesystem writes.
    # This bounds owned build processes; unrelated host writers are external.
    child = sp.Popen(argv, start_new_session=True, **kw)
    try:
        while child.poll() is None:
            if disk_gate() < FLOOR + 1_000_000_000:
                raise RuntimeError('disk reserve reached; stopping offline build')
            time.sleep(.1)
        if child.returncode:
            raise sp.CalledProcessError(child.returncode, argv)
        disk_gate()
        return child
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=3)
            except sp.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=3)
