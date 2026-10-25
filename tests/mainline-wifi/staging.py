#!/usr/bin/env python3
"""Synthetic private assets only; no golden file or real address is read."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import re

ROOT=Path(__file__).resolve().parents[2]
WIFI=ROOT/'scripts/mainline/wifi'
BIN=Path(os.environ.get('A9_WIFI_TEST_BINARIES','/tmp/sol-wifi-build-android'))

class Staging(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='a9-wifi-staging-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.assets=self.root/'assets';self.assets.mkdir()
        self.rows=[]
        for n,size in [('modemst1',2097152),('modemst2',2097152),('fsg',2097152),('fsc',131072)]:
            self.asset('private/'+n+'.bin',(b'fixture-'+n.encode())*(size//(8+len(n)))+b'X'*(size%(8+len(n))))
        self.asset('private/wlan_mac.bin',b'Intf0MacAddress=020000000001\nEND\n')
        for n in ['MODEM.MDT']+[f'MODEM.B{i:02d}' for i in list(range(14))+list(range(15,21))+list(range(23,30))]+['MODEMR.JSN','MODEMUW.JSN']:
            self.asset('modem/IMAGE/'+n,b'fixture')
        self.asset('modem/IMAGE/WLANMDSP.MBN',bytes(3814580))
        self.asset('modem/IMAGE/BDWLAN.BIN',bytes(26328))
        (self.assets/'manifest.json').write_text(json.dumps(self.rows))
        for n in ('regulatory.db','regulatory.db.p7s'):(self.assets/n).write_bytes(b'synthetic-signed-db-placeholder')

    def asset(self,name,data):
        p=self.assets/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        self.rows.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})

    def stage(self,mode='kernel',name='vendor'):
        dst=self.root/name;dst.mkdir();(dst/'build.prop').write_text('fixture=1\n')
        r=subprocess.run(['python3','-I',str(WIFI/'stage-vendor.py'),'--stage',str(dst),'--assets',str(self.assets),'--binaries',str(BIN)],env=os.environ|{'A9_WIFI_PD_MAPPER':mode},capture_output=True,text=True)
        return dst,r

    def test_private_staging_modes_and_no_live_paths(self):
        if not (BIN/'build-manifest.json').is_file():self.skipTest('build static ARM64 userspace first')
        for mode in ('kernel','userspace'):
            dst,r=self.stage(mode,mode);self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual((dst/'bin/pd-mapper').exists(),mode=='userspace')
            self.assertTrue((dst/'bin/diag-router').is_file())
            self.assertTrue((dst/'bin/qrtr-lookup').is_file())
            self.assertTrue((dst/'bin/a9ml-wifi-firmware.sh').is_file())
            self.assertIn('diag-router ',(dst/'etc/a9-wifi/assets.sha256').read_text())
            self.assertNotIn('qrtr-lookup ',(dst/'etc/a9-wifi/assets.sha256').read_text())
            rc=(dst/'etc/init/init.a9ml-wifi.rc').read_text()
            self.assertEqual('service vendor.pd_mapper ' in rc,mode=='userspace')
            self.assertNotIn('persist.vendor.a9ml.wifi.services=1\n',(dst/'build.prop').read_text())
            self.assertNotIn('/dev/block',rc)
            self.assertNotIn('write /sys/class/remoteproc',rc)
            self.assertEqual((dst/'etc/a9-wifi/seeds/fsc.bin').stat().st_mode&0o777,0o400)
            self.assertFalse(any(p.name=='wlan_mac.bin' for p in dst.rglob('*')))
            # ath10k feature-flag file (read from the cached linux-firmware store, not git).
            fw5=dst/'firmware/ath10k/WCN3990/hw1.0/hisense/hlte556n/firmware-5.bin'
            self.assertEqual(fw5.read_bytes()[:11],b'QCA-ATH10K\0');self.assertEqual(fw5.stat().st_size,60)
            self.assertEqual(hashlib.sha256(fw5.read_bytes()).hexdigest(),'80daf4f3a89e6ea8222e45387bb362c854fcd30c02e9da59ca723b39b269cdd2')
            rows=[r for r in json.loads((dst/'etc/a9-wifi/build-manifest.json').read_text())['installed'] if r['path'].endswith('hlte556n/firmware-5.bin')]
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['bytes'],60)

    def test_missing_or_foreign_ath10k_firmware5_refuses_staging(self):
        if not (BIN/'build-manifest.json').is_file():self.skipTest('build static ARM64 userspace first')
        empty=self.root/'no-fw';empty.mkdir()
        dst=self.root/'vendor-nofw';dst.mkdir();(dst/'build.prop').write_text('fixture=1\n')
        r=subprocess.run(['python3','-I',str(WIFI/'stage-vendor.py'),'--stage',str(dst),'--assets',str(self.assets),'--binaries',str(BIN),'--ath10k-fw-dir',str(empty)],capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0);self.assertIn('firmware-5.bin',r.stderr)
        # The generic (non-qcm2290) variant has different bytes: refused by the pinned hash.
        generic=self.root/'generic-fw/qcm2290';generic.mkdir(parents=True)
        src=Path('/nix/store/7nm4vfj0j5q7w2pn46alb8d0ajr1h1xp-linux-firmware-20250917-zstd/lib/firmware/ath10k/WCN3990/hw1.0/firmware-5.bin.zst')
        if not src.is_file():self.skipTest('cached linux-firmware store absent')
        (generic/'firmware-5.bin.zst').write_bytes(src.read_bytes())
        dst=self.root/'vendor-generic';dst.mkdir();(dst/'build.prop').write_text('fixture=1\n')
        r=subprocess.run(['python3','-I',str(WIFI/'stage-vendor.py'),'--stage',str(dst),'--assets',str(self.assets),'--binaries',str(BIN),'--ath10k-fw-dir',str(self.root/'generic-fw')],capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0);self.assertIn('unexpected ath10k firmware-5.bin',r.stderr)

    def test_seed_integrity_mismatch_fails(self):
        if not (BIN/'build-manifest.json').is_file():self.skipTest('build static ARM64 userspace first')
        (self.assets/'private/fsg.bin').write_bytes(b'bad')
        _,r=self.stage();self.assertNotEqual(r.returncode,0)
        self.assertIn('integrity mismatch',r.stderr)

    def test_mac_private_include_and_symlink_refusal(self):
        out=self.root/'private.dtsi'
        cmd=['python3','-I',str(WIFI/'mac-dtsi.py'),'--assets',str(self.assets),'--output',str(out)]
        r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(out.stat().st_mode&0o777,0o600)
        self.assertIn('local-mac-address',out.read_text());self.assertEqual(r.stdout,'')
        link=self.root/'alias.dtsi';link.symlink_to(out)
        r=subprocess.run(cmd[:-1]+[str(link)],capture_output=True,text=True)
        self.assertNotEqual(r.returncode,0);self.assertIn('symlinks',r.stderr)

    def test_original_checkout_and_golden_outputs_rejected(self):
        for target in ('/home/user/A9/forbidden-wifi-output','/home/user/a9-assets/forbidden-wifi-output','/srv/nas/A9/forbidden-wifi-output'):
            r=subprocess.run(['python3','-I',str(WIFI/'mac-dtsi.py'),'--assets',str(self.assets),'--output',target],capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            r=subprocess.run(['python3','-I',str(WIFI/'build-userspace.py'),'--out',target],capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)

    def test_actual_packer_fsconfig_protects_seeds(self):
        stage=self.root/'pack';(stage/'etc/a9-wifi/seeds').mkdir(parents=True)
        (stage/'etc/a9-wifi/seeds/fsg.bin').write_bytes(b'not-private-fixture')
        for n in ('assets.sha256','build-manifest.json'):(stage/'etc/a9-wifi'/n).write_bytes(b'fixture')
        script=(ROOT/'scripts/mainline/android/pack-vendor.sh').read_text()
        found=re.search(r'''python3 -I - "\$STAGE" .*? <<'PY'\n(.*?)\nPY''',script,re.S)
        self.assertIsNotNone(found)
        config=self.root/'fsconfig';stats=self.root/'stats'
        subprocess.run(['python3','-I','-',str(stage),str(WIFI/'fs-config.txt'),str(config),str(stats)],input=found.group(1),text=True,check=True,capture_output=True)
        rows={f[0]:f[1:4] for line in config.read_text().splitlines() if (f:=line.split())}
        self.assertEqual(rows['vendor/etc/a9-wifi/seeds/fsg.bin'],['0','0','400'])
        self.assertEqual(rows['vendor/etc/a9-wifi/assets.sha256'],['0','0','400'])
        self.assertEqual(rows['vendor/etc/a9-wifi/seeds'],['0','0','700'])

if __name__=='__main__':unittest.main(verbosity=2)
