#!/usr/bin/env python3
"""Read sanitized A9GNSS logcat and A9_SLEEP_OUT kernel records only."""
import argparse
import json
import math
from pathlib import Path
import re
import tarfile

ENUMS={'kind':{'sample','final','armed','screen_off','counter','availability','wakeup','wakeup_availability','wakeup_irq','wakeup_irq_availability','stats_debugfs','resume_monitor'},'fix':{'yes','no'},
       'observer':{'resume_timer'},'reason':{'baseline','resume','periodic','final'},'state':{'complete','failed','unavailable'},
       'wake_source':{'other','alarmtimer','PowerManagerService.Display','PowerManagerService.WakeLocks','wlan','wlan_wow','qcom_rx_wakelock','pm8xxx_pwrkey','qpnp_rtc','rtc0','smp2p','glink','eventpoll','sync','timerfd'},
       'source':{'system_sleep','soc_sleep','rpm','qcom','rpmh','suspend','system_sleep_suspend'},
       'mode':{'none','vlow','vmin'},
       'key':{'count','duration_ticks','duration_ms','entered_ticks','exited_ticks','client_votes','success','fail','failed_freeze','failed_prepare','failed_suspend','failed_suspend_late','failed_suspend_noirq','failed_resume','failed_resume_early','failed_resume_noirq','active_count','event_count','wakeup_count','expire_count','active_time_ms','total_time_ms','max_time_ms','last_change_ms','prevent_suspend_time_ms'}}
NUMERIC={'ttff_ms','accuracy_m','sats_used','satellites','cn0_max_dbhz','elapsed_s','duration_s','rehearsal','sample','samples','value','requested','wifi_forgotten','soc_sources','suspend_fields','wakeup_id','delta','wakeup_fields','irq','irq_available','debugfs_mounted','stats_driver_bound','stats_files','hwirq','boottime_ms','suspended_ms','resume_cycles'}

def safe_name(value):
    """Kernel labels only; redact address-like private data before printing."""
    if value in {'4e00000.usb','4ef8800.usb','1613000.phy','4a8c000.serial'}: return value
    if re.search(r'(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}|\d\.\d',value): return 'redacted'
    value=re.sub(r'[0-9a-fA-F]{7,}','redacted',value)
    return re.sub(r'[^A-Za-z0-9_.:-]','_',value)[:64] or 'unknown'

def parse(text):
    records=[];seen=set()
    # Persistent /dev/kmsg collectors escape embedded newlines as literal
    # \\x0a. Recover tagged records only; do not decode arbitrary log escapes.
    for line in re.split(r'\n|\\x0a(?=A9(?:GNSS|_SLEEP_OUT) )', text):
        for prefix in ('A9GNSS','A9_SLEEP_OUT'):
            pos=line.rfind(prefix+' ')
            if pos<0: continue
            row={'prefix':prefix}
            for part in line[pos+len(prefix)+1:].split():
                key,sep,value=part.partition('=')
                if not sep or key in row: continue
                if key=='session' and re.fullmatch('[a-f0-9]{16}',value): row[key]=value
                elif key in ENUMS and value in ENUMS[key]: row[key]=value
                elif key in {'wake_name','irq_name','irq_chip'}: row[key]=safe_name(value)
                elif key in NUMERIC and re.fullmatch(r'-?\d+(?:\.\d+)?',value):
                    number=float(value) if '.' in value else int(value)
                    if math.isfinite(number) and abs(number)<1e18: row[key]=int(number) if isinstance(number,float) and number.is_integer() else number
            identity=tuple(sorted(row.items()))
            if 'session' in row and 'kind' in row and identity not in seen:
                records.append(row);seen.add(identity)
    return records

