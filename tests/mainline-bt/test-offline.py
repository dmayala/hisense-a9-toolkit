#!/usr/bin/env python3
"""Offline tests for private asset admission and snapshot failure containment."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
BT = REPO/'scripts/mainline/bluetooth'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


fw = load('firmware_test', BT/'firmware.py')
kernel = load('kernel_test', BT/'kernel-assets.py')


class Admission(unittest.TestCase):
    def test_address_invalid(self):
        for value in (bytes(6), bytes([255])*6, bytes([0,0,0,0,0,1]), bytes(5), bytes(7)):
            with self.assertRaises(ValueError): fw.address(value, 'le-bytes')

    def test_fixed_identity_file_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bdaddr.txt'
            with mock.patch.object(fw, 'BDADDR_FILE', p):
                with self.assertRaises(FileNotFoundError): fw.coordinator_address()
                # Synthetic locally administered identity; never a target input.
                valid=b'02:0F:0E:0D:0C:0B'
                for data in (valid,valid+b'\n',valid.lower()+b'\n'):
                    p.write_bytes(data)
                    self.assertEqual(fw.coordinator_address(),bytes([11,12,13,14,15,2]))
                for data in (b'',b'null\n',valid+b'\r\n',b' '+valid,valid+b'\n\n',
                             b'00:00:00:00:00:00',b'FF:FF:FF:FF:FF:FF',b'03:0F:0E:0D:0C:0B'):
                    p.write_bytes(data)
                    with self.assertRaises(ValueError): fw.coordinator_address()
                p.unlink(); source=Path(tmp)/'other'; source.write_bytes(valid)
                p.symlink_to(source)
                with self.assertRaises(ValueError): fw.coordinator_address()

    def test_fixed_identity_stage_and_stale_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); p=root/'bdaddr.txt'; out=root/'runtime'
            args=SimpleNamespace(image='synthetic-pinned-image',output=str(out))
            with mock.patch.object(fw,'BDADDR_FILE',p), \
                 mock.patch.object(fw,'NAMES',[]), \
                 mock.patch.object(fw,'extract',return_value={}), \
                 mock.patch.object(fw,'IMAGE_SHA256',fw.digest(b'fixture')):
                with self.assertRaises(FileNotFoundError): fw.stage(args)
                self.assertFalse(out.exists())
                p.write_bytes(b'null\n')
                with self.assertRaises(ValueError): fw.stage(args)
                self.assertFalse(out.exists())
                (root/'image').write_bytes(b'fixture');args.image=str(root/'image')
                p.write_bytes(b'02:0F:0E:0D:0C:0B\n')
                fw.stage(args);fw.check_directory(out)
                p.write_bytes(b'00:00:5e:00:53:01\n')
                with self.assertRaises(ValueError): fw.check_directory(out)
                p.unlink()
                with self.assertRaises(FileNotFoundError): fw.check_directory(out)

    def test_stock_nv_byte_order(self):
        value = bytes([11,12,13,14,15,2])  # synthetic unicast; not a device identity
        self.assertEqual(fw.address(bytes([1,1,6])+value, 'stock-nv'), value)
        with self.assertRaises(ValueError):
            fw.address(bytes([1,1,6])+value+bytes([1,1,6])+value, 'stock-nv')

    def test_no_arbitrary_image(self):
        with self.assertRaises(ValueError): fw.extract(bytes(1048576))

    def test_symlink_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); (p/'input').write_bytes(b'fixture'); (p/'link').symlink_to(p/'input')
            with self.assertRaises((OSError, ValueError)): fw.read_regular(p/'link', 32)

    def test_missing_private_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises((OSError, ValueError)): fw.check_directory(Path(tmp))

    def test_module_preflight_is_atomic_on_validation_failure(self):
        # Synthetic module metadata is never linked/loaded/executed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); build=root/'build'; out=root/'snapshot'
            build.mkdir(); (out/'modules').mkdir(parents=True)
            (build/'Module.symvers').write_text('fixture\n')
            def ko(path, vm, deps=''):
                path.write_bytes(b'\0vermagic='+vm.encode()+b'\0depends='+deps.encode()+b'\0')
            ko(build/'bluetooth.ko', 'same'); ko(build/'btqca.ko', 'same', 'bluetooth')
            ko(build/'hci_uart.ko', 'different', 'bluetooth,btqca')
            ko(out/'modules/a9_eink.ko', 'same')
            (out/'modules/modules.dep').write_text('a9_eink.ko:\n')
            (out/'modules/modules.load').write_text('a9_eink.ko\n')
            before={str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
            with self.assertRaises(ValueError): kernel.snapshot(build,out)
            after={str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
            self.assertEqual(before, after)

    def test_persist_import_missing_has_no_output(self):
        # Synthetic filesystem result; never opens a real block/device file.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); out=root/'address'
            root.chmod(0o700)
            raw=bytes(33554432)
            args=SimpleNamespace(image='synthetic-regular-image', output=str(out))
            with mock.patch.object(fw, 'read_regular', return_value=raw), \
                 mock.patch.object(fw, 'PERSIST_SHA256', hashlib.sha256(raw).hexdigest()), \
                 mock.patch.object(fw.subprocess, 'run', return_value=SimpleNamespace(returncode=0,stdout=b'')):
                self.assertEqual(fw.import_persist(args),1)
            self.assertFalse(out.exists())
            self.assertFalse(out.with_name(out.name+'.provenance.json').exists())

    def test_persist_import_valid_record_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); root.chmod(0o700); out=root/'address'
            raw=bytes(33554432); value=bytes([11,12,13,14,15,2])
            args=SimpleNamespace(image='synthetic-regular-image', output=str(out))
            with mock.patch.object(fw, 'read_regular', return_value=raw), \
                 mock.patch.object(fw, 'PERSIST_SHA256', hashlib.sha256(raw).hexdigest()), \
                 mock.patch.object(fw.subprocess, 'run', return_value=SimpleNamespace(returncode=0,stdout=bytes([1,1,6])+value)):
                self.assertEqual(fw.import_persist(args),0)
                self.assertEqual(out.read_bytes(),value)
                self.assertEqual(out.stat().st_mode&0o777,0o600)
                with self.assertRaises(FileExistsError):fw.import_persist(args)

    def test_required_firmware_driver_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'metadata-fixture'
            p.write_bytes(b'\0a9_qca_firmware_required=1\0')
            kernel.verify_driver(p)
            p.write_bytes(b'\0vermagic=legacy\0')
            with self.assertRaises(ValueError): kernel.verify_driver(p)

    def test_fdt_malformed(self):
        for value in (b'', bytes(40), b'\xd0\x0d\xfe\xed'+bytes(36)):
            with self.assertRaises(ValueError): kernel.fdt(value)

    def test_opt_in_missing_inputs_stop_before_build(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ, A9_ANDROID='1', A9_BLUETOOTH='1',
                     A9_MAINLINE=tmp, A9_BT_FIRMWARE=tmp+'/absent')
            run=subprocess.run(['bash',str(REPO/'scripts/mainline/kernel/build.sh'),'kernel'],
                               env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertNotEqual(run.returncode,0)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            env.pop('A9_BT_HAL_BUNDLE',None)
            run=subprocess.run(['bash',str(REPO/'scripts/mainline/android/build-vendor.sh'),'--eink'],
                               env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertNotEqual(run.returncode,0)
            self.assertIn(b'A9_BT_HAL_BUNDLE',run.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])


class SetupFirmwareStaging(unittest.TestCase):
    """Host run of a9ml-bluetooth.sh against a fake root (paths rewritten, Android
    tools stubbed). Synthetic firmware bytes and a stub identity gate only."""
    def run_setup(self, mutate=None, identity_hook=''):
        tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, tmp)
        r = Path(tmp); bins = r/'bin'; bins.mkdir()
        props = r/'props'; props.write_text('')
        stub = {'getprop': '[ "$1" = ro.vendor.a9.bluetooth ] && echo 1; exit 0',
                'setprop': f'echo "$1=$2" >> {props}', 'log': f'echo "$3" >> {r}/log',
                'insmod': 'exit 0', 'chown': f'echo "$@" >> {r}/chown.log; exit 0'}
        for n, body in stub.items():
            (bins/n).write_text('#!/bin/sh\n'+body+'\n'); (bins/n).chmod(0o755)
        q = r/'vendor/firmware/qca'; q.mkdir(parents=True)
        rows = ''
        for n in ('crbtfw21.tlv', 'crnv21.bin'):
            data = b'synthetic '+n.encode(); (q/n).write_bytes(data)
            rows += hashlib.sha256(data).hexdigest()+'  '+n+'\n'
        (r/'vendor/etc/bluetooth').mkdir(parents=True)
        (r/'vendor/etc/bluetooth/firmware.sha256').write_text(rows)
        (r/'vendor/etc/bluetooth/local-bd-address').write_bytes(b'x')
        (r/'vendor/lib/modules').mkdir(parents=True)
        (r/'vendor/lib/modules/modules.bluetooth').write_text('bluetooth.ko\n')
        (r/'vendor/bin').mkdir()
        gate = r/'vendor/bin/a9-bt-identity'; gate.write_text('#!/bin/sh\n'+identity_hook+'\nexit 0\n'); gate.chmod(0o755)
        (r/'sys/module/firmware_class/parameters').mkdir(parents=True)
        (r/'sys/module/firmware_class/parameters/path').write_text(str(r)+'/data/local/tmp/a9fw\n')
        (r/'sys/module/bluetooth').mkdir(parents=True)
        dev = r/'sys/devices/4a8c000.serial/serial0/bluetooth/hci0'; dev.mkdir(parents=True)
        (r/'sys/class/bluetooth').mkdir(parents=True); (r/'sys/class/bluetooth/hci0').symlink_to(dev)
        (r/'data/local/tmp').mkdir(parents=True)
        if mutate: mutate(r)
        script = (BT/'a9ml-bluetooth.sh').read_text()
        for prefix in ('/vendor/', '/data/', '/sys/'):
            script = script.replace(prefix, str(r)+prefix)
        (r/'setup.sh').write_text(script)
        import os
        env = dict(os.environ, PATH=str(bins)+':'+os.environ['PATH'])
        import glob  # Android's /system/bin/sh is mksh; use it when the host has one
        sh = shutil.which('mksh') or next(iter(sorted(glob.glob('/nix/store/*-mksh-*/bin/mksh'))), 'sh')
        run = subprocess.run([sh, str(r/'setup.sh')], env=env, capture_output=True, timeout=60)
        log = (r/'log').read_text() if (r/'log').exists() else ''
        return run.returncode, props.read_text().split(), log, r

    def test_success_stages_verified_copy(self):
        rc, props, log, r = self.run_setup()
        self.assertEqual((rc, props[-1]), (0, 'vendor.a9.bt.ready=1'), log)
        staged = r/'data/local/tmp/a9fw/qca'
        self.assertEqual(sorted(p.name for p in staged.iterdir()), ['crbtfw21.tlv', 'crnv21.bin'])
        self.assertEqual((staged/'crnv21.bin').read_bytes(), b'synthetic crnv21.bin')

    def test_staging_dir_root_0755(self):
        # integ-1 / review 44: a9fw is chowned root:root (stubbed here) and 0755,
        # also when it already existed group-writable (the old shell:shell 0775)
        def m(r):
            (r/'data/local/tmp/a9fw').mkdir(parents=True); (r/'data/local/tmp/a9fw').chmod(0o775)
        for mut in (None, m):
            rc, props, log, r = self.run_setup(mut)
            self.assertEqual(rc, 0, log)
            self.assertEqual(oct((r/'data/local/tmp/a9fw').stat().st_mode & 0o7777), '0o755')
            calls = (r/'chown.log').read_text()
            self.assertIn('root:root '+str(r)+'/data/local/tmp/a9fw', calls)
            self.assertNotIn('shell', calls)

    def test_staging_dir_symlink_refused(self):
        def m(r):
            (r/'elsewhere').mkdir(); (r/'data/local/tmp/a9fw').symlink_to(r/'elsewhere')
        self.assert_fails_closed(m, text='staging directory')

    def test_existing_eink_dir_is_kept(self):
        def m(r):
            (r/'data/local/tmp/a9fw/hisense').mkdir(parents=True)
        rc, props, log, r = self.run_setup(m)
        self.assertEqual(rc, 0, log)
        self.assertTrue((r/'data/local/tmp/a9fw/hisense').is_dir())

    def assert_fails_closed(self, mutate=None, hook='', text=''):
        rc, props, log, r = self.run_setup(mutate, hook)
        self.assertNotEqual(rc, 0)
        self.assertNotIn('vendor.a9.bt.ready=1', props)
        self.assertIn(text, log)

    def test_wrong_search_path(self):
        self.assert_fails_closed(lambda r: (r/'sys/module/firmware_class/parameters/path').write_text('\n'),
                                 text='firmware search path')

    def test_altered_vendor_file(self):
        self.assert_fails_closed(lambda r: (r/'vendor/firmware/qca/crnv21.bin').write_bytes(b'other'),
                                 text='vendor firmware differs')

    def test_extra_vendor_file(self):
        self.assert_fails_closed(lambda r: (r/'vendor/firmware/qca/crnv99.bin').write_bytes(b'x'),
                                 text='vendor firmware differs')

    def test_missing_manifest(self):
        self.assert_fails_closed(lambda r: (r/'vendor/etc/bluetooth/firmware.sha256').unlink(),
                                 text='firmware manifest absent')

    def test_stale_staging(self):
        self.assert_fails_closed(lambda r: (r/'data/local/tmp/a9fw/qca').mkdir(parents=True),
                                 text='stale firmware staging')

    def test_changed_during_setup(self):
        self.assert_fails_closed(hook='echo x >> "$(dirname "$0")/../../data/local/tmp/a9fw/qca/crnv21.bin"',
                                 text='changed during setup')


class VendorFirmwareManifest(unittest.TestCase):
    def test_pinned_exact_set(self):
        sv = load('stage_vendor_test', BT/'stage-vendor.py')
        with tempfile.TemporaryDirectory() as tmp:
            q = Path(tmp)
            pins = {'a.tlv': hashlib.sha256(b'a').hexdigest(), 'b.bin': hashlib.sha256(b'b').hexdigest()}
            (q/'a.tlv').write_bytes(b'a'); (q/'b.bin').write_bytes(b'b')
            real = sv.module
            def patched(name, path):
                m = real(name, path)
                if path.name == 'firmware.py': m.FILE_HASHES = pins
                return m
            with mock.patch.object(sv, 'module', patched):
                self.assertEqual(sv.firmware_sums(q), f"{pins['a.tlv']}  a.tlv\n{pins['b.bin']}  b.bin\n")
                (q/'b.bin').write_bytes(b'x')
                with self.assertRaises(ValueError): sv.firmware_sums(q)
                (q/'b.bin').write_bytes(b'b'); (q/'c.bin').write_bytes(b'c')
                with self.assertRaises(ValueError): sv.firmware_sums(q)


if __name__ == '__main__':
    unittest.main()
