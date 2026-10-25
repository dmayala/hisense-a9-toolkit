"""Durable opening accounting. A request is pending; only open readback is used.

Pre-gate failures and probes retain receipts without consuming opening 1.
A crash after a request but before readback is UNKNOWN, not falsely 'unused';
it blocks another capture until a separate scoped disposition. No device I/O.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid
from ghost_recovery import atomic_json,sync_directory


def atomic_text(path,text):
    path=Path(path);tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        with tmp.open('x') as f:f.write(text);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path);sync_directory(path.parent)
    finally:
        if tmp.exists():tmp.unlink()


def read(out):
    out=Path(out)
    if not (out/'attempt.json').exists() and not out.exists():
        # A signal may arrive after archival but before the shell receives the
        # new path. The latest receipt names that exact archived directory.
        link=out.parent/'last-attempt'
        if link.is_symlink():out=link.resolve()
    return out,json.loads((out/'attempt.json').read_text())


def check(series):
    state=Path(series)/'gate-state.json'
    if state.exists():
        d=json.loads(state.read_text())
        raise RuntimeError('opening already used' if d.get('consumed') else 'gate request outcome UNKNOWN; separate scoped disposition required')


def init(out,series,probe=False):
    out,series=Path(out).absolute(),Path(series).absolute()
    if not probe:check(series)
    atomic_json(out/'attempt.json',dict(version=1,token=uuid.uuid4().hex,series=str(series),
                mode='probe' if probe else 'capture',gate_requested=False,consumed=False))


def event(out,action,evidence=None):
    out,d=read(out);series=Path(d['series']);state=series/'gate-state.json'
    if d['mode']!='capture':raise RuntimeError('probe cannot request or consume an opening')
    if action=='request':
        check(series)
        record=dict(token=d['token'],out=str(out),state='pending',consumed=False)
        atomic_json(state,record) # MUST precede the first gate-open command.
        d['gate_requested']=True
    elif action=='opened':
        record=json.loads(state.read_text())
        if record.get('token')!=d['token']:raise RuntimeError('gate request owner mismatch')
        text=Path(evidence).read_text()
        bools=[s.strip() for s in text.splitlines() if s.strip() in ('Y','N','1','0')]
        if 'GHOST_GATE open' not in text.splitlines() or len(bools)!=3 or any(x not in ('Y','1') for x in bools):
            raise RuntimeError('all three open gates were not verified')
        record.update(state='opened',consumed=True,evidence=str(Path(evidence).absolute()))
        atomic_json(state,record)
        d.update(gate_requested=True,consumed=True)
    else:raise RuntimeError('invalid gate accounting event')
    atomic_json(out/'attempt.json',d)


def breach(out,text):
    """A probe seeing unexpected open/uncertain gates is never an unused pass."""
    out,d=read(out);series=Path(d['series']);state=series/'gate-state.json'
    lines=text.splitlines()
    values=[line.split()[-1] for line in lines if line.startswith('SAFE3_BOOL ')]
    opened='SAFE3_GATE open' in lines and len(values)==3 and all(x in ('Y','1') for x in values)
    atomic_json(out/'closed-gate-violation.json',dict(evidence=text,observed_open=opened))
    if not state.exists():
        atomic_json(state,dict(token=d['token'],out=str(out),state='opened' if opened else 'pending',consumed=opened,probe_violation=True))
    elif opened:
        prior=json.loads(state.read_text());prior.update(state='opened',consumed=True,observed_by=str(out))
        atomic_json(state,prior)
    d.update(gate_requested=True,consumed=opened,probe_violation=True);atomic_json(out/'attempt.json',d)


def finish(out,rc,dips,timeouts,faults,goal,stop,home_restored=False):
    out,d=read(out);series=Path(d['series']);state=series/'gate-state.json'
    record=json.loads(state.read_text()) if state.exists() else {}
    own=record.get('token')==d['token']
    consumed=own and record.get('consumed') is True
    unknown=own and not consumed
    if unknown:
        rc=rc or 9;stop=stop if stop!='none' else 'gate-outcome-unknown'
    row='1\t%d\t%d\t%d\t%d\t%d\t%s\n'%(rc,dips,timeouts,faults,goal,stop)
    atomic_text(out/'result.tsv',row)
    d.update(rc=rc,stop=stop,consumed=consumed,gate_outcome='opened' if consumed else 'unknown' if unknown else 'never-requested',
             home_restored=home_restored)
    atomic_json(out/'attempt.json',d)
    if consumed:atomic_text(series/'series.tsv',row)
    elif d['mode']=='capture' and out.name=='opening-1':
        archive=series/'attempts'/d['token'];archive.parent.mkdir(exist_ok=True)
        os.replace(out,archive);sync_directory(series);sync_directory(archive.parent);out=archive
    link=series/('.last-attempt.'+uuid.uuid4().hex)
    link.symlink_to(out);os.replace(link,series/'last-attempt');sync_directory(series)
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('check','init','request','opened','finish'))
    p.add_argument('--out');p.add_argument('--series');p.add_argument('--probe',action='store_true');p.add_argument('--evidence')
    p.add_argument('--row',nargs=6);p.add_argument('--home-restored',action='store_true');a=p.parse_args()
    try:
        if a.action=='check':check(a.series)
        elif a.action=='init':init(a.out,a.series,a.probe)
        elif a.action in ('request','opened'):event(a.out,a.action,a.evidence)
        else:
            rc,dips,timeouts,faults,goal,stop=a.row
            print(finish(a.out,*map(int,(rc,dips,timeouts,faults,goal)),stop,a.home_restored))
        return 0
    except Exception as e:print('ACCOUNTING REFUSED: '+str(e),file=sys.stderr);return 9


if __name__=='__main__':raise SystemExit(main())
