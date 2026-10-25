#!/usr/bin/env python3
"""Synthetic image provenance tests: no firmware, identity or device access."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / 'scripts/mainline/wifi'
spec = importlib.util.spec_from_file_location('wifi_verify', HERE / 'verify-images.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sha = module.sha
FW5_STORE = Path('/nix/store/7nm4vfj0j5q7w2pn46alb8d0ajr1h1xp-linux-firmware-20250917-zstd/lib/firmware/'
                 'ath10k/WCN3990/hw1.0/qcm2290/firmware-5.bin.zst')


def real_fw5():
    # The 60-byte feature-flag file is read from the cached linux-firmware store,
    # never kept in git.
    if not FW5_STORE.is_file():
        raise unittest.SkipTest('cached linux-firmware store absent')
    return subprocess.check_output(['zstd', '-q', '-d', '-c', str(FW5_STORE)])


def ath10k_features(data):
    # Mirrors ath10k_core_fetch_firmware_api_n (kernel 7.2.10, core.c): magic padded to
    # 4 bytes, then {id:u32, len:u32, data padded to 4} IEs; id 2 = FEATURES bitmap.
    magic = b'QCA-ATH10K\0'
    assert data.startswith(magic)
    off = (len(magic) + 3) // 4 * 4
    features = None
    while len(data) - off >= 8:
        ie_id, ie_len = struct.unpack_from('<II', data, off)
        off += 8
        assert ie_len <= len(data) - off
        if ie_id == 2:
            features = int.from_bytes(data[off:off + ie_len], 'little')
        off += (ie_len + 3) // 4 * 4
    return features


class FakeImage:
    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files[path]


class Images(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a9-wifi-image-fixture-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.binaries = root / 'binaries'
        self.binaries.mkdir()
        sources = json.loads((HERE / 'sources.lock.json').read_text())
        for row in sources:
            patch = HERE / 'patches' / (row['name'] + '.patch')
            if patch.exists():
                row['patch_sha256'] = sha(patch.read_bytes())
        build = {'abi': 'aarch64', 'static': True, 'sources': sources, 'binaries': {},
                 'local_sources': {n: sha((HERE / n).read_bytes()) for n in ('sandbox.c', 'sandbox-policy.h', 'sha256.h', 'postexec.c')}}
        files = {'/build.prop': b'ro.a9ml.wifi=1\n', '/etc/fstab.hlte556n': b'# diskless\n'}
        for role in ('rmtfs', 'tqftpserv', 'diag-router', 'qrtr-lookup', 'a9-wifi-sandbox'):
            data = ('dummy executable fixture ' + role).encode()
            (self.binaries / role).write_bytes(data)
            files['/bin/' + role] = data
            build['binaries'][role] = sha(data)
        rc = (HERE / 'a9-wifi.rc').read_text()
        files['/etc/init/init.a9ml-wifi.rc'] = '\n\n'.join(b for b in rc.split('\n\n') if 'start vendor.pd_mapper' not in b and 'service vendor.pd_mapper ' not in b).encode()
        for name in ('a9ml-wifi-modules.sh', 'a9ml-wifi-firmware.sh'):
            files['/bin/' + name] = (HERE / name).read_bytes()
        self.fw5_path = 'firmware/ath10k/WCN3990/hw1.0/hisense/hlte556n/firmware-5.bin'
        files['/' + self.fw5_path] = real_fw5()
        manifest = {'pd_mapper': 'kernel', 'userspace': build,
                    'installed': [{'path': k[1:], 'bytes': len(v), 'sha256': sha(v)} for k, v in files.items() if k.startswith(('/bin/', '/etc/init/', '/firmware/'))]}
        files['/etc/a9-wifi/build-manifest.json'] = json.dumps(manifest).encode()
        (self.binaries / 'build-manifest.json').write_text(json.dumps(build))
        self.image = FakeImage(files)
        self.snapshot = root / 'snapshot'
        self.snapshot.mkdir()
        payload = {'Image.gz': b'kernel-fixture', 'sm6115-hisense-hlte556n-android.dtb': b'dtb-fixture',
                   'config': b'CONFIG_FIXTURE=y\n', 'modules/modules.wifi': b'ath10k_snoc.ko\n',
                   'modules/ath10k_snoc.ko': b'dummy-module'}
        for rel, data in payload.items():
            p = self.snapshot / rel
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(data)
            if rel.startswith('modules/'):
                files['/lib/' + rel] = data
        (self.snapshot / 'wifi-manifest.json').write_text(json.dumps({'version': 1, 'pd_mapper': 'kernel', 'sha256': {k: sha(v) for k, v in payload.items()}}))
        self.dtbo = self.snapshot / 'recovery_dtbo.img'
        self.dtbo.write_bytes(b'null-dtbo-fixture')
        h = bytearray(4096)
        h[:8] = b'ANDROID!'
        for offset, value in ((8, len(payload['Image.gz'])), (36, 4096), (40, 2), (1648, len(payload['sm6115-hisense-hlte556n-android.dtb']))):
            struct.pack_into('<I', h, offset, value)
        cmd = b'firmware_class.path=/mnt/a9-wifi/firmware'
        h[64:64 + len(cmd)] = cmd
        self.boot = root / 'boot.img'
        self.boot.write_bytes(h + payload['Image.gz'] + bytes(4096 - len(payload['Image.gz'])) + payload['sm6115-hisense-hlte556n-android.dtb'])

    def verify(self):
        return module.verify(self.image, self.boot, self.dtbo, self.snapshot, self.binaries)

    def test_exact_reviewed_local_provenance_and_snapshot_pass(self):
        self.assertTrue(self.verify())

    def test_ath10k_firmware5_is_required_pinned_and_a_valid_non_bmi_feature_file(self):
        data = self.image.files['/' + self.fw5_path]
        self.assertEqual(len(data), 60)
        features = ath10k_features(data)
        self.assertTrue(features >> 19 & 1, 'NON_BMI (bit 19) must be set for WCN3990/SNOC')
        self.assertEqual([b for b in range(32) if features >> b & 1], [6, 18, 19, 20])
        # Missing file, wrong bytes, same-size foreign file and a wrong location all fail.
        manifest = json.loads(self.image.files['/etc/a9-wifi/build-manifest.json'])
        for name, mutate in (('missing', lambda: manifest.__setitem__('installed', [r for r in manifest['installed'] if r['path'] != self.fw5_path])),
                             ('wrong-path', lambda: [r.__setitem__('path', r['path'] + '.x') for r in manifest['installed'] if r['path'] == self.fw5_path])):
            with self.subTest(case=name):
                m = copy.deepcopy(manifest)
                manifest_backup = self.image.files['/etc/a9-wifi/build-manifest.json']
                saved = dict(manifest)
                mutate()
                self.image.files['/' + self.fw5_path + '.x'] = data
                self.image.files['/etc/a9-wifi/build-manifest.json'] = json.dumps(manifest).encode()
                with self.assertRaisesRegex(ValueError, 'firmware-5.bin'):
                    self.verify()
                manifest.clear(); manifest.update(saved)
                self.image.files['/etc/a9-wifi/build-manifest.json'] = manifest_backup
        for bad in (b'x' * 60, data[:-1] + b'\x00', data + b'\x00'):
            self.image.files['/' + self.fw5_path] = bad
            rows = [r for r in manifest['installed'] if r['path'] == self.fw5_path]
            m2 = copy.deepcopy(manifest)
            for r in m2['installed']:
                if r['path'] == self.fw5_path:
                    r['bytes'] = len(bad); r['sha256'] = sha(bad)
            self.image.files['/etc/a9-wifi/build-manifest.json'] = json.dumps(m2).encode()
            with self.assertRaisesRegex(ValueError, 'firmware-5.bin'):
                self.verify()
        self.image.files['/' + self.fw5_path] = data

    def test_vendor_binary_and_module_tampering_fail(self):
        for path in ('/bin/rmtfs', '/lib/modules/ath10k_snoc.ko'):
            old = self.image.files[path]
            self.image.files[path] = b'changed'
            with self.assertRaises(ValueError):
                self.verify()
            self.image.files[path] = old

    def test_boot_dtb_and_dtbo_tampering_fail(self):
        original = self.boot.read_bytes()
        self.boot.write_bytes(original[:-1] + b'X')
        with self.assertRaises(ValueError):
            self.verify()
        self.boot.write_bytes(original)
        other = self.snapshot.parent / 'wrong-dtbo.img'
        other.write_bytes(b'wrong')
        with self.assertRaises(ValueError):
            module.verify(self.image, self.boot, other, self.snapshot, self.binaries)

    def test_stale_patch_record_fails_even_when_local_build_matches(self):
        manifest = json.loads(self.image.files['/etc/a9-wifi/build-manifest.json'])
        manifest['userspace']['sources'][0]['patch_sha256'] = '0' * 64
        self.image.files['/etc/a9-wifi/build-manifest.json'] = json.dumps(manifest).encode()
        (self.binaries / 'build-manifest.json').write_text(json.dumps(manifest['userspace']))
        with self.assertRaisesRegex(ValueError, 'patch changed'):
            self.verify()

    def test_protected_fstab_and_truncated_effective_cmdline_fail(self):
        self.image.files['/etc/fstab.hlte556n'] = b'/dev/block/by-name/persist /persist ext4 ro wait\n'
        with self.assertRaises(ValueError):
            self.verify()
        self.image.files['/etc/fstab.hlte556n'] = b'# diskless\n'
        data = bytearray(self.boot.read_bytes())
        data[608] = ord('x')
        self.boot.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'effective command line'):
            self.verify()


if __name__ == '__main__':
    unittest.main(verbosity=2)
