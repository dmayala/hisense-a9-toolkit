#!/usr/bin/env python3
"""Manifest-driven runtime staging and the real prepare_assets hash check."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT/'scripts/mainline'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('runtime_asset_builder', MAIN/'build-integrated.py')
onb = load('runtime_asset_sweep', MAIN/'acceptance/onb.py')


class Tests(unittest.TestCase):
    def test_staged_runtime_passes_real_prepare_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root/'acceptance-assets'
            runtime = root/'runtime'
            assets.mkdir(); runtime.mkdir()
            if os.environ.get('A9_TEST_ASSETS'):
                shutil.copytree(os.environ['A9_TEST_ASSETS'], assets, dirs_exist_ok=True)
            else:
                # Execute the production manifest expression, without compiling
                # Android binaries; hash checking does not need real APKs.
                files = ['mtp-session', 'drain-clock', 'firmware/a9-waveform.bin',
                         'firmware/a9-ctrl-template.bin', 'acceptance.apk',
                         'photo/a9-camera2-probe.apk', 'decoder/a9-codec2-playback.apk']
                for name in files:
                    target = assets/name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(name.encode())
                tree = ast.parse((MAIN/'acceptance/build.py').read_text())
                assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                                  and any(isinstance(t, ast.Name) and t.id == 'manifest' for t in n.targets))
                context = dict(HERE=MAIN/'acceptance', MAINLINE=MAIN, out=assets,
                               files=files, hashlib=hashlib)
                exec(compile(ast.Module(body=[assignment], type_ignores=[]), '<asset manifest>', 'exec'), context)
                (assets/'assets.json').write_text(json.dumps(context['manifest']))
            manifest = json.loads((assets/'assets.json').read_text())
            # Reproduce the older runtime: everything except the new Vulkan sources.
            missing = {'acceptance/vulkan-probe.c', 'acceptance/vulkan.comp', 'acceptance/build.py'}
            self.assertTrue(missing <= manifest['sources'].keys())
            for name in manifest['sources'].keys() - missing:
                dest = runtime/name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(MAIN/name, dest)
            sweep = onb.Sweep.__new__(onb.Sweep)
            sweep.assets = assets; sweep.adb = 'fake-only'; sweep.assets_ok = False
            calls = []
            sweep.run = lambda *a, **kw: (calls.append(a) or (0, 'Success\n'))
            with patch.object(onb, 'MAINLINE', runtime):
                self.assertFalse(sweep.prepare_assets())
                self.assertEqual(calls, [])
                builder.bundle_acceptance_sources(runtime, assets)
                self.assertTrue(sweep.prepare_assets())
                self.assertTrue(sweep.assets_ok)
                self.assertEqual(len(calls), 3)
                for name, digest in manifest['sources'].items():
                    self.assertEqual(builder.sha(runtime/name), digest)
                (runtime/'acceptance/vulkan.comp').write_text('corrupt')
                calls.clear()
                self.assertFalse(sweep.prepare_assets())
                self.assertEqual(calls, [])
            # Stale manifest pins must fail assembly, before shipping a runtime.
            manifest['sources']['acceptance/vulkan.comp'] = '0'*64
            (assets/'assets.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'source hash mismatch'):
                builder.bundle_acceptance_sources(runtime, assets)


if __name__ == '__main__':
    unittest.main(verbosity=2)
