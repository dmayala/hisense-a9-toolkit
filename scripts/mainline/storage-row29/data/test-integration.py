#!/usr/bin/env python3
"""Focused host regressions; regular fixtures and real Android mksh, no phone."""
import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SHELL = '/home/user/A9-build/mainline/integrated-20261021n/r4-tests/android-sh'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = load(HERE / 'integrate.py', 'data_stage')
builder = load(HERE.parents[1] / 'build-integrated.py', 'integrated_builder')


class Tests(unittest.TestCase):
    def test_render_roundtrip_idempotence_and_refusals(self):
        fstab = '# old RAM comment\n/vendor /vendor ext4 ro wait\n' + stage.RAM + '\n'
        rc = '''# Safety model (details in the design report):
# Nothing on UFS is opened for writing
# they do not depend on this vendor's policy.
on late-fs
    mount_all /vendor/etc/fstab.hlte556n --late
on post-fs-data
    write /dev/kmsg "a9ml: post-fs-data, /data is RAM"
service unrelated /vendor/bin/unrelated
    seclabel u:r:unrelated:s0
'''
        row = stage.render(fstab, rc, 'row29')
        self.assertEqual(row, stage.render(*row, 'row29'))
        self.assertEqual(row[1].count('service a9ml_row29_map '), 1)
        self.assertLess(row[1].index('exec_start a9ml_row29_map'), row[1].index('mount_all '))
        self.assertIn('service unrelated /vendor/bin/unrelated\n    seclabel u:r:unrelated:s0', row[1])
        self.assertNotIn('Nothing on UFS', row[1])
        ram = stage.render(*row, 'ram')
        self.assertEqual(ram, stage.render(*ram, 'ram'))
        self.assertIn(stage.RAM, ram[0])
        self.assertNotIn('exec_start a9ml_row29_map', ram[1])
        self.assertNotIn('service a9ml_row29_map ', ram[1])
        self.assertIn('/data mode ram', ram[1])
        self.assertEqual(row, stage.render(*ram, 'row29'))
        for bad in ('', fstab + stage.RAM, fstab.replace(stage.RAM, '/dev/block/sda /data ext4 rw wait')):
            with self.assertRaises(ValueError):
                stage.render(bad, rc, 'row29')
        with self.assertRaises(ValueError):
            stage.render(fstab, rc + '\non late-fs\n    mount_all /vendor/etc/fstab.hlte556n --late\n', 'row29')

    def test_builder_modes_config_boot_and_all_paths(self):
        source = (HERE.parents[1] / 'build-integrated.py').read_text()
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'apply_data_mode']
        self.assertEqual(len(calls), 3)  # normal, update-suspend, update-vibration
        option = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == '--data-mode']
        self.assertEqual(len(option), 1)
        self.assertEqual(next(k.value.value for k in option[0].keywords if k.arg == 'default'), 'row29')
        with tempfile.TemporaryDirectory(prefix='row29-route-') as tmp:
            out = Path(tmp)
            (out / 'boot.img').write_bytes(b'unchanged boot')
            (out / 'vendor.img').write_bytes(b'old vendor')
            def run(command, log):
                self.assertEqual(log, 'data-mode.log')
                self.assertIn('--data-mode', command)
                destination = out / 'data-mode'
                destination.mkdir()
                (destination / 'vendor.img').write_bytes(b'staged vendor')
            with self.assertRaises(RuntimeError):
                builder.apply_data_mode(out, 'row29', out / 'cache', run)
            self.assertEqual((out / 'vendor.img').read_bytes(), b'old vendor')
            (out / 'snapshot').mkdir()
            (out / 'snapshot/config').write_text('CONFIG_BLK_DEV_DM=y\n')
            builder.apply_data_mode(out, 'row29', out / 'cache', run)
            self.assertEqual((out / 'boot.img').read_bytes(), b'unchanged boot')
            self.assertEqual((out / 'vendor.img').read_bytes(), b'staged vendor')
            (out / 'data-mode').rmdir()
            (out / 'snapshot/config').unlink()
            builder.apply_data_mode(out, 'ram', out / 'cache', run)

    def test_android_checker_real_header_and_extra_target(self):
        with tempfile.TemporaryDirectory(prefix='row29-checker-') as tmp:
            root = Path(tmp)
            node = root / 'node'
            node.touch()
            marker = root / 'marker'
            mountinfo = root / 'mountinfo'
            mountinfo.write_text('32 1 253:7 / /data rw,nosuid,nodev - f2fs /dev/block/dm-7 rw,nodiscard\n')
            script = (HERE / 'check-data.sh').read_text().replace('/dev/block/mapper/a9-row29-data', str(node)).replace('[ -b "$node" ]', '[ -f "$node" ]').replace('/proc/self/mountinfo', str(mountinfo)).replace('/data/local/tmp/row29-persistent-proof', str(marker))
            stubs = '''getprop() { case "$1" in ro.boot.slot_suffix) echo _b;; sys.boot_completed) echo 1;; esac; }
stat() { echo fd:7; }
cat() { case "$1" in */dm/name) echo a9-row29-data;; */dm/uuid) echo A9-ROW29-DATA-20261021;; */size) echo 6191304;; */dev) echo 8:64;; */boot_id) echo "$FIXTURE_BOOT";; *) command cat "$@";; esac; }
ls() { echo sde; }
dmctl() { echo 'Targets in the device-mapper table for a9-row29-data:'; echo '0-6191304: linear, 8:64 2197264'; }
df() { echo fixture; }
sync() { :; }
'''
            script = script.replace('set -eu', 'set -eu\n' + stubs)
            path = root / 'check.sh'
            path.write_text(script)
            def run(mode, boot='two'):
                return subprocess.run([SHELL, str(path), mode], env={**os.environ, 'FIXTURE_BOOT': boot}, capture_output=True, timeout=15)
            self.assertEqual(run('create', 'one').returncode, 0)
            self.assertEqual(run('verify').returncode, 0)
            self.assertNotEqual(run('create').returncode, 0)
            path.write_text(script.replace('8:64 2197264', '8:64 2197265'))
            self.assertNotEqual(run('verify').returncode, 0)
            path.write_text(script.replace('8:64 2197264', '8:64 2197264\n6191304-6191312: error'))
            self.assertNotEqual(run('verify').returncode, 0)
            path.write_text(script)
            marker.write_text('corrupt')
            self.assertNotEqual(run('verify').returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
