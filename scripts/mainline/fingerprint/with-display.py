#!/usr/bin/env python3
"""Keep the existing E Ink admission open for the entire owned FP hook.

A9_FINGERPRINT_ONB selects an executable outer enrollment hook; the default
only observes registration. The B-try owner still bounds and restores the
session, including this process and its child in the same timeout group.
"""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess as sp
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fingerprint_display_play', HERE.parent/'play/onb.py')
play = importlib.util.module_from_spec(spec)
spec.loader.exec_module(play)


def main():
    if (os.environ.get('A9_BTRY_IN_HOOK') != '1' or
            not all(os.environ.get(k) for k in ('ADB', 'A9_BTRY_BOOT', 'A9_BTRY_LOGS'))):
        print('FAIL FP_DISPLAY_CONTEXT', flush=True)
        return 2
    child_hook = Path(os.environ.get('A9_FINGERPRINT_ONB', str(HERE/'onb.py'))).resolve()
    if child_hook == Path(__file__).resolve() or not child_hook.is_file() or not os.access(child_hook, os.X_OK):
        print('FAIL FP_DISPLAY_CHILD', flush=True)
        return 2
    display = play.Hook()
    child = None
    rc = 1
    def interrupted(*_):
        raise InterruptedError()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if display.prepare_eink():
            # Wrap the OUTER hook. Closing at the end of the registration
            # child would freeze the subsequent enrollment/unlock screens.
            print('FP_DISPLAY OPENED', flush=True)
            child = sp.Popen([str(child_hook)])
            rc = child.wait()
    except Exception as error:
        # Exceptions can contain private child output/arguments; print a class.
        print('FAIL FP_DISPLAY ' + type(error).__name__, flush=True)
        rc = 1
    finally:
        try:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except sp.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
        finally:
            if display.eink_attempted:
                try:
                    close_rc, text = display.native(play.EINK_CLOSE)
                    closed = close_rc == 0 and text.strip() == 'EINK_CLOSED'
                except Exception:
                    closed = False
                print('FP_DISPLAY ' + ('CLOSED' if closed else 'CLOSE_FAILED'), flush=True)
                if not closed:
                    rc = 1
    return rc if rc >= 0 else 1


if __name__ == '__main__':
    sys.exit(main())
