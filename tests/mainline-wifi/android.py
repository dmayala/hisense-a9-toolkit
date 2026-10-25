#!/usr/bin/env python3
"""Offline integration checks against pinned actual CF ARM64 files; no execution."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parents[2]/'scripts/mainline/wifi/android'
spec=importlib.util.spec_from_file_location('a9_stage_android',HERE/'stage-android.py')
stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
CF=Path(os.environ.get('A9_WIFI_TEST_CF',str(Path.home()/'A9-build/mainline/android/dl/cf_arm64_x')))

class Android(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='a9-wifi-android-');self.root=Path(self.tmp.name)
        self.vendor=self.root/'vendor';self.vendor.mkdir();(self.vendor/'build.prop').write_text('ro.a9ml.wifi=1\n')
    def tearDown(self):self.tmp.cleanup()
    def test_actual_prebuilt_stage(self):
        stage.stage_prebuilts(self.vendor,CF)
        record=json.loads((self.vendor/'etc/a9-wifi/android-bundle.json').read_text())
        for f in record['files']:
            self.assertEqual(hashlib.sha256((self.vendor/f['target']).read_bytes()).hexdigest(),f['sha256'])
        hal=self.vendor/'lib64/a9-wifi/hal/libwifi-hal.so'
        self.assertIn('libnl.so',stage.needed(hal.read_bytes()))
        # The two libcrypto implementations really differ: preserve base vendor.
        self.assertNotEqual((self.vendor/'lib64/a9-wifi/supplicant/libcrypto.so').read_bytes(),(CF/'tf/VENDOR/lib64/libcrypto.so').read_bytes())
        self.assertFalse((self.vendor/'lib64/libcrypto.so').exists())
        self.assertFalse((self.vendor/'apex').exists())
        services={}
        for p in (self.vendor/'etc/vintf/manifest').glob('*.xml'):
            for h in ET.fromstring(p.read_text()).findall('hal'):
                services[h.findtext('name')]=(h.findtext('version'),h.findtext('fqname'))
        self.assertEqual(services,{'android.hardware.wifi':('3','IWifi/default'),'android.hardware.wifi.supplicant':('4','ISupplicant/default'),'android.hardware.wifi.hostapd':('3','IHostapd/default')})
    def test_tampered_source_is_rejected_before_copy(self):
        row=json.loads((HERE/'prebuilts.lock.json').read_text())['files'][0]
        cf=self.root/'tamper';p=cf/row['source'];p.parent.mkdir(parents=True);p.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'pinned prebuilt changed'):stage.stage_prebuilts(self.vendor,cf)
        self.assertEqual(sorted(p.name for p in self.vendor.iterdir()),['build.prop'])
    def test_dest_symlink_rejected(self):
        (self.vendor/'lib64').symlink_to(self.root/'elsewhere')
        with self.assertRaisesRegex(ValueError,'symlink'):stage.stage_prebuilts(self.vendor,CF)
        self.assertFalse((self.root/'elsewhere').exists())
    def test_build_prop_hardlink_rejected(self):
        os.link(self.vendor/'build.prop',self.root/'same')
        with self.assertRaisesRegex(ValueError,'build.prop'):stage.stage_prebuilts(self.vendor,CF)
        self.assertEqual((self.root/'same').read_text(),'ro.a9ml.wifi=1\n')
    def test_original_and_golden_output_rejected(self):
        for root in ['/home/user/A9','/home/user/a9-assets/Backups/edl_2026-10-14_110759']:
            with self.assertRaises(ValueError):stage.safe_stage(Path(root))
    def test_missing_vendor_refuses_without_writes(self):
        p=self.root/'missing';p.mkdir()
        with self.assertRaisesRegex(ValueError,'vendor stage'):stage.stage_prebuilts(p,CF)
        self.assertEqual(list(p.iterdir()),[])
    def test_init_gate(self):
        rc=(HERE/'a9-wifi-android.rc').read_text()
        self.assertEqual(rc.count('\n    interface aidl '),3)
        blocks=rc.split('\n\n');services=[b for b in blocks if '\nservice ' in '\n'+b]
        self.assertEqual(len(services),3)
        for s in services:
            self.assertIn('\n    disabled',s);self.assertIn('\n    oneshot',s)
            self.assertNotIn('SYS_MODULE',s)
        self.assertIn('property:vendor.a9ml.wifi.android.ready=0',rc)
        self.assertNotIn('setprop vendor.a9ml.wifi.android.ready',rc)
    def test_actual_wrapper_with_host_fixtures(self):
        # Replace only immutable absolute target paths with synthetic host files.
        # The conditional/role/argument code is the production script unchanged.
        fixture=self.root/'fixture';fixture.mkdir();phy=fixture/'phy';phy.mkdir()
        getprop=fixture/'getprop';getprop.write_text('#!/bin/sh\ncase "$1" in ro.a9ml.wifi.android) echo "$TEST_BUILD";; vendor.a9ml.wifi.android.ready) echo "$TEST_READY";; vendor.a9ml.wifi.android.ap) echo "$TEST_AP";; *) exit 1;; esac\n');getprop.chmod(0o755)
        text=(HERE/'a9ml-wifi-android-start.sh').read_text().replace('/system/bin/getprop',str(getprop)).replace('/sys/class/net/wlan0/phy80211',str(phy))
        for name in ['android.hardware.wifi-service','wpa_supplicant','hostapd']:
            executable=fixture/name;executable.write_text('#!/bin/sh\nprintf "EXEC %s\\n" "$0"\nprintf "ARG %s\\n" "$@"\n');executable.chmod(0o755)
            text=text.replace('/vendor/bin/hw/'+name,str(executable))
        wrapper=fixture/'start.sh';wrapper.write_text(text)
        def run(role,build='1',ready='1',ap='0'):
            return subprocess.run(['sh',str(wrapper),*role],capture_output=True,text=True,env=dict(os.environ,TEST_BUILD=build,TEST_READY=ready,TEST_AP=ap))
        for role in (['hal'],['supplicant'],['hostapd']):
            for build,ready in [('0','1'),('1','0'),('','1'),('1','')]:
                p=run(role,build,ready,'1');self.assertNotEqual(p.returncode,0);self.assertEqual(p.stdout,'')
        self.assertIn('EXEC',run(['hal']).stdout)
        self.assertIn('ARG -Dnl80211',run(['supplicant']).stdout)
        self.assertNotEqual(run(['hostapd']).returncode,0)
        self.assertIn('EXEC',run(['hostapd'],ap='1').stdout)
        for bad in ([],['/bin/true'],['hal','extra'],['hal;true']):self.assertNotEqual(run(bad).returncode,0)
        phy.rmdir();self.assertNotEqual(run(['hal']).returncode,0)
    def test_policy_labels(self):
        text=(HERE/'sepolicy/file_contexts').read_text()
        self.assertNotIn('phhsu',text)
        self.assertIn('a9_wifi_private_file',text)
        self.assertIn('a9_wifi_rmtfs_device',text)
    def test_actual_merged_policy(self):
        cache=Path(os.environ.get('A9_WIFI_TEST_GSI_POLICY',str(Path.home()/'A9-build/mainline/android/cache/gsi-sepolicy/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v7-3569721344-1791991462')))
        vendor=self.vendor/'etc/selinux'
        shutil.copytree(CF/'tf/VENDOR/etc/selinux',vendor)
        stage.stage_policy(self.vendor)
        version=(vendor/'plat_sepolicy_vers.txt').read_text().strip()
        files=[cache/'system/etc/selinux/plat_sepolicy.cil',cache/f'system/etc/selinux/mapping/{version}.cil']
        for rel in [f'system/etc/selinux/mapping/{version}.compat.cil','system/system_ext/etc/selinux/system_ext_sepolicy.cil',f'system/system_ext/etc/selinux/mapping/{version}.cil',f'system/system_ext/etc/selinux/mapping/{version}.compat.cil','system/product/etc/selinux/product_sepolicy.cil',f'system/product/etc/selinux/mapping/{version}.cil',f'system/product/etc/selinux/mapping/{version}.compat.cil']:
            if (cache/rel).exists():files.append(cache/rel)
        files += [vendor/'plat_pub_versioned.cil',vendor/'vendor_sepolicy.cil']
        subprocess.run([str(CF/'otatools/bin/secilc'),'-m','-M','true','-G','-N','-c','30',*[str(p) for p in files],'-o',str(self.root/'sepolicy'),'-f','/dev/null'],check=True,capture_output=True,text=True)
        self.assertGreater((self.root/'sepolicy').stat().st_size,0)

if __name__=='__main__':unittest.main(verbosity=2)
