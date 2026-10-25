#!/usr/bin/env python3
"""Host-only regressions for stale audio payloads in the final vendor image."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('integrated', REPO / 'scripts/mainline/build-integrated.py')
integrated = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrated)


class AudioVendor(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / 'audio'
        self.mods = self.audio / 'overlay-r2/lib/modules'
        self.mods.mkdir(parents=True)
        self.vermagic = '7.2.10-a9+ SMP preempt mod_unload modversions aarch64'
        self.fresh = {}
        self.vendor = {}
        for name in ('snd-soc-a9-audio.ko', 'q6afe.ko'):
            module = self.mods / name
            module.write_bytes(('fresh build ' + name).encode())
            self.fresh[name] = {'sha256': integrated.sha(module), 'vermagic': self.vermagic}
            self.vendor['/lib/modules/' + name] = module.read_bytes()
        self.helper = self.root / 'a9ml-audio.sh'
        self.helper.write_bytes(b"ctl set 'TX_DEC2 Volume' 84\n")
        self.vendor['/bin/a9ml-audio.sh'] = self.helper.read_bytes()
        self.manifest = self.audio / 'a5-build.json'
        self.manifest.write_text(json.dumps({'fresh_modules': self.fresh}))

    def debugfs(self, argv, **kwargs):
        self.assertEqual(argv[:2], ['debugfs', '-R'])
        command, source, dest = argv[2].split()
        self.assertEqual(command, 'dump')
        Path(dest).write_bytes(self.vendor[source])

    def verify(self, vermagic=None):
        with patch.object(integrated.subprocess, 'run', side_effect=self.debugfs), \
             patch.object(integrated.subprocess, 'check_output', return_value=(vermagic or self.vermagic) + '\n'):
            return integrated.verify_audio_vendor(self.root / 'vendor.img', self.audio, self.helper, os.environ)

    def test_fresh_payload_and_current_helper_pass(self):
        result = self.verify()
        self.assertEqual(result['q6afe.ko'], self.fresh['q6afe.ko'])
        self.assertEqual(result['a9ml-audio.sh']['sha256'], integrated.sha(self.helper))
        self.assertEqual(json.loads((self.audio / 'vendor-check.json').read_text()), result)

    def test_cached_package_only_is_not_a_fresh_build(self):
        self.manifest.write_text(json.dumps({'fresh_modules': {}}))
        with self.assertRaisesRegex(RuntimeError, 'not built from source'):
            self.verify()

    def test_stale_module_in_staging_or_final_vendor_fails(self):
        for where in ('staging', 'vendor'):
            with self.subTest(where=where):
                module = self.mods / 'q6afe.ko'
                fresh = module.read_bytes()
                if where == 'staging':
                    module.write_bytes(b'old cached q6afe')
                else:
                    self.vendor['/lib/modules/q6afe.ko'] = b'later feature overwrote q6afe'
                with self.assertRaisesRegex(RuntimeError, 'hash mismatch'):
                    self.verify()
                module.write_bytes(fresh)
                self.vendor['/lib/modules/q6afe.ko'] = fresh

    def test_wrong_vendor_vermagic_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'vermagic mismatch'):
            self.verify('different kernel')

    def test_stale_gain_helper_fails(self):
        self.vendor['/bin/a9ml-audio.sh'] = b"ctl set 'TX_DEC2 Volume' 0\n"
        with self.assertRaisesRegex(RuntimeError, 'helper differs'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
