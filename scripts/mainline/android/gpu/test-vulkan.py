#!/usr/bin/env python3
"""Focused host checks; only small synthetic filesystems and an inert bionic dlopen."""
import importlib.util
from pathlib import Path
import shutil
import subprocess as sp
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[1]
ROOT = Path('/home/user/A9-build/mainline')
SYSTEM = ROOT/'adsp/d2-v8-20261017/system-image/system'
NDK = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin')
QEMU = '/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'


def load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


v = load('vulkan', HERE/'vulkan.py')
integ = load('integ', MAIN/'reboot/build14.py')
meta = load('metadata', MAIN/'android/refresh-integrated-vendor.py')
OLD = 'ro.vendor.a9ml.gpu=1\nro.hardware.vulkan=pastel\nro.cpuvulkan.version=4202496\nro.vulkan.apex=com.google.cf.vulkan\nother=keep\n'


class Vulkan(unittest.TestCase):
    def test_1_selection_and_a610_features(self):
        props = v.render(OLD)
        self.assertEqual(v.render(props), props)
        self.assertIn('ro.hardware.vulkan=adreno\n', props)
        self.assertIn('other=keep\n', props)
        self.assertNotIn('ro.vulkan.apex=', props)
        self.assertNotIn('ro.cpuvulkan.version=', props)
        self.assertEqual(v.render('ro.hardware.vulkan=pastel\n'), 'ro.hardware.vulkan=pastel\n')
        features = {}
        for name in v.FEATURES:
            tree = ET.parse(HERE/'vulkan-features'/Path(name).name)
            for f in tree.getroot(): features[f.attrib['name']] = int(f.attrib['version'])
        self.assertEqual(features, {'android.hardware.vulkan.level': 1,
                                   'android.hardware.vulkan.version': 4194307})
        self.assertEqual(v.payload(lambda _: b'ro.hardware.vulkan=pastel\n'), ({}, ()))

    def test_2_same_mesa_build_required(self):
        def read(name): return OLD.encode() if name == 'build.prop' else (v.MESA/name).read_bytes()
        files, removed = v.payload(read)
        self.assertEqual(files[v.DRIVER], (v.MESA/v.DRIVER).read_bytes())
        self.assertEqual(removed, v.REMOVE)
        with self.assertRaises(AssertionError):
            v.payload(lambda name: b'wrong GLES build' if name == v.GLES[0] else read(name))
        with tempfile.TemporaryDirectory() as tmp:
            mesa = Path(tmp)
            for name in (*v.GLES, 'BUILDINFO'):
                p = mesa/name; p.parent.mkdir(parents=True, exist_ok=True); p.symlink_to(v.MESA/name)
            lines = [line for line in (v.MESA/'SHA256SUMS').read_text().splitlines() if v.DRIVER not in line]
            (mesa/'SHA256SUMS').write_text('\n'.join(lines)+'\n')
            with self.assertRaises(AssertionError): v.payload(read, mesa=mesa)

    def test_3_real_ext4_incremental_and_fresh_stage(self):
        self.assertGreaterEqual(shutil.disk_usage('/tmp').free, 15*1024**3+192*1024**2)
        with tempfile.TemporaryDirectory(prefix='a9-vulkan-') as tmp:
            root = Path(tmp); stage = root/'stage'; stage.mkdir()
            initial = {'build.prop': OLD.encode(), 'etc/media_codecs_c2.xml': b'<MediaCodecs/>\n',
                       v.REMOVE[0]: b'INERT CF APEX FIXTURE',
                       'etc/init/init.a9ml-gpu.rc': b'on init\n', **{n: (v.MESA/n).read_bytes() for n in v.GLES}}
            # Existing hw directory, as in every GPU vendor; the HAL itself is
            # deliberately absent to exercise installation as well as selection.
            (stage/'lib64/hw').mkdir(parents=True)
            (stage/'etc/permissions').mkdir(parents=True)
            for name, data in initial.items():
                p = stage/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); p.chmod(0o644)
            base = root/'base.img'
            with base.open('wb') as f: f.truncate(64*1024**2)
            sp.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-d', stage, base], check=True)
            label = root/'label'; commands = []
            for name in (v.GLES[0], 'etc/media_codecs_c2.xml', 'build.prop', 'etc/init/init.a9ml-gpu.rc'):
                label.write_bytes(('u:object_r:'+('same_process_hal_file' if name == v.GLES[0] else 'vendor_configs_file')+':s0\0').encode())
                sp.run(['debugfs', '-w', '-R', f'ea_set -f {label} /{name} security.selinux', base], check=True, capture_output=True)
            output = root/'out'; output.mkdir(); shutil.copyfile(base, output/'vendor.img')
            files, removed = v.payload(lambda name: integ.member(base, name))
            sources = {}
            for i, (name, data) in enumerate(files.items()):
                p = root/f'payload-{i}'; p.write_bytes(data); sources[name] = p
            def run(argv, name): sp.run(list(map(str, argv)), check=True, capture_output=True)
            _, added, changed = integ.install_payload(base, output, meta, sources, run, sources, removed)
            self.assertEqual(set(changed), set(files)|set(removed))
            self.assertEqual(added, {v.DRIVER, *v.FEATURES})
            self.assertEqual(meta.metadata(output/'vendor.img', v.DRIVER)['label'], 'u:object_r:same_process_hal_file:s0')
            for name in v.FEATURES:
                self.assertEqual(meta.metadata(output/'vendor.img', name)['label'], 'u:object_r:vendor_configs_file:s0')
            self.assertNotIn(v.REMOVE[0], set(meta.vendor_files(output/'vendor.img')))
            sp.run(['e2fsck', '-fn', output/'vendor.img'], check=True, capture_output=True)
            sp.run(['python3', '-I', HERE/'vulkan.py', '--stage', stage], check=True, capture_output=True)
            for name, data in files.items(): self.assertEqual((stage/name).read_bytes(), data)
            self.assertFalse((stage/v.REMOVE[0]).exists())
            # The general integrated builder uses this separate refresh path.
            overlay = root/'overlay'; overlay.mkdir()
            refresh = root/'refresh'
            sp.run(['python3', '-I', MAIN/'android/refresh-integrated-vendor.py',
                    '--base-vendor', base, '--overlay', overlay, '--overlay-only',
                    '--out', refresh], check=True, capture_output=True, timeout=30)
            for name, data in files.items():
                self.assertEqual(integ.member(refresh/'vendor.img', name), data)
            self.assertNotIn(v.REMOVE[0], set(meta.vendor_files(refresh/'vendor.img')))
            self.assertEqual(meta.metadata(refresh/'vendor.img', v.DRIVER)['label'], 'u:object_r:same_process_hal_file:s0')


    def test_4_real_bionic_turnip_load_without_gpu(self):
        with tempfile.TemporaryDirectory(prefix='a9-vulkan-load-') as tmp:
            exe = Path(tmp)/'load'
            sp.run([NDK/'aarch64-linux-android34-clang', '-O2', '-Wall', '-Wextra', '-Werror',
                    HERE/'vulkan-load.c', '-ldl', '-o', exe], check=True)
            paths = [ROOT/'android/dl/cf_arm64_x/tf/VENDOR/lib64', SYSTEM/'lib64/bootstrap', SYSTEM/'lib64']
            r = sp.run([QEMU, '-E', 'LD_LIBRARY_PATH='+':'.join(map(str, paths)),
                        SYSTEM/'bin/bootstrap/linker64', exe, v.MESA/v.DRIVER],
                       capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
            self.assertIn('VULKAN_LOAD relocations=1 turnip_hal=1 hardware_tested=0', r.stdout)
            print(r.stdout.strip())


if __name__ == '__main__': unittest.main(verbosity=2)
