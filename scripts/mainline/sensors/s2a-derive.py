#!/usr/bin/env python3
"""Derive the S2a target from the active S1 target: one bounded probe step after enumeration.

Everything else (staging, listener, guardian, health, stop, containment, host) is the
S1 active derivation unchanged. A failed or incomplete probe fails the run closed
(S1-FAILED-OFFLINE through the normal S1 containment); the probe's own log is printed
into the host run.log either way.
"""
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACTIVE = SourceFileLoader('s1_active_derive', str(HERE / 's1-active-derive.py')).load_module()
once = ACTIVE.once

PROBE_STEP = '''        grep -qE '^S1-POLL-RESULT found=1 attempts=[1-9][0-9]* elapsed_ms=[0-9]+$' "$B/s1-observation.log" || fail s1-incomplete-observer
    else
        cat "$B/s1-observation.log"; fail s1-no-enumeration-proof
    fi
    s1_health
    # S2a: first real samples. SUID lookups, attributes, ALS 10 s, accel 5 s, disable; QRTR only,
    # no FastRPC, files or persist from the probe. It bounds itself to ~45 s; 60 s is the outer bound.
    [ -x "$B/s2-sns-probe" ] || fail s2-missing-probe
    echo "S2A-PROBE-START uptime=$(now)"
    if timeout 60 "$B/s2-sns-probe" 400 > "$B/s2-probe.log" 2>&1; then
        cat "$B/s2-probe.log"
        grep -qx 'S2-PROBE-PASS' "$B/s2-probe.log" || fail s2-probe-incomplete
    else
        cat "$B/s2-probe.log"; fail s2-probe-failed
    fi
    echo "S2A-PROBE-END uptime=$(now)"
'''


def derive():
    sources = ACTIVE.derive()
    target = sources['s1-target.sh']
    old = '''        grep -qE '^S1-POLL-RESULT found=1 attempts=[1-9][0-9]* elapsed_ms=[0-9]+$' "$B/s1-observation.log" || fail s1-incomplete-observer
    else
        cat "$B/s1-observation.log"; fail s1-no-enumeration-proof
    fi
'''
    target = once(target, old, PROBE_STEP)
    # The probe and HOLD push the stop past the S1 listener's own 180 s lifetime cap (fix6: stop 91 s after
    # the listener started; S2a adds up to 60 s). S2a runs the listener in the reviewed service mode instead:
    # no lifetime cap, a 4096-requests-per-60 s budget; the guardian still owns stop and join.
    target = once(target, '"$B/s1-listener" --serve "$BOOT" "$TUPLE"', '"$B/s1-listener" --service "$BOOT" "$TUPLE"')
    target = once(target, '# S1 ACTIVE: guardian-owned upstream FastRPC listener; RAM registry delivery required.',
                  '# S1 ACTIVE: guardian-owned upstream FastRPC listener; RAM registry delivery required.\n'
                  '# S2a: plus one bounded SNS probe (s2-sns-probe) after enumeration; a probe failure fails the run closed.')
    # Review 79j N4: the HOLD-time /dev/snd scan forked one ls + one cat per process (~45 s per scan on the
    # phone, attempt 7: the 407 -> 460 s health call). One ls over all fd directories first; if it reports ANY
    # error (a process exited mid-scan, or a directory is unreadable) the original per-process loop runs
    # unchanged, so the fail-closed semantics (unreadable refuses, vanished skips, empty refuses) are kept.
    target = once(target, '''        scanned=0; : > snd.fd
        for d in "$P"/[0-9]*/fd; do
            [ -d "$d" ] || continue
            tries=0; got=0
            while [ "$tries" -lt 3 ]; do
                tries=$((tries+1))
                if ls -l "$d" > snd.one 2>/dev/null; then got=1; break; fi
                [ -d "$d" ] || { got=2; break; }
            done
            [ "$got" != 0 ] || fail snd-scan-unreadable
            [ "$got" = 1 ] || continue
            cat snd.one >> snd.fd; scanned=$((scanned+1))
        done
''', '''        scanned=0; : > snd.fd
        if ls -l "$P"/[0-9]*/fd > snd.fd 2>/dev/null && scanned=$(grep -c '/fd:$' snd.fd) && [ "$scanned" -gt 0 ]; then
            :   # one-shot scan of every fd directory succeeded
        else
        scanned=0; : > snd.fd
        for d in "$P"/[0-9]*/fd; do
            [ -d "$d" ] || continue
            tries=0; got=0
            while [ "$tries" -lt 3 ]; do
                tries=$((tries+1))
                if ls -l "$d" > snd.one 2>/dev/null; then got=1; break; fi
                [ -d "$d" ] || { got=2; break; }
            done
            [ "$got" != 0 ] || fail snd-scan-unreadable
            [ "$got" = 1 ] || continue
            cat snd.one >> snd.fd; scanned=$((scanned+1))
        done
        fi
''')
    return {**sources, 's1-target.sh': target}


if __name__ == '__main__':
    import sys
    sys.stdout.write(derive()['s1-target.sh'])
