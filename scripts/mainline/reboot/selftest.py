#!/usr/bin/env python3
"""Offline checks of the built candidate and actual Android shell blocks."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess as sp
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent
OUT = Path('/home/user/A9-build/mainline/integ-20261023-6r')
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


def member(name):
    return sp.check_output(['debugfs', '-R', 'cat /'+name, OUT/'vendor.img'], stderr=sp.DEVNULL)


def shell_tools(root):
    bindir = root/'bin'; bindir.mkdir()
    for name in ('cat', 'find', 'mkdir', 'chmod', 'timeout', 'tar', 'cp', 'sed', 'sha256sum'):
        p = bindir/name
        p.write_text('#!/bin/sh\nexec env LD_LIBRARY_PATH='+str(ANDROID/'lib64')+' '+QEMU+' '+str(ANDROID/'linker64')+' '+str(ANDROID/'system/bin/toybox')+' '+name+' "$@"\n')
        p.chmod(0o700)
    shutil.copy(ANDROID/'bin/awk', bindir/'awk')
    return bindir


class Checks(unittest.TestCase):
    def test_1_actual_media_resources_and_abi(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = Path(tmp)/'camss.ko'; module.write_bytes(member('lib/modules/qcom-camss.ko'))
            result = sp.check_output(['python3', '-B', HERE/'inspect-camss-elf.py', '--check', module], text=True)
            csid = result.split('csid_res_6115', 1)[1].split('vfe_res_6115', 1)[0]
            for i in range(3): self.assertIn(f'{i} reg csid{i} irq csid{i}', csid)
            module = Path(tmp)/'venus-core.ko'; module.write_bytes(member('lib/modules/venus-core.ko'))
            result = sp.check_output(['python3', '-B', HERE/'inspect-camss-elf.py', '--check', module], text=True)
            self.assertIn('sm6115_min_fw 6 0 39', result)
        bad = sp.run(['python3', '-B', HERE/'inspect-camss-elf.py', '--check',
                      OUT/'baseline/lib/modules/qcom-camss.ko'], capture_output=True)
        self.assertNotEqual(bad.returncode, 0)  # observed stale module is refused
        report = json.loads((OUT/'integrated-build.json').read_text())
        self.assertEqual(report['module_abi']['modules'], 100)
        self.assertEqual(report['module_abi']['failing_modules'], 0)
        self.assertEqual(report['fsck'], 'PASS'); self.assertEqual(report['init_verifier'], 'PASS')
        # The packaged byte strings match the freshly compiled isolated sources.
        for driver, names in [('camss', ['qcom-camss.ko']), ('venus', ['venus-core.ko', 'venus-dec.ko', 'venus-enc.ko'])]:
            for name in names:
                self.assertEqual(member('lib/modules/'+name), (OUT/'media-source/drivers/media/platform/qcom'/driver/name).read_bytes())

    def test_2_android_registry_beside_tee(self):
        source = (MAIN/'android/vendor/bin/a9-sensors.sh').read_text()
        self.assertIn('MNT=${A9S_MNT:-/mnt/vendor/a9-sensors}', source)
        block = source[source.index('# 1. persist:'):source.index('# 2. firmware into')]
        for case in ('tee-bind', 'real-persist', 'sensor-busy'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); bindir = shell_tools(root)
                mnt = root/'mnt/vendor/a9-sensors'; mnt.parent.mkdir(parents=True)
                tee = root/'mnt/vendor/persist'; tee.mkdir(); (tee/'canary').write_bytes(b'TEE untouched')
                cfg = root/'vendor/etc/a9-sensors'; (cfg/'soc0').mkdir(parents=True)
                config = root/'vendor/etc/sensors'; config.mkdir(); (config/'synthetic').write_text('fixture')
                (cfg/'soc0/soc_id').write_text('fixture')
                data = b'synthetic non-calibration registry\n'; name = 'sensors/registry/registry/test'
                with tarfile.open(cfg/'registry-seed.tar', 'w') as tar:
                    info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, io.BytesIO(data))
                (cfg/'registry.sha256').write_text(hashlib.sha256(data).hexdigest()+'  /mnt/vendor/persist/'+name+'\n')
                proc = root/'proc'; proc.mkdir()
                mounts = proc/'mounts'; mounts.write_text('/dev/block/dm-1 '+str(tee)+' ext4 rw 0 0\n')
                with mounts.open('a') as f:
                    if case == 'real-persist': f.write('/dev/block/by-name/persist /mnt/other ext4 rw 0 0\n')
                    if case == 'sensor-busy': f.write('tmpfs '+str(mnt)+' tmpfs rw 0 0\n')
                logdir = root/'logs'; logdir.mkdir()
                mount = bindir/'mount'
                mount.write_text('#!/bin/sh\nprintf touched > "'+str(root/'effects')+'"\nprintf "tmpfs '+str(mnt)+' tmpfs rw 0 0\\n" >> "'+str(mounts)+'"\n')
                mount.chmod(0o700)
                pre = f'MNT={mnt}\nROOTD=$MNT/s1-listener-root\nVND={root}/vendor\nCFG={cfg}\nPROC={proc}\nLOGD={logdir}\n'
                pre += 'log() { :; }\nfail() { echo "FAIL:$1"; exit 42; }\n'
                r = sp.run([ANDROID/'bin/sh'], input=pre+block, text=True, capture_output=True,
                           env=dict(os.environ, PATH=str(bindir)+':'+os.environ['PATH']), timeout=30)
                self.assertEqual((tee/'canary').read_bytes(), b'TEE untouched')
                if case == 'tee-bind':
                    self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
                    self.assertEqual((mnt/'s1-listener-root/persist'/name).read_bytes(), data)
                else:
                    self.assertEqual(r.returncode, 42, r.stdout+r.stderr)
                    self.assertFalse((root/'effects').exists())
        listener = member('bin/s1-listener')
        self.assertIn(b'/mnt/vendor/a9-sensors/s1-listener-root\0', listener)
        self.assertNotIn(b'/mnt/vendor/persist/s1-listener-root\0', listener)

    def test_3_wrapped_identity_and_no_new_storage_mutation(self):
        props = member('build.prop').decode()
        for key in ('ro.vendor.build.security_patch', 'ro.vendor.boot_security_patch'):
            self.assertIn(key+'=2022-04-05\n', props)
        script = member('bin/a9ml-wrapped-start.sh').decode()
        old = (OUT/'baseline/bin/a9ml-wrapped-start.sh').read_text()
        added = ('    setprop vendor.a9.data.ready 0\n', '    setprop vendor.a9.data.ready 1\n')
        for line in added: script = script.replace(line, '')
        self.assertEqual(script, old)  # no added format, deletion or fallback
        hw = member('etc/init/hw/init.hlte556n.rc').decode()
        self.assertIn('on boot && property:vendor.a9.data.ready=1\n', hw)
        self.assertNotIn('\non boot\n', hw)

    def test_4_audio_waits_for_owner_without_output(self):
        audio = member('etc/init/init.a9-audio-output.rc').decode()
        self.assertIn('vendor.a9.audio.state waiting-adsp', audio)
        self.assertEqual(audio.count('start vendor.a9-audio-load'), 1)
        self.assertIn('on property:vendor.a9.sensors.ready=1 && property:vendor.a9.audio.ready=0\n    start vendor.a9-audio-load', audio)
        self.assertNotIn('aplay', audio)

    def test_5_android_event_snapshot_protocol(self):
        spec = importlib.util.spec_from_file_location('sweep', MAIN/'acceptance/onb.py')
        sweep = importlib.util.module_from_spec(spec); spec.loader.exec_module(sweep)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bindir = shell_tools(root)
            # Real ring-sized input used to become one external printf argv and
            # fail with E2BIG. Filtering must happen before command substitution.
            logcat = bindir/'logcat'
            logcat.write_text('#!/bin/sh\npython3 -c \'print("ordinary event\\n"*20000, end=""); print("am_anr synthetic")\'\n')
            logcat.chmod(0o700)
            r = sp.run([ANDROID/'bin/sh'], input='set -e\n'+sweep.EVENTS, text=True, capture_output=True,
                       env=dict(os.environ, PATH=str(bindir)+':'+os.environ['PATH']), timeout=20)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sweep.numbers(r.stdout)['crash_events'], 1)
            self.assertEqual(r.stdout, 'event am_anr synthetic\ncrash_events 1\n')


if __name__ == '__main__': unittest.main(verbosity=2)
