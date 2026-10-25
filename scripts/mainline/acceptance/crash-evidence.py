#!/usr/bin/env python3
"""Offline per-boot crash summaries. Never invokes a device or exports raw text."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

NAME = r'[A-Za-z_][A-Za-z0-9_.:@$<>-]{0,191}'
SYMBOL = r'[A-Za-z_][A-Za-z0-9_.:$<>-]{0,191}'


def collect(boot):
    records = []; seen = set(); record = section = None
    health = None; captures = errors = limited = incomplete = 0
    exec_errors = last_exec_errno = 0
    def keep(complete):
        nonlocal record, section
        if record is None: return
        record['complete'] = int(complete)
        key = json.dumps(record, sort_keys=True)
        if key not in seen and len(seen) < 8192:
            seen.add(key); records.append(record)
        record = section = None
    for name in ('crash-evidence.log.prev', 'crash-evidence.log'):
        p = boot/name
        if not p.is_file() or p.is_symlink(): continue
        with p.open(errors='replace') as source:
            for raw in source:
                line = raw.rstrip('\r\n')
                m = re.fullmatch(r'trace_begin ([0-9]{1,2}) ([1-4]) ([0-9]{1,19}) ([01])', line)
                if m:
                    keep(False)
                    record = dict(kind=int(m[2]), bytes=int(m[3]), prefix_truncated=int(m[4]), sections=[])
                    section = None; continue
                m = re.fullmatch(r'trace_end (0|141|[1-9][0-9]{0,2})', line)
                if m:
                    if record is not None and m[1]=='141': record['prefix_truncated']=1
                    keep(m[1]=='0'); continue
                m = re.fullmatch(r'trace_health ([01]) ([01]) ([01])', line)
                if m: health=dict(zip(('directories_ready','tombstoned_running','crash_dump64_present'),map(int,m.groups())))
                m = re.fullmatch(r'capture_end ([0-9]{1,3})',line)
                if m: captures+=1; errors+=int(m[1]!='0')
                m = re.fullmatch(r'capture_exec_errno ([1-9][0-9]{0,3})',line)
                if m and int(m[1]) <= 4095:
                    exec_errors += 1; last_exec_errno = int(m[1])
                m = re.fullmatch(r'trace_errors ([0-9]{1,3})',line)
                if m: errors+=int(m[1])
                m = re.fullmatch(r'trace_truncated ([01])',line)
                if m: limited+=int(m[1])
                if line=='capture_begin':
                    keep(False); incomplete+=1
                if line.startswith('capture_end '): incomplete=max(0,incomplete-1)
                if record is None: continue
                if line.startswith('trace_section '): section=None
                m = re.fullmatch(r'trace_section ('+NAME+')',line)
                if m and len(record['sections'])<16 and not re.search(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-',m[1]):
                    section=dict(process=m[1],frames=[],native=[]);record['sections'].append(section)
                frames=sum(len(s['frames'])+len(s['native']) for s in record['sections'])
                m = re.fullmatch(r'trace_frame ('+SYMBOL+')',line)
                if m and section is not None and frames<32: section['frames'].append(m[1])
                m = re.fullmatch(r'trace_native ('+SYMBOL+') ('+SYMBOL+')',line)
                if m and section is not None and frames<32:
                    section['native'].append(dict(library=m[1],method=m[2]))
                m = re.fullmatch(r'trace_signal ([0-9]{1,2}) (-?[0-9]{1,3})',line)
                if m and 1<=int(m[1])<=64 and -255<=int(m[2])<=255: record['signal']=int(m[1]);record['code']=int(m[2])
                m = re.fullmatch(r'trace_abort ([0-4])',line)
                if m: record['abort_category']=int(m[1])
                m = re.fullmatch(r'trace_exception ('+SYMBOL+')',line)
                if m: record['exception']=m[1]
                m = re.fullmatch(r'trace_frame_limit ([01])',line)
                if m: record['frame_limit_reached']=int(m[1])
                m = re.fullmatch(r'trace_prefix_limit ([01])',line)
                if m: record['prefix_truncated'] |= int(m[1])
    keep(False)
    fatals = set(); present = 0
    for name in ('logcat.log.prev','logcat.log'):
        p=boot/name
        if not p.is_file() or p.is_symlink(): continue
        present+=1
        with p.open(errors='replace') as source:
            for line in source:
                # Include log header in private-memory identity: ring overlap is
                # deduplicated, distinct timestamps/PIDs remain distinct crashes.
                if re.search(r'\bFATAL EXCEPTION IN SYSTEM PROCESS\b',line):
                    fatals.add(hashlib.sha256(line.rstrip().encode()).digest())
    summary=dict(system_server_fatals=len(fatals),system_server_crash_loop=int(len(fatals)>=2),
                 logcat_files=present,captures=captures,capture_errors=errors,
                 incomplete_captures=incomplete,exec_errors=exec_errors,last_exec_errno=last_exec_errno,
                 file_limit_observations=limited,
                 record_limit_reached=int(len(seen)>=8192),records=len(records),health=health)
    return records,summary


def save(boot):
    records,summary=collect(boot)
    os.umask(0o077)
    for name,kinds in [('tombstones.jsonl',(1,)),('anrs.jsonl',(2,)),('dropbox-crashes.jsonl',(3,4))]:
        p=boot/name
        # Never follow a collected or pre-existing output symlink.
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:
            for row in records:
                if row['kind'] in kinds: f.write(json.dumps(row,sort_keys=True)+'\n')
        p.chmod(0o600)
    p=boot/'crash-summary.json'
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f: json.dump(summary,f,sort_keys=True,indent=2);f.write('\n')
    p.chmod(0o600)
    return summary


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--saved-boot',type=Path,required=True)
    a=ap.parse_args();s=save(a.saved_boot)
    # Fixed numeric summary even if acceptance never reached its B hook.
    print('STABILITY_SUMMARY '+ ' '.join(f'{k}={s[k]}' for k in (
        'system_server_fatals','system_server_crash_loop','logcat_files','captures',
        'capture_errors','incomplete_captures','records')))


if __name__=='__main__': main()
