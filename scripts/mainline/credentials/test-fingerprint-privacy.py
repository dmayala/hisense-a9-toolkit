#!/usr/bin/env python3
"""Host-only tests of the real pinned HAL/string and configuration overlay."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess as sp
import unittest
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fp_privacy', HERE/'fingerprint-privacy.py')
fp = importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)
parser = argparse.ArgumentParser()
parser.add_argument('--vendor', type=Path, required=True)
args, remaining = parser.parse_known_args()


def read(name):
    return sp.check_output(['debugfs', '-R', 'cat /'+name, args.vendor], stderr=sp.DEVNULL)


class Privacy(unittest.TestCase):
    def test_real_hal_only_readonly_strings_change_and_code_is_identical(self):
        old = read(fp.HAL); new = fp.patch_hal(old)
        self.assertEqual(hashlib.sha256(old).hexdigest(), fp.STOCK_SHA)
        allowed = set()
        for before, after, _ in fp.PROPERTIES:
            off = old.index(before+b'\0')
            allowed.update(range(off, off+len(before)))
            self.assertEqual(new[off:off+len(before)], after+bytes(len(before)-len(after)))
        self.assertTrue(all(i in allowed for i,(a,b) in enumerate(zip(old,new)) if a != b))
        self.assertEqual(len(old), len(new))
        # This donor maps code and read-only strings together in an RX segment.
        # Compare instruction-bearing ELF sections, not that whole segment.
        shoff = struct.unpack_from('<Q', old, 40)[0]
        entsize, count = struct.unpack_from('<HH', old, 58)
        executable = 0
        for i in range(count):
            _, kind, flags, _, offset, size, _, _, _, _ = struct.unpack_from('<IIQQQQIIQQ', old, shoff+i*entsize)
            if kind != 8 and flags & 4:
                self.assertEqual(hashlib.sha256(old[offset:offset+size]).digest(),
                                 hashlib.sha256(new[offset:offset+size]).digest())
                executable += 1
        self.assertGreater(executable, 0)
        self.assertEqual(fp.patch_hal(new), new)
        with self.assertRaises(ValueError): fp.patch_hal(new+b'unknown')
        partial = old.replace(b'ro.build.type\0', b'ro.vendor.fp\0\0')
        with self.assertRaises(ValueError): fp.patch_hal(partial)

    def test_real_vendor_payload_is_idempotent_and_keeps_global_build_identity(self):
        payload = fp.payload(read)
        self.assertEqual(fp.payload(lambda n: payload[n] if n in payload else read(n)), payload)
        old = read('build.prop').splitlines()
        for line in old:
            if b'build.type=' in line: self.assertIn(line, payload['build.prop'].splitlines())
        for _, name, value in fp.PROPERTIES:
            self.assertEqual(payload['build.prop'].splitlines().count(name+b'='+value), 1)
        self.assertIn(b'<sunwave:Config ', payload['etc/sw_config.xml'])
        xml = ET.fromstring(payload['etc/sw_config.xml'])
        original = ET.fromstring(read('etc/sw_config.xml'))
        self.assertEqual(xml.attrib, original.attrib)
        for child in original:
            if child.tag != 'diagnosis':
                self.assertEqual(ET.tostring(xml.find(child.tag)), ET.tostring(child))
        for name in ('data_dumping','img_invciper_data','spi_write','spi_write_read','init_chk_badpoint'):
            self.assertEqual(xml.findtext('diagnosis/'+name), 'false')
        self.assertEqual(payload['etc/init/init.a9-fingerprint.rc'].count(fp.INIT), 1)
        self.assertIn(fp.CONTEXT, payload['etc/selinux/vendor_file_contexts'].splitlines())


if __name__ == '__main__': unittest.main(argv=[__file__, *remaining], verbosity=2)
