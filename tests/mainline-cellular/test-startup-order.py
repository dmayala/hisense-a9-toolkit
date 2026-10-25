#!/usr/bin/env python3
"""Host-only early-radio cases, using Android mksh and synthetic files."""
from pathlib import Path
import hashlib, json, os, re, shutil, subprocess as sp, tempfile, unittest

REPO=Path(__file__).resolve().parents[2]
STOCK=REPO/'scripts/mainline/cellular/stock'
SH=Path('/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sh')
RC=(STOCK/'vendor/etc/init/init.a9ml-stock-radio.rc').read_text()


def client_starts(props):
    starts=[]
    for block in RC.split('\n\n'):
        commands=re.findall(r'^    start (vendor\.[a-z0-9_]+)$',block,re.M)
        if not commands:continue
        gates=re.findall(r'property:([a-zA-Z0-9_.]+)=([a-zA-Z0-9_]+)',block)
        if gates and all(props.get(k)==v for k,v in gates):starts+=commands
    return starts


class Startup(unittest.TestCase):
    def fixture(self,mode):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        for name in ('vendor/etc/a9-stock-radio','vendor/radio/qcril_database','data/vendor','dev/socket','sys/module/ipa','sys/module/rmnet','commands'):
            (root/name).mkdir(parents=True,exist_ok=True)
        if mode=='data_modules_failed':
            # INTEG24: legacy qcom_common/IPA imports fail before preparation.
            (root/'sys/module/ipa').rmdir();(root/'sys/module/rmnet').rmdir()
        files=[]
        for i in range(134):
            name=f'mcfg_sw/test/profile{i}.mbn';data=f'fixture{i}\n'.encode()
            p=root/'vendor/firmware/modem_pr/mcfg/configs'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            files.append(hashlib.sha256(data).hexdigest()+'  '+name+'\n')
        (root/'vendor/etc/a9-stock-radio/mcfg-files.sha256').write_text(''.join(files))
        db=b'synthetic database\n';(root/'vendor/radio/qcril_database/qcril.db').write_bytes(db)
        (root/'vendor/etc/a9-stock-radio/qcril-db.sha256').write_text(hashlib.sha256(db).hexdigest()+'  qcril.db\n')
        props={'vendor.a9ml.cellular':'1','ro.a9ml.wifi.session':'1','ro.boot.slot_suffix':'_b',
               'sys.boot_completed':'0','vendor.a9ml.wifi.session.state':'starting',
               'vendor.a9ml.wifi.android.ready':'0','ro.vendor.a6l.ril.slots':'2'}
        if mode=='optout':props['vendor.a9ml.cellular']='0'
        if mode=='owner_failed':props['vendor.a9ml.wifi.session.state']='failed'
        if mode=='data_modules_failed':
            props.update({'vendor.a9ml.cellular.stock.ready':'0',
                          'vendor.a9ml.cellular.stock.state':'failed',
                          'gsm.sim.state':'ABSENT,ABSENT'})
        propfile=root/'props.json';propfile.write_text(json.dumps(props))
        for command in ('getprop','setprop'):
            body=('#!/usr/bin/env python3\nimport os,json,sys\nfrom pathlib import Path\n'
                  'p=Path(os.environ["TEST_PROPS"]);d=json.loads(p.read_text())\n')
            body+=('print(d.get(sys.argv[1],""))\n' if command=='getprop' else
                   'assert sys.argv[1].startswith(("vendor.a9ml.cellular.","ro.vendor.ril.mbn_copy_completed"))\n'
                   'd[sys.argv[1]]=sys.argv[2];p.write_text(json.dumps(d))\n')
            (root/'commands'/command).write_text(body)
        (root/'commands/chown').write_text('#!/bin/sh\nexit 0\n')
        (root/'commands/restorecon').write_text('#!/bin/sh\nexit 0\n')
        chmod=shutil.which('chmod');assert chmod
        # Model root's ability to populate a protected directory as a normal
        # host user. All real file copies/checksums and remaining modes run.
        (root/'commands/chmod').write_text('#!/bin/sh\nif [ "$1" = 0570 ]; then shift; exec '+chmod+' 0750 "$@"; fi\nexec '+chmod+' "$@"\n')
        for path in (root/'commands').iterdir():path.chmod(0o755)
        if mode=='symlink':
            outside=root/'outside';outside.mkdir();(root/'data/vendor/modem_config').symlink_to(outside)
        if mode=='bad_checksum':
            (root/'vendor/firmware/modem_pr/mcfg/configs/mcfg_sw/test/profile0.mbn').write_text('changed')
        source=(STOCK/'vendor/bin/a9ml-stock-radio-prepare.sh').read_text()
        source=re.sub(r'(?<![A-Za-z0-9_./-])/(vendor|data|dev|sys)(?=/|\b)',lambda m:str(root)+'/'+m[1],source)
        env=dict(os.environ,PATH=str(root/'commands')+':'+os.environ['PATH'],TEST_PROPS=str(propfile))
        r=sp.run([SH],input=source,text=True,capture_output=True,timeout=20,env=env)
        return root,json.loads(propfile.read_text()),r

    def test_early_copy_and_independent_radio_release(self):
        root,props,r=self.fixture('success');self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(props['vendor.a9ml.cellular.stock.ready'],'1')
        self.assertEqual(props['vendor.a9ml.cellular.stock.count'],'134')
        for state in ('starting','serving','failed','ending','ended'):
            props['vendor.a9ml.wifi.session.state']=state
            self.assertEqual(set(client_starts(props)),{'vendor.qcrild','vendor.qcrild2'})
        props['vendor.a9ml.wifi.session.state']='serving'
        props['vendor.a9ml.wifi.android.ready']='1'
        self.assertIn('vendor.imsqmidaemon',client_starts(props))
        props['vendor.a9ml.wifi.android.ready']='0'
        self.assertEqual(set(client_starts(props)),{'vendor.qcrild','vendor.qcrild2'})
        for state in ('ending','ended','failed'):
            action=next(b for b in RC.split('\n\n') if 'on property:vendor.a9ml.wifi.session.state='+state+'\n' in b)
            self.assertIn('stop vendor.imsqmidaemon',action)
            self.assertNotIn('vendor.qcrild',action)
            self.assertNotIn('setprop vendor.a9ml.cellular 0',action)
        common=(REPO/'scripts/mainline/wifi/a9-wifi.rc').read_text()
        self.assertLess(common.index('exec_start a9ml_wifi_firmware'),common.index('exec_start a9ml-cellular-prepare'))
        self.assertLess(common.index('exec_start a9ml-cellular-prepare'),common.index('exec_start a9ml-stock-radio-prepare'))
        self.assertIn('property:vendor.a9ml.cellular.stock.state=stopped',RC)
        self.assertNotIn('property:sys.boot_completed=1',RC)

    def check_refused(self,mode):
        root,props,r=self.fixture(mode)
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(props['vendor.a9ml.cellular.stock.ready'],'0')
        self.assertEqual(client_starts(props),[])
        self.assertNotIn('ro.vendor.ril.mbn_copy_completed',props)
        if (root/'outside').exists():self.assertEqual(list((root/'outside').iterdir()),[])

    def test_bad_source_never_releases_clients(self):self.check_refused('bad_checksum')
    def test_symlink_never_releases_clients(self):self.check_refused('symlink')
    def test_optout_never_releases_clients(self):self.check_refused('optout')
    def test_failed_wlan_owner_still_prepares_radio(self):
        root,props,r=self.fixture('owner_failed')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(set(client_starts(props)),{'vendor.qcrild','vendor.qcrild2'})
        props['vendor.a9ml.cellular']='0'
        self.assertEqual(client_starts(props),[])

    def test_no_sim_and_failed_data_modules_still_release_both_radio_hals(self):
        root,props,r=self.fixture('data_modules_failed')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(props['vendor.a9ml.cellular.stock.ready'],'1')
        self.assertEqual(props['vendor.a9ml.cellular.stock.count'],'134')
        self.assertEqual(props['vendor.a9ml.cellular.stock.netmgr'],'blocked-kernel-abi')
        self.assertEqual(props['gsm.sim.state'],'ABSENT,ABSENT')
        self.assertEqual(set(client_starts(props)),{'vendor.qcrild','vendor.qcrild2'})
        self.assertFalse((root/'sys/module/ipa').exists())
        self.assertFalse((root/'sys/module/rmnet').exists())


if __name__=='__main__':unittest.main(verbosity=2)