def report(text,session=None,printing=True):
    rows=parse(text)
    if session is None:
        session=next((r['session'] for r in reversed(rows) if r['prefix']=='A9GNSS'),None)
        if session is None: session=next((r['session'] for r in reversed(rows)),None)
    rows=[r for r in rows if r['session']==session]
    gps=[r for r in rows if r['prefix']=='A9GNSS' and r['kind'] in ('sample','final')]
    valid=lambda r: r.get('fix')=='yes' and 0<r.get('accuracy_m',-1)<100000 and r.get('ttff_ms',-1)>=0
    fixed=[r for r in gps if valid(r)]
    final=next((r for r in reversed(rows) if r['prefix']=='A9_SLEEP_OUT' and r['kind']=='final'),{})
    sleep_started=any(r['prefix']=='A9_SLEEP_OUT' and r['kind']=='screen_off' for r in rows)
    result={'GNSS_CAPTURE':bool(gps),'GNSS_FIX':bool(fixed),'GNSS_FINISHED':any(r['kind']=='final' for r in gps),
            'metrics':fixed[-1] if fixed else gps[-1] if gps else {},
            'SLEEP_CAPTURE':bool(final),'SLEEP_PHASE_REACHED':sleep_started,'sleep':final,'deltas':[],
            'wakeups':[r for r in rows if r['kind']=='wakeup'],
            'wakeup_irqs':[r for r in rows if r['kind']=='wakeup_irq']}
    counters={}
    for r in rows:
        if r['prefix']!='A9_SLEEP_OUT' or r['kind']!='counter' or 'value' not in r or 'sample' not in r: continue
        key=(r.get('source'),r.get('mode'),r.get('key'))
        counters.setdefault(key,[]).append(r)
    for (source,mode,key),values in counters.items():
        values.sort(key=lambda r:r['sample'])
        if values[-1]['sample']>values[0]['sample']:
            result['deltas'].append(dict(source=source,mode=mode,key=key,delta=values[-1]['value']-values[0]['value']))
    soc_counts=[d for d in result['deltas'] if d['mode'] in ('vlow','vmin') and d['key']=='count']
    result['SOC_SLEEP_ENTRY']=any(d['delta']>0 for d in soc_counts) if soc_counts else None
    if printing:
        for r in rows: print(r['prefix']+' '+' '.join(k+'='+str(v) for k,v in r.items() if k!='prefix'))
        for d in result['deltas']: print('A9_SLEEP_DELTA '+json.dumps(d,sort_keys=True,separators=(',',':')))
        print('SOC_SLEEP_ENTRY '+('OBSERVED' if result['SOC_SLEEP_ENTRY'] else 'NO_INCREMENT' if soc_counts else 'UNAVAILABLE'))
        print(('PASS' if fixed else 'FAIL' if gps else 'UNAVAILABLE')+' GNSS_FIX')
        print(('RECORDED' if final else 'INCOMPLETE' if sleep_started else 'NOT_REACHED')+' SLEEP_CAPTURE')
        availability=[r for r in rows if r['prefix']=='A9_SLEEP_OUT' and r['kind']=='availability']
        for key,label in [('soc_sources','SOC_SLEEP_COUNTERS'),('suspend_fields','SUSPEND_COUNTERS')]:
            print(label+' '+('available' if any(r.get(key,0)>0 for r in availability) else 'unavailable' if availability else 'not_measured'))
    return result

def read_input(path):
    """Only retain target-tag lines; raw phone logs are never echoed."""
    def selected(stream):
        return ''.join(line.decode(errors='replace') for line in stream
                       if b'A9GNSS ' in line or b'A9_SLEEP_OUT ' in line)
    chunks=[]
    if path.is_dir():
        roots=[path] if any((path/n).exists() for n in ('logcat.log','kernel.log','kernel.log.prev')) else list(path.glob('boot-*'))
        if len(roots)!=1: raise SystemExit('Select one pulled boot directory')
        for name in ('logcat.log.prev','logcat.log','kernel.log.prev','kernel.log'):
            p=roots[0]/name
            if p.is_file():
                with p.open('rb') as f: chunks.append(selected(f))
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as tar:
            members=[m for m in tar.getmembers() if m.isfile() and Path(m.name).name in ('logcat.log','logcat.log.prev','kernel.log','kernel.log.prev')]
            if len({str(Path(m.name).parent) for m in members})!=1: raise SystemExit('Select an archive containing one boot')
            for m in sorted(members,key=lambda m:(0 if m.name.endswith('.prev') else 1,m.name)):
                with tar.extractfile(m) as f: chunks.append(selected(f))
    else:
        with path.open('rb') as f: chunks.append(selected(f))
    return '\n'.join(chunks)

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('input',type=Path);ap.add_argument('--json',type=Path)
    args=ap.parse_args();result=report(read_input(args.input))
    if args.json: args.json.write_text(json.dumps(result,indent=2)+'\n')
    return 0 if result['GNSS_FIX'] else 1

if __name__=='__main__': raise SystemExit(main())
