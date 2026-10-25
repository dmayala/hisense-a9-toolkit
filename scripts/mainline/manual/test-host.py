#!/usr/bin/env python3
"""Actual manual scripts, fake adb, Android mksh; never accesses a device."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess as sp
import tempfile
import time
import unittest

HERE=Path(__file__).resolve().parent
ANDROID=Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU='/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'
FAKE=r'''#!/usr/bin/env python3
import json,os,pathlib,re,shutil,subprocess as sp,sys
r=pathlib.Path(os.environ['FIXTURE']);args=sys.argv[1:]
payload=sys.stdin.read() if args[:2]==['shell','-T'] and 'nc 127.0.0.1 2324' in args[-1] else ''
with (r/'calls').open('a') as f:f.write(json.dumps(dict(args=args,root=bool(payload),payload=payload))+'\n')
if args[0]=='push':
 for source in args[1:-1]:
  dest=pathlib.Path(args[-1]);dest=dest/pathlib.Path(source).name if dest.is_dir() else dest
  shutil.copyfile(source,dest);dest.chmod(0o755)
 sys.exit(0)
if args[0]=='pull':shutil.copyfile(args[1],args[2]);sys.exit(0)
assert args[:2]==['shell','-T']
text=payload or args[-1]
if payload and re.search(r'\b(setprop|svc|settings|input|dumpsys|cmd)\b',payload):
 print('A9_MANUAL_RC=1');sys.exit(0)
for old,new in [('/sys/fs/pstore',r/'pstore'),('/sys/fs -cf',str(r)+' -cf'),('/tmp/a9-hvenc-kdebug',r/'recovery-mount'),('/proc/mounts',r/'mounts'),('/proc/',str(r/'proc')+'/'),('/sys/module/venus_enc/parameters',r/'params'),('/sys/class/power_supply/a9-usb/input_suspend',r/'input_suspend'),('/sys/class/power_supply/battery',r/'battery'),('/sys/kernel/debug/wakeup_sources',r/'wakeups'),('/data/local/tmp/',str(r/'device')+'/')]:text=text.replace(old,str(new))
env=dict(os.environ,PATH=str(r/'bin')+':'+os.environ['PATH'],LD_LIBRARY_PATH=os.environ['ANDROID_LIB'])
result=sp.run([os.environ['QEMU'],os.environ['ANDROID_LINKER'],os.environ['ANDROID_SH'],'-c',text],env=env,capture_output=True)
sys.stdout.buffer.write(result.stdout.replace(str(r/'proc').encode(),b'/proc'))
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
'''
STUB=r'''#!/usr/bin/env python3
import os,pathlib,sys
r=pathlib.Path(os.environ['FIXTURE']);name=pathlib.Path(sys.argv[0]).name;a=sys.argv[1:]
if name=='getprop':print('_b' if a[0]=='ro.boot.slot_suffix' else ('fixture' if a[0]=='ro.twrp.version' else (r/'cpu').read_text().strip()))
elif name=='setprop':(r/'cpu').write_text(a[1])
elif name=='settings':
 if a[0]=='get':print((r/'stayon').read_text().strip())
 else:(r/'stayon').write_text(a[-1] if a[0]=='put' else 'null')
elif name=='svc':
 if a[:2]==['power','stayon']:(r/'stayon').write_text('0')
elif name=='input':(r/'awake').write_text('1' if a[-1]=='KEYCODE_WAKEUP' else '0')
elif name=='cmd':print('Wifi is connected to test')
elif name=='dumpsys':print('mWakefulness='+('Awake' if (r/'awake').read_text()=='1' else 'Asleep'))
elif name=='dmesg':print('synthetic A9_ENC_TRACE')
elif name=='mount':
 with (r/'mounted').open('a') as f:f.write(' '.join(a)+'\n')
elif name=='id':print('0')
'''
CLOCK=r'''#!/usr/bin/env python3
import os,pathlib
r=pathlib.Path(os.environ['FIXTURE']);p=r/'clock_calls';n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1))
print('DRAIN_CLOCK',100000000000+n*2000000000,100000000000+n*1000000000,1)
(r/'battery/drain_snapshot').write_text('capacity 70\ncharge_counter '+str(10000-n*500)+'\ncharge_full_design 4000000\ncounter_sample_ms 1\ncounter_elapsed_ms 2000\ncounter_wakeups 1\n')
'''


class Manual(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.r=r=Path(self.temp.name)
        for d in ('bin','device','params','battery','image/hvenc-qualification','image/acceptance-assets','scripts','proc/987/root/kdebuginfo/mainline','pstore'):(r/d).mkdir(parents=True)
        (r/'mounts').write_text('')
        for name in ('a9_mapped_eos','a9_bidir_buffers','a9_output_size','a9_stock_work_mode'):(r/'params'/name).write_text('N')
        for name,value in [('cpu','0'),('stayon','3'),('awake','1'),('input_suspend','0'),('wakeups','name count\ntest 1\n'),('battery/drain_counter_supported','1'),('battery/drain_capacity','70'),('battery/status','Discharging')]: (r/name).write_text(value)
        def write(p,text):p.write_text(text);p.chmod(0o755)
        write(r/'fake-adb',FAKE)
        for n in ('getprop','setprop','settings','svc','input','cmd','dumpsys','dmesg','mount','id'):write(r/'bin'/n,STUB)
        # Both the outer nc shell and its quoted-command child use Android mksh.
        write(r/'bin/sh',f'#!/bin/sh\nexec {QEMU} {ANDROID}/linker64 {ANDROID}/system/bin/sh "$@"\n')
        for name in ('common.sh','hvenc.sh','hvenc-pull.sh','drain.sh'):
            data=(HERE/name).read_text()
            if name=='drain.sh':
                self.assertIn('end=$((SECONDS + 3600))',data)
                data=data.replace('end=$((SECONDS + 3600))','end=$((SECONDS + 2))').replace('sleep 10','sleep 0.1')
            write(r/'scripts'/name,data)
        native='#!/bin/sh\nprintf MP4 > "$1/output.mp4"\nprintf "native %s\\n" "$1"\ncase "$1" in */N) exit ${FAIL_N:-0};; esac\n'
        write(r/'image/hvenc-qualification/qualify',native)
        for n in ('avc.mp4','hevc.mp4'):(r/'image/hvenc-qualification'/n).write_bytes(b'synthetic input')
        write(r/'image/acceptance-assets/drain-clock',CLOCK)
        self.env=dict(os.environ,TMPDIR=str(r),FIXTURE=str(r),ADB=str(r/'fake-adb'),QEMU=QEMU,ANDROID_LINKER=str(ANDROID/'linker64'),ANDROID_SH=str(ANDROID/'system/bin/sh'),ANDROID_LIB=str(ANDROID/'lib64'))

    def tearDown(self):self.temp.cleanup()
    def run_script(self,name,**env):
        return sp.run(['bash',self.r/'scripts'/name,self.r/'image',self.r/'out'],env=dict(self.env,**env),text=True,capture_output=True,timeout=25)
    def restored(self):
        self.assertEqual((self.r/'input_suspend').read_text().strip(),'0')
        self.assertEqual((self.r/'stayon').read_text().strip(),'3')
    def saved_boots(self):
        root=self.r/'proc/987/root/kdebuginfo/mainline'
        (root/'counter').write_text('136')
        for n in range(132,137):
            boot=root/f'boot-{n:010}';boot.mkdir()
            for name in ('info.txt','kernel.log','logcat.log','pstore.previous.log'):
                (boot/name).write_text(f'fixture {n} {name}\n')
        return root
    def test_pull_existing_private_logger_mount_without_mounting(self):
        import tarfile
        self.saved_boots()
        (self.r/'pstore/console-ramoops-0').write_bytes(b'fixture prior console')
        p=sp.run(['bash',self.r/'scripts/hvenc-pull.sh',self.r/'pull'],env=self.env,text=True,capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        with tarfile.open(self.r/'pull/mainline.tar') as archive:
            self.assertEqual(len(archive.getnames()),12)
            self.assertEqual(archive.extractfile('boot-0000000135/kernel.log').read(),b'fixture 135 kernel.log\n')
            self.assertNotIn('boot-0000000133/kernel.log',archive.getnames())
        with tarfile.open(self.r/'pull/pstore.tar') as archive:
            self.assertEqual(archive.extractfile('pstore/console-ramoops-0').read(),b'fixture prior console')
        self.assertFalse((self.r/'mounted').exists())
    def test_pull_denied_private_mount_reports_recovery_without_reboot(self):
        p=sp.run(['bash',self.r/'scripts/hvenc-pull.sh',self.r/'pull'],env=self.env,text=True,capture_output=True,timeout=20)
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('--recovery',p.stderr)
        calls=[json.loads(l) for l in (self.r/'calls').read_text().splitlines()]
        self.assertFalse(any('reboot' in ' '.join(c['args']) or 'mount -t' in c['payload'] for c in calls))
    def test_pull_already_in_recovery_reads_same_mainline_directory(self):
        import tarfile
        root=self.saved_boots();(self.r/'recovery-mount').mkdir()
        shutil.copytree(root,self.r/'recovery-mount/mainline')
        (self.r/'pstore/console-ramoops-0').write_bytes(b'recovery fixture')
        p=sp.run(['bash',self.r/'scripts/hvenc-pull.sh',self.r/'pull','--recovery'],env=self.env,text=True,capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('kdebuginfo',(self.r/'mounted').read_text())
        with tarfile.open(self.r/'pull/mainline.tar') as archive:
            self.assertIn('boot-0000000135/logcat.log',archive.getnames())
    def test_encoder_N_failure_does_not_skip_Y_and_restores_everything(self):
        p=self.run_script('hvenc.sh',FAIL_N='21');self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertEqual((self.r/'out/N.rc').read_text().strip(),'21');self.assertEqual((self.r/'out/Y.rc').read_text().strip(),'0')
        for n in ('N','Y'):
            self.assertEqual((self.r/'out'/f'{n}.mp4').read_bytes(),b'MP4')
            self.assertIn('A9_ENC_TRACE',(self.r/'out'/f'{n}-dmesg.log').read_text())
            self.assertIn('a9_bidir_buffers '+n,(self.r/'out'/f'{n}-parameters.txt').read_text())
        self.assertEqual((self.r/'cpu').read_text().strip(),'0')
        self.assertTrue(all(p.read_text().strip()=='N' for p in (self.r/'params').iterdir()))
        calls=[json.loads(l) for l in (self.r/'calls').read_text().splitlines()]
        self.assertTrue(any('setprop debug.stagefright' in ' '.join(c['args']) and not c['root'] for c in calls))
    def test_encoder_Y_only_live_capture_and_transport_loss_stops_NY(self):
        p=sp.run(['bash',self.r/'scripts/hvenc.sh',self.r/'image',self.r/'y-only','Y'],env=self.env,text=True,capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertFalse((self.r/'y-only/N.log').exists())
        self.assertIn('a9_bidir_buffers Y',(self.r/'y-only/Y-parameters.txt').read_text())
        self.assertIn('A9_ENC_TRACE',(self.r/'y-only/Y-kernel-live.log').read_text())
        self.assertTrue((self.r/'y-only/Y-logcat-live.rc').exists())
        p=sp.run(['bash',self.r/'scripts/hvenc.sh',self.r/'image',self.r/'lost','NY'],env=dict(self.env,FAIL_N='255'),text=True,capture_output=True,timeout=20)
        self.assertEqual(p.returncode,255,p.stdout+p.stderr)
        self.assertFalse((self.r/'lost/Y.log').exists())
        self.assertIn('collect saved boot logs',p.stderr)
        self.assertTrue(all(p.read_text().strip()=='N' for p in (self.r/'params').iterdir()))
    def test_drain_real_transports_clock_delta_and_restore(self):
        p=self.run_script('drain.sh');self.assertEqual(p.returncode,0,p.stdout+p.stderr);self.restored()
        text=(self.r/'out/result.txt').read_text();self.assertIn('suspend_s 1.000',text);self.assertIn('drain_uAh 500',text)
    def test_drain_interrupt_restores_charging(self):
        p=sp.Popen(['bash',self.r/'scripts/drain.sh',self.r/'image',self.r/'out'],env=self.env,stdout=sp.DEVNULL,stderr=sp.DEVNULL)
        end=time.monotonic()+10
        while (self.r/'input_suspend').read_text().strip()!='1' and time.monotonic()<end:time.sleep(.02)
        self.assertEqual((self.r/'input_suspend').read_text().strip(),'1');p.send_signal(signal.SIGTERM);p.wait(timeout=15);self.restored()
    def test_floor_refusal_never_pauses_charging(self):
        (self.r/'battery/drain_capacity').write_text('35')
        p=self.run_script('drain.sh');self.assertNotEqual(p.returncode,0);self.restored()

    def test_detached_watchdog_survives_parent_SIGKILL(self):
        p=sp.Popen(['bash',self.r/'scripts/drain.sh',self.r/'image',self.r/'out'],env=self.env,stdout=sp.DEVNULL,stderr=sp.DEVNULL)
        end=time.monotonic()+10
        while (self.r/'input_suspend').read_text().strip()!='1' and time.monotonic()<end:time.sleep(.02)
        self.assertEqual((self.r/'input_suspend').read_text().strip(),'1')
        p.kill();p.wait();end=time.monotonic()+10
        while time.monotonic()<end and (self.r/'input_suspend').read_text().strip()!='0':time.sleep(.05)
        self.assertEqual((self.r/'input_suspend').read_text().strip(),'0')
        # A delayed bounded pause completion must be overwritten as well.
        (self.r/'input_suspend').write_text('1')
        end=time.monotonic()+10
        while time.monotonic()<end and (self.r/'input_suspend').read_text().strip()!='0':time.sleep(.05)
        self.assertEqual((self.r/'input_suspend').read_text().strip(),'0')
        end=time.monotonic()+10
        while time.monotonic()<end:
            log=self.r/'out/watchdog.log'
            if log.exists() and log.read_text().splitlines().count('0')>=10:break
            time.sleep(.1)
        self.assertGreaterEqual(log.read_text().splitlines().count('0'),10)
        time.sleep(.2)



if __name__=='__main__':unittest.main(verbosity=2)
