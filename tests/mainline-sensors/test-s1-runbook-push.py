#!/usr/bin/env python3
"""Runbook push-in-parts, resource check, bytecode, and holder/lock teardown. Fake adb only, no phone.

Everything under test is extracted from the real runbook text."""
import os,re,shutil,signal,subprocess as sp,tempfile,time
from pathlib import Path
RB=Path('/home/user/A9/runs/sensors-s1-runbook.md'); text=RB.read_text()
BASH=shutil.which('bash')
def section(n):
    a=text.index(f'\n## {n}.'); b=text.index(f'\n## {n+1}.') if f'\n## {n+1}.' in text else len(text)
    return text[a:b]
script=sp.run(['awk','/^## 1\\./{on=1} /^## 5\\./{on=0} on && /^```bash$/{b=1;next} on && /^```/{b=0;next} on && b'],input=text,capture_output=True,text=True,check=True).stdout
bounded=re.search(r'^bounded\(\) \{\n.*?^\}\n',text,re.S|re.M)[0]
# --- static properties
assert 'sys.dont_write_bytecode=True' in script and 'rm -rf "$BUNDLE/__pycache__"' in script
assert "present==listed" in script
assert not re.search(r'push "\$BUNDLE" ',script), 'single whole-bundle push is back'
assert 'pre-push-resources.txt' in script and 'MemAvailable' in script
fn5=re.search(r'^USB_LOCK=.*?^end_holder\(\) \{\n.*?^\}\n',section(5),re.S|re.M)[0]
fn6=re.search(r'^USB_LOCK=.*?^end_holder\(\) \{\n.*?^\}\n',section(6),re.S|re.M)[0]
assert fn5==fn6
assert 'a9-bootlog.sh pull' in section(5)
assert 'end_holder "$BOOT_PID"' in section(5) and 'end_holder "$BOOT_PID"' in section(6)
block=script[script.index('SIDECAR=/data/local/tmp/sensors-s1'):]
block=block[:block.index('\n',block.index('test "$(R "cd $SIDECAR'))+1]
with tempfile.TemporaryDirectory() as td:
    td=Path(td)
    # --- parted push
    adb=td/'adb'
    adb.write_text(f'#!{BASH}\necho "$*" >> "$LOG"\nif [ -n "$HANG" ]; then case "$*" in *push*"$HANG"*) exec sleep 30;; esac; fi\nexit 0\n'); adb.chmod(0o755)
    bundle=td/'bundle'
    for f in ('SHA256SUMS','tuple','s1-target.sh','firmware/a','firmware/b','modules/m.ko','receipts/r.json','__pycache__/x.pyc'):
        (bundle/f).parent.mkdir(parents=True,exist_ok=True); (bundle/f).write_text('x')
    manifest=td/'MANIFEST'; manifest.write_text('h  s1-target.sh\nh  firmware/a\nh  firmware/b\nh  modules/m.ko\nh  receipts/r.json\n')
    def push(hang=''):
        sess=td/('sess'+str(len(list(td.iterdir())))); sess.mkdir()
        src=(f'set -euo pipefail\nADB={adb}\nA9_TRANSPORT_ID=3\nSESSION={sess}\nBUNDLE={bundle}\nMANIFEST={manifest}\n{bounded}'
             'R() { echo $(( $(wc -l < "$MANIFEST") + 2 )); }\n'+block.replace(' 90 "$ADB"',' 2 "$ADB"')+'echo PUSHED-ALL\n')
        env={**os.environ,'LOG':str(sess/'adb.log'),'HANG':hang}
        p=sp.run([BASH,'-c',src],capture_output=True,text=True,env=env,timeout=60)
        return p,sess
    p,sess=push()
    assert 'PUSHED-ALL' in p.stdout,p
    calls=(sess/'adb.log').read_text().splitlines()
    pushes=[c for c in calls if ' push ' in c]
    assert [c.split()[3].rsplit('/',1)[1] for c in pushes]==['SHA256SUMS','firmware','modules','receipts','s1-target.sh','tuple'],pushes
    assert not any('__pycache__' in c for c in calls) and all(c.endswith('/sensors-s1/') for c in pushes)
    assert calls[0].endswith('mkdir /data/local/tmp/sensors-s1') and sum('echo alive' in c for c in calls)==6
    assert len((sess/'push-parts.log').read_text().splitlines())==6
    p,sess=push('firmware')           # the firmware part stalls: it is named, later parts are never pushed
    assert 'PUSHED-ALL' not in p.stdout and 'S1-STAGING-TIMEOUT step=push-firmware' in p.stdout and 'adbd shell answers? ' in p.stdout,p
    calls=(sess/'adb.log').read_text().splitlines()
    assert not any('modules' in c for c in calls if ' push ' in c) and (sess/'staging-timeout.txt').exists()
    # --- holder / lock teardown
    run=td/'xdg'; run.mkdir()
    env={**os.environ,'XDG_RUNTIME_DIR':str(run)}
    lock=run/'a9-usb.lock'
    holder=td/'android-boot.sh'
    holder.write_text(f'#!{BASH}\ntrap "" TERM\nexec 9>"$XDG_RUNTIME_DIR/a9-usb.lock"\nflock -n 9 || exit 3\nsleep 1000 &\nwait\n')
    def teardown(pid,extra=''):
        src=f'USB_LOCK={lock}\n'+fn5.split('\n',1)[1]+f'TERM_WAIT=2\nend_holder {pid}\necho RC=$?\n'
        return sp.run([BASH,'-c','set -u\n'+src],capture_output=True,text=True,env=env,timeout=60)
    def spawn(argv,**kw): return sp.Popen(argv,env=env,start_new_session=True,stdout=sp.DEVNULL,stderr=sp.DEVNULL,**kw)
    def free(): return sp.run(['flock','-n',str(lock),'true']).returncode==0
    def alive(pid):
        try: os.kill(pid,0); return open(f'/proc/{pid}/stat').read().split()[2]!='Z'
        except (ProcessLookupError,FileNotFoundError): return False
    def reap(*procs):
        for pr in procs:
            try: pr.kill()
            except Exception: pass
            pr.wait()
    # a) holder ignores TERM and its sleep child keeps the lock fd: KILL exact pid, then the child, lock freed
    h=spawn([BASH,str(holder),'--write']); time.sleep(1.0)
    assert not free()
    kids=sp.run(['pgrep','-P',str(h.pid)],capture_output=True,text=True).stdout.split()
    r=teardown(h.pid); h.wait(timeout=10)
    assert 'RC=0' in r.stdout and 'KILL by exact PID' in r.stdout and free(),r
    assert all(not alive(int(k)) for k in kids),kids
    # b) alive pid that is not our wrapper: refused, untouched
    other=spawn(['sleep','1000']); r=teardown(other.pid)
    assert 'RC=1' in r.stdout and 'refusing to signal' in r.stdout and alive(other.pid),r; reap(other)
    # c) holder already gone but a foreign process holds the lock file: reported, not killed
    gone=spawn(['sleep','0']); gone.wait()
    foreign=spawn([shutil.which('python3'),'-c',f'import fcntl,os,time;f=open({str(lock)!r},"a");fcntl.flock(f,fcntl.LOCK_EX);time.sleep(1000)']); time.sleep(1.0)
    r=teardown(gone.pid)
    assert 'RC=1' in r.stdout and 'unexpected pid' in r.stdout and alive(foreign.pid),r; reap(foreign)
    # d) well-behaved holder exits on TERM: nothing is killed
    nice=td/'nice-android-boot.sh'; nice.write_text(f'#!{BASH}\nexec 9>"$XDG_RUNTIME_DIR/a9-usb.lock"\nflock -n 9\nsleep 1000 &\nwait\n')
    n=spawn([BASH,str(nice),'--write']); time.sleep(1.0)
    r=teardown(n.pid)   # TERM stops bash but not the sleep child holding fd 9: cleaned as a child
    assert 'RC=0' in r.stdout and free(),r; n.wait(timeout=10)
print('S1 RUNBOOK PUSH-PARTS AND HOLDER TEARDOWN TEST PASS')
