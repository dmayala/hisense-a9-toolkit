#!/usr/bin/env python3
"""Coordinator-only, one returning s2idle pm_test with fsynced HOST markers.

No partition writes, reboot, retry or stage advancement. The phone's /data is
RAM: BEGIN/END live here on the host, before GO/ACK respectively.
"""
import argparse
import datetime
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('level', choices=('freezer', 'devices', 'platform', 'processors', 'core'))
    ap.add_argument('--log', type=Path, help='fresh durable host log; never overwritten')
    ap.add_argument('--helper', default='/vendor/bin/a9ml-suspend',
                    choices=('/vendor/bin/a9ml-suspend', '/data/local/tmp/a9ml-suspend-pmtest'))
    args = ap.parse_args()
    if args.level in ('processors', 'core'):
        ap.error('processors/core are unsupported for s2idle; no phone action taken')
    os.umask(0o077)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = args.log or Path.home() / f'A9-build/mainline/suspend-a6l-20261020/pmtest-{stamp}-{args.level}.log'
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    adb = os.environ.get('ADB', 'adb')
    # Distinct phone logs prevent a second invocation from truncating another
    # helper's evidence. The helper itself holds the exclusive pmtest flock.
    run = secrets.token_hex(8)
    target = f'/data/local/tmp/a9-pmtest-{run}'
    with path.open('x') as log:
        def record(kind, message):
            log.write(f'{datetime.datetime.now(datetime.timezone.utc).isoformat()} {kind} {message}\n')
            log.flush()
            os.fsync(log.fileno())

        record('PREPARE', f'level={args.level}; host markers durable, phone data volatile')
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        print(f'Host log: {path}', flush=True)

        def root(body, expected_boot=None):
            script = "printf 'PMTEST_BOOT='; cat /proc/sys/kernel/random/boot_id\n"
            if expected_boot:
                script += ('if [ "$(cat /proc/sys/kernel/random/boot_id)" != '
                           + shlex.quote(expected_boot) + ' ]; then exit 1; fi\n')
            script += body + "\necho PMTEST_EOF\nexit\n"
            try:
                result = subprocess.run([adb, 'shell', 'timeout 4 nc 127.0.0.1 2324'],
                                        input=script, text=True, capture_output=True, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                return None
            lines = result.stdout.replace('\r', '').splitlines()
            if result.returncode or len(result.stdout) > 65536 or 'PMTEST_EOF' not in lines:
                return None
            boots = [s.removeprefix('PMTEST_BOOT=') for s in lines if s.startswith('PMTEST_BOOT=')]
            if len(boots) != 1 or not re.fullmatch(r'[0-9a-f-]{36}', boots[0]):
                return None
            return boots[0], lines

        pre = root(f'if [ -x {args.helper} ] && [ -r /sys/power/pm_test ]; then '
                   'echo PMTEST_AVAILABLE; else echo PMTEST_UNAVAILABLE; fi')
        if not pre or 'PMTEST_AVAILABLE' not in pre[1]:
            record('REFUSED', 'helper/pm_test/root transport missing; needs PM_DEBUG kernel; no launch')
            return 1
        boot = pre[0]  # Keep raw boot identity only in memory; never print/log it.
        child = (f'{args.helper} pmtest {args.level} > {target}.log 2>&1; '
                 f'rc=$?; printf "%s\\n" "$rc" > {target}.rc')
        launch = root('nohup /system/bin/sh -c ' + shlex.quote(child)
                      + ' < /dev/null > /dev/null 2>&1 &\necho PMTEST_LAUNCHED', boot)
        # Never resubmit a possibly delivered launch, GO or ACK.
        if not launch or launch[0] != boot:
            record('UNKNOWN', 'launch transport incomplete; no resubmit, stop and use original owner')
            return 1
        record('LAUNCHED', f'level={args.level}; waiting for armed helper')
        seen = 0
        token = None
        go = ack = end = False
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            snapshot = root(f'cat {target}.log 2>/dev/null\n'
                            f'if [ -f {target}.rc ]; then printf "PMTEST_RC="; cat {target}.rc; fi')
            if not snapshot:
                time.sleep(0.25)
                continue
            if snapshot[0] != boot:
                record('RESET', f'level={args.level}; boot changed; stop, no ACK or retry')
                return 1
            messages = [line for line in snapshot[1] if line.startswith('PM ')]
            if len(messages) < seen:
                record('UNKNOWN', 'target log shrank; no ACK or retry')
                return 1
            for message in messages[seen:]:
                record('TARGET', message)
            seen = len(messages)
            ready = [re.fullmatch(r'PM pmtest=ready,level=' + args.level
                                 + r',token=([0-9a-f]{32}),watchdog=armed30', s) for s in messages]
            ready = [m.group(1) for m in ready if m]
            if ready and not go:
                if len(ready) != 1:
                    record('UNKNOWN', 'ambiguous helper ready; no GO')
                    return 1
                token = ready[0]
                record('BEGIN', f'level={args.level}; admitted, freeze entry not yet proven')
                go = True
                root(f'printf "%s\\n" {token} > /data/local/tmp/a9-pmtest-go\necho PMTEST_GO', boot)
            endings = [s for s in messages if s.startswith(f'PM pmtest=end,level={args.level},')
                       and s.endswith(',restored=none')]
            waiting = f'PM waiting=pmtest-host-ack,token={token}' in messages if token else False
            if go and endings and waiting and not ack:
                if len(endings) != 1:
                    record('UNKNOWN', 'ambiguous helper end; no ACK')
                    return 1
                record('END', endings[0])  # fsync completes BEFORE authorizing disarm
                end = True
                ack = True
                root(f'printf "%s\\n" {token} > /data/local/tmp/a9-pmtest-ack\necho PMTEST_ACK', boot)
            terminal = [s for s in snapshot[1] if s.startswith('PMTEST_RC=')]
            if terminal:
                passed = (terminal == ['PMTEST_RC=0'] and end and ack
                          and f'PM result=pmtest-returned-and-host-acknowledged,level={args.level},pass=1' in messages
                          and 'PM watchdog=disarmed' in messages)
                record('PASS' if passed else 'FAIL', f'level={args.level}; ' + terminal[0])
                print(f'pmtest {args.level}: {"PASS" if passed else "FAIL"}; {path}', flush=True)
                return 0 if passed else 1
            time.sleep(0.25)
        record('TIMEOUT', f'level={args.level}; no completed result; stop, no ACK/retry/stage advance')
        print('No completed result; stop here and use the existing owner recovery path.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
