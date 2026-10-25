#!/usr/bin/env python3
"""Host-only Vulkan row replay; every app/GPU/transport operation is inert."""
import importlib.util
import os
from pathlib import Path
import subprocess as sp
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('vulkan_fake',HERE/'selftest.py')
fake=importlib.util.module_from_spec(spec);spec.loader.exec_module(fake)
m=fake.sweep
ANDROID=Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU='/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'
HOST_PATH=os.environ.get('PATH',os.defpath)
GOOD=dict(completed=1,vk_load=1,vk_device=1,vk_a610=1,vk_turnip=1,vk_api=4194307,vk_driver=18,
          vk_stage=8,vk_rc=0,vk_submitted=1,vk_fence=1,vk_words=64,vk_bad=0)
KERNEL='kernel_lines 100\ngpu_faults 0\niommu_faults 0\n'

class Vulkan(unittest.TestCase):
    setUp=fake.Tests.setUp

    def row(self,result,kernel=(0,KERNEL)):
        s=fake.Fake();calls=[]
        def app(package,activity,extras,seconds):
            calls.append('dispatch');self.assertEqual((package,activity,extras,seconds),(m.PKG,'.Probe','--es mode vulkan',20));return result
        def native(script,timeout):
            calls.append('diagnostics');self.assertEqual(script,m.VULKAN_KERNEL);self.assertEqual(timeout,8);return kernel
        with patch.object(s,'app',side_effect=app),patch.object(s,'native',side_effect=native):s.vulkan()
        self.assertEqual(calls,['dispatch','diagnostics'])
        return s.results['04_gpu_vulkan']

    def test_identity_dispatch_readback_timeout_and_crash_verdicts(self):
        self.assertEqual(self.row(GOOD)['status'],'PASS')
        for key,value in [('completed',0),('vk_load',0),('vk_device',0),('vk_a610',0),('vk_turnip',0),
                          ('vk_driver',10),('vk_api',0),('vk_stage',7),('vk_rc',2),('vk_rc',-4),
                          ('vk_submitted',0),('vk_fence',0),('vk_words',63),('vk_bad',1),('error',1),('timed_out',1),('vk_turnip','1')]:
            self.assertEqual(self.row(dict(GOOD,**{key:value}))['status'],'FAIL',key)
        self.assertEqual(self.row(dict(timed_out=1))['status'],'FAIL') # no result after a crash/stall
        for kernel in [(1,''),(0,''),(0,KERNEL.replace('gpu_faults 0','gpu_faults 1')),
                       (0,KERNEL.replace('iommu_faults 0','iommu_faults 1'))]:
            self.assertEqual(self.row(GOOD,kernel)['status'],'FAIL')
        result=self.row(dict(GOOD,private_name='PRIVATE_DEVICE_UUID'))
        self.assertNotIn('PRIVATE',str(result));self.assertTrue(all(type(v) is int for v in result['evidence'].values()))

    def test_real_sweep_attempts_vulkan_and_failure_never_gates_other_rows(self):
        s=fake.Fake();original=s.app
        def app(package,activity,extras='',seconds=90):
            if extras=='--es mode vulkan':return dict(completed=0,timed_out=1)
            return original(package,activity,extras,seconds)
        with patch.object(s,'app',side_effect=app):s.execute()
        self.assertEqual(s.results['04_gpu_vulkan']['status'],'FAIL')
        for row in ('12_wifi','08_22_sensors','18_hardware_encoder','01_restart','cleanup'):
            self.assertEqual(s.results[row]['status'],'PASS',row)

    def test_actual_android_shell_fault_counter_and_read_failure(self):
        root=Path(self.tmp.name);bin=root/'bin';bin.mkdir();fixture=root/'fixture'
        (bin/'dmesg').write_text('#!/bin/sh\ncat '+str(fixture)+'\nexit "${DMESG_RC:-0}"\n');(bin/'dmesg').chmod(0o700)
        (bin/'awk').write_text('#!/bin/sh\nexec '+QEMU+' '+str(ANDROID/'linker64')+' '+str(ANDROID/'system/bin/awk')+' "$@"\n');(bin/'awk').chmod(0o700)
        def scan(text,rc='0'):
            fixture.write_text(text)
            return sp.run([QEMU,ANDROID/'linker64',ANDROID/'system/bin/sh','-c','PATH='+str(bin)+':'+HOST_PATH+'\n'+m.VULKAN_KERNEL],
                          env=dict(os.environ,LD_LIBRARY_PATH=str(ANDROID/'lib64'),DMESG_RC=rc),text=True,capture_output=True,timeout=15)
        result=scan('[1] msm_dpu initialized\n[2] adreno firmware loaded\n')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(m.numbers(result.stdout),dict(kernel_lines=2,gpu_faults=0,iommu_faults=0))
        result=scan('[3] msm 3d00000.gpu: hang detected PRIVATE\n[4] arm-smmu: Unhandled context fault\n')
        self.assertEqual(m.numbers(result.stdout),dict(kernel_lines=2,gpu_faults=1,iommu_faults=1))
        self.assertNotIn('PRIVATE',result.stdout)
        self.assertNotEqual(scan('',rc='1').returncode,0) # pipefail, not a clean empty kernel

if __name__=='__main__':unittest.main(verbosity=2)
