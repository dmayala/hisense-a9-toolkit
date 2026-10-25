#!/usr/bin/env python3
"""Derive active S1 from SHA-pinned D1 and the unchanged passive S1 derivation.

The guardian exclusively launches the listener, stops ADSP before joining it,
and never certifies OFFLINE until the worker records real return/FD release.
No generated script is an execution admission.
"""
import argparse
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = SourceFileLoader('s1_passive_derive', str(HERE / 's1-derive.py')).load_module()
once = BASE.replace_once


def derive():
    sources = BASE.derive()
    target = sources['s1-target.sh']
    target = once(target, (HERE / 's1-checks.sh').read_text(), (HERE / 's1-active-checks.sh').read_text())
    target = once(target, '# S1: derived D1 lifecycle; enumeration subset only; registry transport NOT_IMPLEMENTED.',
                  '# S1 ACTIVE: guardian-owned upstream FastRPC listener; RAM registry delivery required.')
    target = once(target, 'end=$(($(now)+16))\n', 'end=$(($(now)+44))\n')
    target = once(target, 'boot-id) cat "$P/sys/kernel/random/boot_id"; exit;;\nwriter)', '''boot-id) cat "$P/sys/kernel/random/boot_id"; exit;;
listener)
    trap '' HUP INT TERM
    [ "$(cat "$O/tuple")" = "$TUPLE" ] && [ -f "$O/listener/armed" ] || exit 1
    rc=125
    if [ ! -e "$B/abort.$BOOT.$TUPLE" ] && [ ! -f "$O/sealed" ] && [ ! -f "$O/listener.launch.sealed" ]; then
        if "$B/s1-listener" --serve "$BOOT" "$TUPLE" </dev/null > "$B/s1-listener.log" 2>&1; then rc=0; else rc=$?; fi
    fi
    # A signal death (rc > 128) or a failed exec (126/127) prints nothing itself: record it in the log first.
    echo "S1-LISTENER-WORKER-EXIT rc=$rc" >> "$B/s1-listener.log" || :
    echo "$rc" > "$O/listener/done.tmp"; mv "$O/listener/done.tmp" "$O/listener/done"
    exit "$rc";;
writer)''')
    target = once(target, '    until [ -e "$B/abort.$BOOT.$TUPLE" ] || [ -f "$O/sealed" ]; do\n',
                  '    until [ -e "$B/abort.$BOOT.$TUPLE" ] || [ -f "$O/sealed" ]; do\n        s1_listener_maybe_launch || { echo abort > "$B/abort.$BOOT.$TUPLE"; break; }\n')
    target = once(target, '    # Bound the transfer to attended Power/PON2 when an executor was killed\n',
                  '    echo sealed > "$O/listener.launch.sealed"\n    # Bound the transfer to attended Power/PON2 when an executor was killed\n')
    target = once(target, '        (stop_bounded); then\n        echo OFFLINE > "$O/result.tmp"',
                  '        (stop_bounded) && s1_listener_join 12 && s1_fastrpc_drained 4; then\n        echo OFFLINE > "$O/result.tmp"')
    target = once(target, 'if (stop_bounded); then echo OFFLINE > "$O/late-result"; exit; fi',
                  'if (stop_bounded) && s1_listener_join 12 && s1_fastrpc_drained 4; then echo OFFLINE > "$O/late-result"; exit; fi')
    target = once(target, 'if (identify; gate offline); then echo OFFLINE > "$O/late-result"; exit; fi',
                  'if (identify; gate offline) && s1_listener_join 12 && s1_fastrpc_drained 4; then echo OFFLINE > "$O/late-result"; exit; fi')
    # Review 79 F3: classify a crashed ADSP distinctly (still a failure).
    target = once(target, '    *) return 1;;\n    esac\n    gate offline\n',
                  '    crashed) echo crashed > "$O/adsp-crashed"; return 1;;\n    *) return 1;;\n    esac\n    s1_note_crashed\n    gate offline\n')
    target = once(target, "    else\n        echo 'REQUIRED, UNACKNOWLEDGED:",
                  "    elif [ -f \"$O/adsp-crashed\" ]; then\n"
                  "        echo 'ADSP CRASHED (recovery disabled): failed, consumed run; listener/DMA drain not certified; AP expected alive: coordinator adb return to slot A (reboot, no retry); attended Power/PON2 then golden restore only if adb does not answer' > \"$O/fallback\"\n"
                  "        echo ADSP-CRASHED > \"$O/result.tmp\"\n"
                  "    else\n        echo 'REQUIRED, UNACKNOWLEDGED:")
    target = once(target, '        if await_guard && [ "$(cat "$O/result")" = OFFLINE ]; then\n            [ "$DONE" = 1 ] || echo S1-FAILED-OFFLINE\n',
                  '        if await_guard && [ "$(cat "$O/result")" = OFFLINE ]; then\n            [ "$DONE" = 1 ] || { s1_listener_dump run-failed; echo S1-FAILED-OFFLINE; }\n')
    target = once(target, '        else\n            echo CONTAINMENT-UNVERIFIED\n',
                  '        elif [ -f "$O/result" ] && [ "$(cat "$O/result")" = ADSP-CRASHED ]; then\n            echo S1-ADSP-CRASHED\n        else\n            echo CONTAINMENT-UNVERIFIED\n')
    target = once(target, '[ "$(cat "$O/result")" = OFFLINE ] || fail fallback-required\n    quiescent',
                  'if [ "$(cat "$O/result")" = ADSP-CRASHED ]; then echo S1-ADSP-CRASHED; fail adsp-crashed; fi\n    [ "$(cat "$O/result")" = OFFLINE ] || fail fallback-required\n    quiescent')
    target = once(target, '[ ! -d "$S/module/qcom_q6v5_pas" ] || fail prior-binding\n',
                  '[ ! -d "$S/module/qcom_q6v5_pas" ] || fail prior-binding\n[ ! -d "$S/module/fastrpc" ] || fail s1-prior-fastrpc\n')
    target = once(target, 'ps -A -o ARGS > processes\n',
                  'ps -A -o ARGS > processes\ngrep -iE "s1-listener([ /]|$)" processes && fail s1-prior-listener\n')
    target = once(target, 's1_health\nmkdir -p "$R/data/local/tmp/sensors-s1-ledger"\n',
                  '[ ! -e "$B/s1-listener.log" ] || fail s1-stale-listener-log\ns1_health\nmkdir -p "$R/data/local/tmp/sensors-s1-ledger"\n')
    target = once(target, '    [ "$ko" != qcom_q6v5_pas.ko ] || args=a9_adsp_manual_start=1\n',
                  '    [ "$ko" != qcom_q6v5_pas.ko ] || args=a9_adsp_manual_start=1\n    [ "$ko" != fastrpc.ko ] || args=a9_sensor_fastrpc=1\n')
    target = once(target, '[ ! -e "$B/abort.$BOOT.$TUPLE" ] || fail host-abort\nDONE=1\n', '''[ ! -e "$B/abort.$BOOT.$TUPLE" ] || fail host-abort
[ -f "$O/listener/done" ] && [ "$(cat "$O/listener/done")" = 0 ] || fail s1-listener-close
s1_fastrpc_drained 4 || fail s1-fastrpc-drain
grep -qE '^S1-LISTENER-CLOSED result=0 persist_reads=[1-9][0-9]* persist_writes=[0-9]+$|^S1-LISTENER-CLOSED result=0 persist_reads=0 persist_writes=[1-9][0-9]*$' "$B/s1-listener.log" || fail s1-listener-close-marker
s1_listener_delivery || fail s1-listener-delivery-marker
cat "$B/s1-listener.log"
DONE=1
''')
    target = target.replace('echo S1-ENUMERATION-ONLY-OFFLINE\n', 'echo S1-PASS-ONE-START-SENSOR-UP-SNS-RAM-REGISTRY-OFFLINE\n')
    host = sources['s1-host.py']
    host = host.replace('S1-ENUMERATION-ONLY-OFFLINE', 'S1-PASS-ONE-START-SENSOR-UP-SNS-RAM-REGISTRY-OFFLINE')
    host = host.replace('ENUMERATION-ONLY-ATTENDED-S1', 'PASS-ATTENDED-S1')
    host = once(host, "['run',t,boot],340 if not a.rehearse else 2", "['run',t,boot],400 if not a.rehearse else 2")
    host = once(host, "['contain',t,boot],18", "['contain',t,boot],48")
    host = once(host, "        (out/'result').write_text('FAILED-OFFLINE\\n' if contained else 'CONTAINMENT-UNVERIFIED\\n')\n        if not contained:\n            (out/'fallback').write_text(",
                '''        crashed=False
        if not contained:
            try: crashed='S1-ADSP-CRASHED' in (out/'run.log').read_text(errors='replace')+(out/'contain.log').read_text(errors='replace')
            except BaseException: crashed=False
        (out/'result').write_text('FAILED-OFFLINE\\n' if contained else 'ADSP-CRASHED\\n' if crashed else 'CONTAINMENT-UNVERIFIED\\n')
        if crashed:
            (out/'fallback').write_text('ADSP-CRASHED: failed, consumed run; listener/DMA drain not certified; coordinator adb return to slot A (reboot, no retry); '+owner.strip()+' owns '+receipt['fallback']+' only if adb does not answer\\n')
        elif not contained:
            (out/'fallback').write_text(''')
    return {'s1-target.sh': target, 's1-host.py': host}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.expanduser().resolve()
    if 'a9-assets' in destination.parts:
        raise ValueError('a9-assets is never writable')
    sources = derive()
    destination.mkdir(parents=True, exist_ok=True)
    for name, text in sources.items():
        target = destination / name
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        target.write_text(text)
        target.chmod(0o755)
    print('Active S1 harness generated; independent execution admission still required')
