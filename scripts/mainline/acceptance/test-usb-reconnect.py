#!/usr/bin/env python3
"""One host regression: FunctionFS reconnect/readback; all transports inert."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('reconnect_fixture',Path(__file__).with_name('selftest.py'))
fake=importlib.util.module_from_spec(spec);spec.loader.exec_module(fake)
m=fake.sweep

class Reconnect(unittest.TestCase):
    setUp=fake.Tests.setUp

    def test_mtp_readback_and_restart_baseline_survive_transport_bounce(self):
        class Sweep(fake.Fake):
            def __init__(s,permanent=False):
                super().__init__();s.after_mtp=False;s.getter_reads=0;s.baseline_reads=0
                s.readback_missing=False;s.permanent=permanent;s.setters=0
                s.touched=s.assets_ok=True
            def native(s,source,timeout=78):
                if 'native_ready' in source:raise AssertionError('diagnostic helper is not USB readiness')
                return super().native(source,timeout)
            def shell(s,source,**kw):
                if source.startswith('umask 077; mkdir /data/local/tmp/a9-sweep-usb-'):s.setters+=1
                if s.after_mtp and source=='svc usb getFunctions 2>&1':
                    s.getter_reads+=1;s.readback_missing=s.permanent or s.getter_reads==1
                    if s.readback_missing:return 255,''
                if s.after_mtp and source=='dumpsys usb' and s.readback_missing:return 1,''
                if source=='cat /proc/sys/kernel/random/boot_id; getprop ro.boot.slot_suffix':
                    s.baseline_reads+=1
                    if s.baseline_reads<3:return 255,''
                return super().shell(source,**kw)
            def run(s,argv,**kw):
                result=super().run(argv,**kw)
                if str(argv[0]).endswith('mtp-session'):s.after_mtp=True
                return result
        with patch.object(m.sp,'run',side_effect=AssertionError('real subprocess forbidden')):
            s=Sweep();self.assertTrue(s.usb())
            row=s.results['11_adb_mtp'];self.assertEqual(row['status'],'PASS')
            self.assertEqual(row['evidence']['mtp_session'],1)
            self.assertEqual(row['evidence']['framework_readable'],1)
            self.assertEqual(row['evidence']['framework_mtp'],1)
            self.assertGreaterEqual(s.getter_reads,2);self.assertEqual(s.setters,1)
            self.assertTrue(s.restart());s.restart_health(True,True)
            self.assertEqual(s.baseline_reads,3)
            self.assertEqual(s.results['01_restart']['status'],'PASS')
            self.assertEqual(s.results['01_restart']['evidence']['request_count'],1)
            self.assertEqual(sum(c==['FAKE_ADB','reboot'] for c in s.calls),1)
            # Host MTP success cannot manufacture a readable framework result.
            bad=Sweep(permanent=True);bad.usb();e=bad.results['11_adb_mtp']['evidence']
            self.assertEqual(bad.results['11_adb_mtp']['status'],'FAIL')
            self.assertEqual(e['mtp_session'],1);self.assertEqual(e['framework_readable'],0)
            self.assertEqual(e['framework_mtp'],-1);self.assertEqual(e['request_rc'],255)
            self.assertEqual(bad.setters,1)

    def test_failed_cleanup_or_mtp_verdict_does_not_skip_restart(self):
        # Reproduce integ26: PIN cleanup failed while the transport is healthy.
        # Keep that verdict; execute each USB/restart method independently.
        for mtp_failure in (False, True):
            s=fake.Fake();s.touched=s.assets_ok=True
            s.pin_cleanup_ok=s.cleanup_ok=False;s.no_mtp=mtp_failure
            s.put('cleanup',False,pin_cleanup_ok=0)
            with patch.object(m.sp,'run',side_effect=AssertionError('real subprocess forbidden')):
                s.usb_restart()
            self.assertEqual(s.results['cleanup']['status'],'FAIL')
            self.assertEqual(s.results['11_adb_mtp']['status'],'FAIL' if mtp_failure else 'PASS')
            self.assertEqual(s.results['01_restart']['status'],'PASS')
            self.assertEqual(sum(c==['FAKE_ADB','reboot'] for c in s.calls),1)

if __name__=='__main__':unittest.main(verbosity=2)
