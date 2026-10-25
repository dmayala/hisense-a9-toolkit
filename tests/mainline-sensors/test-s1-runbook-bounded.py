#!/usr/bin/env python3
"""Runbook staging helper: a host-side timeout names its step and records transport state.

Extracts `bounded()` and the sections 1-4 script from the real runbook; fake adb only."""
import os,re,stat,subprocess as sp,tempfile
from pathlib import Path
RB=Path('/home/user/A9/runs/sensors-s1-runbook.md')
text=RB.read_text()
fn=re.search(r'^bounded\(\) \{\n.*?^\}\n',text,re.S|re.M)[0]
script=sp.run(['awk','/^## 1\\./{on=1} /^## 5\\./{on=0} on && /^```bash$/{b=1;next} on && /^```/{b=0;next} on && b'],input=text,capture_output=True,text=True,check=True).stdout
assert sp.run(['bash','-n'],input=script,text=True).returncode==0
# every bounded host call that can hang on the transport goes through the helper
assert 'bounded "push-$entry" 90 "$ADB"' in script and not re.search(r'^timeout \d+ "\$ADB".*push',script,re.M)
sec5=text[text.index('\n## 5.'):text.index('\n## 6.')]
assert 'staging-timeout.txt' in sec5
with tempfile.TemporaryDirectory() as td:
    td=Path(td); adb=td/'adb'
    adb.write_text('#!'+__import__('shutil').which('bash')+'\ncase "$*" in *push*) exec sleep 30;; *devices*) echo "0000000 offline";; *get-state*) echo offline;; *ok*) exit 0;; *fail*) exit 3;; esac\n')
    adb.chmod(0o755)
    def run(*args):
        sess=td/('s'+str(len(list(td.iterdir())))); sess.mkdir()
        src=f'set -euo pipefail\nADB={adb}\nA9_TRANSPORT_ID=3\nSESSION={sess}\n{fn}\nrc=0\nbounded "$@" || rc=$?\necho RC=$rc\n'
        p=sp.run(['bash','-c',src,'x',*args],capture_output=True,text=True,timeout=40)
        return p,sess
    p,sess=run('bundle-push','2',str(adb),'push','x')
    assert 'RC=124' in p.stdout and 'S1-STAGING-TIMEOUT step=bundle-push after 2s' in p.stdout,p
    rec=(sess/'staging-timeout.txt').read_text(); assert 'offline' in rec and 'NOT consumed' in rec
    p,sess=run('x','5',str(adb),'ok'); assert 'RC=0' in p.stdout and not (sess/'staging-timeout.txt').exists()
    p,sess=run('x','5',str(adb),'fail'); assert 'RC=3' in p.stdout and not (sess/'staging-timeout.txt').exists()
print('S1 RUNBOOK BOUNDED-STEP TEST PASS')
