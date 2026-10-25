#!/usr/bin/env python3
"""Actual controller prepare/run/failure/stop through capture's FAKE adb.

Invoked by capture-run-test.sh inside the real capture ctl/barrier. Native
launches, mounts, tools and hardware endpoints are synthetic. Controller stages,
full offline proof, mutable gates, auditor, two guards and store I/O execute.
Never accepts a transport other than the supplied local fake-adb executable.
"""
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import sys

spec = importlib.util.spec_from_file_location('coordinator', Path(__file__).with_name('coordinator.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def prepare_fixture(f):
    proc = f.timed_fixture(proof=0)
    firmware = f.root / 'mnt/a9-wifi/firmware'
    firmware.mkdir()
    for name in ('qcom', 'ath10k', 'regulatory.db', 'regulatory.db.p7s', 'hisense'):
        (firmware / name).symlink_to('/nonexistent-fixture')
    fw = f.root / 'sys/module/firmware_class/parameters/path'
    fw.parent.mkdir(parents=True)
    fw.write_text(str(firmware) + '\n')
    modules = f.root / 'modules.wifi'
    modules.write_text('')
    (proc / 'mounts').write_text('/dev/block/ram0 /data ext4 rw,nosuid,nodev 0 0\n' +
                               f'a9-tools {f.script.parent} tmpfs rw,nosuid,nodev 0 0\n')
    runcon = f.bin / 'runcon-fixture'
    runcon.write_text('#!' + shutil.which('sh') + '\nexit 99\n')
    runcon.chmod(0o700)
    tracer = f.script.parent / 'strace'
    tracer.write_text('#!' + shutil.which('sh') + '\necho --kill-on-exit\n')
    tracer.chmod(0o700)
    trace = f.root / 'synthetic-native.trace'
    trace.write_text(m.valid_trace())
    text = f.script.read_text()
    text = text.replace('/system/bin/runcon', str(runcon)).replace("' /proc/mounts", "' " + str(proc / 'mounts'))
    text = text.replace('/vendor/lib/modules/modules.wifi', str(modules))
    text = text.replace('/vendor/bin/a9ml-wifi-firmware.sh', 'true')
    # Only native launch and environment dependencies are modeled. These are
    # not controller-stage substitutions. Other tests execute real ptrace.
    hooks = r'''
tool() { :; }
stat() {
    case "$*" in
        *'%u:%a:%h:%s'*) echo 0:600:1:13;;
        *'%u:%a'*) echo 0:700;;
        '-f -c %T '*) echo tmpfs;;
        '-L -c %t:%T /dev/block/ram0') echo 1:0;;
        '-L -c %F /dev/block/ram0') echo 'block device';;
        *) observed_stat "$@";;
    esac
}
if [ "$model_stage" = prepare ]; then
nohup() {
    [ "$3" = --worker ] || return 1
    role=$4
    case "$role" in rmtfs) tracer=101;; tqftpserv) tracer=102;; diag-router) tracer=103;; *) return 1;; esac
    echo 1234 > "$D/$role.tracer-start"
    echo "$tracer" > "$D/$role.tracer"
    cp TRACE "$D/$role.trace"
    echo "A9_WIFI_READY $role" > "$D/$role.log"
    if [ "$role" = diag-router ]; then
        printf 'A9_WIFI_LOCAL_NODE 7\nA9_WIFI_DIAG_HANDSHAKE\n' >> "$D/$role.log"
    fi
}
fi
if [ "$model_stage" = prepare ]; then
    mkdir -p PROC/$$
    cat /proc/$$/stat > PROC/$$/stat
fi
'''.replace('PROC/', str(proc) + '/').replace('TRACE ', shlex.quote(str(trace)) + ' ')
    # Save the complete observed-stat implementation, including Toybox dialect.
    text = text.replace('\nstat() {', '\nobserved_stat() {', 1)
    text = text.replace('case "${1:-}" in', hooks + '\ncase "${1:-}" in', 1)
    f.script.write_text(text)
    shutil.rmtree(f.d)


def rehearse(fake):
    assert fake.is_file() and fake.resolve() == (Path(os.environ['FAKE']) / 'bin/adb').resolve()
    assert 'fake adb requires -s' in fake.read_text()  # refuse real adb
    for failure in (False, True):
        f = m.Controller(methodName='runTest')
        f.setUp()
        try:
            prepare_fixture(f)
            def ctl(*args):
                payload = 'exec ' + shlex.join([shutil.which('sh'), str(f.script), *args]) + '\n'
                return subprocess.run([str(fake), '-s', 'FAKESERIAL', 'shell', 'nc 127.0.0.1 2324'],
                                      input=payload, env=f.env, text=True, capture_output=True,
                                      timeout=90*m.TIMEOUT_SCALE)
            r = ctl('prepare', '1'*64, '2'*64, '3'*64)
            assert r.returncode == 0 and 'PREPARED:' in r.stdout, r.stdout + r.stderr
            assert not (f.d / 'start-attempted').exists()
            if failure:
                with (f.d / 'rmtfs.trace').open('a') as out:
                    out.write('123 openat(AT_FDCWD, "/dev/block/by-name/modemst1", O_RDONLY) = -1 EPERM (denied)\n')
            r = ctl('run', 'I_AUTHORIZE_ONE_MPSS_START', 'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF', '1'*64)
            assert (r.returncode != 0) == failure, r.stdout + r.stderr
            assert (f.d / 'start-attempted').exists() != failure
            assert (f.d / 'complete').exists(), r.stdout + r.stderr
            assert f.state.read_text().strip() == 'offline'
            assert len((f.d / 'kills').read_text().splitlines()) == 9
            if failure:
                assert (f.d / 'abort').exists()
            print('FAKE-ADB actual prepare/run/' + ('preflight-failure' if failure else '180s-check') + '/stop PASS', flush=True)
        finally:
            f.doCleanups()


def main():
    if sys.argv[1:] == ['--standalone']:
        with tempfile.TemporaryDirectory(prefix='wifi-fake-adb-') as tmp:
            os.environ['FAKE'] = tmp
            fake = Path(tmp) / 'bin/adb'
            fake.parent.mkdir()
            fake.write_text('#!/bin/sh\n' +
                            '[ "$1" = -s ] && [ "$2" = FAKESERIAL ] || { echo "fake adb requires -s" >&2; exit 1; }\n' +
                            '[ "$3" = shell ] && [ "$4" = "nc 127.0.0.1 2324" ] || exit 1\nexec sh\n')
            fake.chmod(0o700)
            rehearse(fake)
    else:
        rehearse(Path(sys.argv[1]))


if __name__ == '__main__':
    main()
