#!/usr/bin/env python3
"""Five full fake-hook paths for FIX5; no real transports or credentials."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess as sp
import tempfile
import types

HERE=Path(__file__).resolve().parent


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def main():
    ex=load('fix5_extract',HERE/'extract.py')
    code=(HERE/'onb.py').read_text()
    start=code.index('wifi=module(');end=code.index('PKG=',start)
    code=code[:start]+code[end:]
    module=types.ModuleType('fake_fix5');module.__file__=str(HERE/'onb.py')
    module.wifi=types.SimpleNamespace(Hook=object,START='START',CAP='CAP',CONNECT='CONNECT')
    module.extract=ex;module.xtra=types.SimpleNamespace(attempt=lambda _:None)
    exec(compile(code,str(HERE/'onb.py'),'exec'),module.__dict__)
    outcomes={}
    with tempfile.TemporaryDirectory(prefix='a9-fix5-radio-') as temp:
        os.environ['A9_BTRY_HOLD']='4200';os.environ['A9_BTRY_LOGS']=temp
        for case,warning in [('healthy_shell_denied',None),('request_failed','BOOT_REQUEST'),('prep_failed','PREPARED'),('client_not_running','RUNNING'),('interface_missing','INTERFACES')]:
            hook=module.Hook();hook.adb='fake';hook.ids=set();hook.transferred=False
            hook.rehearsal=True;hook.seconds=75;hook.calls=[]
            hook.credentials=lambda:('synthetic','synthetic');hook.saved=lambda:{1}
            hook.status=lambda:True;hook.internet=lambda:True
            hook.until=lambda _,predicate,*args:predicate();module.time.sleep=lambda _:None
            def gps():return 'A9GNSS session='+hook.session+' kind=sample fix=no elapsed_s=0\n'
            def transport(context,command,*args,**kwargs):
                hook.calls.append((context,command))
                if isinstance(command,list):return 0,''
                if command=='getprop ro.boot.slot_suffix':return 0,'_b'
                if 'boot_completed' in command:return 0,'1'
                if 'wifi.session.state' in command:return 0,'serving'
                if command=='getprop vendor.a9ml.cellular\n':return (1,'') if case=='request_failed' else (0,'1\n')
                if 'stock.ready' in command:return 0,'0' if case=='prep_failed' else '1'
                if 'RADIO_STATE ' in command:
                    assert context=='native'
                    state='restarting' if case=='client_not_running' else 'running'
                    return 0,'RADIO_STATE vendor.qcrild '+state+'\nRADIO_STATE vendor.qcrild2 running\n'
                # Reproduce the boot62 denial instead of pretending shell can read it.
                if 'init.svc.vendor.qcrild' in command:raise AssertionError('private service property read through shell')
                if 'lshal' in command:return 0,'0' if case=='interface_missing' else '2'
                if 'is-location-enabled' in command:return 0,'false'
                if command=='cat /proc/uptime':return 0,'100.0 100.0'
                if command.startswith('logcat '):return 0,gps()
                return 0,''
            hook.run=lambda *a,**k:transport('run',*a,**k)
            hook.shell=lambda *a,**k:transport('shell',*a,**k)
            hook.native=lambda *a,**k:transport('native',*a,**k)
            output=io.StringIO()
            with contextlib.redirect_stdout(output):hook.execute()
            text=output.getvalue()
            assert 'PASS FOREGROUND_GNSS_STARTED' in text and 'PASS FIRST_A9GNSS_LOGCAT_LINE' in text
            assert 'FAIL STOCK_RADIO' not in text
            calls=[c for _,c in hook.calls if isinstance(c,str)]
            assert sum('am start -W' in c for c in calls)==1
            assert 'setprop vendor.a9ml.cellular 1\n' not in calls
            if warning:assert 'WARN STOCK_RADIO_'+warning in text
            else:assert 'PASS STOCK_RADIO_RUNNING' in text and 'PASS STOCK_RADIO_INTERFACES' in text
            outcomes[case]=dict(gnss_started=True,first_summary=True,radio_warning=warning)
        # Actual Android shell reducer, including second-instance state and
        # untrusted property text; only fixed enums can leave the native reader.
        S=Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system')
        q='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
        script=Path(temp)/'state.sh'
        script.write_text('getprop() { case "$1" in init.svc.vendor.qcrild) echo running;; *) echo PRIVATE;; esac; }\n'+module.RADIO_STATE)
        r=sp.run([q,'-E',f'LD_LIBRARY_PATH={S}/lib64/bootstrap:{S}/lib64',str(S/'bin/bootstrap/linker64'),str(S/'bin/sh'),str(script)],check=True,text=True,capture_output=True)
        assert r.stdout=='RADIO_STATE vendor.qcrild running\nRADIO_STATE vendor.qcrild2 unknown\n'
        # No optional failure handler may swallow the owner's interruption.
        interrupted=module.Hook()
        def stop(*a,**k):raise InterruptedError()
        interrupted.native=stop
        try:interrupted.optional_radio()
        except InterruptedError:pass
        else:raise AssertionError('interruption swallowed')
    print(json.dumps(dict(host_only=True,phone_access=False,cases=outcomes,android_state_reducer=True,interrupt_preserved=True),indent=2))


if __name__=='__main__':main()
