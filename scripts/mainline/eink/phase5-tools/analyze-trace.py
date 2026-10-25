#!/usr/bin/env python3
"""Run locally on the user's saved trace. Emits timing/values, no task names."""
import json,re,sys
from pathlib import Path
if len(sys.argv)!=2:raise SystemExit('usage: analyze-trace.py trace-private.txt')
pattern=re.compile(r'-(\d+)\s+(?:\(\s*\d+\)\s+)?\[(\d+)\]\s+(\S+)\s+(\d+)\.(\d+):\s+(\w+):\s*(.*)')
def quantiles(v):
 if not v:return {'n':0}
 v=sorted(v)
 return {'n':len(v),'min_us':round(v[0],3),'p50_us':round(v[(len(v)-1)//2],3),'p95_us':round(v[int((len(v)-1)*.95)],3),'max_us':round(v[-1],3)}
phases=[];pending=False;active=None;unparsed=0
for line in Path(sys.argv[1]).open(errors='replace'):
 m=pattern.search(line)
 if not m:unparsed+=1;continue
 pid,cpu,flags,sec,frac,event,payload=m.groups();pid=int(pid);cpu=int(cpu)
 stamp=int(sec)*10**9+int((frac+'000000000')[:9])
 if event=='msm_atomic_commit_tail_start':pending=False;active=None
 elif event=='msm_atomic_flush_commit':pending=True
 elif event=='msm_atomic_wait_flush_start' and pending:
  active={'waiter_pid':pid,'start_ns':stamp,'end_ns':None,'samples':[],'vsync_ns':[],'sched':[],'idle':[]};phases.append(active);pending=False
 if active is not None:
  # h/H are hardirq context; s is softirq. IRQ samples can have the waiter pid.
  if event.endswith('_flush_value'):
   v=re.search(r'value=(0x[0-9a-fA-F]+|[0-9a-fA-F]+)',payload)
   if v:active['samples'].append({'ns':stamp,'cpu':cpu,'pid':pid,'irq_context':bool(re.search('[hHs]',flags)),'value':int(v[1],16)})
  elif event.endswith('_vsync_in'):active['vsync_ns'].append(stamp)
  elif event in ['sched_switch','sched_waking','sched_wakeup','sched_migrate_task']:
   # Numeric-only fields, no comm strings.
   active['sched'].append({'ns':stamp,'cpu':cpu,'event':event,'fields':dict(re.findall(r'\b(prev_pid|next_pid|pid|prio|target_cpu|orig_cpu|dest_cpu|prev_state)=(\S+)',payload))})
  elif event=='cpu_idle':
   active['idle'].append({'ns':stamp,'cpu':cpu,'fields':dict(re.findall(r'\b(state|cpu_id)=(\S+)',payload))})
  if event=='msm_atomic_wait_flush_finish':active['end_ns']=stamp;active=None
out=[]
for p in phases:
 samples=[s for s in p['samples'] if s['pid']==p['waiter_pid'] and not s['irq_context']]
 gaps=[{'gap_us':(b['ns']-a['ns'])/1000,'before_ns':a['ns'],'after_ns':b['ns'],'before_value':a['value'],'after_value':b['value'],'before_cpu':a['cpu'],'after_cpu':b['cpu']} for a,b in zip(samples,samples[1:]) if b['ns']>=a['ns']]
 out.append({**{k:v for k,v in p.items() if k not in ['samples','vsync_ns']},'duration_us':None if p['end_ns'] is None else (p['end_ns']-p['start_ns'])/1000,'poll_return_spacing':quantiles([g['gap_us'] for g in gaps]),'largest_poll_gaps':sorted(gaps,key=lambda x:x['gap_us'],reverse=True)[:10],'poll_zero_samples':sum(s['value']==0 for s in samples),'poll_nonzero_samples':sum(s['value']!=0 for s in samples),'first_observed_zero_ns':next((s['ns'] for s in samples if s['value']==0),None),'vsync_entry_ns':p['vsync_ns']})
print(json.dumps({'phases':out,'unparsed_lines':unparsed,'limitations':['Poll return spacing includes instrumentation. No observed zero does not prove hardware stayed nonzero between samples. Inspect buffer-stats and kprobe-profile nmissed before drawing absence conclusions. A trace starting mid-phase cannot reconstruct its beginning.']},indent=2))
