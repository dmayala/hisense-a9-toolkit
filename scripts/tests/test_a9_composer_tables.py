"""Synthetic fixtures contain layout metadata and invented codes, no vendor data."""

import contextlib
import importlib.util
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "a9_composer_tables.py"
SPEC = importlib.util.spec_from_file_location("a9_composer_tables", MODULE_PATH)
tables = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tables
SPEC.loader.exec_module(tables)


def synthetic_elf():
    data = bytearray(tables.SUPPORTED_SIZE)
    tables.ELF_HEADER.pack_into(data, 0, *tables.EXPECTED_HEADER)
    for i, program in enumerate(tables.EXPECTED_LOADS):
        tables.PROGRAM_HEADER.pack_into(data, 64 + i * 56, *program)
    tables.SECTION_HEADER.pack_into(data, 2048120 + 64, *tables.RODATA)
    tables.SECTION_HEADER.pack_into(data, 2048120 + 26 * 64,
                                   0, 3, 0, 0, 2022972, 278, 0, 0, 1, 0)
    data[2022972 + 249:2022972 + 257] = b".rodata\0"
    return data


def synthetic_family(extra=False):
    # Three nominal codes + one padding byte. Every regular slot is invented
    # [3,0,1,2,0]; slot256 has a separate meaning from any regular diagonal.
    rows = bytearray(bytes((3, 0, 1, 2, 0)) * 256)
    rows.extend(bytes((3, 1, 0, 0, 0)) if extra else bytes(5))
    return rows, tables.TableSpec("synthetic", 0, 5, (3,), extra)


