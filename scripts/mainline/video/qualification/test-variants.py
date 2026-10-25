#!/usr/bin/env python3
"""One-boot variant control-flow test; all transports and encoders are inert."""
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('variants',HERE/'variants.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
spec=importlib.util.spec_from_file_location('acceptance',HERE.parents[1]/'acceptance/onb.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class Fake:
 def __init__(self,out):
  self.out=out;self.qualification_end=time.monotonic()+700;self.command_end=self.qualification_end
  self.hvenc_qualification_attempted=False;self.results={};self.calls=[]
 def native(self,script,timeout):
  self.calls.append(('parameters',script))
  flags=re.findall(r'printf \'%s\\n\' ([YN]) >',script)
  assert len(flags)==3
  return 0,''.join('HVENC_PARAMETER '+n+' '+str(int(x=='Y'))+'\n' for n,x in zip(('a9_mapped_eos','a9_output_size','a9_stock_work_mode'),flags))
 def put(self,name,ok,**kw):self.results[name]=dict(status='PASS' if ok else 'FAIL',evidence=kw)

class Tests(unittest.TestCase):
 def test_four_cases_are_independent_and_restore_defaults(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Fake(Path(tmp));original_end=s.qualification_end;seen=[]
   def qualify(s):
    self.assertFalse(s.hvenc_qualification_attempted)
    self.assertLessEqual(s.qualification_end,original_end-12)
    seen.append(s.out.name);s.hvenc_qualification_attempted=True
    s.put('18_hardware_encoder',len(seen)==3,attempted=1)
    (s.out/'hvenc-qualification.json').write_text('{}\n')
   v.run(s,qualify)
   self.assertEqual(seen,['case-1','case-2','case-3','case-4'])
   result=json.loads((Path(tmp)/'hvenc-variants.json').read_text())
   self.assertEqual([(x['output_size'],x['stock_work_mode']) for x in result['cases']],[(1,1),(1,0),(0,1),(0,0)])
   self.assertEqual(sum(x['passed'] for x in result['cases']),1)
   self.assertEqual(result['reset_ok'],1);self.assertEqual(result['phone_boots_requested'],0)
   self.assertEqual(s.results['18_hardware_encoder']['status'],'PASS')
   self.assertEqual(s.qualification_end,original_end);self.assertEqual(s.out,Path(tmp))
   self.assertEqual(len(s.calls),5);self.assertTrue(s.hvenc_qualification_attempted)
   v.run(s,qualify);self.assertEqual(len(s.calls),5)
 def test_case_exception_keeps_later_variants(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Fake(Path(tmp));seen=[]
   def qualify(s):
    seen.append(s.out.name)
    if len(seen)==1:raise OSError('inert first-case failure')
    s.put('18_hardware_encoder',True,attempted=1)
   v.run(s,qualify)
   self.assertEqual(len(seen),4)
   result=json.loads((Path(tmp)/'hvenc-variants.json').read_text())
   self.assertEqual(result['cases'][0]['internal_error'],1)
   self.assertEqual(sum(row['passed'] for row in result['cases']),3)
 def test_interruption_restores_and_never_starts_another_case(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Fake(Path(tmp))
   def interrupted(s):raise InterruptedError()
   with self.assertRaises(InterruptedError):v.run(s,interrupted)
   self.assertEqual(len(s.calls),2)
   self.assertEqual(json.loads((Path(tmp)/'hvenc-variants.json').read_text())['reset_ok'],1)
 def test_short_budget_never_extends_hold(self):
  with tempfile.TemporaryDirectory() as tmp:
   s=Fake(Path(tmp));s.qualification_end=time.monotonic()+150;end=s.qualification_end
   v.run(s,lambda s:self.fail('no time for a bounded case'))
   self.assertEqual(len(s.calls),1);self.assertEqual(s.qualification_end,end)
   self.assertEqual(s.results['18_hardware_encoder']['evidence']['attempted'],0)
 def test_owner_interruption_cannot_start_pin_or_usb(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);shim=root/'interrupt.py';shim.write_text('def run(s):\n    raise InterruptedError()\n')
   class Sweep:
    independent=m.Sweep.independent
    end=time.monotonic()+700
    hvenc_qualify=hvenc_variants=touched=True
    results={}
    def execute(self):pass
    def cleanup(self):self.put('cleanup',True)
    def persist(self):pass
    def save(self):pass
    def collect_anr_evidence(self,force=False):pass
    def fingerprint_privacy(self):self.put('30_fp_no_dumps',True)
    def final_pin(self):raise AssertionError('PIN after interruption')
    def usb_restart(self):raise AssertionError('reboot after interruption')
    def put(self,name,ok,**kw):self.results[name]=dict(status='PASS' if ok else 'FAIL',evidence=kw)
   original=importlib.util.spec_from_file_location
   def choose(name,path,*a,**kw):return original(name,shim if name=='last_hvenc' else path,*a,**kw)
   env=dict(A9_BTRY_IN_HOOK='1',ADB='INERT',A9_BTRY_LOGS=tmp,A9_SWEEP_HVENC_QUALIFY='1',A9_SWEEP_HVENC_VARIANTS='1')
   with patch.dict(os.environ,env),patch.object(m,'Sweep',Sweep),patch.object(m.signal,'signal'),patch.object(importlib.util,'spec_from_file_location',choose):
    self.assertEqual(m.main(),1)
 def test_default_off_and_variant_reservation(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,dict(A9_BTRY_LOGS=tmp,A9_SWEEP_HVENC_QUALIFY='1',A9_SWEEP_SECONDS='2370',A9_SWEEP_PIN_LIFECYCLE='fresh-disposable-formatted-b')):
   with patch.dict(os.environ,A9_SWEEP_HVENC_VARIANTS='0'):ordinary=m.Sweep()
   with patch.dict(os.environ,A9_SWEEP_HVENC_VARIANTS='1'):variants=m.Sweep()
   self.assertEqual(ordinary.hvenc_extra_reserve,0);self.assertEqual(variants.hvenc_extra_reserve,v.EXTRA_RESERVE)
   self.assertAlmostEqual(ordinary.qualification_end,variants.qualification_end,delta=1)
   self.assertAlmostEqual(ordinary.command_end-variants.command_end,v.EXTRA_RESERVE,delta=1)

if __name__=='__main__':unittest.main(verbosity=2)
