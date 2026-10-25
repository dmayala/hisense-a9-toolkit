#!/usr/bin/env python3
"""Release regressions using host ext4 fixtures, real policy and APEX signing.

No target binaries, daemon entry points, phone scripts or device tools execute.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess as sp
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = Path.home()/'A9-build/mainline'


def load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m


b = load('release', HERE/'build.py')
i = load('integrated', HERE.parent/'build-integrated.py')


def fixture(directory, files):
    directory.mkdir()
    tree = directory/'tree'; tree.mkdir()
    for name, data in files.items():
        p = tree/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    image = directory/'fixture.img'
    with image.open('wb') as f:
        f.truncate(16*1024**2)
    sp.run(['mke2fs', '-q', '-F', '-t', 'ext4', '-d', str(tree), str(image)], check=True,
           stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    label = directory/'label'; label.write_bytes(b'u:object_r:vendor_file:s0\0')
    batch = directory/'labels'; batch.write_text(''.join(
        f'ea_set -f {label} /{name} security.selinux\n' for name in files))
    sp.run(['debugfs', '-w', '-f', str(batch), str(image)], check=True,
           stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    return image


class ReleaseTests(unittest.TestCase):
    def test_1_secure_props_and_dev_selection(self):
        text = 'ro.debuggable=1\nro.debuggable=0\nro.secure=0\nro.adb.secure=0\nro.build.type=userdebug\nro.vendor.build.type=userdebug\nro.vendor.build.tags=test-keys\nro.vendor.build.fingerprint=a/b/c:16/id/inc:userdebug/test-keys\nro.vendor.build.security_patch=2022-04-05\n'
        p = b.properties(text, True)
        for key, val in b.SECURE.items():
            self.assertEqual([l for l in p.splitlines() if l.startswith(key+'=')], [key+'='+val])
        self.assertIn(':user/release-keys\n', p)
        self.assertIn('ro.vendor.build.security_patch=2022-04-05', p)
        self.assertIn('persist.sys.usb.config=none', p)
        partitions=b.properties('ro.system_ext.build.type=eng\nro.product.build.tags=test-keys\n')
        self.assertIn('ro.system_ext.build.type=user\n',partitions)
        self.assertIn('ro.product.build.tags=release-keys\n',partitions)
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(i.release_default())
        for value, expected in [('0', False), ('1', True)]:
            with patch.dict(os.environ, {'A9_RELEASE': value}):
                self.assertEqual(i.release_default(), expected)
        with patch.dict(os.environ, {'A9_RELEASE': 'yes'}):
            with self.assertRaises(ValueError): i.release_default()

    def test_2_consoles_removed_functional_roots_preserved(self):
        old = b.read(ARGS.vendor, 'etc/init/hw/init.hlte556n.rc').decode()
        new = b.release_rc(old, True, True)
        for token in ('service a9ml_rootsh ', 'start a9ml_rootsh', '/adb_keys',
                      '/securize_disable', '/acm.gs9', '/ncm.gs8', 'a9ml_test_awake',
                      'start a9ml_deadman', 'service a9ml_deadman ', 'start adbd'):
            self.assertNotIn(token, new)
        for token in ('service a9ml_metadata_mount ', 'service a9ml_wrapped_mount ',
                      'seclabel u:r:a9ml_rootsh:s0', 'start vendor.qseecomd',
                      'start vendor.keymaster-4-1', '/sys/module/a9_boot_deadline/parameters/boot_completed'):
            self.assertIn(token, new)
        usb = b.read(ARGS.vendor, 'etc/init/android.hardware.usb.gadget-service.a9.rc').decode()
        self.assertIn('start adbd', b.release_rc(usb, True))
        debug = b.release_rc(b.read(ARGS.vendor, 'etc/init/init.a9ml-debug.rc').decode(), True)
        self.assertIn('service vendor.a9ml_logd ', debug)
        self.assertIn('restart adbd', debug)
        self.assertNotIn('start a9ml_rootsh', debug)
        remote = b.release_rc(b.read(ARGS.system, 'system/etc/init/phh-remotectl.rc').decode())
        self.assertNotIn('service phh-remotectl', remote)
        self.assertNotIn('persist.adb.tcp.port', remote)
        self.assertEqual(new, b.release_rc(new, True, True))

    def test_3_boot_requires_actual_primary_enforcing(self):
        with tempfile.TemporaryDirectory(dir=ARGS.out) as d:
            p = Path(d)/'boot.img'; data = bytearray(4096); data[:8] = b'ANDROID!'
            struct.pack_into('<I', data, 40, 2)
            for primary, extra, ok in [('enforcing=1 a9_deadline=180', '', True),
                    ('enforcing=0', 'enforcing=1', False), ('', 'enforcing=1', False),
                    ('enforcing=1 selinux_force_permissive=1', '', False),
                    ('enforcing=1', 'androidboot.force_debuggable=1', False)]:
                data[64:576] = primary.encode().ljust(512,b'\0')
                data[608:1632] = extra.encode().ljust(1024,b'\0'); p.write_bytes(data)
                if ok: b.check_boot(p)
                else:
                    with self.assertRaises(ValueError): b.check_boot(p)
                self.assertEqual(p.read_bytes(), data)
        b.check_boot(ARGS.boot)

    def test_4_ext4_readback_metadata_removal_immutable_source(self):
        with tempfile.TemporaryDirectory(dir=ARGS.out) as d:
            d = Path(d)
            src = fixture(d/'source', {'build.prop': b'ro.debuggable=1\n',
                                       'etc/adb_keys': b'synthetic fixture; no real host key\n'})
            digest = b.sha(src); work = d/'work'; work.mkdir()
            changed = {'build.prop': b.properties('ro.debuggable=1\n', True).encode()}
            b.install(src, d/'output.img', changed, {'etc/adb_keys'}, work)
            self.assertEqual(b.sha(src), digest)
            self.assertEqual(b.read(d/'output.img', 'build.prop'), changed['build.prop'])
            with self.assertRaises(ValueError): b.install(src, src, changed, set(), work)

    def test_5_real_policy_zero_permissives_logger_domain_retained(self):
        directory = ARGS.out/'policy'; directory.mkdir(exist_ok=True)
        changed, receipt = b.prepare_policy(ARGS.system, ARGS.vendor, directory, ARGS.ota/'bin/secilc')
        self.assertEqual(receipt['permissive_domains'], 0)
        self.assertEqual(b.permissive_count(changed['etc/selinux/precompiled_sepolicy']), 0)
        self.assertIn(b'(type a9ml_rootsh)', changed['etc/selinux/vendor_sepolicy.cil'])
        self.assertNotIn(b'(typepermissive ', changed['etc/selinux/vendor_sepolicy.cil'])
        (ARGS.out/'policy-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')

    def test_6_actual_apex_both_signatures_and_filesystem(self):
        # Independent temporary test keys, never copied from the production store.
        # Test only two payloads at a time; production key preparation is explicit.
        with tempfile.TemporaryDirectory(dir=ARGS.out) as d:
            d = Path(d); keys = d/'keys'; keys.mkdir(mode=0o700); (keys/'apex').mkdir(mode=0o700)
            env = dict(os.environ, PATH=str(ARGS.java.parent)+':'+os.environ.get('PATH',''))
            sp.run(['openssl','req','-new','-x509','-newkey','rsa:4096','-nodes','-days','1',
                    '-subj','/CN=A9 host fixture/', '-keyout',str(keys/'releasekey.pem'),
                    '-out',str(keys/'releasekey.x509.pem')], check=True,
                   stdout=sp.DEVNULL, stderr=sp.DEVNULL)
            sp.run(['openssl','pkcs8','-topk8','-nocrypt','-in',str(keys/'releasekey.pem'),
                    '-outform','DER','-out',str(keys/'releasekey.pk8')],check=True)
            for p in keys.iterdir():
                if p.is_file():p.chmod(0o600)
            for part, image, path in [('vendor', ARGS.vendor, 'apex/com.android.hardware.audio.apex'),
                    ('system', ARGS.system, 'system/system_ext/apex/com.android.vndk.v28.apex')]:
                key = keys/'apex'/(Path(path).stem+'.pem')
                shutil.copyfile(keys/'releasekey.pem', key); key.chmod(0o600)
                out = d/part; out.mkdir()
                data, receipt = b.sign_apex(image, path, out, keys, ARGS.ota, ARGS.java, env)
                self.assertGreater(len(data), 0)
                self.assertGreater(receipt['filesystem_bytes_unchanged'], 0)
                self.assertNotEqual(hashlib.sha256(b.read(image,path)).hexdigest(), receipt['signed_sha256'])
                key.chmod(0o644)
                with self.assertRaises(ValueError): b.sign_apex(image, path, out, keys, ARGS.ota, ARGS.java, env)

    def test_7_release_dispatch_and_dev_noop(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=ARGS.out) as d:
            out = Path(d); calls = []
            args = SimpleNamespace(release=False, release_system=ARGS.system, release_keys=out/'keys')
            def run(argv, log):
                calls.append((argv, log)); target=out/'release'; target.mkdir()
                for n in ('boot.img','vendor.img','system.img'): (target/n).write_bytes(n.encode())
                (target/'release-build.json').write_text('{"release":true}')
            self.assertIsNone(i.apply_release(out,args,run)); self.assertEqual(calls,[])
            args.release=True; self.assertTrue(i.apply_release(out,args,run)['release'])
            self.assertEqual({p.name for p in out.glob('*.img')}, {'vendor.img','boot.img','system.img'})
            self.assertIn('--system',calls[0][0]); self.assertIn('--keys',calls[0][0])

    def test_8_trust_seal_and_no_image_in_place_or_key_generation(self):
        profile=json.loads((HERE/'los-v8-trust.json').read_text())
        self.assertEqual(b.sha(ARGS.system), profile['system_sha256'])
        self.assertEqual(len(profile['signed_files']),702)
        self.assertEqual(len(profile['resign_apexes']),7)
        for p in profile['remove']: self.assertIn(p,profile['signed_files'])
        self.assertEqual(profile['platform_certificate_sha256'],
                         'a82af580b4247139a966a8eb8b79ceecd148952d981a59cda9086a834d2f020b')
        with patch.dict(os.environ,{'A9_RELEASE':'1'}):
            p=sp.run([sys.executable,'-I',str(HERE.parent/'build-integrated.py'),
                      '--out',str(ARGS.out/'must-not-exist')],capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0); self.assertIn('requires --release-system',p.stderr)
            self.assertFalse((ARGS.out/'must-not-exist').exists())

    def test_9_full_finalizer_small_ext4_pair(self):
        # End-to-end real debugfs/fsck/policy packaging. Only disk admission is
        # mocked: synthetic images consume <100MiB, not a full GSI's budget.
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=ARGS.out) as d:
            d=Path(d); profile=json.loads((HERE/'los-v8-trust.json').read_text())
            system_paths=['system/etc/selinux/plat_sepolicy.cil',
                'system/etc/selinux/mapping/202504.cil',
                'system/system_ext/etc/selinux/system_ext_sepolicy.cil',
                'system/system_ext/etc/selinux/mapping/202504.cil',
                'system/product/etc/selinux/product_sepolicy.cil',
                'system/product/etc/selinux/mapping/202504.cil']
            for prefix,directory in [('plat','system/etc/selinux'),
                    ('system_ext','system/system_ext/etc/selinux'),('product','system/product/etc/selinux')]:
                system_paths.append(directory+'/'+prefix+'_sepolicy_and_mapping.sha256')
            sf={p:b.read(ARGS.system,p) for p in system_paths}
            sf.update({'system/build.prop':b'ro.debuggable=1\nro.secure=0\nro.adb.secure=0\n',
                       'system/framework/fixture.apk':b'INERT SIGNED-BYTE PRESERVATION FIXTURE',
                       'system/apex/remove-me.apex':b'INERT REMOVAL FIXTURE'})
            system=fixture(d/'system',sf)
            vendor_paths=['etc/selinux/plat_sepolicy_vers.txt','etc/selinux/plat_pub_versioned.cil',
                          'etc/selinux/vendor_sepolicy.cil','etc/selinux/precompiled_sepolicy',
                          'etc/init/hw/init.hlte556n.rc','bin/a9ml-usb.sh','build.prop']
            vf={p:b.read(ARGS.vendor,p) for p in vendor_paths}
            for prefix in ('plat','system_ext','product'):
                p='etc/selinux/precompiled_sepolicy.'+prefix+'_sepolicy_and_mapping.sha256'
                vf[p]=b.read(ARGS.vendor,p)
            vf['etc/adb_keys']=b'INERT FIXTURE'; vendor=fixture(d/'vendor',vf)
            keys=d/'keys'; keys.mkdir(mode=0o700)
            profile.update(system_sha256=b.sha(system),resign_apexes=[],
                remove=['system/apex/remove-me.apex'],signed_files={'system/framework/fixture.apk':
                    {'sha256':hashlib.sha256(sf['system/framework/fixture.apk']).hexdigest()}})
            trust=d/'trust.json'; trust.write_text(json.dumps(profile))
            argv=['build.py','--boot',str(ARGS.boot),'--vendor',str(vendor),'--system',str(system),
                  '--keys',str(keys),'--out',str(d/'output'),'--system-trust',str(trust),
                  '--otatools',str(ARGS.ota),'--java',str(ARGS.java)]
            before=(b.sha(system),b.sha(vendor),b.sha(ARGS.boot))
            with patch.object(sys,'argv',argv), patch.object(b.shutil,'disk_usage',
                    return_value=SimpleNamespace(free=50*1024**3)):
                b.main()
            receipt=json.loads((d/'output/release-build.json').read_text())
            self.assertTrue(receipt['boot_bytes_unchanged'])
            self.assertEqual(receipt['policy']['permissive_domains'],0)
            self.assertEqual(before,(b.sha(system),b.sha(vendor),b.sha(ARGS.boot)))
            for part in ('system','vendor'):
                p='system/build.prop' if part=='system' else 'build.prop'
                data=b.read(d/'output'/(part+'.img'),p)
                self.assertIn(b'ro.debuggable=0\n',data); self.assertIn(b'ro.adb.secure=1\n',data)


if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--boot',type=Path,default=ROOT/'integ-20261024-26/boot.img')
    ap.add_argument('--vendor',type=Path,default=ROOT/'integ-20261024-26/vendor.img')
    ap.add_argument('--system',type=Path,default=Path.home()/'A9-build/roms/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8.img')
    ap.add_argument('--ota',type=Path,default=ROOT/'android/dl/cf_arm64_x/otatools')
    ap.add_argument('--java',type=Path,default=Path.home()/'A9-build/tools/jdk21/bin/java')
    ARGS=ap.parse_args(); os.umask(0o077); ARGS.out.mkdir(parents=True,exist_ok=True)
    unittest.main(argv=[sys.argv[0]],verbosity=2)
