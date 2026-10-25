#!/usr/bin/env python3
"""Five host-only groups: Android owner evidence and bounded cleanup readback."""
import importlib.util
import os
from pathlib import Path
import subprocess as sp
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('wifi_owner_fake', HERE/'selftest.py')
fake = importlib.util.module_from_spec(spec); spec.loader.exec_module(fake)
m = fake.sweep
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


class Owner(unittest.TestCase):
    setUp = fake.Tests.setUp

    def android_owner(self, state, reason='', log=''):
        root = Path(self.tmp.name); trial = root/'trial'; session = root/'session'; commands = root/'commands'
        for p in (trial, session, commands): p.mkdir(exist_ok=True)
        (trial/'session-end').write_text(reason+'\n')
        (trial/'fault').write_text('PRIVATE_FAULT\n'); (trial/'start-attempted').touch()
        (session/'run.out').write_text(log)
        for name in ('tail','awk','printf'):
            command = str(ANDROID/'system/bin/awk') if name == 'awk' else str(ANDROID/'system/bin/toybox')+' '+name
            p=commands/name; p.write_text('#!/bin/sh\nexec '+QEMU+' '+str(ANDROID/'linker64')+' '+command+' "$@"\n'); p.chmod(0o700)
        writes = root/'writes'
        source = ('''getprop() { case "$1" in vendor.a9ml.wifi.session.state) printf '%s\\n' "$TEST_STATE";;
            vendor.a9ml.wifi.s2.start) printf '1\\n';; esac; }
setprop() { printf 'UNEXPECTED_RESTART\\n' >> "$TEST_WRITES"; return 99; }
set -e
'''+m.play.START).replace('/mnt/a9-wifi',str(root))
        r=sp.run([QEMU,ANDROID/'linker64',ANDROID/'system/bin/sh'],input=source,text=True,capture_output=True,
                 env=dict(os.environ,LD_LIBRARY_PATH=str(ANDROID/'lib64'),PATH=str(commands)+':'+os.environ.get('PATH',''),
                          TEST_STATE=state,TEST_WRITES=str(writes)),timeout=10)
        self.assertFalse(writes.exists()); self.assertNotIn('PRIVATE',r.stdout)
        return r

    def test_terminal_state_is_distinct_and_never_restarted(self):
        for state,code in [('ending',4),('ended',5),('failed',6),('PRIVATE_STATE',8)]:
            r=self.android_owner(state,'requested','STOP: Android HAL/supplicant readiness timeout; PRIVATE_PAYLOAD\n')
            self.assertEqual(r.returncode,12,r.stderr)
            d=m.numbers(r.stdout)
            self.assertEqual(d['owner_state'],code); self.assertEqual(d['owner_failure_code'],5)
            self.assertEqual(d['owner_start_attempted'],1); self.assertEqual(d['owner_fault_present'],1)

    def test_orderly_end_reasons_and_unknown_logs_stay_numeric(self):
        for reason,code in [('requested',1),('evidence-ram-low',2),('session-length',3),('PRIVATE_REASON',0)]:
            r=self.android_owner('ended',reason,'STOP: PRIVATE_UNCLASSIFIED\n')
            d=m.numbers(r.stdout)
            self.assertEqual(d['owner_end_reason'],code); self.assertEqual(d['owner_failure_code'],0)
        r=self.android_owner('serving')
        self.assertEqual(r.returncode,0,r.stderr); self.assertIn('READY',r.stdout)

    def test_cleanup_requires_readback_even_after_accepted_request(self):
        calls=[]
        def shell(source,**kwargs):
            calls.append(source)
            return (0,'') if 'set-wifi-enabled' in source else (0,'Wifi is enabled\nPRIVATE_NETWORK\n')
        before=self.clock.now
        ok,d=m.play.disable_wifi(shell,seconds=3)
        self.assertFalse(ok); self.assertEqual(d['wifi_disable_rc'],0)
        self.assertGreater(d['wifi_disable_polls'],0); self.assertEqual(self.clock.now-before,3)
        self.assertEqual(sum('set-wifi-enabled' in c for c in calls),1)

    def test_cleanup_accepts_already_off_and_waits_for_async_disable(self):
        for outputs in [iter(['Wifi is disabled\n']),iter(['Wifi is enabled\n','Wi-Fi is disabled\n'])]:
            def shell(source,**kwargs):
                return (1,'') if 'set-wifi-enabled' in source else (0,next(outputs))
            ok,d=m.play.disable_wifi(shell,seconds=3)
            self.assertTrue(ok); self.assertEqual(d['wifi_disable_rc'],1)
            self.assertEqual(d['wifi_status_rc'],0)
        ok,_=m.play.disable_wifi(lambda *a,**kw:(1,'Wifi is disabled\n'),seconds=1)
        self.assertFalse(ok)  # failed reader with plausible partial output isn't proof

    def test_failed_owner_evidence_and_cleanup_verdict(self):
        sweep=fake.Fake(); original=sweep.native
        def native(source,**kwargs):
            if source==m.play.START:
                sweep.last_remote_rc=12
                return 1,'owner_state 6\nowner_end_reason 0\nowner_failure_code 3\nPRIVATE_PAYLOAD\n'
            return original(source,**kwargs)
        with patch.object(sweep,'native',side_effect=native): self.assertFalse(sweep.wifi())
        evidence=sweep.results['12_wifi']['evidence']
        self.assertEqual(evidence['owner_state'],6); self.assertEqual(evidence['owner_exit'],12)
        self.assertEqual(evidence['owner_failure_code'],3); self.assertEqual(sweep.secret_inputs,[])
        sweep.touched=True; sweep.wifi_on=True; original=sweep.shell; count=[0]
        def shell(source,**kwargs):
            if source.startswith('cmd wifi set-wifi-enabled disabled'): return 1,''
            if source.startswith('cmd wifi status'):
                count[0]+=1; return 0,'Wifi is enabled\n' if count[0]<2 else 'Wifi is disabled\n'
            return original(source,**kwargs)
        with patch.object(sweep,'shell',side_effect=shell): sweep.cleanup()
        self.assertEqual(sweep.results['cleanup']['status'],'PASS')
        self.assertEqual(sweep.results['cleanup']['evidence']['wifi_disable_polls'],2)
        self.assertNotIn('PRIVATE',self.output.getvalue())


if __name__=='__main__': unittest.main(verbosity=2)
