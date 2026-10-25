#!/usr/bin/env python3
"""Focused Android-shell/host evidence fixtures. No device access or crash-tool entry points."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess as sp
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
extract=load('crash_extract',HERE/'crash-evidence.py')
fake=load('crash_fake',HERE/'selftest.py')
ANDROID=Path('/home/user/A9-build/mainline/sol-wifi/target-audit')
QEMU='/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


class Crash(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.dirs=[self.root/n for n in ('tomb','anr','dropbox')]
        for d in self.dirs:d.mkdir()
        self.bin=self.root/'bin';self.bin.mkdir()
        for name in ('head','stat','awk','gzip'):
            command=str(ANDROID/'system/bin/awk') if name=='awk' else str(ANDROID/'system/bin/toybox')+' '+name
            p=self.bin/name;p.write_text('#!/bin/sh\nexec '+QEMU+' '+str(ANDROID/'linker64')+' '+command+' "$@"\n');p.chmod(0o700)
        p=self.bin/'getprop';p.write_text('#!/bin/sh\nprintf "running\\n"\n');p.chmod(0o700)

    def capture(self):
        lines=(HERE.parent/'boot-deadline/crash-evidence.h').read_text().splitlines()
        script=''.join(json.loads(l.strip()) for l in lines if l.strip().startswith('"'))
        script=script.replace('PATH=/system/bin:/system/xbin','PATH='+str(self.bin)+':'+os.environ['PATH'])
        for original,d in zip(('/data/tombstones','/data/anr','/data/system/dropbox'),self.dirs):script=script.replace(original,str(d))
        script=script.replace('/apex/com.android.runtime/bin/crash_dump64',str(self.bin/'getprop'))
        r=sp.run([QEMU,ANDROID/'linker64',ANDROID/'system/bin/sh'],input=script,
            env=dict(os.environ,LD_LIBRARY_PATH=str(ANDROID/'lib64')),text=True,capture_output=True,timeout=25)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertNotIn('PRIVATE',r.stdout);self.assertNotIn('BADC0DE',r.stdout)
        return r.stdout

    def test_android_three_sources_and_double_scrub(self):
        (self.dirs[0]/'tombstone_00').write_text('Cmdline: /vendor/bin/hw/android.hardware.media.c2@1.2-service-v4l2-64 PRIVATE_ARG\n'
            'pid: 7777, tid: 7777, name: PRIVATE_THREAD >>> /vendor/bin/hw/android.hardware.media.c2@1.2-service-v4l2-64 <<<\n'
            'signal 6 (SIGABRT), code -1 (SI_QUEUE), fault addr 0xBADC0DE\nAbort message: fdsan PRIVATE_ABORT\n'
            '    #00 pc 00000BADC0DE /apex/com.android.runtime/lib64/bionic/libc.so (abort+164) (BuildId: PRIVATE_ID)\n')
        (self.dirs[1]/'anr_PRIVATE_ID').write_text('Cmd line: system_server PRIVATE_ARG\n  at android.os.BinderProxy.transactNative(Native method)\n')
        data='Process: system_server PRIVATE_ARG\njava.lang.IllegalStateException: PRIVATE_MESSAGE\n  at com.android.server.locksettings.LockSettingsService.init(Source.java:7777)\n'
        (self.dirs[2]/'system_server_crash@PRIVATE_ID.txt.gz').write_bytes(gzip.compress(data.encode()))
        (self.dirs[2]/'data_app_crash@PRIVATE_ID.txt').write_text('Process: com.example.app PRIVATE_ARG\njava.lang.RuntimeException: PRIVATE\n')
        text=self.capture();self.assertIn('trace_signal 6 -1',text);self.assertIn('trace_native libc.so abort',text)
        boot=self.root/'boot';boot.mkdir();(boot/'crash-evidence.log').write_text('capture_begin\n'+text+'capture_end 0\nPRIVATE_PAYLOAD\n')
        s=extract.save(boot);self.assertEqual(s['records'],4);self.assertEqual(s['health'],dict(directories_ready=1,tombstoned_running=1,crash_dump64_present=1))
        exported=''.join((boot/n).read_text() for n in ('tombstones.jsonl','anrs.jsonl','dropbox-crashes.jsonl','crash-summary.json'))
        self.assertNotIn('PRIVATE',exported);self.assertNotIn('7777',exported);self.assertNotIn('BADC0DE',exported)
        self.assertIn('system_server',exported);self.assertIn('IllegalStateException',exported)
        for n in ('tombstones.jsonl','anrs.jsonl','dropbox-crashes.jsonl','crash-summary.json'):self.assertEqual((boot/n).stat().st_mode&0o777,0o600)

    def test_skips_limits_and_read_failure(self):
        (self.dirs[0]/'tombstone_00.pb').write_text('PRIVATE protobuf')
        p=self.dirs[0]/'tombstone_symlink';p.symlink_to('/PRIVATE')
        (self.dirs[2]/'system_server_crash@PRIVATE.txt.gz').write_bytes(b'bad PRIVATE gzip')
        (self.dirs[0]/'tombstone_limit').write_text('Cmdline: /system/bin/example\n'+
            '    #00 pc BADC0DE /system/lib64/libexample.so (run+2)\n'*80+'X'*70000)
        text=self.capture();self.assertIn('trace_errors 1',text);self.assertIn('trace_frame_limit 1',text)
        self.assertRegex(text,r'trace_begin 1 1 [0-9]+ 1')
        self.assertEqual(text.count('trace_native '),32)

    def test_early_system_server_loop_and_ring_overlap(self):
        (self.root/'crash-evidence.log').write_text('capture_begin\ncapture_exec_errno 2\ncapture_end 127\n')
        a='10-24 01:02:03.000 123 123 E AndroidRuntime: FATAL EXCEPTION IN SYSTEM PROCESS: main\n'
        b=a.replace('03.000 123','04.000 456')
        (self.root/'logcat.log.prev').write_text(a)
        (self.root/'logcat.log').write_text(a+b+'PRIVATE_MESSAGE\n')
        s=extract.save(self.root);self.assertEqual(s['system_server_fatals'],2);self.assertEqual(s['system_server_crash_loop'],1)
        self.assertEqual(s['captures'],1);self.assertIsNone(s['health'])
        self.assertEqual(s['exec_errors'],1);self.assertEqual(s['last_exec_errno'],2)
        self.assertNotIn('PRIVATE',(self.root/'crash-summary.json').read_text())
        with patch.dict(os.environ,{'A9_BTRY_LOGS':str(self.root)}):sweep=fake.Fake()
        sweep.record_crash_events([a,a,b]);self.assertEqual(sweep.system_server_fatals,2)
        self.assertNotIn('123',(self.root/'stability-events.jsonl').read_text())

    def test_interrupted_capture_and_malformed_input(self):
        (self.root/'crash-evidence.log').write_text('capture_begin\ntrace_begin 1 1 80 0\ntrace_section system_server\n'
            'trace_section PRIVATE ARGV\ntrace_signal 999 -1\nPRIVATE_PAYLOAD\n')
        rows,s=extract.collect(self.root);self.assertEqual(s['incomplete_captures'],1)
        self.assertEqual(rows[0]['complete'],0);self.assertNotIn('signal',rows[0]);self.assertNotIn('PRIVATE',json.dumps(rows))
        target=self.root/'tombstones.jsonl';target.symlink_to(self.root/'outside')
        with self.assertRaises(OSError):extract.save(self.root)

    def test_file_limit_is_explicit(self):
        for i in range(65):(self.dirs[0]/('tombstone_%02d'%i)).write_text('Cmdline: /system/bin/example\n')
        text=self.capture();self.assertEqual(text.count('trace_begin '),64)
        self.assertIn('trace_truncated 1',text)
        (self.root/'crash-evidence.log').write_text('capture_begin\n'+text+'capture_end 0\n')
        rows,s=extract.collect(self.root);self.assertEqual(s['file_limit_observations'],1)


if __name__=='__main__':unittest.main(verbosity=2)
