#!/usr/bin/env python3
"""Host-owned renewal of the existing Android deadman lease, until owner end."""
import argparse
import ctypes
import math
import os
import signal
import subprocess
import time


def renew(adb, deadline, now=time.monotonic, run=subprocess.run):
    remaining = math.ceil(deadline-now())
    if remaining <= 0:
        return False
    # Ordinary shell owns the FBE lease. Use the existing vendor contract,
    # including its unchanged 21600s cap and 600s freshness after owner loss.
    source = r'''[ "$(getprop ro.boot.slot_suffix)" = _b ] || exit 1
stay=$(sed -n 's/^STAY=//p' /vendor/bin/a9ml-deadman.sh)
case "$stay" in /dev/a9-row29-hold/stay|/data/local/tmp/a9-stay) ;; *) exit 1;; esac
read -r up rest < /proc/uptime || exit 1
up=${up%%.*}
case "$up" in ''|*[!0-9]*) exit 1;; esac
cap=$((up + REMAINING + 60))
[ "$cap" -le 21600 ] || cap=21600
setprop vendor.a9ml.maxstay "$cap" || exit 1
touch "$stay"
'''.replace('REMAINING', str(remaining))
    try:
        return run([adb, 'shell', '-T', source], stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False  # Reconnect after reboot; never renew beyond owner end.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deadline', type=float, required=True)
    args = parser.parse_args()
    parent = os.getppid()
    # The existing b-try owner alone controls this keeper's lifetime.
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        return 1
    if os.getppid() != parent or parent == 1:
        return 1
    next_renewal = 0
    while not stopped and os.getppid() == parent and time.monotonic() < args.deadline:
        if time.monotonic() >= next_renewal:
            okay = renew(os.environ['ADB'], args.deadline)
            print('HOLD_LEASE renewed='+str(int(okay)), flush=True)
            next_renewal = time.monotonic() + (60 if okay else 3)
        time.sleep(min(1, max(0, args.deadline-time.monotonic())))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
