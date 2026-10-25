#!/usr/bin/env python3
"""Fake-only row isolation and final PIN ordering; no device access."""
import contextlib,importlib.util,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('independent_fake',HERE/'selftest.py');fake=importlib.util.module_from_spec(spec);spec.loader.exec_module(fake);m=fake.sweep
class Rows(unittest.TestCase):
 setUp=fake.Tests.setUp
 def test_photo_exception_preserves_other_rows_and_cleanup(self):
  s=fake.Fake();original=s.app
  def app(package,activity,extras='',seconds=90):
   if package=='org.a9.cameraprobe' and 'front' in extras:raise RuntimeError('PRIVATE_CAMERA')
   return original(package,activity,extras,seconds)
  with patch.object(s,'app',side_effect=app):s.execute()
  self.assertEqual(s.results['19_front_photo']['status'],'FAIL')
  self.assertEqual(s.results['19_front_photo']['evidence']['row_error'],1)
  for row in ('19_rear_photo','19_front_video','19_rear_video','08_22_sensors','13_bluetooth','12_wifi','18_hardware_encoder','cleanup'):self.assertEqual(s.results[row]['status'],'PASS',row)
  self.assertNotIn('PRIVATE_CAMERA',(s.out/'acceptance.json').read_text()+self.output.getvalue())
 def test_display_exception_and_checkin_exception_are_local(self):
  s=fake.Fake()
  with patch.object(s,'eink',side_effect=RuntimeError('PRIVATE')),patch.object(s,'checkin',side_effect=ValueError('PRIVATE')):s.execute()
  for row in ('02_03_display_hwc','28_checkin'):self.assertEqual(s.results[row]['status'],'FAIL',row)
  for row in ('06_07_input_devices','25_thermal','29_wrapped_fbe','24_battery','12_wifi','28_play_install_launch','26_selinux','cleanup'):self.assertEqual(s.results[row]['status'],'PASS',row)
 def test_pin_failure_is_last_even_after_qualification_and_reserves_time(self):
  for qualify in ('0','1'):
   order=[];made=[];original=importlib.util.spec_from_file_location
   shim=Path(self.tmp.name)/('qualification-'+qualify+'.py');shim.write_text('def run(s):\n s.order.append("qualification")\n s.put("18_hardware_encoder",True,qualification=1)\n')
   class Sweep(fake.Fake):
    def pin_lifecycle(this):
     order.append('pin');this.pin_budget=this.command_end-m.time.monotonic()
     this.pin_cleanup_ok=False;this.put('29_pin_lifecycle',False,cleanup_attempted=1,cleanup_ok=0)
   def factory():
    s=Sweep();s.order=order;made.append(s);return s
   def load(name,path):return original(name,shim if name=='last_hvenc' else path)
   with patch.dict(os.environ,ADB='INERT',A9_BTRY_IN_HOOK='1',A9_SWEEP_HVENC_QUALIFY=qualify,A9_SWEEP_PIN_LIFECYCLE='fresh-disposable-formatted-b'),patch.object(m,'Sweep',side_effect=factory),patch.object(importlib.util,'spec_from_file_location',side_effect=load),patch.object(m.signal,'signal'):
    self.assertEqual(m.main(),1)
   s=made[0];self.assertGreaterEqual(s.pin_budget,180)
   self.assertEqual(order,['qualification','pin'] if qualify=='1' else ['pin'])
   for row in ('12_wifi','13_bluetooth','19_front_photo','19_rear_photo','19_front_video','19_rear_video','18_hardware_encoder','26_selinux','A_stability'):self.assertEqual(s.results[row]['status'],'PASS',row)
   self.assertEqual(s.results['cleanup']['status'],'FAIL');self.assertEqual(s.results['cleanup']['evidence']['pin_cleanup_ok'],0)
   self.assertNotIn('SECRET_TEST',(s.out/'acceptance.json').read_text())
 def test_interruption_is_not_swallowed_as_a_row_failure(self):
  s=fake.Fake()
  with self.assertRaises(InterruptedError):s.independent(('12_wifi',),lambda:(_ for _ in ()).throw(InterruptedError()))
 def test_owner_interruption_never_starts_final_pin(self):
  made=[]
  class Sweep(fake.Fake):
   def execute(this):
    this.touched=True
    this.put('29_wrapped_fbe',True)
    raise InterruptedError('PRIVATE')
   def pin_lifecycle(this):raise AssertionError('new credential after interruption')
  def factory():
   s=Sweep();made.append(s);return s
  with patch.dict(os.environ,ADB='INERT',A9_BTRY_IN_HOOK='1',A9_SWEEP_PIN_LIFECYCLE='fresh-disposable-formatted-b'),patch.object(m,'Sweep',side_effect=factory),patch.object(m.signal,'signal'):
   self.assertEqual(m.main(),1)
  self.assertEqual(made[0].results['29_pin_lifecycle']['status'],'NOT_RUN')
  self.assertEqual(made[0].results['cleanup']['status'],'PASS')
 def test_pin_fail_with_confirmed_cleanup_is_a_row_verdict(self):
  made=[]
  class Sweep(fake.Fake):
   def pin_lifecycle(this):this.put('29_pin_lifecycle',False,set=0,cleanup_ok=1)
  def factory():
   s=Sweep();made.append(s);return s
  with patch.dict(os.environ,ADB='INERT',A9_BTRY_IN_HOOK='1',A9_SWEEP_PIN_LIFECYCLE='fresh-disposable-formatted-b'),patch.object(m,'Sweep',side_effect=factory),patch.object(m.signal,'signal'):
   self.assertEqual(m.main(),0)
  s=made[0];self.assertEqual(s.results['29_pin_lifecycle']['status'],'FAIL')
  for row in ('12_wifi','13_bluetooth','19_rear_video','18_hardware_encoder','cleanup'):self.assertEqual(s.results[row]['status'],'PASS',row)
 def test_bad_af_measurement_does_not_skip_torch_or_video(self):
  s=fake.Fake();s.assets_ok=True;original=s.app
  def app(package,activity,extras='',seconds=90):
   if 'camera_controls' in extras:return dict(completed=1,available=1,af_available=1,af_locked='PRIVATE',torch_available=1,torch_fired=1,torch_off_result=1,torch_manager_on=1,torch_manager_off=1)
   return original(package,activity,extras,seconds)
  with patch.object(s,'app',side_effect=app):s.sensors_media()
  for face in ('front','rear'):
   self.assertEqual(s.results['19_'+face+'_af']['status'],'FAIL')
   self.assertEqual(s.results['19_'+face+'_torch']['status'],'PASS')
   self.assertEqual(s.results['19_'+face+'_video']['status'],'PASS')
if __name__=='__main__':unittest.main(verbosity=2)
