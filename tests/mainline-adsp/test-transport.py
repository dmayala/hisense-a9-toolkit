#!/usr/bin/env python3
"""Run the actual root protocol in Android mksh under QEMU, with fake adb/nc.

Only adb delivery, nc privilege transition, uid and boot-id are fixtures. No
device, listener, firmware, module or remoteproc is accessed.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile

ap=argparse.ArgumentParser()
ap.add_argument('--sysroot',required=True,type=Path)
ap.add_argument('--qemu',required=True,type=Path)
a=ap.parse_args()
host=Path(__file__).resolve().parents[2]/'scripts/mainline/adsp/d0-host.py'
spec=importlib.util.spec_from_file_location('d0_host',host)
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
boot='11111111-1111-1111-1111-111111111111'
with tempfile.TemporaryDirectory(prefix='adsp-transport-') as td:
    r=Path(td); x=r/'bin'; x.mkdir()
    sr=r/'sr'; shutil.copytree(a.sysroot,sr,symlinks=True)
    android=[str(a.qemu),'-L','/',str(sr/'system/bin/linker64'),str(sr/'system/bin/sh')]
    # Native wrappers permit Android mksh to exec without ARM binfmt support.
    for name,body in {
        'id':'printf "%s\\n" "$FIX_UID"',
        'cat':'exec '+shlex.quote(shutil.which('cat'))+' "$@"',
        'nc':'test "$*" = "127.0.0.1 2324" || exit 9\nexport FIX_UID=$ROOT_UID\nexec '+shlex.join(android)+' -s',
    }.items():
        p=x/name; p.write_text('#!'+shutil.which('bash')+'\n'+body+'\n'); p.chmod(0o755)
    target=r/'target.sh'
    target.write_text('''test "$1" = run && test "$2" = "argument with spaces" || exit 9
# Must see EOF: consuming the wrapper's status/exit lines would break protocol.
if read -r stolen; then echo "stole protocol input: $stolen"; exit 8; fi
printf 'target output\\n'
printf 'target diagnostic\\n' >&2
exit "$TARGET_RC"
''')
    (r/'boot').write_text(boot+'\n')
    adb=r/'fake-adb'
    adb.write_text('#!'+shutil.which('python3')+'\n'+'''
import json,os,subprocess as sp,sys
from pathlib import Path
assert sys.argv[1:3]==['-t','4']
if sys.argv[3:]==['get-serialno']: print('fixture'); sys.exit(0)
assert sys.argv[3:5]==['shell','-T']
assert sys.argv[5] in ('nc 127.0.0.1 2324','/system/bin/sh -s')
root=Path(os.environ['FIX_ROOT']); android=json.loads(os.environ['ANDROID'])
script=sys.stdin.buffer.read().replace(b'/proc/sys/kernel/random/boot_id',str(root/'boot').encode())
import shlex
script=script.replace(b'/system/bin/sh /data/local/tmp/adsp-d0/d0-target.sh',
    (shlex.join(android)+' '+shlex.quote(str(root/'target.sh'))).encode())
script=script.replace(b'/system/bin/sh -c',(shlex.join(android)+' -c').encode())
if os.environ.get('DROP_COMPLETION')=='1':
    script=script[:script.index(b'd0_rc=$?')]+b'exit\\n'
shell=sys.argv[5].replace('/system/bin/sh',shlex.join(android))
# QEMU linker diagnostics belong to the fixture, not the protocol stream.
with (root/'linker.stderr').open('ab') as err:
    p=sp.run(android+['-c',shell],input=script,stdout=sp.PIPE,stderr=err)
for line in p.stdout.splitlines(keepends=True):
    if line.startswith((b'linker: Warning: failed to find generated linker configuration',
                        b'WARNING: linker: Warning: failed to find generated linker configuration')):
        continue
    sys.stdout.buffer.write(line)
sys.exit(p.returncode)
'''); adb.chmod(0o755)
    os.environ.update(PATH=str(x)+os.pathsep+os.environ['PATH'],FIX_ROOT=str(r),
        ANDROID=json.dumps(android),ROOT_UID='0',FIX_UID='2000',TARGET_RC='0',
        LD_LIBRARY_PATH=str(sr/'system/lib64'))
    for mode,rc in [('loopback',0),('loopback',7),('adbd',0)]:
        os.environ['TARGET_RC']=str(rc)
        os.environ['FIX_UID']='0' if mode=='adbd' else '2000'
        log=r/f'{mode}-{rc}.log'
        transport=mod.Transport(adb,'4','fixture',mode,boot)
        try: actual=transport.target(['run','argument with spaces'],5,log)
        except RuntimeError:
            print(log.with_suffix('.transport.log').read_text(),flush=True)
            raise
        assert actual==rc
        assert log.read_text()=='target output\ntarget diagnostic\n',log.read_text()
        print('PASS Android mksh protocol',mode,'target rc',rc,flush=True)
    transport=mod.Transport(adb,'4','fixture','loopback',boot)
    log=r/'staging.log'
    assert transport.execute(['/system/bin/sh','-c',"printf '%s\\n' \"staging ' quoted\"; exit 5"],5,log)==5
    assert log.read_text()=="staging ' quoted\n"
    print('PASS Android mksh staging command quoting and nonzero status',flush=True)
    probe=Path(__file__).resolve().parents[2]/'scripts/mainline/adsp/d0-probe.sh'
    pr=r/'proberoot'; (pr/'dev').mkdir(parents=True); (pr/'data/local/tmp').mkdir(parents=True)
    (pr/'dev/kmsg').write_text('')
    os.environ.update(ROOT_UID='0',FIX_UID='2000',DROP_COMPLETION='0',PROBE_ROOT=str(pr))
    shutil.copy(shutil.which('setsid'),x/'setsid') if shutil.which('setsid') else None
    log=r/'probe.log'
    transport=mod.Transport(adb,'4','fixture','loopback',boot)
    assert transport.execute(['/system/bin/sh','-c',probe.read_text()],20,log)==0,log.read_text()
    assert 'D0-ROOT-PROBE PASS' in log.read_text()
    assert 'd0-root-probe' in (pr/'dev/kmsg').read_text()
    assert not list((pr/'data/local/tmp').iterdir())
    print('PASS Android mksh staging probe',flush=True)
    (pr/'dev/kmsg').unlink(); (pr/'dev/kmsg').mkdir()  # simulated write denial
    log=r/'probe-deny.log'
    assert transport.execute(['/system/bin/sh','-c',probe.read_text()],20,log)!=0
    assert 'PASS' not in log.read_text() and not list((pr/'data/local/tmp').iterdir())
    print('PASS Android mksh staging probe refuses unwritable /dev/kmsg',flush=True)
    os.environ.pop('PROBE_ROOT')
    for case in ('not-root','wrong-boot','missing-completion','adbd-shell-uid'):
        os.environ.update(ROOT_UID='2000' if case=='not-root' else '0',FIX_UID='2000',
            DROP_COMPLETION='1' if case=='missing-completion' else '0')
        (r/'boot').write_text(('22222222-2222-2222-2222-222222222222' if case=='wrong-boot' else boot)+'\n')
        log=r/(case+'.log')
        transport=mod.Transport(adb,'4','fixture','adbd' if case=='adbd-shell-uid' else 'loopback',boot)
        try: transport.target(['run','argument with spaces'],5,log)
        except RuntimeError: pass
        else: raise AssertionError(case)
        assert not log.exists()
        if case in ('not-root','wrong-boot','adbd-shell-uid'):
            assert 'target output' not in log.with_suffix('.transport.log').read_text()
        print('PASS Android mksh protocol refuses',case,flush=True)
print('ADSP-D0-TRANSPORT TEST PASS')
