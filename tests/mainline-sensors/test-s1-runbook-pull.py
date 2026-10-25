#!/usr/bin/env python3
"""Runbook evidence pull: root chmod first, no silent loss, missing names reported. Fake adb/R only.

The pull block of section 5 is extracted from the real runbook; the same pull in the D1/D2 runbooks is checked
textually (those use the in-shell R of section 3)."""
import os,re,shutil,subprocess as sp,tempfile
from pathlib import Path
BASH=shutil.which('bash')
RUNS=Path('/home/user/A9/runs')
text=(RUNS/'sensors-s1-runbook.md').read_text()
sec5=text[text.index('\n## 5.'):text.index('\n## 6.')]
start=sec5.index('R() {\n  local log'); end=sec5.index("# Only after the S1 host has exited")
block=sec5[start:end]
rfn=block[:block.index('\n}\n')+3]
sec3=text[text.index('\n## 3.'):text.index('\n## 4.')]
assert rfn in sec3, 'section 5 R() differs from section 3 R()'
trues=[l for l in block.split('pull_evidence() {')[1].splitlines() if '|| true' in l]
assert len(trues)==1 and 'grep -E' in trues[0], ('silent || true is back in the pull', trues)   # only the no-match grep
for f,side in (('adsp-d1-runbook.md','adsp-d1'),('adsp-d2-runbook.md','adsp-d2')):
    t=(RUNS/f).read_text()
    assert f"R 'chmod -R a+rX /data/local/tmp/{side} /data/local/tmp/{side}-ledger'" in t and 'EVIDENCE NOT FULLY PULLED' in t,f
    assert not re.search(r'pull /data/local/tmp/adsp-d\d-ledger "\$SESSION/target-ledger" \|\| true',t),f
for f in ('sensors-s1-runbook.md','adsp-d1-runbook.md','adsp-d2-runbook.md'):
    assert not re.search(r'mapfile -t NAMES < <\(.*\) </dev/null$',(RUNS/f).read_text(),re.M),(f,'mapfile stdin is overridden to /dev/null: NAMES would always be empty')
body=block[block.index('pull_evidence() {'):block.index('\nif [ -n "${A9_TRANSPORT_ID')]
assert 'pull_evidence\nfi' in block
with tempfile.TemporaryDirectory() as td:
    td=Path(td)
    def phone():
        ph=td/f'phone{len(list(td.glob("phone*")))}'
        for f in ('s1-listener.log','kmsg.log','s1-observation.log','abort.x.y','tuple','other-ignored'):
            (ph/'sensors-s1').mkdir(parents=True,exist_ok=True); (ph/'sensors-s1'/f).write_text(f); (ph/'sensors-s1'/f).chmod(0)   # root-only
        (ph/'sensors-s1-ledger/boot1/listener').mkdir(parents=True); (ph/'sensors-s1-ledger/boot1/listener/done').write_text('0\n')
        (ph/'sensors-s1-ledger/boot1/listener/done').chmod(0)
        for d in ('sensors-s1-ledger/boot1/listener','sensors-s1-ledger/boot1','sensors-s1-ledger'): (ph/d).chmod(0o300)
        return ph
    adb=td/'adb'
    adb.write_text(f'''#!{BASH}
a="$*"
case "$a" in
  *get-state*) echo device;;
  *"shell -T"*) ls -1 "$PH/sensors-s1" 2>/dev/null; exit 0;;
  *" pull "*) src=$(echo "$a" | awk '{{print $(NF-1)}}'); dst=$(echo "$a" | awk '{{print $NF}}')
     m="$PH/${{src#/data/local/tmp/}}"; cp -r "$m" "$dst" 2>/dev/null || exit 1; exit 0;;
esac
''')
    adb.chmod(0o755)
    def run(fix_ok):
        ph=phone(); sess=td/f's{len(list(td.glob("s*")))}'; sess.mkdir()
        # fake R: the "chmod" call repairs permissions in the fake phone (as root would); the evidence call prints text
        rf=('R() {\n  case "$1" in\n    *chmod*) '+('chmod -R u+rwX,a+rX "$PH/sensors-s1" "$PH/sensors-s1-ledger"; echo chmod-ok' if fix_ok else 'echo root shell unavailable >&2; return 1')+';;\n'
            '    *) '+('echo "== listener evidence"' if fix_ok else 'return 1')+';;\n  esac\n}\n')
        src=f'set -euo pipefail\nADB={adb}\nA9_TRANSPORT_ID=3\nSESSION={sess}\nPH={ph}\n{rf}{body}\npull_evidence\necho FINISHED\n'
        p=sp.run([BASH,'-c',src],capture_output=True,text=True,env={**os.environ,'PH':str(ph)},timeout=60)
        return p,sess
    p,sess=run(True)
    assert 'FINISHED' in p.stdout and 'nothing missing' in p.stdout,p
    assert (sess/'s1-listener.log').is_file() and (sess/'kmsg.log').is_file() and (sess/'abort.x.y').is_file() and not (sess/'other-ignored').exists()
    assert (sess/'target-ledger/boot1/listener/done').read_text()=='0\n' and (sess/'listener-evidence.txt').read_text().startswith('== listener evidence')
    assert not (sess/'pull-missing.txt').read_text().strip()
    p,sess=run(False)   # root shell unavailable: nothing readable; every loss is named, the script still finishes
    miss=(sess/'pull-missing.txt').read_text()
    assert 'FINISHED' in p.stdout and 'EVIDENCE NOT FULLY PULLED' in p.stdout,p
    for needle in ('WARN root chmod failed','MISSING listener-evidence.txt','MISSING target-ledger','MISSING s1-listener.log','MISSING kmsg.log'): assert needle in miss,(needle,miss)
print('S1 RUNBOOK EVIDENCE-PULL TEST PASS')
