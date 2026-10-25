#!/usr/bin/env python3
"""Offline: real C helper with fake control surfaces, host runner with fake adb.

The fake transport executes phone-side command strings in Android mksh/QEMU.
No real adb executable, device, sysfs or partition is accessed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import subprocess as sp
import tempfile
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / 'scripts/mainline/suspend'
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--sysroot', type=Path, required=True)
ap.add_argument('--qemu', type=Path, required=True)
a = ap.parse_args()
a.out.mkdir(parents=True, exist_ok=True)
wrap = ['open', 'read', 'write', 'close', 'unlink', 'glob', 'readlink', 'ioctl']
binary = a.out / 'fixture-helper'
sp.run(['gcc', '-std=c11', '-O0', '-I'+str(HERE/'include'),
        str(SOURCE/'a9ml-suspend.c'), str(HERE/'fixture.c'),
        *['-Wl,--wrap='+s for s in wrap], '-o', str(binary)], check=True)
android = [str(a.qemu), '-L', '/', str(a.sysroot/'system/bin/linker64'), str(a.sysroot/'system/bin/sh')]
toybox = [*android[:4], str(a.sysroot/'system/bin/toybox')]
env_base = {**os.environ, 'LD_LIBRARY_PATH': str(a.sysroot/'system/lib64')}


def fixture(root, mode='ok', available=True):
    files = {'/dev/kmsg':'', '/dev/watchdog0':'', '/sys/power/wake_lock':'',
             '/sys/power/state':'', '/sys/power/pm_test':'[none] freezer devices platform processors core\n',
             '/sys/module/suspend/parameters/pm_test_delay':'5\n',
             '/sys/module/qcom_wdt/parameters/a9_guard':'Y\n',
             '/sys/module/qcom_pon/parameters/beacon':'2\n',
             '/sys/module/pstore/parameters/backend':'ramoops\n',
             '/sys/module/printk/parameters/console_suspend':'N\n',
             '/data/local/tmp/.keep':'', '/boot':'11111111-1111-1111-1111-111111111111\n'}
    if not available:
        del files['/sys/power/pm_test']
    for name, text in files.items():
        p = root / name.lstrip('/'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    return {**env_base, 'FIX_ROOT':str(root), 'FIX_MODE':mode}


def direct(root, level, mode='ok', available=True, wrong_ack=False):
    env = fixture(root, mode, available)
    p = sp.Popen([binary, 'pmtest', level], env=env, stdout=sp.PIPE, stderr=sp.STDOUT)
    output = b''
    token = None
    deadline = time.monotonic()+8
    try:
        while time.monotonic()<deadline:
            if select.select([p.stdout], [], [], .1)[0]:
                chunk = os.read(p.stdout.fileno(), 65536)
                if not chunk:
                    break
                output += chunk
            ready = re.search(rb'pmtest=ready,level=\w+,token=([0-9a-f]{32})', output)
            if ready and not token:
                token = ready[1].decode()
                trace = (root/'trace').read_text()
                assert 'FREEZE' not in trace and 'DISARM' not in trace
                (root/'data/local/tmp/a9-pmtest-go').write_text(token+'\n')
            if b'PM waiting=pmtest-host-ack' in output:
                ack = root/'data/local/tmp/a9-pmtest-ack'
                if wrong_ack:
                    ack.write_text('wrong-token\n'); time.sleep(.15)
                    assert p.poll() is None and 'DISARM' not in (root/'trace').read_text()
                    wrong_ack = False
                ack.write_text(token+'\n')
            if mode == 'restore-fail' and b'cleanup-not-proven' in output:
                # Stand in for the armed hardware expiry; killing this fixture
                # process does not touch a real watchdog/device.
                assert 'DISARM' not in (root/'trace').read_text()
                p.kill()
            if p.poll() is not None:
                output += p.stdout.read()
                break
        rc = p.wait(timeout=2)
    finally:
        if p.poll() is None:
            p.kill(); p.wait()
    trace = (root/'trace').read_text() if (root/'trace').exists() else ''
    (root/'stdout').write_bytes(output)
    return rc, output.decode(), trace


results = {}
with tempfile.TemporaryDirectory(prefix='a9-pmtest-') as td:
    tmp = Path(td)
    for level in ('freezer', 'devices', 'platform'):
        rc, output, trace = direct(tmp/level, level, wrong_ack=level=='freezer')
        assert rc == 0, output
        assert trace.count('FEED\n') == 1 and trace.count('DISARM\n') == 1
        assert trace.index('GO_READ') < trace.index('FREEZE') < trace.index('SELECT none') < trace.index('ACK_READ') < trace.index('DISARM')
        assert trace.index('MAGIC_CLOSE') < trace.index('WATCHDOGD_START')
        assert 'PM result=pmtest-returned-and-host-acknowledged' in output
    results['C returning levels, GO barrier, wrong ACK rejected, disarm after correct ACK'] = 'PASS'
    for level in ('processors', 'core', 'invalid'):
        rc, output, trace = direct(tmp/level, level)
        assert rc == 2 and not trace, output
    rc, output, trace = direct(tmp/'no-debug', 'freezer', available=False)
    assert rc == 1 and 'WDT_OPEN' not in trace and 'WATCHDOGD_STOP' not in trace
    for mode in ('no-delay', 'write-fail', 'restore-fail'):
        rc, output, trace = direct(tmp/mode, 'platform', mode)
        assert rc == ( -9 if mode == 'restore-fail' else 1 ), output
        if mode=='restore-fail':
            assert 'DISARM' not in trace and 'pmtest=end' not in output
        else:
            assert 'pmtest=end' in output and trace.index('ACK_READ') < trace.index('DISARM')
    results['C unsupported/no-debug refusal and failed-return/restore behavior'] = 'PASS'

    # Real Android shell with wrappers for its external commands. QEMU user
    # cannot directly exec another ARM ELF on this host; native scripts reenter
    # QEMU for each Android applet/shell. Kernel/control surfaces stay fixtures.
    for mode in ('ok', 'no-delay', 'reset'):
        root = tmp/('host-'+mode); env = fixture(root, mode)
        x = root/'bin'; x.mkdir()
        def executable(name, text):
            p=x/name; p.write_text('#!'+shutil.which('bash')+'\n'+text+'\n'); p.chmod(0o755); return p
        shell = executable('android-sh', 'exec '+shlex.join(android)+' "$@"')
        executable('cat', 'exec '+shlex.join([*toybox,'cat'])+' "$@"')
        executable('nohup', 'exec '+shlex.join([*toybox,'nohup'])+' "$@"')
        helper = executable('helper', 'exec '+shlex.quote(str(binary))+' "$@"')
        hostlog = root/'host.log'
        fake = x/'fake-adb'
        fake.write_text('#!'+shutil.which('python3')+'\n'+'''
import os,subprocess as sp,sys
from pathlib import Path
assert sys.argv[1:]==['shell','timeout 4 nc 127.0.0.1 2324']
r=Path(os.environ['FIX_ROOT']); s=sys.stdin.read()
# Verify durable host markers precede the target-side control operation.
if '> /data/local/tmp/a9-pmtest-go' in s: assert ' BEGIN ' in (r/'host.log').read_text()
if '> /data/local/tmp/a9-pmtest-ack' in s: assert ' END ' in (r/'host.log').read_text()
s=s.replace('/proc/sys/kernel/random/boot_id',str(r/'boot'))
s=s.replace('/vendor/bin/a9ml-suspend',str(r/'bin/helper'))
s=s.replace('/system/bin/sh',str(r/'bin/android-sh'))
s=s.replace('/sys/power/pm_test',str(r/'sys/power/pm_test'))
s=s.replace('/data/local/tmp',str(r/'data/local/tmp'))
p=sp.run([str(r/'bin/android-sh'),'-s'],input=s,text=True,capture_output=True)
sys.stdout.write(p.stdout);sys.exit(p.returncode)
'''); fake.chmod(0o755)
        env.update(ADB=str(fake), PATH=str(x)+os.pathsep+env['PATH'])
        # Exercises the actual public shell entry, Python host runner and all
        # phone-side shell text; the C fixture is the launched helper.
        p=sp.run(['bash',str(SOURCE/'phone-test.sh'),'pmtest','devices','--log',str(hostlog)],env=env,capture_output=True,text=True,timeout=20)
        text=hostlog.read_text(); trace=(root/'trace').read_text()
        assert ' BEGIN ' in text, p.stdout+p.stderr+text
        assert '11111111-1111-1111-1111-111111111111' not in text
        if mode=='ok':
            assert p.returncode==0 and ' END ' in text and ' PASS ' in text, p.stdout+p.stderr+text
        elif mode=='no-delay':
            assert p.returncode==1 and ' END ' in text and ' FAIL ' in text and 'DISARM' in trace
        else:
            assert p.returncode==1 and ' RESET ' in text and ' END ' not in text and 'DISARM' not in trace, p.stdout+p.stderr+text
        shutil.copyfile(hostlog,a.out/(mode+'-host.log'))
    results['actual host/shell/C flow with fake adb: pass, failed return, reset stops without END/ACK'] = 'PASS'
    results['phone shell and cat/nohup executed in Android mksh/Toybox under QEMU'] = 'PASS'
(a.out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
