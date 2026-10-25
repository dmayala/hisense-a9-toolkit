#!/usr/bin/env python3
"""Replay the retained integ27 failure sequence; every transport is inert."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess as sp
import tempfile
import unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
MAIN=HERE.parent
ROOT=MAIN.parents[1]
FROZEN='eda8a0d48380ad3baf52d8b964274f60ef612db7'
def load(name,path,old=False):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec)
 if old:
  text=sp.check_output(['git','show',FROZEN+':'+str(path.relative_to(ROOT))],cwd=ROOT,text=True)
  exec(compile(text,str(path),'exec'),m.__dict__)
 else:spec.loader.exec_module(m)
 return m
v=load('replay_variants',MAIN/'video/qualification/variants.py')
r=load('replay_single',MAIN/'video/qualification/run.py')
d=load('replay_drain',HERE/'drain.py')
fixture=json.loads((HERE/'fixtures/integ27-harness.json').read_text())
class Clock:
 def __init__(self):self.t=1000.
 def now(self):self.t+=.0001;return self.t
 def sleep(self,n):self.t+=n
class Sweep:
 def __init__(self,out,clock):
  self.out=out;self.assets=out/'assets';self.adb='INERT';self.clock=clock
  self.qualification_end=clock.t+7000;self.command_end=self.qualification_end
  self.hvenc_qualify=self.touched=True;self.hvenc_qualification_attempted=False
  self.results={'A_stability':dict(status='PASS',evidence={})};self.native_calls=[]
 def put(self,key,ok,**kw):self.results[key]=dict(status='PASS' if ok else 'FAIL',evidence=kw)
 def native(self,script,timeout):
  self.native_calls.append(script)
  flags=re.findall(r"printf '%s\\n' ([YN]) >",script)
  assert len(flags)==3
  return 0,''.join('HVENC_PARAMETER '+n+' '+str(int(flag=='Y'))+'\n' for n,flag in zip(('a9_mapped_eos','a9_output_size','a9_stock_work_mode'),flags))
 def shell(self,cmd,**kw):
  if cmd.startswith('getprop'):return 0,'_b\n1\n'
  if cmd.startswith('mktemp'):return 0,'/data/local/tmp/a9-hvenc.ABCDEF\n'
  if cmd.startswith('timeout'):self.clock.t+=55;return 124,'HVENC_QUAL op=6 phase=0 rc=0 frames=0\n'
  if cmd.startswith('stat'):return 1,''
  if cmd.startswith(('chmod','rm -r --')):return 0,''
  raise AssertionError(cmd)
 def run(self,args,**kw):assert args[0]=='INERT' and args[1]=='push';return 0,''
class Tests(unittest.TestCase):
 def test_recorded_drain_cleanup_four_skips_then_pin(self):
  rows=fixture['sequence'];self.assertEqual([x['row'] for x in rows],['10_hourly_drain','cleanup']+['18_hardware_encoder']*4+['29_pin_lifecycle'])
  for old in (True,False):
   with self.subTest(frozen=old),tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'A9_BTRY_IN_HOOK':'1','ADB':'INERT'},clear=False):
    clock=Clock();out=Path(tmp);module=load('frozen_drain',HERE/'drain.py',True) if old else d
    native=module.Native({'control':2});native.root=lambda source:(_ for _ in ()).throw(RuntimeError('retained wakeup command failed'))
    native.snapshot=lambda:dict(boot_ns=int(clock.t*1e9),mono_ns=int(clock.t*1e9),uncertainty_ns=1,capacity=70,charge_counter=3000000-int(clock.t),charge_full_design=4000000,usb_present=1,wifi=1,screen_off=1,input_paused=1,charging=0)
    native.mode=lambda pause:None;native.prepare_screen=lambda:None;native.restore=lambda:None
    class Watch:
     def __init__(self,*a):pass
     def ping(self):pass
     def finish(self):return True
    result,restored=module.measure(native,Watch,out,10000,clock.now,clock.sleep)
    if old:self.assertEqual(result,rows[0]['evidence'])
    else:
     self.assertEqual(result['attempted'],1);self.assertEqual(result['measured'],1)
     self.assertEqual(result['wakeup_sources_recorded'],0);self.assertTrue(restored)
    # Replay cleanup success, then the real wrapper and real single qualifier.
    s=Sweep(out,clock);s.cleanup_ok=True
    wrapper=load('frozen_variants',MAIN/'video/qualification/variants.py',True) if old else v
    with patch.object(wrapper.time,'monotonic',clock.now),patch.object(r.v,'verify_bundle',return_value={}):wrapper.run(s,r.run)
    cases=json.loads((out/'hvenc-variants.json').read_text())['cases']
    self.assertEqual(len(cases),4)
    for i,case in enumerate(cases):
     ev=case['result']['evidence']
     if old:self.assertEqual(ev,rows[i+2]['evidence'])
     else:
      self.assertEqual(ev['attempted'],1);self.assertEqual(ev['slot_b'],1)
      self.assertEqual(ev['staged'],1);self.assertEqual(ev['budget_ok'],1)
      self.assertEqual(ev['skip_reason'],0)
      self.assertGreater(ev['remaining_ms'],145000)
    self.assertEqual(len(s.native_calls),5)
    self.assertIn("printf '%s\\n' N",s.native_calls[-1])
    self.assertEqual(rows[-1]['evidence']['finally_unsecured'],1)
 def test_sysfs_fallback_uses_actual_android_shell_and_interrupts_propagate(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'A9_BTRY_IN_HOOK':'1','ADB':'INERT'}):
   root=Path(tmp);w=root/'wakeup0';w.mkdir();(w/'name').write_text('fixture\n')
   for name in ('active_count','event_count','wakeup_count','total_time_ms','prevent_suspend_time_ms'):(w/name).write_text('7\n')
   native=d.Native({'control':2})
   shell='/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sh'
   def run(source):
    source=source.replace('/sys/kernel/debug/wakeup_sources',str(root/'missing')).replace('/sys/class/wakeup/wakeup*',str(root/'wakeup*'))
    return sp.check_output([shell],input='set -e\n'+source,text=True,timeout=5)
   with patch.object(native,'root',side_effect=run):self.assertTrue(native.wakeups(root/'before'))
   self.assertIn('fixture 7 7 7 7 7',(root/'before').read_text())
   with patch.object(native,'root',side_effect=InterruptedError()):
    with self.assertRaises(InterruptedError):native.wakeups(root/'after')
if __name__=='__main__':unittest.main(verbosity=2)