class ComposerTablesTests(unittest.TestCase):
    def test_nominal_zero_is_payload_and_extra_slot_can_be_active(self):
        data, spec = synthetic_family(True)
        result = tables.inventory_family(data, spec)["bins"][0]
        self.assertEqual(result["length_header_counts"], {"3": 257})
        self.assertEqual(result["declared_payload_code_counts"], {"0": 258, "1": 257, "2": 256})
        self.assertEqual(result["zero_padding_bytes"], 257)
        self.assertEqual(result["slots_with_nonzero_payload"], 257)
        self.assertEqual(result["slot_256"], {"length_header": 3, "nonzero_payload_bytes": 1})

    def test_zero_length_extra_slot_is_still_inventoried(self):
        data, spec = synthetic_family()
        result = tables.inventory_family(data, spec)["bins"][0]
        self.assertEqual(result["length_header_counts"], {"0": 1, "3": 256})
        self.assertEqual(result["zero_padding_bytes"], 260)
        self.assertEqual(result["slots_with_nonzero_payload"], 256)
        self.assertEqual(result["slot_256"]["length_header"], 0)

    def test_bad_headers_at_first_last_and_extra_slots(self):
        for slot, value in ((0, 0), (255, 4), (256, 3), (0, 255)):
            with self.subTest(slot=slot, value=value):
                data, spec = synthetic_family()
                data[slot * 5] = value
                with self.assertRaisesRegex(tables.AuditError, "length header"):
                    tables.inventory_family(data, spec)

    def test_payload_alphabet_and_nonzero_padding(self):
        for offset, value, message in ((2, 3, "payload code"), (4, 1, "padding"),
                                       (256 * 5 + 1, 2, "padding")):
            with self.subTest(offset=offset):
                data, spec = synthetic_family()
                data[offset] = value
                with self.assertRaisesRegex(tables.AuditError, message):
                    tables.inventory_family(data, spec)

    def test_truncated_last_slot_and_shifted_region(self):
        data, spec = synthetic_family()
        for broken_data, broken_spec in ((data[:-1], spec),
                                         (data, tables.TableSpec("shift", 1, 5, (3,)))):
            with self.subTest(spec=broken_spec):
                with self.assertRaisesRegex(tables.AuditError, "bounds"):
                    tables.inventory_family(broken_data, broken_spec)

    def test_bin_boundary_uses_next_bin_header(self):
        data, _ = synthetic_family()
        # A second bin with a shorter declared payload and its own padding.
        data.extend(bytes((1, 2, 0, 0, 0)) * 256 + bytes(5))
        spec = tables.TableSpec("two_bins", 0, 5, (3, 1))
        bins = tables.inventory_family(data, spec)["bins"]
        self.assertEqual(bins[1]["file_offset"], 1285)
        self.assertEqual(bins[1]["length_header_counts"], {"0": 1, "1": 256})
        data[1285] = 3
        with self.assertRaisesRegex(tables.AuditError, "bin 1 slot 0"):
            tables.inventory_family(data, spec)

    def test_elf_accepts_synthetic_layout_without_vendor_payload(self):
        self.assertEqual(tables.validate_elf(synthetic_elf())["machine"], "AArch64")

    def test_elf_wrong_class_machine_and_truncated_headers(self):
        for offset, value in ((4, 1), (5, 2), (18, 62)):
            with self.subTest(offset=offset):
                data = synthetic_elf()
                data[offset] = value
                with self.assertRaisesRegex(tables.AuditError, "ELF header"):
                    tables.validate_elf(data)
        with self.assertRaises(tables.AuditError):
            tables.validate_elf(synthetic_elf()[:-1])

    def test_elf_invalid_load_and_section_extents(self):
        for offset in (64 + 32, 2048120 + 64 + 32):
            with self.subTest(offset=offset):
                data = synthetic_elf()
                struct.pack_into("<Q", data, offset, len(data) + 1)
                with self.assertRaises(tables.AuditError):
                    tables.validate_elf(data)

    def test_elf_bad_section_name_and_missing_rodata(self):
        for offset, value in ((2048120 + 64, 999), (2022972 + 249, ord("X"))):
            with self.subTest(offset=offset):
                data = synthetic_elf()
                if value == 999:
                    struct.pack_into("<I", data, offset, value)
                else:
                    data[offset] = value
                with self.assertRaises(tables.AuditError):
                    tables.validate_elf(data)

    def test_temperature_ranges_and_invalid_endpoint(self):
        data = bytearray(tables.TEMPERATURE_OFFSET + 51)
        for index in range(51):
            value = index // 3 if index <= 32 else 11 if index <= 37 else 12 if index <= 43 else 13
            data[tables.TEMPERATURE_OFFSET + index] = value
        ranges = tables.inventory_temperature(data)["bin_input_index_ranges"]
        self.assertEqual(ranges[-1], {"bin": 13, "input_index_min": 44, "input_index_max": 50})
        data[-1] = 14
        with self.assertRaisesRegex(tables.AuditError, "index mapping"):
            tables.inventory_temperature(data)
        with self.assertRaises(tables.AuditError):
            tables.inventory_temperature(data[:-1])

    def test_full_hash_gate_rejects_valid_layout_with_invented_payload(self):
        with self.assertRaisesRegex(tables.AuditError, "SHA256"):
            tables.audit(synthetic_elf())
        with self.assertRaisesRegex(tables.AuditError, "size"):
            tables.audit(b"\x7fELF")

    def test_exclusive_output_refuses_files_symlinks_and_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b"keep")
            link = Path(directory) / "link"
            link.symlink_to(path)
            dangling = Path(directory) / "dangling"
            dangling.symlink_to(Path(directory) / "missing")
            for destination in (path, link, dangling):
                with self.subTest(destination=destination):
                    with self.assertRaises(FileExistsError):
                        tables.write_output(destination, {"test": True})
            self.assertEqual(path.read_bytes(), b"keep")
            output = Path(directory) / "new.json"
            tables.write_output(output, {"z": 2, "a": 1})
            self.assertEqual(output.read_text(), '{"a":1,"z":2}\n')
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_input_type_size_and_cli_error_create_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "synthetic"
            binary.write_bytes(synthetic_elf())
            self.assertEqual(len(tables.read_input(binary)), tables.SUPPORTED_SIZE)
            output = root / "audit.json"
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(tables.main([str(binary), "--output", str(output)]), 2)
            self.assertIn("SHA256", error.getvalue())
            self.assertFalse(output.exists())
            link = root / "link"
            link.symlink_to(binary)
            with self.assertRaises(OSError):
                tables.read_input(link)
            binary.write_bytes(b"short")
            with self.assertRaises(tables.AuditError):
                tables.read_input(binary)
            fifo = root / "fifo"
            tables.os.mkfifo(fifo)
            with self.assertRaises(tables.AuditError):
                tables.read_input(fifo)


if __name__ == "__main__":
    unittest.main()
