#!/usr/bin/env python3
"""Fake tracefs lifecycle rehearsal only; cannot access real tracefs or phone."""
from pathlib import Path
import json, os, subprocess as sp, sys, tempfile
SRC=Path(__file__).resolve().parent
OUT=Path(sys.argv[1]).resolve()
stub=r'''#!/usr/bin/env python3
from pathlib import Path
import os,shutil,sys
r=Path(os.environ['FIXTURE']);cmd=Path(sys.argv[0]).name
if cmd=='mkdir':
 for arg in sys.argv[1:]:
  p=Path(arg);p.mkdir()
  if p.parent==r/'trace/instances':
   group=p.name
   for n in ['tracing_on','current_tracer','buffer_size_kb','trace_clock','trace','trace_pipe','options/overwrite','events/enable','per_cpu/cpu0/stats']:
    f=p/n;f.parent.mkdir(exist_ok=True,parents=True);f.write_text('mono local' if n=='trace_clock' else '0')
   events=['msm/'+n for n in ['msm_atomic_commit_tail_start','msm_atomic_commit_tail_finish','msm_atomic_flush_commit','msm_atomic_wait_flush_start','msm_atomic_wait_flush_finish']]
   events+=['sched/'+n for n in ['sched_switch','sched_waking','sched_wakeup','sched_migrate_task']]+['power/cpu_idle']+['irq/'+n for n in ['irq_handler_entry','irq_handler_exit','softirq_entry','softirq_exit']]
   events += [group+'/'+group+'_'+n for n in ['flush_in','flush_value','vsync_in','vsync_out','wait_in','wait_out']]
   for name in events:
    if os.environ['SCENARIO']=='missing' and name=='msm/msm_atomic_flush_commit':continue
    if os.environ['SCENARIO']=='probe-enable-fail' and name.endswith('_flush_value'):continue
    d=p/'events'/name;d.mkdir(exist_ok=True,parents=True)
    for n in ['enable','filter','format']:(d/n).write_text('0')
   if os.environ['SCENARIO']=='probe-fail':
    (r/'trace/kprobe_events').unlink();(r/'trace/kprobe_events').symlink_to('/dev/full')
elif cmd=='rmdir':
 p=Path(sys.argv[1]);assert p.parent==r/'trace/instances'
 assert (p/'tracing_on').read_text().strip()=='0' and (p/'events/enable').read_text().strip()=='0'
 shutil.rmtree(p);(r/'cleaned').touch()
elif cmd=='dmesg':print('synthetic private dmesg')
'''
watch=r'''#!/usr/bin/env python3
from pathlib import Path
import json,os,sys,time
r=Path(os.environ['FIXTURE']);inst=Path(sys.argv[2]);out=Path(sys.argv[3])
group=inst.name;assert sys.argv[1]=='capture'
assert (inst/'events/msm/msm_atomic_wait_flush_start/filter').read_text().strip()=='common_pid == 41'
assert (inst/'events/sched/sched_switch/filter').read_text().strip()=='prev_pid == 41 || next_pid == 41'
probes=(r/'trace/kprobe_events').read_text();assert 'r64:' in probes and 'value=$retval:x32' in probes and 'dpu_encoder_phys_vid_vblank_irq' in probes
(inst/'tracing_on').write_text('1\n')
(out/'trace-private.txt').write_text('synthetic phase5 trace\n');(out/'trigger.json').write_text(json.dumps({'triggered':True}))
(inst/'tracing_on').write_text('0\n')
if os.environ['SCENARIO']=='watcher-fail':sys.exit(17)
'''
results=[]
for scenario in ['success','missing','probe-fail','probe-enable-fail','watcher-fail']:
 with tempfile.TemporaryDirectory(prefix='a9-p5-wrapper-') as tmp:
  r=Path(tmp);(r/'trace/instances').mkdir(parents=True);(r/'trace/kprobe_events').touch();(r/'trace/kprobe_profile').touch();(r/'bin').mkdir()
  for n in ['mkdir','rmdir','dmesg']:
   p=r/'bin'/n;p.write_text(stub);p.chmod(0o755)
  for name,text in [('comm','a9_eink'),('sched','policy: FIFO50'),('status','Cpus_allowed_list: 0-7')]:
   p=r/'proc/41'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text+'\n')
  for n in ['sys/kernel/sched_rt_runtime_us','sys/kernel/sched_rt_period_us','interrupts','uptime']:
   p=r/'proc'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('1000000\n')
  s=(SRC/'phase5-capture.sh').read_text().replace('tr=/sys/kernel/tracing','tr='+str(r/'trace')).replace('/proc/',str(r/'proc')+'/').replace('/sys/class/drm/',str(r/'sys/class/drm')+'/')
  script=r/'phase5-capture.sh';script.write_text(s);p=r/'phase5-watch';p.write_text(watch);p.chmod(0o755)
  env=dict(os.environ,PATH=str(r/'bin')+':'+os.environ['PATH'],FIXTURE=str(r),SCENARIO=scenario)
  run=sp.run(['sh',str(script),str(r/'output'),'1','30000'],env=env,capture_output=True,text=True,timeout=10)
  assert (r/'cleaned').exists() and not list((r/'trace/instances').iterdir()),run.stderr
  if scenario=='success':
   assert run.returncode==0,run.stderr
   probes=(r/'trace/kprobe_events').read_text().splitlines();assert sum(x.startswith('-:') for x in probes)==6 and len(probes)==12
  else:
   assert run.returncode==(17 if scenario=='watcher-fail' else 1),(scenario,run.returncode,run.stderr)
   if scenario=='probe-enable-fail':
    probes=(r/'trace/kprobe_events').read_text().splitlines();assert sum(x.startswith('-:') for x in probes)==2 and len(probes)==4
  results.append({'scenario':scenario,'rc':run.returncode,'own_instance_probe_cleanup':'PASS','fake_tracefs_only':True})
(OUT/'wrapper-tests.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results))
