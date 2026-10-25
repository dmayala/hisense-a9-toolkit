#!/usr/bin/env python3
"""Native B commands on stdin; framework calls belong to ordinary adb shell."""
from pathlib import Path
import os
import re
import shlex
import subprocess as sp
import sys
import tempfile


def error_kind(output):
    """Fixed categories only; raw adb errors may contain private identifiers."""
    if re.search(r'offline|no devices|device .*not found', output, re.I): return 1
    if re.search(r'closed|disconnected|connection reset|broken pipe', output, re.I): return 2
    if re.search(r'permission denied|operation not permitted', output, re.I): return 3
    if re.search(r'no space left|read-only file system', output, re.I): return 4
    if re.search(r'no such file|not found', output, re.I): return 5
    return 0


def report_result(result):
    """Print native diagnostics before interpreting transport/remote status."""
    output = result.stdout.replace('\r', '')
    sys.stdout.write(re.sub(r'^__A9_ONB_RC=\d+\n?', '', output, flags=re.M))
    sys.stdout.flush()
    status = re.findall(r'^__A9_ONB_RC=(\d+)$', output, re.M)
    if result.returncode or status != ['0']:
        raise RuntimeError(f'Native B command failed: transport_rc={result.returncode} '
                           f'remote_status={status or "missing"}; see output above')


def main():
    if os.environ.get('A9_BTRY_IN_HOOK') != '1':
        raise RuntimeError('root helper must run inside the B hook')
    adb = os.environ['ADB']
    out = Path(os.environ['A9_BTRY_LOGS'])
    source = ('exec 2>&1\nset -e\n'
              'trap \'r=$?; [ "$r" -eq 0 ] || printf '
              '"A9_ONB_NATIVE_EXIT rc=%s line=%s\\n" "$r" "${LINENO:-unknown}"\' EXIT\n'
              '[ "$(id -u)" = 0 ] || exit 98\n'
              '[ "$(getprop ro.boot.slot_suffix)" = _b ] || exit 97\n'
              + sys.stdin.read() + '\n')
    remote = f'/data/local/tmp/a9-btry-root-{os.getpid()}.sh'
    launch = (f'sh {remote} </dev/null 2>&1; r=$?; rm -f {remote}; '
              'printf "\\n__A9_ONB_RC=%s\\n" "$r"; exit\n')
    kind = os.environ.get('A9_BTRY_ROOT', 'nc')
    with tempfile.TemporaryDirectory(prefix='onb-root-', dir=out) as tmp:
        script = Path(tmp)/'command.sh'
        script.write_text(source)
        if kind == 'nc':
            args = [adb, 'shell', '-T', 'timeout 60 nc 127.0.0.1 2324']
            input_text = launch
        elif kind in ('su', 'adb'):
            args = [adb, 'shell', '-T',
                    'su -c '+shlex.quote(launch) if kind == 'su' else launch]
            input_text = None
        else:
            raise ValueError('A9_BTRY_ROOT must be nc (debug default), su, or adb')
        phase = 1; code = -1; failure_output = ''
        try:
            print(f'ONB root step={os.environ.get("A9_BTRY_ROOT_STEP", "native")} '
                  f'transport={kind} push={remote}', file=sys.stderr, flush=True)
            pushed = sp.run([adb, 'push', str(script), remote], text=True,
                            stdout=sp.PIPE, stderr=sp.STDOUT, timeout=30)
            sys.stderr.write(pushed.stdout)
            failure_output = pushed.stdout
            if pushed.returncode:
                code = pushed.returncode
                raise RuntimeError(f'Native script push failed rc={pushed.returncode}')
            phase = 2
            print(f'ONB root execute: {shlex.join(args)}', file=sys.stderr, flush=True)
            r = sp.run(args, input=input_text, text=True, stdout=sp.PIPE,
                       stderr=sp.STDOUT, timeout=70)
            code = r.returncode; phase = 3
            failure_output = r.stdout
            report_result(r)
        except Exception as error:
            if isinstance(error, sp.TimeoutExpired): code = 124
            elif isinstance(error, OSError): code = 127
            # Fixed numeric protocol survives callers that suppress stderr.
            # 1=push, 2=execute/timeout, 3=completion/remote-status validation.
            print(f'A9_ONB_TRANSPORT phase={phase} rc={code}', flush=True)
            print(f'A9_ONB_ERROR kind={6 if isinstance(error, sp.TimeoutExpired) else error_kind(failure_output)}', flush=True)
            if isinstance(error, sp.TimeoutExpired) and error.stdout:
                partial = error.stdout
                sys.stdout.write(partial.decode(errors='replace') if isinstance(partial, bytes) else partial)
                sys.stdout.flush()
            failed = out/f'onb-root-failed-{os.getpid()}.sh'
            failed.write_text(source)
            first = out/'onb-root-failed-command.sh'
            if not first.exists():
                first.write_text(source)
            print(f'ONB root failure script: {failed}', file=sys.stderr, flush=True)
            raise


if __name__ == '__main__':
    main()
