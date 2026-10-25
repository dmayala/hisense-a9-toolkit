#!/usr/bin/env python3
"""Five offline groups; actual Android shell, synthetic firmware, no device I/O."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess as sp
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent; MAIN = HERE.parent
OUT = Path('/home/user/A9-build/mainline/integ-20261023-6r2')
BASE = OUT.with_name('integ-20261023-6r')
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def member(image, name):
    return sp.check_output(['debugfs','-R','cat /'+name,image],stderr=sp.DEVNULL)


class Tests(unittest.TestCase):
    def fixture(self, root):
        bindir = root/'bin'; bindir.mkdir()
        for name in ('cat','mkdir','chmod','cp','rm','ln','readlink','stat'):
            p = bindir/name
            stub = '[ "$1" != -c ] || [ "$2" != %u ] || { echo 0; exit; }\n' if name == 'stat' else ''
            p.write_text('#!/bin/sh\n'+stub+'exec env LD_LIBRARY_PATH='+str(ANDROID/'lib64')+' '+QEMU+' '+str(ANDROID/'linker64')+' '+str(ANDROID/'system/bin/toybox')+' '+name+' "$@"\n')
            p.chmod(0o700)
        prop = bindir/'getprop'; prop.write_text('#!/bin/sh\necho 1\n'); prop.chmod(0o700)
        def mapped(text): return re.sub(r'/(sys|mnt|vendor|data)(?=/|\b)', lambda m:str(root)+m[0], text)
        helper = root/'vendor/bin/a9ml-wifi-firmware.sh'; helper.parent.mkdir(parents=True)
        helper.write_text(mapped((MAIN/'wifi/a9ml-wifi-firmware.sh').read_text()).replace('#!/system/bin/sh','#!'+str(ANDROID/'bin/sh'),1)); helper.chmod(0o700)
        for name in ('qcom/sm6115/hisense/hlte556n/modem.mdt','ath10k/WCN3990/hw1.0/hisense/hlte556n/board.bin','regulatory.db','regulatory.db.p7s'):
            p = root/'vendor/firmware'/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'synthetic firmware\n')
        bridge = root/'mnt/a9-wifi'; bridge.mkdir(parents=True); bridge.chmod(0o700)
        path = root/'sys/module/firmware_class/parameters/path'; path.parent.mkdir(parents=True)
        data = root/'data/local/tmp'; data.mkdir(parents=True)
        for name in ('a9-waveform.bin','a9-ctrl-template.bin'): (data/('row28-'+name)).write_bytes(b'display fixture\n')
        return mapped,path,dict(os.environ,PATH=str(bindir)+':'+os.environ['PATH'])

    def open_prefix(self, root, mode, corrupt=False):
        mapped,path,env = self.fixture(root)
        value = root/('mnt/a9-wifi/firmware' if mode == 'bridge' else 'data/local/tmp/a9fw' if mode == 'plain' else 'unknown')
        path.write_text(str(value)+'\n')
        if corrupt:
            value.mkdir(); (value/'qcom').symlink_to(root/'unexpected')
        play = load('play_firmware_test', MAIN/'play/onb.py')
        prefix = play.EINK_OPEN.split('# Same proven settling interval',1)[0]
        r = sp.run([ANDROID/'bin/sh'],input='set -eu\n'+mapped(prefix),text=True,capture_output=True,env=env,timeout=20)
        return r,path,value

    def test_1_android_display_keeps_shared_and_plain_paths(self):
        for mode in ('bridge','plain','unknown'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); r,path,value = self.open_prefix(root,mode)
                self.assertEqual(r.returncode == 0,mode != 'unknown',r.stdout+r.stderr)
                self.assertEqual(path.read_text(),str(value)+'\n')
                if mode != 'unknown': self.assertEqual((root/'data/local/tmp/a9fw/hisense/a9-ctrl-template.bin').read_bytes(),b'display fixture\n')
                if mode == 'bridge': self.assertTrue((value/'qcom/sm6115/hisense/hlte556n/modem.mdt').is_file())

    def test_2_android_corrupt_bridge_refuses_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); r,path,value = self.open_prefix(root,'bridge',True)
            self.assertNotEqual(r.returncode,0)
            self.assertEqual(path.read_text(),str(value)+'\n')
            self.assertFalse((root/'data/local/tmp/a9fw/hisense/a9-waveform.bin').exists())

    def test_3_android_controller_admission(self):
        source = (MAIN/'wifi/coordinator-target.sh').read_text()
        block = source[source.index('firmware_gate() ('):source.index('\nadmission_file() {')]
        for mode in ('bridge','changed','missing'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); mapped,path,env = self.fixture(root)
                bridge = root/'mnt/a9-wifi/firmware'; bridge.mkdir()
                (bridge/'qcom').symlink_to(root/'vendor/firmware/qcom')
                path.write_text(str(bridge if mode != 'changed' else root/'data/local/tmp/a9fw')+'\n')
                if mode == 'missing': (root/'vendor/firmware/qcom/sm6115/hisense/hlte556n/modem.mdt').unlink()
                r = sp.run([ANDROID/'bin/sh'],input=mapped(block)+'\nfirmware_gate\n',text=True,capture_output=True,env=env,timeout=20)
                self.assertEqual(r.returncode == 0,mode == 'bridge',r.stderr)

    def test_4_packaged_delta_preserves_storage_and_safety(self):
        report = json.loads((OUT/'integrated-build.json').read_text())
        self.assertTrue(report['old_safety_code_byte_identical']); self.assertEqual(report['byte_identical_modules'],100)
        for name in ('bin/a9ml-wrapped-start.sh','bin/a9-sensors.sh','bin/a9ml-wifi-session.sh','build.prop','etc/init/hw/init.hlte556n.rc'):
            self.assertEqual(member(BASE/'vendor.img',name),member(OUT/'vendor.img',name))
        controller = member(OUT/'vendor.img','etc/a9-wifi/session/tools/coordinator-target.sh')
        self.assertEqual(controller,(MAIN/'wifi/coordinator-target.sh').read_bytes())
        self.assertEqual(hashlib.sha256((BASE/'boot.img').read_bytes()).digest(),hashlib.sha256((OUT/'boot.img').read_bytes()).digest())

    def test_5_camera_launch_failure_is_not_capture_evidence(self):
        fake = load('camera_failure_fake',MAIN/'acceptance/selftest.py')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'A9_BTRY_LOGS':tmp}):
            s = fake.Fake(); s.assets_ok = True
            with patch.object(s,'shell',side_effect=lambda text,**kwargs:(1,'') if text.startswith('run-as ') else (0,'')):
                result = fake.sweep.Sweep.app(s,'org.a9.cameraprobe','.CaptureActivity')
            self.assertEqual(result,dict(probe_result=0,app_stage=1,command_rc=1))
            app = s.app
            with patch.object(s,'app',side_effect=lambda pkg,*args,**kwargs:result if pkg == 'org.a9.cameraprobe' else app(pkg,*args,**kwargs)):
                s.sensors_media()
            self.assertEqual(json.loads((Path(tmp)/'service-inventory.json').read_text())['camera_count'],2)
            for face in ('front','rear'):
                row = s.results['19_'+face+'_photo']; self.assertEqual(row['status'],'FAIL')
                self.assertEqual(row['evidence']['command_rc'],1); self.assertEqual(row['evidence']['no_camera'],0)


if __name__ == '__main__': unittest.main(verbosity=2)
