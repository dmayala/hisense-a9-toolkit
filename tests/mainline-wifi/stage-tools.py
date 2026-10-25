#!/usr/bin/env python3
"""Read real cached pinned tool bytes; mutate only in-memory or /tmp fixtures.

No target ELF, device tool, network client or shell command is executed.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
HERE = REPO / 'scripts/mainline/wifi'
spec = importlib.util.spec_from_file_location('wifi_tool_stage', HERE / 'stage-tools.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock = json.loads((HERE / 'coordinator-tools.lock.json').read_text())
        cls.rows = {r['name']: r for r in cls.lock['tools']}
        cls.data = {name: Path(row['store']).read_bytes() for name, row in cls.rows.items()}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a9-wifi-tools-stage-', dir=os.environ.get('A9_WIFI_TOOLS_TEST_TMP'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_real_cached_alias_hash_elf_help_and_source_provenance(self):
        actual = module.inputs()
        for name in ('strace', 'iw'):
            self.assertEqual(actual[name], self.data[name])
            self.assertEqual(len(actual[name]), self.rows[name]['bytes'])
            self.assertEqual(module.sha(actual[name]), self.rows[name]['sha256'])
        self.assertEqual(actual['coordinator-target.sh'], (HERE / 'coordinator-target.sh').read_bytes())
        self.assertEqual(actual['audit-opens.awk'], (HERE / 'audit-opens.awk').read_bytes())

    def test_runtime_pins_match_lock(self):
        text = (HERE / 'coordinator-target.sh').read_text()
        for name in ('strace', 'iw'):
            match = re.search('^' + name.upper() + r'_SHA256=([0-9a-f]{64})$', text, re.M)
            self.assertIsNotNone(match)
            self.assertEqual(match[1], self.rows[name]['sha256'])

    def test_changed_binary_pin_size_or_source_archive_is_rejected(self):
        for name, row in self.rows.items():
            with self.subTest(name=name):
                changed = bytearray(self.data[name]); changed[-1] ^= 1
                with self.assertRaises(ValueError): module.binary(row, changed)
                wrong = copy.deepcopy(row); wrong['sha256'] = '0' * 64
                with self.assertRaises(ValueError): module.binary(wrong, self.data[name])
                wrong = copy.deepcopy(row); wrong['bytes'] += 1
                with self.assertRaises(ValueError): module.binary(wrong, self.data[name])
        wrong = copy.deepcopy(self.rows['strace'])
        wrong['provenance']['source_archive_sha256'] = '0' * 64
        with self.assertRaises(ValueError): module.strace_source(wrong)

    def test_interp_wrong_machine_truncated_elf_and_needed_library_are_rejected(self):
        for mutate in ('interp', 'machine', 'truncated'):
            b = bytearray(self.data['iw'])
            offset = struct.unpack_from('<Q', b, 32)[0]
            if mutate == 'interp': struct.pack_into('<I', b, offset, 3)
            if mutate == 'machine': struct.pack_into('<H', b, 18, 62)
            if mutate == 'truncated': b = b[:100]
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): module.static_arm64(b)
        b = bytearray(64 + 56 + 16)
        b[:7] = b'\x7fELF\x02\x01\x01'
        struct.pack_into('<HH', b, 16, 2, 183)
        struct.pack_into('<Q', b, 32, 64)
        struct.pack_into('<HH', b, 54, 56, 1)
        struct.pack_into('<IIQQQQQQ', b, 64, 2, 0, 120, 0, 0, 16, 16, 0)
        struct.pack_into('<qQ', b, 120, 1, 1)
        with self.assertRaises(ValueError): module.static_arm64(b)

    def test_stage_private_exact_payload_and_hash_inventory_without_execution(self):
        out = module.stage(self.root / 'tools')
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        sums = {}
        for line in (out / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ')
            sums[name] = digest
            self.assertEqual(module.sha((out / name).read_bytes()), digest)
        self.assertEqual(set(sums), {'strace', 'iw', 'coordinator-target.sh', 'audit-opens.awk', 'coordinator-tools.lock.json'})
        self.assertEqual((out / 'strace').read_bytes(), self.data['strace'])
        self.assertEqual((out / 'iw').read_bytes(), self.data['iw'])
        for name in ('strace', 'iw'):
            self.assertEqual(stat.S_IMODE((out / name).stat().st_mode), 0o700)
        for name in ('coordinator-target.sh', 'audit-opens.awk', 'coordinator-tools.lock.json', 'SHA256SUMS'):
            self.assertEqual(stat.S_IMODE((out / name).stat().st_mode), 0o600)
        # A second invocation must not replace tools or any existing output.
        (out / 'preserve').write_text('keep')
        with self.assertRaises(ValueError): module.stage(out)
        self.assertEqual((out / 'preserve').read_text(), 'keep')

    def test_source_golden_store_other_checkout_and_symlink_outputs_are_rejected(self):
        for path in (REPO / 'tool-output', Path('/home/user/A9/runs/tool-output'),
                     Path('/home/user/a9-assets/Backups/tool-output'), Path('/srv/nas/A9/tool-output'),
                     Path(self.rows['iw']['store']).parent / 'tool-output'):
            with self.subTest(path=path), self.assertRaises(ValueError): module.output_path(path)
        checkout = self.root / 'other-checkout'; checkout.mkdir(); (checkout / '.git').write_text('gitdir: /elsewhere\n')
        with self.assertRaises(ValueError): module.output_path(checkout / 'output')
        (self.root / 'link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError): module.output_path(self.root / 'link/output')


if __name__ == '__main__':
    unittest.main(verbosity=2)
