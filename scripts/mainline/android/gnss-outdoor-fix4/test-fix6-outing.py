#!/usr/bin/env python3
"""Host-only boot69 regression, persistent logger and Android label checks."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess as sp
import tempfile
import types

HERE=Path(__file__).resolve().parent
ROOT=Path('/home/user/A9-build/mainline')
S=ROOT/'adsp/d2-v8-20261017/system-image/system'
Q='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
LINK=[Q,'-E',f'LD_LIBRARY_PATH={S}/lib64/bootstrap:{S}/lib64',str(S/'bin/bootstrap/linker64')]
TOKEN='0123456789abcdef'


def main():
    spec=importlib.util.spec_from_file_location('outing_ex',HERE/'extract.py')
    ex=importlib.util.module_from_spec(spec);spec.loader.exec_module(ex)
    source=(HERE/'onb.py').read_text()
    start=source.index('wifi=module(');end=source.index('PKG=',start)
    hook=types.ModuleType('mock_outing');hook.__file__=str(HERE/'onb.py')
    hook.wifi=types.SimpleNamespace(Hook=object);hook.extract=ex
    exec(compile(source[:start]+source[end:],str(HERE/'onb.py'),'exec'),hook.__dict__)
    saved=ROOT/'outing-fix5-20261023/repull/boot-0000000069/kernel.log'
    actual=ex.report(ex.read_input(saved),printing=False)
    assert actual['GNSS_CAPTURE'] and actual['GNSS_FIX'] and actual['GNSS_FINISHED']
    assert actual['metrics']['ttff_ms']==459562 and actual['metrics']['sats_used']==34
    assert {r['irq'] for r in actual['wakeup_irqs']}=={116}
    buf=io.StringIO()
    with contextlib.redirect_stdout(buf):ex.report('',TOKEN)
    assert 'UNAVAILABLE GNSS_FIX' in buf.getvalue() and 'FAIL GNSS_FIX' not in buf.getvalue()
    gps=f'A9GNSS session={TOKEN} kind=final fix=yes ttff_ms=459562 accuracy_m=3.54 sats_used=34 satellites=41 elapsed_s=900\n'
    with tempfile.TemporaryDirectory(prefix='fix6-outing-') as temp:
        p=Path(temp);root=p/'proc/142/root/kdebuginfo/mainline'
        boot=root/'boot-0000000069';boot.mkdir(parents=True)
        (root/'counter').write_text('69\n')
        (boot/'info.txt').write_text('A9_MAINLINE_LOG boot=boot-0000000069 pid=142\nPRIVATE_METADATA\n')
        (boot/'kernel.log.prev').write_text('6,123,1,-;'+gps)
        (boot/'kernel.log').write_text('6,124,2,-;A9_SLEEP_OUT session='+TOKEN+' kind=final samples=11 elapsed_s=600 rehearsal=0\n')
        command=hook.PERSISTENT_LOG.replace('SESSION',TOKEN).replace('/proc/',str(p/'proc')+'/')
        # pidof is a fixture. All reads stay under the temporary /proc tree.
        pre='pidof() { echo 142; }\ntimeout() { shift; "$@"; }\n'
        for tool in ['grep','head']:
            pre+=tool+'() { '+' '.join(LINK+[str(S/'bin'/tool)])+' "$@"; }\n'
        (p/'test.sh').write_text(pre+command)
        def read():
            return sp.check_output(LINK+[str(S/'bin/sh'),str(p/'test.sh')],text=True,timeout=20)
        text=read();assert 'PRIVATE_METADATA' not in text
        assert ex.report(text,TOKEN,False)['GNSS_FIX'] and ex.report(text,TOKEN,False)['SLEEP_CAPTURE']
        h=hook.Hook();h.session=TOKEN;calls=[]
        def native(cmd,**kw):calls.append(cmd);return (0,text) if 'pidof' in cmd else (0,'')
        h.native=native
        assert ex.report(h.sleep_log(),TOKEN,False)['GNSS_FIX'] and len(calls)==1
        # A stale boot header or the wrong session cannot masquerade as current.
        (boot/'info.txt').write_text('A9_MAINLINE_LOG boot=boot-0000000069 pid=141\n')
        assert read()==''
        (boot/'info.txt').write_text('A9_MAINLINE_LOG boot=boot-0000000069 pid=142\n')
        (boot/'kernel.log.prev').write_text(gps.replace(TOKEN,'fedcba9876543210'))
        assert not ex.report(read(),TOKEN,False)['GNSS_FIX']
        # Actual Android awk must redact identifier-shaped labels, keep driver
        # names, and agree with the host extractor. Inputs are synthetic.
        sleep=(HERE/'sleep.sh').read_text()
        awk=sleep[sleep.index("NAME_AWK='")+len("NAME_AWK='"):sleep.index("\nprevious_wake=''")-1]
        names=['hs_uart_wakeup','eventpoll','4ef8800.usb','deadbeef12345','12.3456,78.9012','aa:bb:cc:dd:ee:ff']
        result=sp.check_output(LINK+[str(S/'bin/awk'),awk+'\n{ print safe_name($0) }'],input='\n'.join(names)+'\n',text=True,timeout=15).splitlines()
        assert result==[ex.safe_name(n) for n in names]
        assert result[:3]==names[:3] and result[3:]==['redacted']*3
        # Rehearse only the phase-2 timing loop with a simulated clock. No
        # powerctl, sysrq, /proc writes or real sleeps can run in this fixture.
        deadman=sp.check_output(['debugfs','-R','cat /bin/a9ml-deadman.sh',str(ROOT/'integ-20261023-6/vendor.img')],stderr=sp.DEVNULL,text=True)
        phase=deadman[deadman.index('# phase 1'):deadman.index('log "firing')]
        (p/'deadman-test.sh').write_text('''clock=0; HOLD=5400; DEADMAN=2400; REASON=''
up() { echo "$clock"; }
getprop() { echo 1; }
held() { return 1; }
cap() { echo 7200; }
log() { :; }
sleep() { clock=$((clock+1800)); }
'''+phase+'\n[ "$clock" = 5400 ] && [ "$REASON" = hold-expired ]\n')
        sp.run(LINK+[str(S/'bin/sh'),str(p/'deadman-test.sh')],check=True,capture_output=True,timeout=20)
        stage_spec=importlib.util.spec_from_file_location('outing_stage',HERE/'stage.py')
        stage=importlib.util.module_from_spec(stage_spec);stage_spec.loader.exec_module(stage)
        original=ROOT/'integ-20261023-6/before-boot69-addendum/launcher/b-try-outdoor.sh'
        if not original.exists():original=ROOT/'gnss-outdoor-fix3-20261023/b-try-outdoor.sh'
        runner=original.read_text()
        changed=stage.outdoor_runner(runner)
        assert changed.count('timeout 600 "$ADB" exec-out')==4
        assert '--json "$out/outdoor-result.json"' in changed and 'replug by +50 min.' in changed
        (p/'runner.sh').write_text(changed)
        sp.run(['bash','-n',str(p/'runner.sh')],check=True,capture_output=True)
    assert 'A9_BTRY_HOLD=4200' in (HERE/'run.sh').read_text()
    assert 'time.sleep(1800)' in source and 'min(1200,max(0,3900-' in source
    print(json.dumps(dict(host_only=True,phone_access=False,boot69_gnss_fix=True,ttff_ms=459562,
                         boot69_wakeup_irq=116,persistent_current_boot=True,stale_header_rejected=True,
                         android_mksh_awk=True,sanitizer_agrees=True,quiet_seconds=1800,
                         replug_seconds=1200,host_hold_seconds=4200,deadman_simulated_seconds=5400,
                         archive_pull_timeouts_preserved=True,post_pull_result_json=True),indent=2))


if __name__=='__main__':main()
