#!/usr/bin/env python3
"""Offline parser/staging tests. Fixtures are synthetic ELF, never executed."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("stage_hal", Path(__file__).with_name("stage-hal.py"))
hal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hal)


def fake_elf(needed=(), machine=183):
    data = bytearray(512)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", data, 18, machine)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 2)
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 4, 0, 0, 0, len(data), len(data), 4096)
    table = b"\0" + b"".join(x.encode() + b"\0" for x in needed)
    dyn = [(5, 320), (10, len(table))]
    offset = 1
    for name in needed:
        dyn.append((1, offset))
        offset += len(name) + 1
    dyn.append((0, 0))
    struct.pack_into("<IIQQQQQQ", data, 120, 2, 4, 192, 192, 192, len(dyn) * 16, len(dyn) * 16, 8)
    for i, entry in enumerate(dyn):
        struct.pack_into("<qQ", data, 192 + 16 * i, *entry)
    data[320:320 + len(table)] = table
    return bytes(data)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundle, self.stage = self.root / "bundle", self.root / "vendor"
        self.bundle.mkdir()
        self.stage.mkdir()
        self.manifest = {"schema": 1, "transport": "linux-hci-user", "android_release": "16",
                         "source_build": {"hardware_interfaces_revision": "fixture",
                                          "build_command": "synthetic fixture; never run",
                                          "source_review": "fixture",
                                          "transport_failure_returns": True,
                                          "uart_fallback_disabled": True}, "files": {}}
        self.add(hal.HAL, fake_elf(["libc.so"]))

    def tearDown(self):
        self.temp.cleanup()

    def add(self, relative, data):
        path = self.bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.manifest["files"][relative] = hashlib.sha256(data).hexdigest()

    def check(self):
        (self.bundle / "manifest.json").write_text(json.dumps(self.manifest))
        return hal.inspect(self.bundle, self.stage)

    def test_good(self):
        self.assertIn(hal.HAL, self.check()[1])

    def test_path_traversal(self):
        self.manifest["files"]["../escape"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "allowlist"):
            self.check()

    def test_hash_mismatch(self):
        self.manifest["files"][hal.HAL] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.check()

    def test_wrong_arch(self):
        self.add(hal.HAL, fake_elf(machine=62))
        with self.assertRaisesRegex(ValueError, "AArch64"):
            self.check()

    def test_missing_dependency(self):
        self.add(hal.HAL, fake_elf(["libbt-test.so"]))
        with self.assertRaisesRegex(ValueError, "missing vendor dependency"):
            self.check()

    def test_bundle_dependency(self):
        self.add(hal.HAL, fake_elf(["libbt-test.so"]))
        self.add("lib64/libbt-test.so", fake_elf(["libc.so"]))
        self.check()

    def test_vendor_dependency(self):
        self.add(hal.HAL, fake_elf(["libbt-test.so"]))
        (self.stage / "lib64").mkdir()
        (self.stage / "lib64/libbt-test.so").write_bytes(fake_elf(["libc.so"]))
        self.check()

    def test_symlink_input(self):
        path = self.bundle / hal.HAL
        data = path.read_bytes()
        path.unlink()
        (self.root / "outside").write_bytes(data)
        path.symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.check()

    def test_shared_library_collision(self):
        self.add("lib64/libbt-test.so", fake_elf())
        (self.stage / "lib64").mkdir()
        (self.stage / "lib64/libbt-test.so").write_bytes(b"different vendor library")
        with self.assertRaisesRegex(ValueError, "replace a different"):
            self.check()

    def test_no_uart_attestation(self):
        self.manifest["source_build"]["uart_fallback_disabled"] = False
        with self.assertRaisesRegex(ValueError, "UART fallback"):
            self.check()

    def test_bad_elf(self):
        self.add(hal.HAL, b"not an executable")
        with self.assertRaisesRegex(ValueError, "ELF64"):
            self.check()


if __name__ == "__main__":
    unittest.main()
