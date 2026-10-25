#!/usr/bin/env python3
"""Private seed safety and offline image preparation refusal tests."""
import importlib.util
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('s1_prepare', REPO / 'scripts/mainline/sensors/s1-prepare.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class Preparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s1-prepare-test-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def seed(self):
        seed = self.root / 'seed'
        (seed / 'registry/registry').mkdir(parents=True)
        (seed / 'registry/registry/synthetic').write_text('test fixture only\n')
        return seed

    def test_seed_is_unchanged_archive_has_only_files_and_dirs(self):
        seed = self.seed()
        source = seed / 'registry/registry/synthetic'
        before = (source.read_bytes(), source.stat().st_mode, source.stat().st_mtime_ns)
        archive, manifest = self.root / 'seed.tar', self.root / 'registry.sha256'
        self.assertEqual(p.registry_archive(seed, archive, manifest), 1)
        self.assertEqual(before, (source.read_bytes(), source.stat().st_mode, source.stat().st_mtime_ns))
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                self.assertTrue(member.isfile() or member.isdir())
                self.assertTrue(member.name.startswith('sensors/'))
                self.assertNotIn('..', Path(member.name).parts)
                self.assertEqual(member.uid, 0)
            self.assertEqual(tar.extractfile('sensors/registry/registry/synthetic').read(), source.read_bytes())
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assertEqual(manifest.read_text(), p.sha(source) + '  /mnt/vendor/persist/sensors/registry/registry/synthetic\n')

    def test_symlink_file_and_directory_rejected(self):
        seed = self.seed()
        for target in (seed / 'registry', seed / 'registry/registry/synthetic'):
            link = seed / 'link'
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                p.checked_tree(seed)
            link.unlink()

    def test_fifo_unsafe_name_and_oversize_rejected(self):
        import os
        seed = self.seed()
        fifo = seed / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            p.checked_tree(seed)
        fifo.unlink()
        odd = seed / 'escape\nname'
        odd.touch()
        with self.assertRaises(ValueError):
            p.checked_tree(seed)
        odd.unlink()
        with self.assertRaises(ValueError):
            p.checked_tree(seed, max_bytes=1)

    def test_output_symlink_cannot_redirect_private_data(self):
        base = self.root / 'work'
        base.mkdir()
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (base / 'sensors-s1').symlink_to(elsewhere)
        with patch.object(p, 'WORK', base), self.assertRaises(ValueError):
            p.prepare(base / 'sensors-s1/redirect', Path('/absent'), Path('/absent'), Path('/absent'), Path('/absent'))
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_output_existing_and_outside_root_rejected_before_reads(self):
        base = self.root / 'work'
        dest = base / 'sensors-s1/existing'
        dest.mkdir(parents=True)
        for output in (dest, self.root / 'outside'):
            with patch.object(p, 'WORK', base), self.assertRaises(ValueError):
                p.prepare(output, Path('/absent'), Path('/absent'), Path('/absent'), Path('/absent'))

    def test_dynamic_and_truncated_observer_refused(self):
        elf = bytearray(120)
        elf[:6] = b'\x7fELF\x02\x01'
        elf[18:20] = b'\xb7\x00'
        struct.pack_into('<Q', elf, 32, 64)
        struct.pack_into('<HH', elf, 54, 56, 1)
        file = self.root / 'observer'
        for value in (b'bad', elf):
            file.write_bytes(value)
            with self.assertRaises(ValueError):
                p.static_observer(file)
        struct.pack_into('<I', elf, 64, 3)  # PT_INTERP, Android cannot run host libc linker
        file.write_bytes(elf)
        with self.assertRaises(ValueError):
            p.static_observer(file)


if __name__ == '__main__':
    unittest.main()
