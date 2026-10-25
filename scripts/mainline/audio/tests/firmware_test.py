#!/usr/bin/env python3
"""Offline synthetic split-ELF and staging-boundary tests. No real firmware/device I/O."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'firmware.py'
SPEC = importlib.util.spec_from_file_location('a9_audio_firmware_under_test', MODULE_PATH)
fw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fw)
RELOC = 1 << 27


def elf(phdrs=None, *, machine=164, phoff=52, phentsize=32, elf_class=1):
    if phdrs is None:
        phdrs = [(1, 0x1000, 0x10000, 0x10000, 4, 4096, RELOC, 4096)]
    ident = b'\x7fELF' + bytes([elf_class, 1, 1]) + bytes(9)
    header = struct.pack('<HHIIIIIHHHHHH', 2, machine, 1, 0, phoff, 0, 0, 52,
                         phentsize, len(phdrs), 0, 0, 0)
    return ident + header + bytes(max(0, phoff - 52)) + b''.join(struct.pack('<8I', *p) for p in phdrs)


DOMAIN = {'sr_domain': {'soc': 'msm', 'domain': 'adsp', 'subdomain': 'audio_pd',
                        'qmi_instance_id': 74},
          'sr_service': [{'provider': 'avs', 'service': 'audio'}]}


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sol-audio-fwtest-')
        self.root = Path(self.tmp.name)
        self.assets = self.root / 'assets'
        (self.assets / 'adsp').mkdir(parents=True)
        self.files = {'ADSP.MDT': elf(), 'ADSP.B00': b'abcd',
                      'ADSPUA.JSN': (json.dumps(DOMAIN) + '\n').encode()}
        self.write_assets()
        self.dest = self.root / 'vendor-stage'
        self.env = patch.dict(os.environ, {'A9_AUDIO': '1'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write_assets(self):
        records = []
        for name, data in self.files.items():
            path = self.assets / 'adsp' / name
            path.write_bytes(data)
            records.append({'copy': str(path), 'sha256': fw.sha(data), 'bytes': len(data),
                            'source': '/offline/golden/read-only/' + name})
        (self.assets / 'audio-provenance.json').write_text(json.dumps(records))

    def loaded(self):
        return fw.read_assets(self.assets)

    def stage(self):
        return fw.stage_files(*self.loaded(), self.dest)

    def assert_rejected(self, call, *args):
        with self.assertRaises((ValueError, KeyError, OSError)):
            call(*args)

    def test_valid_elf_geometry_and_domain(self):
        files, g = self.loaded()
        self.assertEqual(set(files), {'adsp.mdt', 'adsp.b00', 'adspua.jsn'})
        self.assertEqual(g['load_segments'], 1)
        self.assertEqual(g['original_min'], 0x10000)
        self.assertEqual(g['original_end'], 0x11000)
        self.assertEqual(g['relocated_base'], fw.BASE)
        self.assertEqual(g['relocated_end'], fw.BASE + 4096)
        self.assertEqual(g['spare_bytes'], fw.SIZE - 4096)

    def test_hash_and_nonloadable_headers_are_excluded(self):
        h = elf([(1, 0, 0, 0xffff0000, 1000, 1000, 2 << 24, 4096),
                 (0, 0, 0, 0, 500, 500, 0, 0),
                 (1, 0, 0, 0x10000, 4, 4096, RELOC, 4096)])
        g = fw.image_geometry(h, {'adsp.b02': b'abcd'})
        self.assertEqual(g['load_segments'], 1)

    def test_bss_load_needs_no_split_file(self):
        g = fw.image_geometry(elf([(1, 0, 0, 0x10000, 0, 8192, RELOC, 4096)]), {})
        self.assertEqual(g['nonzero_segments'], 0)
        self.assertEqual(g['relocated_end'], fw.BASE + 8192)

    def test_reject_bad_arch_class_and_program_headers(self):
        for h in [b'', b'\x7fELF', elf(machine=183), elf(elf_class=2),
                  elf(phentsize=16), elf(phdrs=[]), elf()[:60]]:
            with self.subTest(length=len(h)):
                self.assert_rejected(fw.image_geometry, h, {'adsp.b00': b'abcd'})

    def test_reject_nonrelocatable_overflow_filesz_and_oversize(self):
        for ph in [(1, 0, 0, 0x10000, 4, 4096, 0, 4096),
                   (1, 0, 0, 0xfffff000, 4, 8192, RELOC, 4096),
                   (1, 0, 0, 0x10000, 5, 4, RELOC, 4096),
                   (1, 0, 0, 0x10000, 4, fw.SIZE + 4096, RELOC, 4096)]:
            with self.subTest(ph=ph):
                self.assert_rejected(fw.image_geometry, elf([ph]), {'adsp.b00': b'abcd'})

    def test_exact_reserved_memory_fit(self):
        g = fw.image_geometry(elf([(1, 0, 0, 0x10000, 4, fw.SIZE, RELOC, 4096)]), {'adsp.b00': b'abcd'})
        self.assertEqual(g['spare_bytes'], 0)

    def test_reject_missing_short_and_long_split(self):
        for blobs in [{}, {'adsp.b00': b'abc'}, {'adsp.b00': b'abcde'}]:
            with self.subTest(blobs=blobs):
                self.assert_rejected(fw.image_geometry, elf(), blobs)

    def test_wrong_hash_and_wrong_size_provenance(self):
        for field, value in [('sha256', '0' * 64), ('bytes', 999)]:
            self.write_assets()
            p = self.assets / 'audio-provenance.json'
            manifest = json.loads(p.read_text())
            manifest[0][field] = value
            p.write_text(json.dumps(manifest))
            self.assert_rejected(self.loaded)

    def test_missing_provenance_and_missing_audio_domain(self):
        p = self.assets / 'audio-provenance.json'
        p.write_text('[]')
        self.assert_rejected(self.loaded)
        self.write_assets()
        (self.assets / 'adsp' / 'ADSPUA.JSN').unlink()
        self.assert_rejected(self.loaded)

    def test_wrong_audio_domain_or_provider(self):
        for domain in [dict(DOMAIN, sr_domain=dict(DOMAIN['sr_domain'], qmi_instance_id=75)),
                       dict(DOMAIN, sr_service=[{'provider': 'avs', 'service': 'other'}])]:
            self.files['ADSPUA.JSN'] = json.dumps(domain).encode()
            self.write_assets()
            self.assert_rejected(self.loaded)

    def test_unexpected_and_case_ambiguous_source(self):
        (self.assets / 'adsp' / 'not-firmware.bin').write_bytes(b'x')
        self.assert_rejected(self.loaded)
        (self.assets / 'adsp' / 'not-firmware.bin').unlink()
        (self.assets / 'adsp' / 'adsp.b00').write_bytes(b'abcd')
        self.assert_rejected(self.loaded)

    def test_source_asset_symlink_rejected(self):
        p = self.assets / 'adsp' / 'ADSP.B00'
        p.unlink()
        outside = self.root / 'outside.bin'; outside.write_bytes(b'abcd')
        p.symlink_to(outside)
        self.assert_rejected(self.loaded)
        self.assertEqual(outside.read_bytes(), b'abcd')

    def test_source_adsp_directory_symlink_escape_rejected(self):
        original = self.assets / 'adsp'
        outside = self.root / 'external-adsp'
        original.rename(outside)
        original.symlink_to(outside, target_is_directory=True)
        self.assert_rejected(self.loaded)
        self.assertEqual((outside / 'ADSP.B00').read_bytes(), b'abcd')

    def test_source_provenance_symlink_rejected(self):
        original = self.assets / 'audio-provenance.json'
        outside = self.root / 'external-provenance.json'
        original.rename(outside)
        original.symlink_to(outside)
        before = outside.stat()
        self.assert_rejected(self.loaded)
        self.assertEqual(outside.stat().st_mtime_ns, before.st_mtime_ns)

    def test_default_off_writes_nothing(self):
        with patch.dict(os.environ, {'A9_AUDIO': '0'}):
            self.assert_rejected(self.stage)
        self.assertFalse(self.dest.exists())
        with patch.dict(os.environ, {}, clear=True):
            self.assert_rejected(self.stage)
        self.assertFalse(self.dest.exists())

    def test_no_write_nas_or_a9_data(self):
        files, g = self.loaded()
        for dest in [Path('/share'), Path('/srv/nas/A9/NEVER-CREATE-sol-audio-test'),
                     self.root / 'a9-assets' / 'vendor']:
            with self.subTest(dest=dest):
                # Never permit the test itself to mutate NAS if the guard is broken.
                with patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir reached protected destination')) as mkdir, \
                     patch.object(Path, 'write_bytes', side_effect=AssertionError('write reached protected destination')) as write:
                    self.assert_rejected(fw.stage_files, files, g, dest)
                    mkdir.assert_not_called(); write.assert_not_called()
        self.assertFalse((self.root / 'a9-assets').exists())

    def test_empty_git_placeholder_is_not_repository(self):
        (self.dest / '.git').mkdir(parents=True)
        fw.stage_files(*self.loaded(), self.dest)
        self.assertTrue((self.dest / fw.FIRMWARE_DIR / 'adsp.mdt').is_file())

    def test_refuse_git_checkout_and_nested_git(self):
        for git_is_file in [False, True]:
            repo = self.root / ('repo-file' if git_is_file else 'repo-dir')
            repo.mkdir()
            if git_is_file: (repo / '.git').write_text('gitdir: /offline/other')
            else:
                (repo / '.git').mkdir()
                (repo / '.git' / 'HEAD').write_text('ref: refs/heads/main\n')
            self.assert_rejected(fw.stage_files, *self.loaded(), repo / 'nested' / 'vendor')
            self.assertFalse((repo / 'nested').exists())

    def test_stage_layout_manifest_modes_and_repeat_idempotent(self):
        info = self.stage()
        payload = self.dest / fw.FIRMWARE_DIR
        self.assertEqual((payload / 'adsp.b00').read_bytes(), b'abcd')
        self.assertFalse((payload / 'adspua.jsn').exists())
        self.assertTrue((self.dest / 'firmware' / 'adspua.jsn').is_file())
        meta = json.loads((self.dest / 'etc/a9-audio/firmware.json').read_text())
        self.assertEqual(meta['mapper_owner'], 'wifi; no mapper daemon or remoteproc-start action installed')
        self.assertEqual(meta['firmware_name'], fw.FIRMWARE_DIR.removeprefix('firmware/') + '/adsp.mdt')
        before = {str(p.relative_to(self.dest)): p.read_bytes() for p in self.dest.rglob('*') if p.is_file()}
        self.assertEqual(self.stage(), info)
        after = {str(p.relative_to(self.dest)): p.read_bytes() for p in self.dest.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        for p in self.dest.rglob('*'):
            if p.is_file(): self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o644)

    def test_different_firmware_preflight_no_partial_writes(self):
        payload = self.dest / fw.FIRMWARE_DIR
        payload.mkdir(parents=True)
        (payload / 'adsp.b00').write_bytes(b'not-original')
        self.assert_rejected(self.stage)
        self.assertFalse((payload / 'adsp.mdt').exists())
        self.assertFalse((self.dest / 'etc').exists())

    def test_payload_parent_symlink_escape(self):
        outside = self.root / 'outside'; outside.mkdir()
        self.dest.mkdir(); (self.dest / 'firmware').symlink_to(outside, target_is_directory=True)
        self.assert_rejected(self.stage)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.dest / 'etc').exists())

    def test_payload_file_symlink_even_matching_bytes(self):
        outside = self.root / 'outside.bin'; outside.write_bytes(b'abcd')
        payload = self.dest / fw.FIRMWARE_DIR; payload.mkdir(parents=True)
        (payload / 'adsp.b00').symlink_to(outside)
        self.assert_rejected(self.stage)
        self.assertEqual(outside.read_bytes(), b'abcd')
        self.assertFalse((payload / 'adsp.mdt').exists())

    def test_metadata_parent_and_file_symlink_escape(self):
        for leaf in [False, True]:
            with self.subTest(leaf=leaf):
                self.dest = self.root / ('metadata-leaf' if leaf else 'metadata-parent')
                outside = self.root / ('out-leaf' if leaf else 'out-parent'); outside.mkdir()
                if leaf:
                    (self.dest / 'etc/a9-audio').mkdir(parents=True)
                    source = outside / 'firmware.json'; source.write_bytes(b'outside')
                    (self.dest / 'etc/a9-audio/firmware.json').symlink_to(source)
                else:
                    (self.dest / 'etc').mkdir(parents=True)
                    (self.dest / 'etc/a9-audio').symlink_to(outside, target_is_directory=True)
                self.assert_rejected(self.stage)
                self.assertFalse((self.dest / 'firmware').exists())
                self.assertEqual(list(outside.iterdir()), [source] if leaf else [])

    def test_hardlinked_payload_is_not_modified_outside_stage(self):
        outside = self.root / 'external-original'; outside.write_bytes(b'abcd'); outside.chmod(0o600)
        payload = self.dest / fw.FIRMWARE_DIR; payload.mkdir(parents=True)
        os.link(outside, payload / 'adsp.b00')
        before = outside.stat()
        self.assert_rejected(self.stage)
        after = outside.stat()
        self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(outside.read_bytes(), b'abcd')

    def test_hardlinked_metadata_is_not_modified_outside_stage(self):
        self.stage()
        metadata = self.dest / 'etc/a9-audio/firmware.json'
        outside = self.root / 'external-metadata'
        os.link(metadata, outside)
        before = outside.stat()
        self.assert_rejected(self.stage)
        after = outside.stat()
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))


if __name__ == '__main__':
    unittest.main(verbosity=2)
