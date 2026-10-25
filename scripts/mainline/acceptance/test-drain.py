#!/usr/bin/env python3
"""Five host-only groups; synthetic battery/control files, never device tools."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess as sp
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
m=load('drain',HERE/'drain.py')

class Clock:
    t=0
    def now(self):return self.t
    def sleep(self,n):self.t+=n

class Native:
    def __init__(self,clock,fault=None):
        self.clock=clock;self.context={};self.paused=False;self.restores=0;self.fault=fault
    def wakeups(self,path):path.write_text('name active_count\nfixture 1\n');path.chmod(0o600)
    def mode(self,pause):self.paused=pause
    def prepare_screen(self):pass
    def restore(self):self.paused=False;self.restores+=1
    def snapshot(self):
        t=self.clock.t
        if self.fault=='interrupt' and t>=120:raise InterruptedError()
        if self.fault=='read' and t>=120:raise ValueError('invalid counter')
        return dict(capacity=34 if self.fault=='floor' and t>=120 else 70,
                    boot_ns=int(t*10**9),mono_ns=int(t*10**9/4),uncertainty_ns=10,
                    charge_counter=3000000-int(t*1000/3600),charge_full_design=4000000,
                    usb_present=1,wifi=int(not(self.fault=='wifi' and t>=120)),
                    screen_off=1,input_paused=int(self.paused and not(self.fault=='unpause' and t>=120)),charging=0)

class Watch:
    def __init__(self,native):self.native=native;self.pings=0
    def ping(self):self.pings+=1
    def finish(self):self.native.restore();return True

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.out=Path(self.temp.name)
    def measure(self,fault=None):
        clock=Clock();native=Native(clock,fault);watch=Watch(native)
        result,okay=m.measure(native,lambda *a:watch,self.out,5000,clock.now,clock.sleep)
        return result,okay,native,clock,watch

    def test_1_full_hour_counter_and_suspend_residency(self):
        d,okay,n,c,w=self.measure()
        self.assertTrue(okay);self.assertFalse(n.paused)
        self.assertEqual(c.t,3600);self.assertEqual(d['elapsed_ms'],3600000)
        self.assertEqual(d['charge_delta_uah'],1000);self.assertEqual(d['drain_uah_per_hour'],1000)
        self.assertEqual(d['suspend_ms'],2700000);self.assertEqual(d['suspend_permille'],750)
        self.assertEqual(d['clock_uncertainty_ns'],20)
        self.assertEqual(d['measured'],1);self.assertGreaterEqual(n.restores,2);self.assertEqual(w.pings,61)
        self.assertEqual((self.out/'drain-result.json').stat().st_mode&0o777,0o600)

        # The dedicated QG counter is relative; hardware batches cover the
        # sleeping interval. Missing coverage must restore and stay unmeasured.
        for coverage, expected in ((1000, 1), (980, 0)):
            clock=Clock();native=Native(clock);native.context={'counter_kind':1}
            original=native.snapshot
            def snapshot():
                d=original();t=int(clock.t*1000)
                d.update(counter_sample_ms=t*coverage//1000,counter_elapsed_ms=t,
                         counter_wakeups=t//19200)
                return d
            native.snapshot=snapshot;watch=Watch(native)
            result,okay=m.measure(native,lambda *a:watch,self.out,5000,clock.now,clock.sleep)
            self.assertTrue(okay);self.assertFalse(native.paused)
            self.assertEqual(result['measured'],expected)
            if expected:
                self.assertEqual(result['counter_coverage_permille'],1000)
                self.assertEqual(result['drain_uah_per_hour'],1000)
            else:self.assertEqual(result['stage_failed'],1)


    def test_2_default_short_budget_and_unsupported_gauge_never_pause(self):
        s=load('sweep_drain_test',HERE/'onb.py')
        with patch.dict(os.environ,{'A9_BTRY_LOGS':str(self.out),'A9_BTRY_HOLD':'2400'},clear=True):
            ordinary=s.Sweep();self.assertFalse(ordinary.drain_requested);self.assertEqual(ordinary.drain_reserved,0)
            with patch.dict(os.environ,A9_SWEEP_DRAIN='1',A9_BTRY_OWNER_END=str(time.monotonic()+2400)):
                short=s.Sweep();self.assertEqual(short.drain_reserved,0)
                with patch.object(m,'Native',side_effect=AssertionError('charging touched')):m.run(short)
                self.assertEqual(short.results[m.ROW]['evidence']['insufficient_time'],1)
            # Reserving the hour does not consume the final PIN/HVENC windows,
            # and a prerequisite refusal returns that reservation before cleanup.
            with patch.dict(os.environ,A9_SWEEP_DRAIN='1',A9_BTRY_HOLD='7200',
                            A9_BTRY_OWNER_END=str(time.monotonic()+7200),A9_SWEEP_HVENC_QUALIFY='1',
                            A9_SWEEP_PIN_LIFECYCLE='fresh-disposable-formatted-b'):
                long=s.Sweep();self.assertEqual(long.drain_reserved,3900)
                self.assertAlmostEqual(long.pin_end-long.qualification_end,240)
                self.assertAlmostEqual(long.qualification_end-long.end,3900)
                with patch.object(m,'Native',side_effect=AssertionError('charging touched')):m.run(long)
                self.assertEqual(long.end,long.qualification_end)
                self.assertEqual(long.results[m.ROW]['evidence']['wifi_prerequisite'],0)
        with patch.dict(os.environ,{'A9_BTRY_IN_HOOK':'1','ADB':'FAKE_UNREACHABLE'},clear=True):
            native=m.Native()
            with patch.object(native,'root',return_value='unsupported_counter 1\n') as root:
                context,reason=native.inspect();self.assertIsNone(context);self.assertEqual(reason,{'unsupported_counter':1})
                self.assertEqual(root.call_count,1)
            ready='unsupported_counter 0\ncounter_kind 1\nboot 00000000-0000-0000-0000-000000000000\ncapacity 71\ncounter 0\nstayon 3\nawake 1\ncontrol 2\n'
            with patch.object(native,'root',return_value=ready), patch.object(native,'framework',return_value='stayon 3\nawake 1\n'):
                context,reason=native.inspect()
                self.assertEqual(context['counter_kind'],1);self.assertEqual(reason,{})

        clock=Clock();native=Native(clock)
        d,okay=m.measure(native,lambda *a:self.fail('watchdog admitted'),self.out,3899,clock.now,clock.sleep)
        self.assertTrue(okay);self.assertEqual(d['attempted'],0);self.assertEqual(native.restores,0)

    def test_3_low_battery_transport_wifi_and_override_failures_restore(self):
        for fault in ('floor','read','wifi','unpause'):
            with self.subTest(fault=fault):
                d,okay,n,c,w=self.measure(fault)
                self.assertTrue(okay);self.assertFalse(n.paused);self.assertEqual(d['measured'],0)
                self.assertEqual(d['stage_failed'],1);self.assertLess(c.t,3600);self.assertGreaterEqual(n.restores,2)
        clock=Clock();native=Native(clock,'interrupt');watch=Watch(native);restored=[]
        with self.assertRaises(InterruptedError):
            m.measure(native,lambda *a:watch,self.out,5000,clock.now,clock.sleep,restored.append)
        self.assertFalse(native.paused);self.assertEqual(restored,[True])
        # An uncertain independent restore must fail the sweep cleanup too.
        clock=Clock();native=Native(clock);watch=Watch(native);watch.finish=lambda:False
        result,okay=m.measure(native,lambda *a:watch,self.out,5000,clock.now,clock.sleep)
        self.assertFalse(okay);self.assertEqual(result['restored'],0)

    def test_4_independent_watchdog_parent_sigkill_and_deadline(self):
        # Become a subreaper only inside this fixture, so the SIGKILL parent's
        # orphan watchdog is still ours to wait/reap (no leaked host process).
        lib=ctypes.CDLL(None);self.assertEqual(lib.prctl(36,1,0,0,0),0)
        fixture=self.out/'watch.py';marker=self.out/'restored';ready=self.out/'ready';report=self.out/'watch.json'
        fixture.write_text('''import importlib.util,os,subprocess as sp,sys,time
from pathlib import Path
s=importlib.util.spec_from_file_location('drain',sys.argv[2]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
marker=Path(sys.argv[3]);ready=Path(sys.argv[4]);report=Path(sys.argv[5])
if sys.argv[1]=='parent':
 p=sp.Popen([sys.executable,__file__,'child',*sys.argv[2:]],stdin=sp.PIPE,stdout=sp.DEVNULL,stderr=sp.DEVNULL,start_new_session=True,close_fds=True)
 while not ready.exists():time.sleep(.01)
 marker='1'*32
 helper=sp.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True,close_fds=True,env=dict(os.environ,A9_DRAIN_COMMAND=marker))
 p.stdin.write(('GROUP '+str(helper.pid)+' '+str(m.group_birth(helper.pid))+' '+marker+'\\n').encode());p.stdin.flush()
 print(str(p.pid)+' '+str(helper.pid),flush=True)
 time.sleep(10)
else:
 class Native:
  def restore(self):marker.write_text('charging-enabled')
  def battery_safe(self):return True
 m.watch_loop(sys.stdin,Native(),time.monotonic()+5,report,lambda:ready.write_text('armed'))
''')
        parent=sp.Popen([sys.executable,fixture,'parent',HERE/'drain.py',marker,ready,report],stdout=sp.PIPE,text=True,start_new_session=True)
        child=None;helper=None
        try:
            self.assertTrue(select_readable(parent.stdout,5));child,helper=map(int,parent.stdout.readline().split())
            parent.kill();parent.wait(timeout=2)
            until=time.monotonic()+5
            while not report.exists() and time.monotonic()<until:time.sleep(.02)
            self.assertEqual(marker.read_text(),'charging-enabled')
            self.assertEqual(json.loads(report.read_text())['reason'],3)
            self.assertEqual(json.loads(report.read_text())['stopped_command_groups'],1)
            self.assertEqual(json.loads(report.read_text())['command_groups_cleared'],1)
            _,status=os.waitpid(helper,0);helper=None;self.assertEqual(os.WTERMSIG(status),signal.SIGKILL)
            os.waitpid(child,0);child=None
            # Vanished leader: retain exact descendant identity via inherited
            # marker and pidfd, without trusting a reusable numeric group ID.
            orphan=self.out/'orphan';marker='2'*32
            source='import os,subprocess as s,sys,time; p=s.Popen([sys.executable,"-c","import time;time.sleep(30)"]); open(sys.argv[1],"w").write(str(p.pid)); time.sleep(.1)'
            leader=sp.Popen([sys.executable,'-c',source,orphan],start_new_session=True,
                            env=dict(os.environ,A9_DRAIN_COMMAND=marker))
            birth=m.group_birth(leader.pid);leader.wait(timeout=2)
            helper=int(orphan.read_text())
            d=m.stop_groups({leader.pid:(birth,marker)})
            self.assertEqual(d,dict(stopped=1,cleared=1))
            _,status=os.waitpid(helper,0);helper=None;self.assertEqual(os.WTERMSIG(status),signal.SIGKILL)
            # communicate(timeout) must not wait on a descendant's inherited
            # pipe after its leader exits. The leader remains an anchor until
            # command() kills its group, and all pipe draining stays bounded.
            started=time.monotonic()
            with self.assertRaises(sp.TimeoutExpired):
                m.command([sys.executable,'-c',source,orphan],timeout=.2)
            self.assertLess(time.monotonic()-started,1)
            helper=int(orphan.read_text());_,status=os.waitpid(helper,0);helper=None
            self.assertEqual(os.WTERMSIG(status),signal.SIGKILL)
            # Even successful EOF must clear a child that redirected stdout.
            # Do not unregister the group merely because its leader exited.
            detached=source.replace('p=s.Popen([sys.executable,"-c","import time;time.sleep(30)"])',
                                    'p=s.Popen([sys.executable,"-c","import time;time.sleep(30)"],stdout=s.DEVNULL,stderr=s.DEVNULL)')
            self.assertEqual(m.command([sys.executable,'-c',detached,orphan]),'')
            helper=int(orphan.read_text());_,status=os.waitpid(helper,0);helper=None
            self.assertEqual(os.WTERMSIG(status),signal.SIGKILL)
        finally:
            if parent.poll() is None:parent.kill();parent.wait()
            parent.stdout.close()
            if child is not None:
                try:os.killpg(child,signal.SIGKILL)
                except ProcessLookupError:pass
                os.waitpid(child,0)
            if helper is not None:
                try:os.killpg(helper,signal.SIGKILL)
                except ProcessLookupError:pass
                os.waitpid(helper,0)
            lib.prctl(36,0,0,0,0)
        # Timeout and low-battery paths execute the same restoration finally.
        clock=Clock();n=Native(clock);n.battery_safe=lambda:False
        class Stream:
            def readline(self):return ''
        def wait(*args):clock.sleep(args[-1]);return [],[],[]
        d=m.watch_loop(Stream(),n,100,self.out/'floor-watch.json',lambda:None,clock.now,wait)
        self.assertEqual(d['reason'],5);self.assertEqual(d['restored'],1)
        clock=Clock();d=m.watch_loop(Stream(),n,0,self.out/'deadline-watch.json',lambda:None,clock.now,wait)
        self.assertEqual(d['reason'],1);self.assertEqual(d['restored'],1)
        clock=Clock();n.battery_safe=lambda:True
        d=m.watch_loop(Stream(),n,1000,self.out/'hung-watch.json',lambda:None,clock.now,wait)
        self.assertEqual(d['reason'],4);self.assertEqual(d['restored'],1)

    def test_5_real_android_clock_and_input_suspend_readback(self):
        build=load('build_drain_test',HERE/'build.py');build.build_drain_clock(self.out)
        qemu='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
        output=sp.check_output([qemu,self.out/'drain-clock'],text=True,timeout=10)
        d=m.fields(output);self.assertGreaterEqual(int(d['boot_ns']),int(d['mono_ns'])-int(d['uncertainty_ns']))
        file=self.out/'input_suspend';file.write_text('0\n')
        with patch.dict(os.environ,{'A9_BTRY_IN_HOOK':'1','ADB':'FAKE_UNREACHABLE','PATH':os.environ['PATH']},clear=True):
            n=m.Native(dict(control=2))
            framework=patch.object(n,'framework',return_value='wifi 1\nscreen_off 1\n');framework.start();self.addCleanup(framework.stop)
            shell='/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sh'
            def root(source):
                source=source.replace(m.CONTROLS[2],str(file))
                return sp.check_output([shell],input='set -e\n'+source,text=True,timeout=5)
            with patch.object(n,'root',side_effect=root):
                n.mode(True);self.assertEqual(file.read_text().strip(),'1')
                n.mode(False);self.assertEqual(file.read_text().strip(),'0')
            # Dedicated preflight and watchdog use fresh drain SOC; the
            # snapshot obtains charge/SOC/coverage in one kernel transaction.
            n.context={'control':2,'counter_kind':1}
            with patch.object(n,'root',return_value='71\n') as capture:
                self.assertTrue(n.battery_safe())
                self.assertIn('/battery/drain_capacity',capture.call_args[0][0])
            data='boot_ns 10\nmono_ns 10\nuncertainty_ns 1\ncapacity 71\ncharge_counter -1\ncharge_full_design 4000000\nusb_present 1\nwifi 1\nscreen_off 1\ninput_paused 1\ncharging 0\ncounter_sample_ms 100\ncounter_elapsed_ms 100\ncounter_wakeups 0\n'
            with patch.object(n,'root',return_value=data) as capture:
                self.assertEqual(n.snapshot()['charge_counter'],-1)
                self.assertIn('cat /sys/class/power_supply/battery/drain_snapshot',capture.call_args[0][0])
                self.assertNotIn('for entry in capacity',capture.call_args[0][0])
            with patch.object(n,'root',return_value=data.replace('counter_wakeups 0','counter_wakeups -1')):
                with self.assertRaises(ValueError):n.snapshot()
            n.context={'control':2}
            # Run the actual snapshot connection predicate in Android mksh;
            # an enabled but disconnected radio must not pass standby.
            with patch.object(n,'root',return_value=''), patch.object(n,'framework',return_value='') as capture:
                with self.assertRaises(ValueError):n.snapshot()
            predicate=next(line for line in capture.call_args[0][0].splitlines() if line.startswith('cmd wifi status'))
            for status,expected in (('Wifi is enabled',0),('Wifi is connected to fixture',1)):
                output=sp.check_output([shell],input="cmd() { printf '%s\\n' '"+status+"'; }\n"+predicate,
                                       text=True,timeout=5)
                self.assertEqual(m.fields(output)['wifi'],str(expected))
            # Host GNU grep accepts BRE \\|; Android Toybox grep does not.
            # Exercise the production predicate with the actual Android tool.
            predicate=next(line for line in capture.call_args[0][0].splitlines() if line.startswith('dumpsys power'))
            env=dict(os.environ,PATH=str(Path(shell).parent)+':'+os.environ['PATH'])
            for state,expected in (('Asleep',1),('Dozing',1),('Awake',0)):
                output=sp.check_output([shell],input="dumpsys() { echo mWakefulness="+state+"; }\n"+predicate,
                                       env=env,text=True,timeout=5)
                self.assertEqual(m.fields(output)['screen_off'],str(expected))

    def test_6_framework_commands_use_shell_and_keep_watchdog_tracking(self):
        with patch.dict(os.environ, A9_BTRY_IN_HOOK='1', ADB='INERT'):
            n=m.Native(); tracker=object(); n.watchdog=tracker
            routed=[]
            def native(source):
                self.assertNotRegex(source, r'\b(svc|input|settings|cmd|dumpsys|setprop)\b')
                if source.startswith('model='):
                    return 'unsupported_counter 0\ncounter_kind 1\nboot 00000000-0000-0000-0000-000000000000\ncapacity 71\ncounter 0\ncontrol 2\n'
                return 'DRAIN_CLOCK 20 10 1\ncapacity 71\ncharge_counter 0\ncharge_full_design 4000000\nusb_present 1\ninput_paused 1\ncharging 0\ncounter_sample_ms 1\ncounter_elapsed_ms 1\ncounter_wakeups 0\n'
            def framework(argv, source=None, timeout=12, watcher=None):
                self.assertEqual(argv[:3], ['INERT','shell','-T']);self.assertIs(watcher,tracker)
                routed.append(argv[-1])
                stubs="settings() { echo 0; }; dumpsys() { echo mWakefulness=Asleep; }; svc() { :; }; input() { :; }; cmd() { echo 'Wifi is connected to fixture'; };\n"
                shell='/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sh'
                env=dict(os.environ,PATH=str(Path(shell).parent)+':'+os.environ['PATH'])
                return sp.check_output([shell],input=stubs+argv[-1],env=env,text=True,timeout=5)
            with patch.object(n,'root',side_effect=native), patch.object(m,'command',side_effect=framework):
                context,reason=n.inspect();self.assertEqual(reason,{})
                n.mode(True);n.prepare_screen();self.assertEqual(n.snapshot()['screen_off'],1);n.restore()
            self.assertEqual(len(routed),4)
            self.assertTrue(any('svc power stayon false' in command for command in routed))
            self.assertTrue(any('settings put global' in command for command in routed))

def select_readable(stream,timeout):
    import select
    return bool(select.select([stream],[],[],timeout)[0])

if __name__=='__main__':unittest.main(verbosity=2)
