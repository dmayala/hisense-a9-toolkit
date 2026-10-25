#!/usr/bin/env python3
"""Production watch/recover/open against fake sysfs; no device/transport access.

The kernel's synchronous grant is modeled when the written recover_stall request
is next observed. Physical frames advance only with all three gates open. Unlike
r3's fixture, kind becomes0 only AFTER the request, and old template kmsg is gone.
"""
from pathlib import Path
import argparse,json,os,shutil,subprocess as sp,tempfile
R=Path(__file__).resolve().parents[4]
ap=argparse.ArgumentParser();ap.add_argument('--script',type=Path,default=R/'scripts/mainline/android/vendor/bin/a9ml-eink.sh');ap.add_argument('--shell',default='sh');ap.add_argument('--out',type=Path,required=True);ap.add_argument('--expect-old',action='store_true');a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
# Commands operate on only this temporary fixture. Real sysfs/proc/dmesg are
# never read. No production-shell functions are replaced or extracted.
stub=r'''#!/usr/bin/env python3
from pathlib import Path
import os,sys,re,json,subprocess
r=Path(os.environ['FIXTURE']);e=r/'sys/devices/platform/eink-display/drm/card1/card1-DSI-1/eink';p=r/'props';cmd=Path(sys.argv[0]).name
params=[r/'sys/module'/n/'parameters'/v for n,v in [('a9_eink','enable'),('fp9929a','allow_power_on'),('panel_hisense_hlte556n_eink','enable')]]
def prop(n):
 lines=[s.split('=',1)[1] for s in p.read_text().splitlines() if s.startswith(n+'=')];return lines[-1] if lines else ''
def put(n,v):
 with p.open('a') as f:f.write(n+'='+v+'\n')
def status(**updates):
 t=(e/'status').read_text()
 for k,v in updates.items():t=re.sub(r'\b'+k+r' [^ \n]+',k+' '+str(v),t,count=1)
 (e/'status').write_text(t)
def grant():
 if (e/'recover_stall').read_text().strip()=='1' and not (r/'grant').exists():
  (r/'grant').write_text('1');status(stall_fault=0,updates=435,dry=1,aborted=0)
  (e/'stall_recovery').write_text('kind 0 retries 1 limit 2 backoff_ms 30000 result 0 pmic_hard 0 transport_hard 0 grants '+('3' if os.environ['SCENARIO']=='refilled' else '1')+'\n')
if cmd=='cat':
 if len(sys.argv)==2 and Path(sys.argv[1]).resolve()==e/'stall_recovery':grant()
 os.execv(os.environ['REAL_CAT'],[os.environ['REAL_CAT'],*sys.argv[1:]])
elif cmd=='dmesg':
 grant()
 # The old waveform/template event has rotated out of the kernel ring.
 print('[ 100.0] a9-eink: frame-progress fault latched')
 if (r/'grant').exists():print('[ 101.0] a9-eink: starvation recovery granted 1/2')
elif cmd=='getprop':print(prop(sys.argv[1]))
elif cmd=='setprop':
 if len(sys.argv)>2 and len(sys.argv[2])>91:raise SystemExit('Android property value too long')
 put(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else '')
 if sys.argv[1]=='vendor.a9ml.eink.gate' and sys.argv[2]=='open':
  (r/'open-gates').write_text(''.join(x.read_text().strip() for x in params))
elif cmd=='sleep':
 delay=sys.argv[1]
 if delay=='5' and os.environ['SCENARIO']=='cancel':put('vendor.a9ml.eink.gate','closed')
 if delay in ('1','0.5') and prop('vendor.a9ml.eink.gate')=='open':
  enabled=all(x.read_text().strip() in ('1','Y') for x in params)
  if enabled:
   status(enable=1,updates=436,frames=3100,dry=0,aborted=0)
   (r/'progress').write_text('gate=open params=111 enable=1 updates=436 frames=3100 dry=0\n')
   if os.environ['SCENARIO']=='hard':
    (e/'stall_recovery').write_text('kind 2 retries 1 limit 2 backoff_ms 30000 result 0 pmic_hard 0 transport_hard 1 grants 1\n')
   else:
    # Let the production watcher inspect progress once, then cancel after a
    # later poll. Its ordinary close path is tested separately after return.
    n=r/'poll';v=int(n.read_text())+1 if n.exists() else 1;n.write_text(str(v))
    if v>=2:put('vendor.a9ml.eink.gate','closed')
'''
for scenario in (['wrapped'] if a.expect_old else ['wrapped','refilled','hard','cancel','manual-kind0']):
 with tempfile.TemporaryDirectory(prefix='a9-reopen-') as directory:
  root=Path(directory)
  setup='eval "$(sed \'/^R=$(mktemp/,$d\' "$1")"; mk "$2" N'
  sp.run(['bash','-c',setup,'fixture',str(R/'scripts/mainline/android/tests/test-eink-gates.sh'),str(root)],check=True)
  e=root/'sys/devices/platform/eink-display/drm/card1/card1-DSI-1/eink'
  (e/'stall_recovery').write_text('kind 3 retries 0 limit 2 backoff_ms 5000 result 0 pmic_hard 0 transport_hard 0 grants '+('2' if scenario=='refilled' else '0')+'\n')
  (e/'recover_stall').touch();(e/'stall_snapshot').write_text('valid 1 first_at_ns 100000000000 in_commit 1 front_null 0 stage 2 phase 5 phase_age_us 36000\nlatest_kind 3 raw_ret 0 return_after_cut_us 90000 return_ok 1 commit_hard 0 transport_error 0\n')
  s=(e/'status').read_text().replace('enable 0','enable 0').replace('transport 0','transport 1').replace('stall_fault 0','stall_fault 1').replace('updates 2','updates 404').replace('frames 0','frames 3078',1).replace('aborts 0','aborts 190').replace('commit_fail 0','commit_fail 1')+'dry 0 aborted 1\n';(e/'status').write_text(s)
  with (root/'props').open('a') as f:f.write('vendor.a9ml.eink.gate=open\n')
  for name in ('cat','dmesg','getprop','setprop','sleep'):
   p=root/'stubs'/name;p.write_text(stub);p.chmod(0o755)
  # Recursive `sh script ...` follows the same interpreter as the parent.
  p=root/'stubs/sh';p.write_text('#!/bin/sh\nexec '+str(Path(a.shell).resolve() if '/' in a.shell else shutil.which(a.shell))+' "$@"\n');p.chmod(0o755)
  env=dict(os.environ,PATH=str(root/'stubs')+':'+os.environ['PATH'],FIXTURE=str(root),SCENARIO=scenario,REAL_CAT=shutil.which('cat'),A9ML_FAULT_DIR=str(root/'faults'),A9ML_SYS=str(root/'sys'),A9ML_PROC=str(root/'proc'),A9ML_KMSG=str(root/'kmsg'),A9ML_FWDIR=str(root/'fw'))
  verb='watch'
  if scenario=='manual-kind0':
   (e/'stall_recovery').write_text('kind 0 retries 1 limit 2 backoff_ms 30000 result 0 pmic_hard 0 transport_hard 0 grants 1\n');verb='recover'
  run=sp.run([a.shell,'-x',str(a.script),verb],env=env,capture_output=True,text=True,timeout=60)
  (a.out/(scenario+'.trace')).write_text(run.stdout+run.stderr)
  props=(root/'props').read_text();kmsg=(root/'kmsg').read_text();(a.out/(scenario+'.kmsg')).write_text(kmsg);(a.out/(scenario+'.props')).write_text(props)
  gates=''.join((root/'sys/module'/n/'parameters'/v).read_text().strip() for n,v in [('a9_eink','enable'),('fp9929a','allow_power_on'),('panel_hisense_hlte556n_eink','enable')])
  if a.expect_old:
   assert run.returncode!=0 and not (root/'progress').exists() and gates=='000'
   assert 'no \'control template loaded\' line' in kmsg
   print('PASS reproduces r3: grant consumed, old template event absent, reopen refused, no driven progress')
  elif scenario in ('wrapped','refilled'):
   assert run.returncode==0 and (root/'grant').exists()
   assert (root/'open-gates').read_text()=='111' and (root/'progress').exists()
   assert 'snapshot' in kmsg and 'in_commit 1' in kmsg and 'phase 5' in kmsg
   assert 'recovery driven progress' in kmsg
   print('PASS '+scenario+': kind3 -> monotonic grant/kind0 -> normal admission -> gates111 -> driven frames/updates advance; snapshot in kmsg')
  elif scenario=='cancel':
   assert run.returncode==0 and not (root/'grant').exists() and gates=='000'
   print('PASS explicit close during backoff cancels with no kernel grant or reopen')
  elif scenario=='manual-kind0':
   assert run.returncode!=0 and gates=='000'
   print('PASS kind0 manual request cannot manufacture another grant; nonzero rc')
  else:
   assert run.returncode!=0 and gates=='000'
   assert 'transport completion hard fault' in kmsg
   print('PASS new hard transport error after grant closes all gates and retains cause')
