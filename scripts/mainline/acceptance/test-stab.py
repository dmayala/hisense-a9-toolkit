#!/usr/bin/env python3
"""Five host groups for the observed stability evidence/startup gaps."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess as sp
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
HOST_PATH = os.environ['PATH']
spec = importlib.util.spec_from_file_location('stab_fake', HERE/'selftest.py')
fake = importlib.util.module_from_spec(spec); spec.loader.exec_module(fake)
m = fake.sweep
spec = importlib.util.spec_from_file_location('stab_queue', HERE.parents[2]/'tests/mainline-cellular/test-init-queue.py')
queue = importlib.util.module_from_spec(spec); spec.loader.exec_module(queue)
ANDROID = Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


class Stab(unittest.TestCase):
    setUp = fake.Tests.setUp

    def android_traces(self):
        root = Path(self.tmp.name); anr = root/'anr'; anr.mkdir(); tomb = root/'tomb'; tomb.mkdir()
        (anr/'pre_watchdog_PRIVATE_TIME').write_text(
            'Cmd line: system_server PRIVATE_ARG\n"main" prio=5 tid=1 Native\n'
            '  at android.os.BinderProxy.transactNative(Native method)\n'
            '  at com.android.server.powerstats.PowerStatsHALWrapper.getPowerStats(Power.java:12345)\n'
            '"PRIVATE_THREAD" prio=5 tid=777\n'
            '  at android.os.MessageQueue.nativePollOnce(Native method)\n'
            'PRIVATE_PAYLOAD\n'+'x'*270000+'\n')
        (anr/'anr_PRIVATE_TIME').write_text('Cmd line: com.android.phone PRIVATE_ARG\n"main" tid=99\n'
            '  at com.android.internal.telephony.RIL.getRadioProxy(RIL.java:12345)\n'
            'Cmd line: system_server\n"android.fg" tid=77\n'
            '  at android.os.MessageQueue.nativePollOnce(Native method)\n')
        (tomb/'tombstone_00').write_text('Cmdline: /vendor/bin/hw/android.hardware.media.c2@1.2-service-v4l2-64 PRIVATE_ARG\n'
            'pid: 12345, tid: 12345, name: PRIVATE_THREAD  >>> /vendor/bin/hw/android.hardware.media.c2@1.2-service-v4l2-64 <<<\n'
            '    #00 pc 0000000000000123 /apex/com.android.runtime/lib64/bionic/libc.so (abort+164) (BuildId: PRIVATE_ID)\n')
        (tomb/'tombstone_00.pb').write_text('Cmd line: private.protobuf\n')
        (anr/'symlink').symlink_to(tomb/'tombstone_00')
        bindir=root/'bin'; bindir.mkdir()
        for name in ('head','stat','awk'):
            command = str(ANDROID/'system/bin/awk') if name=='awk' else str(ANDROID/'system/bin/toybox')+' '+name
            p=bindir/name; p.write_text('#!/bin/sh\nexec '+QEMU+' '+str(ANDROID/'linker64')+' '+command+' "$@"\n'); p.chmod(0o700)
        r=sp.run([QEMU,ANDROID/'linker64',ANDROID/'system/bin/sh'],
            input=m.ANR_TRACES.replace('/data/anr',str(anr)).replace('/data/tombstones',str(tomb)),
            env=dict(os.environ,PATH=str(bindir)+':'+HOST_PATH,LD_LIBRARY_PATH=str(ANDROID/'lib64')),
            text=True,capture_output=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertNotIn('PRIVATE',r.stdout); self.assertNotIn('12345',r.stdout)
        return r.stdout

    def test_android_stack_summary_and_host_round_trip(self):
        text=self.android_traces(); sweep=fake.Fake()
        self.assertIn('trace_files 3',text)
        self.assertIn('trace_native libc.so abort',text)
        with patch.object(sweep,'native',return_value=(0,text)), patch.object(sweep,'shell',return_value=(0,'')):
            sweep.collect_anr_evidence(force=True)
            sweep.collect_anr_evidence(force=True)
        path=Path(self.tmp.name)/'stability-traces.jsonl'
        rows=[json.loads(l) for l in path.read_text().splitlines()]
        self.assertEqual(len(rows),3)  # repeated samples retain the original evidence
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual({r['kind'] for r in rows},{1,4,5})
        wd=next(r for r in rows if r['kind']==1)
        self.assertEqual(wd['prefix_truncated'],1)
        self.assertEqual(wd['hints']['powerstats_wait'],1)
        self.assertTrue(all(r['complete']==1 for r in rows))
        sections=next(r for r in rows if r['kind']==4)['sections']
        self.assertEqual([s['process'] for s in sections],['com.android.phone','system_server'])
        self.assertEqual(sections[0]['frames'][0]['method'],'com.android.internal.telephony.RIL.getRadioProxy')
        self.assertEqual(sweep.anr_evidence['processes']['com.android.phone']['radio_wait'],1)
        native=next(r for r in rows if r['kind']==5)
        self.assertEqual(native['sections'][0]['process'],'android.hardware.media.c2@1.2-service-v4l2-64')
        self.assertEqual(native['sections'][-1]['native'],[dict(library='libc.so',method='abort')])
        for name in ('service@private.example','service PRIVATE_ARG','service@1.2/private','service@1.2;payload'):
            self.assertIsNone(sweep.process_name(name))

    def test_partial_capture_is_saved_and_fails_diagnostic_observation(self):
        sweep=fake.Fake()
        text='trace_begin 1 1 123 0\ntrace_section system_server\ntrace_frame 1 android.os.BinderProxy.transactNative\nPRIVATE_PAYLOAD\n'
        with patch.object(sweep,'native',return_value=(1,text)), patch.object(sweep,'shell',return_value=(0,'')):
            sweep.collect_anr_evidence(force=True)
        path=Path(self.tmp.name)/'stability-traces.jsonl'; row=json.loads(path.read_text())
        self.assertEqual(row['complete'],0); self.assertEqual(row['reader_rc'],1)
        self.assertEqual(sweep.losses['diagnostic'],1)
        self.assertNotIn('PRIVATE_PAYLOAD',path.read_text())

    def test_missing_measurements_and_startup_artifacts_cannot_pass(self):
        sweep=fake.Fake(); original=sweep.native
        def partial(source,**kwargs):
            if source==m.SNAPSHOT:return 0,'anrs 0\n'
            return original(source,**kwargs)
        with patch.object(sweep,'native',side_effect=partial):
            sweep.monitor(force=True)
        self.assertEqual(sweep.losses['incomplete'],1)
        self.assertEqual(sweep.lost,1)
        sweep=fake.Fake()
        with patch.object(sweep,'snapshot',return_value=dict(snapshot_ok=1,anrs=2,tombstones=0,crash_events=0)), patch.object(sweep,'collect_anr_evidence'):
            sweep.monitor(force=True)
        self.assertGreater(sweep.crashes,0)

    def test_event_causes_are_fixed_categories(self):
        sweep=fake.Fake()
        prefix='123.4 123 456 I am_anr : [0,111,com.android.phone,999,'
        sweep.record_crash_events([prefix+'Process PRIVATE_ID failed to complete startup]',
            prefix+'android.safetycenter.action.REFRESH_SAFETY_SOURCES PRIVATE_EXTRAS]',
            '125.4 123 456 I am_crash : [0,111,com.google.android.tts,999,Exception,Cannot access credential-protected data from direct boot PRIVATE_PAYLOAD]'])
        path=Path(self.tmp.name)/'stability-events.jsonl'
        rows=[json.loads(l) for l in path.read_text().splitlines()]
        self.assertEqual([r['cause_code'] for r in rows],[1,2,3])
        self.assertNotIn('PRIVATE',path.read_text())

    def test_statistics_start_precedes_framework_and_is_packaged(self):
        main=HERE.parent; early=(main/'android/early-stats.rc').read_text()
        trace=queue.replay([early],{})
        self.assertLess(trace.index('start vendor.power.stats-default'),trace.index('event zygote-start'))
        self.assertLess(trace.index('start vendor.memtrack-default'),trace.index('event zygote-start'))
        memtrack=Path('/home/user/A9-build/mainline/android/dl/cf_arm64_x/apex/com.android.hardware.memtrack/root')
        memrc=(memtrack/'etc/memtrack-default-apex.rc').read_text()
        self.assertIn('service vendor.memtrack-default ',memrc)
        self.assertNotIn('interface aidl android.hardware.memtrack.IMemtrack/default',memrc)
        apex=Path('/home/user/A9-build/mainline/android/dl/cf_arm64_x/apex/com.android.hardware.power/root')
        rc=(apex/'etc/com.android.hardware.power.rc').read_text()
        self.assertIn('service vendor.power.stats-default ',rc)
        self.assertNotIn('interface aidl android.hardware.power.stats.IPowerStats/default',rc)
        self.assertIn('IPowerStats/default',(apex/'etc/vintf/android.hardware.power.stats.xml').read_text())
        self.assertIn('early-stats.rc',(main/'android/build-vendor.sh').read_text())
        self.assertIn("payload['etc/init/hw/init.hlte556n.rc'] = stats_rc",(main/'reboot/build14.py').read_text())
        spec=importlib.util.spec_from_file_location('stab_pack',main/'android/refresh-integrated-vendor.py')
        pack=importlib.util.module_from_spec(spec);spec.loader.exec_module(pack)
        inherited=b'on boot\n    start existing_service\n'
        first=pack.early_stats(inherited)
        self.assertTrue(first.startswith(inherited))
        self.assertEqual(pack.early_stats(first),first)
        tail=b'\non shutdown\n    stop existing_service\n'
        self.assertEqual(pack.early_stats(first+tail),first+tail)
        policy=(main/'android/sepolicy/rootsh-enforcing.cil').read_text()
        self.assertIn('(allow shell runtime_event_log_tags_file (file (getattr map open read)))',policy)
        self.assertIn('(allow a9ml_rootsh tombstone_data_file (file (getattr open read)))',policy)


if __name__=='__main__': unittest.main(verbosity=2)
