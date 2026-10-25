"""Timed late-signal replay with pinned old-wrapper false-success control."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
OLD='80596c20c89a366f8db43dc823c3c01f0dea57a3'
PIN='c922a8ae271cd6031648ee56e53cbcab3c2e2e3f0f30182fb84d57226e2ec354'


def main():
    dest=Path(sys.argv[1]).absolute();dest.mkdir()
    old=subprocess.check_output(['git','show',OLD+':scripts/eink-probe/sessions/r0-ghost.sh'],cwd=ROOT,text=True)
    assert hashlib.sha256(old.encode()).hexdigest()==PIN
    old_path=dest/'old-r0-ghost.sh';old_path.write_text(old)
    base=(ROOT/'tests/eink-probe/rehearse-ghost.sh').read_text().split('case_ fail-pre-reboot')[0]
    base=base.replace('D=$(cd "$(dirname "$0")" && pwd)','D='+str(ROOT/'tests/eink-probe'))
    rows=[]
    for name,runner,want in [('old',old_path,0),('new',ROOT/'scripts/eink-probe/sessions/r0-ghost.sh',9)]:
        out=dest/name;out.mkdir()
        script=base
        # This only permits the unchanged old write command to use an unbarriered
        # fake child. The new runner still consumes the actual production pipe.
        script+='\npython3 - "$R/bootwt/scripts/mainline/android/android-boot.sh" <<\'FIX\'\nfrom pathlib import Path\nimport sys\np=Path(sys.argv[1]);s=p.read_text().replace(\'[ -n "$fd" ] || exit 8\',\'[ -n "$fd" ] || return 0\');p.write_text(s)\nFIX\n'
        if name=='old':
            # A negative control is expected to exhibit success after TERM.
            # Assert the observed process/row below instead of accepting it.
            script=script.replace('if [ -n "${LATE_SIGNAL:-}" ]; then\n    [ -e', 'if false; then\n    [ -e')
        script+=f'''\nLATE_SIGNAL=TERM case_ replay ok 1 {want} 'echo 2 > "$R/final.sleep"'\ncp "$SER/series.tsv" "$REPLAY_OUT/series.tsv"\ncp "$SER/opening-1/chain.log" "$REPLAY_OUT/chain.log"\ncp "$SER/out.txt" "$REPLAY_OUT/out.txt"\n[ "$failures" = 0 ]\n'''
        path=out/'run.sh';path.write_text(script)
        env=dict(os.environ,RUNNER=str(runner),REPLAY_OUT=str(out),PYTHONDONTWRITEBYTECODE='1')
        for k in ('GHOST_EXECUTION_TUPLE','GHOST_TUPLE_SHA'):env.pop(k,None)
        with (out/'replay.log').open('w') as log:
            p=subprocess.run(['bash',str(path)],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=35)
        text=(out/'replay.log').read_text();assert p.returncode==0,text
        match=re.search(r'runner rc ([0-9]+)',text);assert match,text
        rc=int(match[1]);fields=(out/'series.tsv').read_text().strip().split('\t')
        assert 'r0 rc=0' in (out/'chain.log').read_text()
        assert (rc==0)==(name=='old') and fields[1]==str(want),(name,rc,fields)
        assert fields[6]==('none' if name=='old' else 'signal')
        rows.append(dict(runner=name,process_rc=rc,series_rc=int(fields[1]),stop=fields[6],result='EXPECTED FALSE SUCCESS' if name=='old' else 'PASS'))
    (dest/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(rows))

if __name__=='__main__':main()
