#!/usr/bin/env python3
"""SWEEP17 partial results, transport diagnostics and fresh-data owner replay. Host only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess as sp
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
HOST_PATH = os.environ['PATH']
spec = importlib.util.spec_from_file_location('sweep17_fake', HERE/'selftest.py')
fake = importlib.util.module_from_spec(spec); spec.loader.exec_module(fake)
m = fake.sweep
spec = importlib.util.spec_from_file_location('sweep17_root', HERE.parent/'boot-deadline/onb-root.py')
root = importlib.util.module_from_spec(spec); spec.loader.exec_module(root)


class Sweep17(unittest.TestCase):
    setUp = fake.Tests.setUp

    def test_still_progress_and_deadline(self):
        sweep = m.Sweep()
        partial = dict(completed=0, camera_phase=3, warmup_frames=0, activity_resumed=True)
        terminal = dict(completed=1, **{'pass': True}, jpeg_bytes=1000, warmup_frames=20)
        replies = iter([partial, terminal])
        def shell(source, *a, **kw):
            return (0, json.dumps(next(replies))) if 'cat files/result.json' in source else (0, '')
        with patch.object(sweep, 'shell', side_effect=shell), patch.object(sweep, 'monitor'):
            d = sweep.app('org.a9.cameraprobe', '.CaptureActivity', seconds=10)
        self.assertTrue(d['pass']); self.assertEqual(d['warmup_frames'], 20)
        with patch.object(sweep, 'shell', side_effect=lambda source,*a,**kw:
                          (0,json.dumps(partial)) if 'cat files/result.json' in source else (0,'')), patch.object(sweep, 'monitor'):
            d = sweep.app('org.a9.cameraprobe', '.CaptureActivity', seconds=4)
        self.assertEqual(d['timed_out'], 1); self.assertEqual(d['camera_phase'], 3)
        self.assertEqual(d['warmup_frames'], 0); self.assertNotIn('pass', d)
        fake_sweep = fake.Fake(); fake_sweep.assets_ok = True; original = fake_sweep.app
        with patch.object(fake_sweep, 'app', side_effect=lambda pkg,*a,**kw:
                          dict(app_stage=3,timed_out=1) if pkg == 'org.a9.cameraprobe' else original(pkg,*a,**kw)):
            fake_sweep.sensors_media()
        self.assertEqual(fake_sweep.results['19_rear_photo']['evidence']['warmup_frames'], -1)
        self.assertEqual(fake_sweep.results['19_rear_photo']['status'], 'FAIL')

    def test_partial_camera(self):
        sweep = m.Sweep(); replies = iter([
            dict(completed=0, camera_stage=1, af_available=0, torch_available=0),
            dict(completed=1, camera_stage=5, duration_ms=11000)])
        def shell(source, *args, **kwargs):
            if 'cat files/result.json' in source: return 0, json.dumps(next(replies))
            return 0, ''
        with patch.object(sweep, 'shell', side_effect=shell), patch.object(sweep, 'monitor'):
            d = sweep.app(m.PKG, '.Probe', '--es mode camera', seconds=10)
        self.assertEqual(d['completed'], 1)
        partial = dict(completed=0, available=1, camera_stage=2, af_available=0, torch_available=0)
        with patch.object(sweep, 'shell', side_effect=lambda source,*a,**kw: (0,json.dumps(partial)) if 'cat files/result.json' in source else (0,'')), patch.object(sweep, 'monitor'):
            d = sweep.app(m.PKG, '.Probe', '--es mode camera', seconds=4)
        self.assertEqual(d['timed_out'], 1); self.assertEqual(d['completed'], 0)
        self.assertEqual(d['camera_stage'], 2); self.assertEqual(d['af_available'], 0)
        with patch.object(sweep, 'shell', return_value=(0,json.dumps(dict(partial,error=1)))):
            d = sweep.app(m.PKG, '.Probe', '--es mode camera', seconds=4)
        self.assertEqual(d['error'], 1); self.assertNotIn('timed_out', d)


    def test_missing_capabilities(self):
        sweep = fake.Fake(); sweep.assets_ok = True; original = sweep.app; order = []
        def app(package, activity, extras='', seconds=90):
            order.append((package,extras))
            if '--es mode camera' in extras: return dict(probe_result=0, app_stage=3, timed_out=1)
            return original(package, activity, extras, seconds)
        with patch.object(sweep, 'app', side_effect=app): sweep.sensors_media()
        for face in ('front','rear'):
            self.assertEqual(sweep.results['19_'+face+'_video']['status'], 'FAIL')
            for feature in ('af','torch'):
                row = sweep.results['19_'+face+'_'+feature]
                self.assertEqual(row['status'], 'NOT_RUN'); self.assertEqual(row['evidence']['exposed'], -1)
        photo = [i for i,c in enumerate(sweep.calls) if c[:2] == ['APP','org.a9.cameraprobe']]
        self.assertEqual(len(photo),2)
        photo_positions = [i for i,(p,e) in enumerate(order) if p == 'org.a9.cameraprobe']
        video_positions = [i for i,(p,e) in enumerate(order) if '--es mode camera' in e]
        self.assertLess(max(photo_positions),min(video_positions))
        # A completed video lacking software selection or encoder EOS cannot pass.
        for removed in ('software_encoder','encoder_eos'):
            sweep = fake.Fake(); sweep.assets_ok = True; original = sweep.app
            def no_encode(package, activity, extras='', seconds=90):
                d = original(package, activity, extras, seconds)
                if '--es mode camera' in extras: d.pop(removed)
                return d
            with patch.object(sweep,'app',side_effect=no_encode): sweep.sensors_media()
            self.assertEqual(sweep.results['19_front_video']['status'],'FAIL')


    def test_root_diagnostics(self):
        # Exercise the real root helper with an inert executable in place of adb.
        adb = Path(self.tmp.name)/'fake-adb'
        adb.write_text('#!/bin/sh\nif [ "$1" = push ]; then printf "%s\\n" "$TEST_PUSH_ERROR"; exit 1; fi\nexit 99\n')
        adb.chmod(0o700)
        for message,kind in [("device 'PRIVATE_ID' not found",1),('connection reset PRIVATE_ID',2),
                             ('Permission denied PRIVATE_ID',3),('No space left on device PRIVATE_ID',4)]:
            env = dict(os.environ, ADB=str(adb), A9_BTRY_IN_HOOK='1', TEST_PUSH_ERROR=message)
            r = sp.run([sys.executable,'-B',str(HERE.parent/'boot-deadline/onb-root.py')],input='printf "inert 1\\n"',
                       env=env,text=True,capture_output=True,timeout=5)
            self.assertNotEqual(r.returncode,0)
            self.assertIn('A9_ONB_TRANSPORT phase=1 rc=1',r.stdout)
            self.assertIn('A9_ONB_ERROR kind='+str(kind),r.stdout)
            self.assertNotIn('PRIVATE_ID',r.stdout)
        self.assertEqual(root.error_kind('unknown private text'),0)


    def test_fresh_data_start(self):
        # Execute START under the cached Android shell. No device or radio command.
        android = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
        qemu = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'
        for state in ('serving','starting','failed',''):
            source = ('getprop() { case "$1" in vendor.a9ml.wifi.session.state) printf "%s\\n" "$TEST_STATE";; '
                      'vendor.a9ml.wifi.s2.start) printf "1\\n";; esac; }\n'
                      'setprop() { printf "UNEXPECTED_RESTART\\n"; return 99; }\nset -e\n'+m.play.START)
            r = sp.run([qemu,android/'linker64',android/'system/bin/sh'],input=source,
                       env=dict(os.environ,LD_LIBRARY_PATH=str(android/'lib64'),PATH=HOST_PATH,TEST_STATE=state),text=True,capture_output=True,timeout=5)
            self.assertEqual(r.returncode == 0,state in ('serving','starting'),r.stderr)
            self.assertNotIn('UNEXPECTED_RESTART',r.stdout)
        self.assertNotIn('persist.',m.play.START)

if __name__ == '__main__': unittest.main(verbosity=2)
