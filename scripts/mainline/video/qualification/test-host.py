#!/usr/bin/env python3
"""Five focused host groups. Every transport is an inert fake; no native ELF entry runs."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import tempfile
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
MAIN=HERE.parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

v=load('validation_test',HERE/'validation.py')
runner=load('runner_test',HERE/'run.py')
extract=load('extract_test',HERE/'extract.py')
fake=load('acceptance_test',MAIN/'acceptance/selftest.py')
GOOD=dict(status=0,avc_frames=20,avc_eos=1,avc_named=1,hevc_frames=20,hevc_eos=1,hevc_named=1,
          encoder_named=1,surface_frames=30,encoded_frames=30,encoder_eos=1,muxer_ok=1,
          mp4_frames=30,duration_us=3000000,pts_span_us=2900000,roundtrip_frames=30,
          roundtrip_eos=1,roundtrip_named=1,bytes=40000)
RESULT='HVENC_RESULT '+' '.join(k+'='+str(GOOD[k]) for k in v.FIELDS)
FIRMWARE=('A9_VENUS_ENC op=session_start phase=response rc=0 state=3 codec=0x2\n'
          'A9_VENUS_ENC op=bufreq source=firmware type=0x1 size=147456 count=4 min=4\n'
          'A9_VENUS_ENC op=buffer.done type=0x2 used=999 offset=0 flags=0 hfi_flags=0 tag=1\n')

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.addCleanup(self.env.stop)

    def test_1_exact_numeric_protocol_and_partial_timeout(self):
        self.assertTrue(v.passed(v.parse(RESULT,0)))
        for text,rc in [(RESULT,124),(RESULT,255),(RESULT+'\n'+RESULT,0),('PRIVATE_ID '+RESULT,0),
                        (RESULT.replace('encoder_named=1','encoder_named=0'),0),
                        (RESULT.replace('hevc_eos=1','hevc_eos=0'),0),
                        (RESULT.replace('duration_us=3000000','duration_us=0'),0),
                        (RESULT.replace('encoded_frames=30','encoded_frames=1'),0)]:
            self.assertFalse(v.passed(v.parse(text,rc)),(text,rc))
        d=v.parse('PRIVATE_ID\nHVENC_QUAL op=6 phase=0 rc=0 frames=0\nHVENC_QUAL op=untrusted phase=1 rc=0 frames=0',124)
        self.assertEqual(d,dict(command_rc=124,completed=0,last_op=6,last_phase=0,last_rc=0,last_frames=0))

    def test_2_actual_mp4_decode_and_bundle_pins(self):
        path=self.root/'synthetic.mp4'
        sp.run([v.FFMPEG,'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=10',
                '-frames:v','30','-an','-c:v','libx264','-threads','1','-bf','0',path],check=True,timeout=20)
        self.assertEqual(v.inspect(path)['host_mp4_ok'],1)
        self.assertEqual(v.inspect(path,20)['host_mp4_ok'],0)
        self.assertEqual(v.inspect(path,30,'hevc')['host_mp4_ok'],0)
        broken=self.root/'broken.mp4';broken.write_bytes(path.read_bytes()[:120])
        self.assertEqual(v.inspect(broken)['host_mp4_ok'],0)
        source=self.root/'main';source.mkdir();(source/'probe.c').write_text('inert')
        bundle=self.root/'bundle';bundle.mkdir();receipt=self.root/'encoder.json';receipt.write_text('{}')
        sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        for n in ('qualify','avc.mp4','hevc.mp4'):(bundle/n).write_bytes(b'INERT')
        manifest=dict(schema=1,synthetic_only=True,hardware_tested=False,
                      encoder_receipt_sha256=sha(receipt),files={n:sha(bundle/n) for n in ('qualify','avc.mp4','hevc.mp4')},
                      sources={'probe.c':sha(source/'probe.c')})
        (bundle/'manifest.json').write_text(json.dumps(manifest));v.verify_bundle(bundle,source,receipt)
        (source/'probe.c').write_text('changed')
        with self.assertRaises(AssertionError):v.verify_bundle(bundle,source,receipt)

    def test_3_fake_transport_once_deadlines_cleanup_and_refusals(self):
        class Sweep:
            def __init__(s):
                s.out=self.root;s.assets=self.root/'assets';s.adb='INERT';s.calls=[]
                s.hvenc_qualify=s.touched=s.cleanup_ok=s.pin_cleanup_ok=True
                s.qualification_end=time.monotonic()+300;s.command_end=s.qualification_end
                s.results={'A_stability':dict(status='PASS',evidence={})};s.rc=0;s.text=RESULT
            def put(s,key,ok,**evidence):s.results[key]=dict(status='PASS' if ok else 'FAIL',evidence=evidence)
            def shell(s,cmd,timeout=20,**kw):
                s.calls.append((cmd,timeout,kw));assert timeout<=60
                if cmd.startswith('getprop'):return 0,'_b\n1\n'
                if cmd.startswith('mktemp'):return 0,'/data/local/tmp/a9-hvenc.ABCDEF\n'
                if cmd.startswith('timeout'):
                    assert '-k 2 55 ' in cmd and kw==dict(capture_partial=True)
                    return s.rc,s.text
                if cmd.startswith('stat'):return 0,'123\n'
                if cmd.startswith(('chmod','rm -r -- /data/local/tmp/a9-hvenc.')):return 0,''
                raise AssertionError(cmd)
            def run(s,args,timeout):
                s.calls.append((args,timeout,{}));assert args[0]=='INERT'
                if args[1]=='pull':Path(args[-1]).write_bytes(b'x'*123)
                else:assert args[1]=='push' and len(args)==6
                return 0,''
        os.environ['A9_BTRY_IN_HOOK']='1'
        with patch.object(runner.v,'verify_bundle',return_value={}),patch.object(runner.v,'inspect',return_value={'host_mp4_ok':1}):
            s=Sweep();runner.run(s);runner.run(s)
            self.assertEqual(s.results['18_hardware_encoder']['status'],'PASS')
            self.assertEqual(sum(isinstance(c[0],str) and c[0].startswith('timeout ') for c in s.calls),1)
            self.assertTrue(s.calls[-1][0].startswith('rm -r -- /data/local/tmp/a9-hvenc.'))
            (self.root/'hvenc-output.mp4').unlink()
            fail=Sweep();fail.rc=124;fail.text='PRIVATE_ID\nHVENC_QUAL op=6 phase=0 rc=0 frames=0'
            runner.run(fail)
            self.assertEqual(fail.results['18_hardware_encoder']['status'],'FAIL')
            self.assertEqual(fail.results['A_stability']['status'],'FAIL')
            self.assertNotIn('PRIVATE_ID',(self.root/'hvenc-qualification.json').read_text())
            (self.root/'hvenc-output.mp4').unlink()
            for field in ('touched',):
                denied=Sweep();setattr(denied,field,False);runner.run(denied)
                self.assertFalse(denied.calls)
            late=Sweep();late.qualification_end=time.monotonic()+144;runner.run(late)
            self.assertFalse(late.calls)
            self.assertEqual(late.results['18_hardware_encoder']['evidence']['skip_reason'],3)

    def test_4_real_sweep_qualification_before_usb_and_legacy_unchanged(self):
        m=fake.sweep
        with patch.dict(os.environ,{'A9_BTRY_LOGS':str(self.root),'A9_SWEEP_SECONDS':'3300',
                'A9_SWEEP_PLAY_PACKAGE':'org.fossify.math','A9_SWEEP_HVENC_QUALIFY':'1',
                'A9_BTRY_IN_HOOK':'1','ADB':'INERT'}):
            clock=fake.Clock();spec_fn=importlib.util.spec_from_file_location
            shim=self.root/'fake-qual.py'
            shim.write_text("import time\ndef run(s):\n    s.calls.append(['QUALIFICATION'])\n    s.hvenc_qualification_attempted=True\n    s.qualified_remaining=s.qualification_end-time.monotonic()\n    s.put('18_hardware_encoder',True,qualification=1,attempted=1)\n")
            s=None
            class Sweep(fake.Fake):
                def cleanup(this):
                    this.calls.append(['CLEANUP']);super().cleanup()
                def collect_anr_evidence(this,force=False):
                    this.calls.append(['ANR']);super().collect_anr_evidence(force)
            def factory():
                nonlocal s
                s=Sweep();return s
            def spec(name,path,*a,**kw):return spec_fn(name,shim if name=='last_hvenc' else path,*a,**kw)
            with patch.object(m.time,'monotonic',clock.monotonic),patch.object(m.time,'sleep',clock.sleep),\
                 patch.object(m.play.Hook,'credentials',lambda _:['SECRET_TEST_SSID','SECRET_TEST_PASSWORD']),\
                 patch.object(m,'Sweep',side_effect=factory),patch.object(importlib.util,'spec_from_file_location',side_effect=spec),\
                 patch.object(m.signal,'signal'),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(),0)
            qualified=next(i for i,c in enumerate(s.calls) if c==['QUALIFICATION'])
            usb=next(i for i,c in enumerate(s.calls) if c[0].endswith('mtp-session'))
            self.assertLess(qualified,usb)
            self.assertTrue(any(c==['CLEANUP'] for c in s.calls[:qualified]))
            self.assertTrue(any(c==['FAKE_ADB','reboot'] for c in s.calls))
            self.assertFalse(any(c[0]=='ENCODER' for c in s.calls))
            self.assertGreaterEqual(s.qualified_remaining,145)
            # Real subprocess deadline calculation, with commands that consume
            # their entire allowance: diagnostics cannot steal HVENC's window.
            clock.now=s.qualification_end-300
            s.hvenc_qualification_attempted=False
            s.hvenc_finalizing=False;s.command_end=s.qualification_end
            def slow(argv, **kw):
                clock.sleep(kw['timeout'])
                raise m.sp.TimeoutExpired(argv,kw['timeout'])
            with patch.object(m.time,'monotonic',clock.monotonic),patch.object(m.sp,'run',side_effect=slow):
                for _ in range(5): fake.Fake.__mro__[1].run(s,['INERT'],timeout=78)
                self.assertEqual(clock.now,s.qualification_end-240)
                s.hvenc_finalizing=True
                for _ in range(5): fake.Fake.__mro__[1].run(s,['INERT'],timeout=78)
                self.assertEqual(clock.now,s.qualification_end-155)
                s.hvenc_qualification_attempted=True
                fake.Fake.__mro__[1].run(s,['INERT'],timeout=60)
                self.assertEqual(clock.now,s.qualification_end-95)

            with patch.dict(os.environ,{'A9_SWEEP_HVENC_QUALIFY':'bad'}),patch.object(m,'Sweep',side_effect=AssertionError('transport')),\
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(),2)
            with patch.dict(os.environ,{'A9_BTRY_OWNER_END':str(time.monotonic()+200)}):
                owner=m.Sweep();self.assertLessEqual(owner.qualification_end,time.monotonic()+185)

    def test_5_boot_diagnostics_privacy_timeout_and_previous_pstore(self):
        boot=self.root/'boot-0000000001';boot.mkdir()
        (boot/'kernel.log').write_text('private ID\n'+FIRMWARE)
        (boot/'kernel.early.log').write_text(FIRMWARE)  # duplicate ring, not extra responses
        (boot/'pstore.previous.log').write_text('Kernel panic OLD\n'+FIRMWARE.replace('rc=0','rc=-110'))
        (boot/'logcat.log').write_text('PRIVATE_ID\nA9_ENC_TRACE op=layout.map phase=begin\n'
            'A9_ENC_TRACE op=input_usage phase=end cpu_read=1 usage=65537\n'
            'A9_ENC_TRACE op=input_usage phase=end cpu_read=1 usage=65537 PRIVATE_ID\n'+RESULT+'\n')
        d=extract.save(boot);self.assertEqual(d['native_pass'],1);self.assertEqual(d['diagnostics_ok'],1)
        self.assertEqual(d['firmware_response_records'],1)
        safe=(boot/'hvenc-diagnostics.log').read_text();self.assertNotIn('PRIVATE_ID',safe)
        self.assertIn('A9_ENC_TRACE op=layout.map phase=begin',safe)
        self.assertEqual(safe.count('A9_ENC_TRACE op=input_usage phase=end cpu_read=1 usage=65537'),1)
        self.assertEqual((boot/'hvenc-diagnostics.json').stat().st_mode&0o777,0o600)
        (boot/'kernel.log').write_text(FIRMWARE+'venus: response timed out (state 3, codec 0x2)\nBUG: synthetic splat\n')
        d=extract.save(boot);self.assertEqual(d['diagnostics_ok'],0)
        self.assertEqual(d['firmware_timeouts'],1);self.assertEqual(d['kernel_splat_lines'],1)
        # Prior boot has no probe; copying the final boot's host result is refused.
        other=self.root/'boot-0000000002';other.mkdir()
        host=self.root/'host.json';host.write_text('{"host_mp4_ok":1}')
        extract.save(other,host);self.assertFalse((other/'hvenc-qualification.json').exists())
        (other/'hvenc-diagnostics.log').unlink();(other/'hvenc-diagnostics.log').symlink_to(host)
        with self.assertRaises(OSError):extract.save(other)

    def test_6_first_submission_and_watchdog_are_not_completions(self):
        queued=('A9_VENUS_ENC op=ftb phase=buffer.end rc=0 state=5 codec=0x2\n'
                'A9_VENUS_ENC op=etb phase=buffer.end rc=0 state=5 codec=0x2\n')
        marker='A9_VENUS_RECOVERY event=1 stage=0 rc=0 irq=0x10'
        (self.root/'kernel.log').write_text(queued)
        records,d=extract.collect(self.root)
        self.assertEqual(records,queued.splitlines())
        self.assertEqual(d['buffer_completions'],0)
        self.assertEqual(d['diagnostics_ok'],0)
        (self.root/'kernel.log').write_text(FIRMWARE+marker+'\n'+marker+' PRIVATE_ID\n')
        (self.root/'pstore.previous.log').write_text(marker+'\n')
        (self.root/'logcat.log').write_text(marker+'\n')  # bridge must not double count
        records,d=extract.collect(self.root)
        self.assertEqual(d['firmware_watchdog_records'],1)
        self.assertEqual(d['firmware_errors'],1)
        self.assertEqual(d['firmware_response_records'],1)
        self.assertEqual(d['buffer_completions'],1)
        self.assertEqual(d['diagnostics_ok'],0)
        self.assertNotIn('PRIVATE_ID','\n'.join(records))

if __name__=='__main__':unittest.main(verbosity=2)
