#!/usr/bin/env python3
"""Offline S2 candidate checks. Fake root/iw/framework only; no phone or network.
A9_S2_AWK_JSON and A9_S2_SHELL_JSON can select cached Android binaries under QEMU.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[2]
SRC=REPO/'scripts/mainline/wifi/android/s2'
FIX=Path(__file__).with_name('fixtures')/'android-s2'
AWK=json.loads(os.environ.get('A9_S2_AWK_JSON','["awk"]'))
SHELL=json.loads(os.environ.get('A9_S2_SHELL_JSON','["sh"]'))
JDK=Path(os.environ.get('A9_S2_JDK','/home/user/A9-build/tools/jdk21'))
spec=importlib.util.spec_from_file_location('s2encoder',SRC/'s2-encode.py')
encoder=importlib.util.module_from_spec(spec);spec.loader.exec_module(encoder)


def parse(name,text,**args):
    cmd=AWK[:]
    for k,v in args.items():cmd+=['-v',f'{k}={v}']
    return subprocess.run(cmd+['-f',str(SRC/name)],input=text,text=True,capture_output=True)


class Radio(unittest.TestCase):
    def test_real_s1_power_absence_only_before(self):
        raw=(FIX/'s1b-iw-dev-masked.txt').read_text()
        before=parse('power.awk',raw,stage='before',rc=0)
        after=parse('power.awk',raw,stage='after',rc=0)
        self.assertEqual(before.returncode,0);self.assertIn('branch=unreported',before.stdout)
        self.assertNotEqual(after.returncode,0);self.assertIn('branch=missing',after.stdout)
        self.assertNotIn('[MASKED]',before.stdout+after.stdout)

    def test_power_matrix_and_no_raw_copy(self):
        for record,branch,accept in [('txpower 20.00 dBm','accepted',True),('txpower 0.00 dBm','accepted',True),
                ('txpower 21.0 dBm','over-cap',False),('txpower -1.0 dBm','negative',False),
                ('txpower 20 dB','unit',False),('txpower NaN dBm','number',False),
                ('txpower 20 dBm private-marker','fields',False),
                ('txpower 20 dBm\ntxpower 20 dBm','duplicate',False)]:
            for stage in ('before','after'):
                r=parse('power.awk',record+'\n',stage=stage,rc=0)
                self.assertEqual(r.returncode==0,accept);self.assertIn('branch='+branch,r.stdout)
                self.assertNotIn('private-marker',r.stdout)
        r=parse('power.awk','txpower 20.00 dBm\n',stage='before',rc=22)
        self.assertNotEqual(r.returncode,0);self.assertIn('branch=command-failed',r.stdout)

    def test_real_channel_ceiling_and_reject_flags(self):
        raw=(FIX/'trial15-iw-phy.txt').read_text()
        r=parse('channel.awk',raw)
        self.assertEqual(r.returncode,0);self.assertIn('max_dbm=20.0',r.stdout)
        row=next(x for x in raw.splitlines() if re.search(r'\* 2427(?:\.0)? MHz',x))
        for edit in (row.replace('20.0','21.0'),row+' (no IR)',row+' (radar detection)',row+' (disabled)',row.replace('20.0','NaN'),row+'\n'+row):
            self.assertNotEqual(parse('channel.awk',edit+'\n').returncode,0)
        self.assertNotEqual(parse('channel.awk','').returncode,0)

    def test_real_regulatory_and_cn_intersection(self):
        raw=(FIX/'s1-reg-get.txt').read_text()
        self.assertEqual(parse('reg.awk',raw,p=0).returncode,0)
        cn=raw.replace('country 99:','country CN:')
        self.assertEqual(parse('reg.awk',cn,p=0).returncode,0)
        for edit in (cn.replace('country US:','country CN:'),cn.replace('phy#0','phy#0 (self-managed)'),cn+cn,''):
            self.assertNotEqual(parse('reg.awk',edit,p=0).returncode,0)


class PrivateProtocol(unittest.TestCase):
    def test_public_scan_identity_and_no_root_fallback(self):
        with tempfile.TemporaryDirectory(prefix='s2-public-scan-') as t:
            r=subprocess.run([str(JDK/'bin/javac'),'--release','17','-d',t,
                str(SRC/'Protocol.java'),str(SRC/'PublicScan.java'),str(FIX/'PublicScanTest.java')],capture_output=True)
            self.assertEqual(r.returncode,0,'public scan compile')
            r=subprocess.run([str(JDK/'bin/java'),'-cp',t,'a9.wifi.s2.PublicScanTest'],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,'public scan identity replay')
            self.assertRegex(r.stdout,r'^S2_PUBLIC_SCAN cases=\d+ result=pass\n$')

    def test_encoder_pipe_only_and_fixed_error(self):
        body=json.dumps({'ssid':'fixture network','passphrase':'fixturepass!'}).encode()
        r=subprocess.run(['python3','-I',str(SRC/'s2-encode.py')],input=body,capture_output=True)
        self.assertEqual(r.returncode,0);self.assertEqual(r.stdout,encoder.encode(body));self.assertEqual(r.stderr,b'')
        r=subprocess.run(['python3','-I',str(SRC/'s2-encode.py'),'--root-stdin'],input=body,capture_output=True)
        self.assertEqual(r.returncode,0);self.assertIn(b"receive <<'A9_ANDROID_S2_PRIVATE_INPUT'",r.stdout)
        self.assertLess(r.stdout.index(b'export TMPDIR'),r.stdout.index(b"receive <<"))
        for raw in (b'private-marker',b'x'*1025,json.dumps({'ssid':'a','passphrase':'quote"private-marker'}).encode()):
            r=subprocess.run(['python3','-I',str(SRC/'s2-encode.py')],input=raw,capture_output=True)
            self.assertNotEqual(r.returncode,0);self.assertEqual(r.stdout,b'');self.assertEqual(r.stderr,b'S2_ENCODE input=rejected\n')
        with tempfile.TemporaryFile() as dst:
            r=subprocess.run(['python3','-I',str(SRC/'s2-encode.py')],input=body,stdout=dst,stderr=subprocess.PIPE)
            self.assertNotEqual(r.returncode,0);self.assertEqual(dst.tell(),0)

    def test_java_protocol_real_renderer_grammar(self):
        with tempfile.TemporaryDirectory(prefix='s2-java-test-') as t:
            r=subprocess.run([str(JDK/'bin/javac'),'--release','17','-d',t,str(SRC/'Protocol.java'),str(FIX/'ProtocolTest.java')],capture_output=True)
            self.assertEqual(r.returncode,0,'Java compile')
            r=subprocess.run([str(JDK/'bin/java'),'-cp',t,'a9.wifi.s2.ProtocolTest'],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stdout);self.assertRegex(r.stdout,r'^S2_PROTOCOL cases=\d+ result=pass\n$')


class Worker(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='s2-worker-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.b=self.root/'data/local/tmp/a9-wifi-s2';self.b.mkdir(parents=True);self.b.chmod(0o700)
        self.d=self.root/'mnt/a9-wifi/trial/android-s2';self.d.mkdir(parents=True);self.d.chmod(0o700)
        (self.root/'mnt/a9-wifi/session').mkdir(parents=True)
        (self.root/'mnt/a9-wifi/trial/session-total').write_text('3600\n')
        up=int(float(Path('/proc/uptime').read_text().split()[0]))
        (self.root/'mnt/a9-wifi/trial/started.uptime').write_text(str(up-100)+'\n')
        self.calls=self.root/'calls'
        self.bin=self.root/'bin';self.bin.mkdir()
        ip=self.bin/'ip';ip.write_text("#!/bin/sh\necho link-down >> '"+str(self.calls)+"'\n");ip.chmod(0o700)
        self.mounts=self.root/'mounts';self.mounts.write_text('/dev/block/ram0 /data ext4 rw 0 0\ntmpfs /mnt/a9-wifi/trial tmpfs rw,noswap,nodev,nosuid,noexec 0 0\n')
        self.swaps=self.root/'swaps';self.swaps.write_text('Filename Type Size Used Priority\n')
        for f in ['s2-target.sh','reg.awk','channel.awk','power.awk','s2-encode.py']:shutil.copyfile(SRC/f,self.b/f)
        for f in ['bridge.jar','provenance.json']:(self.b/f).write_text('fixture')
        self.seal()
        for f in ['s1-reg-get.txt','trial15-iw-phy.txt','s1b-iw-dev-masked.txt']:shutil.copyfile(FIX/f,self.root/f)
        iw=self.root/'iw';iw.write_text('''#!/bin/sh
case "$*" in
 'reg get') cat 'ROOT/s1-reg-get.txt';;
 'phy phy0 info') cat 'ROOT/trial15-iw-phy.txt';;
 'dev wlan0 info') if [ -f 'ROOT/associated' ]; then echo 'txpower 20.00 dBm'; else cat 'ROOT/s1b-iw-dev-masked.txt'; fi;;
 'dev wlan0 set txpower limit 2000') exit ${FAKE_LIMIT_RC:-0};;
 *) exit 1;;
esac
'''.replace('ROOT',str(self.root)));iw.chmod(0o700)
        text=(SRC/'s2-target.sh').read_text()
        # Keep all real admission/parsing/ordering. Substitute only OS and hardware boundaries.
        fake='''
id() { echo 0; }
uname() { echo 7.2.fixture; }
getprop() {
 case "$1" in
 ro.boot.slot_suffix) echo ${FAKE_SLOT:-_b};;
 vendor.a9ml.wifi.session.state) echo ${FAKE_STATE:-serving};;
 vendor.a9ml.wifi.session.stop) echo 0;;
 init.svc.logd) echo ${FAKE_LOGD:-stopped};;
 *) echo 1;;
 esac
}
stat() {
 case "$*" in
 '-c %u:%a '*) v=$(command stat -c %a "$3");echo "0:$v";;
 '-c %u:%a:%h '*) v=$(command stat -c %a:%h "$3");echo "0:$v";;
 '-c %u:%h '*) echo 0:1;;
 *) command stat "$@";;
 esac
}
readlink() { echo /sys/class/ieee80211/phy0; }
sha256sum() { if [ "$1" = 'ROOT/iw' ]; then echo '6a6951202b0e1120e7ba05fc57d7faec60790ede5614b5477c79fa386002703b  iw'; elif [ "$1" = 'HOST_AWK' ]; then echo '39d9a31c314cb3f7166d127b3fdc12e97b5f100308003a0869147a38ad11bf55  awk'; else command sha256sum "$@";fi; }
ip() { echo link-down >> 'ROOT/calls'; }
'''.replace('ROOT',str(self.root)).replace('HOST_AWK',shutil.which('awk'))
        text=text.replace('set -u\n','set -u\n'+fake,1)
        text=re.sub(r'bridge\(\) \{.*?\n\}', '''bridge() {
 echo "bridge-$2" >> 'ROOT/calls'
 if [ "${FAKE_BRIDGE_FAIL:-}" = "$2" ]; then return 1;fi
 case "$2" in connect) rm -f "$D/input";touch 'ROOT/associated';; esac
 echo "S2_BRIDGE fixture=accepted"
}'''.replace('ROOT',str(self.root)),text,count=1,flags=re.S)
        for old,new in [('/system/bin/awk',shutil.which('awk')),('/mnt/a9-wifi/tools/iw',str(iw)),
                ('/proc/mounts',str(self.mounts)),('/proc/swaps',str(self.swaps)),
                ('/data/local/tmp/a9-wifi-s2',str(self.b)),('/mnt/a9-wifi',str(self.root/'mnt/a9-wifi'))]:
            text=text.replace(old,new)
        text=text.replace('for dir in /data/local /data/local/tmp ',
            'for dir in '+str(self.root/'data/local')+' '+str(self.root/'data/local/tmp')+' ')
        # Mount fixture must use the rewritten exact paths too.
        self.mounts.write_text(self.mounts.read_text().replace('/mnt/a9-wifi',str(self.root/'mnt/a9-wifi')))
        stop=self.root/'stop';stop.write_text("#!/bin/sh\necho session-stop >> '"+str(self.calls)+"'\n");stop.chmod(0o700)
        text=text.replace('/vendor/bin/a9ml-wifi-session.sh',str(stop))
        self.script=self.root/'worker';self.script.write_text(text)
        (self.d/'input').write_text('synthetic-private-marker');(self.d/'input').chmod(0o600)

    def seal(self):
        names=['bridge.jar','s2-target.sh','reg.awk','channel.awk','power.awk','s2-encode.py','provenance.json']
        p=self.b/'GO';p.write_text('I_AUTHORIZE_REVIEWED_ANDROID_S2\ncountry=US frequency=2427 persistent_userdata=no\n'+''.join(hashlib.sha256((self.b/f).read_bytes()).hexdigest()+'  '+f+'\n' for f in names));p.chmod(0o400)

    def run_worker(self,mode='run',data=None,**env):
        r=subprocess.run(SHELL+[str(self.script),mode],input=data,capture_output=True,text=True,env=dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ["PATH"],**env))
        self.assertNotIn('synthetic-private-marker',r.stdout+r.stderr)
        return r

    def test_success_order_cleanup_and_one_attempt(self):
        r=self.run_worker();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('result=success',r.stdout);self.assertIn('branch=unreported',r.stdout);self.assertIn('dbm=20.00',r.stdout)
        self.assertEqual(self.calls.read_text().splitlines(),['bridge-probe','bridge-scan','bridge-connect','bridge-web','bridge-cleanup','session-stop','link-down'])
        self.assertFalse((self.d/'input').exists())
        self.assertNotEqual(self.run_worker().returncode,0)

    def test_failed_gate_still_revokes_and_stops(self):
        r=self.run_worker(FAKE_BRIDGE_FAIL='connect');self.assertNotEqual(r.returncode,0)
        self.assertNotIn('bridge-web',self.calls.read_text());self.assertIn('session-stop',self.calls.read_text())
        self.assertFalse((self.d/'input').exists())

    def test_public_scan_failure_prevents_connect_and_scrubs_without_changing_receiver(self):
        r=self.run_worker(FAKE_BRIDGE_FAIL='scan')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('phase=public-scan result=failed',r.stdout)
        self.assertEqual(self.calls.read_text().splitlines(),
            ['bridge-probe','bridge-scan','bridge-cleanup','session-stop','link-down'])
        self.assertFalse((self.d/'input').exists())

    def test_probe_exercises_scan_failure_without_private_input_or_attempt(self):
        shutil.rmtree(self.d)
        r=self.run_worker(mode='probe',FAKE_BRIDGE_FAIL='scan')
        self.assertNotEqual(r.returncode,0)
        self.assertNotIn('public_preflight=accepted',r.stdout)
        self.assertNotIn('bridge-connect',self.calls.read_text())
        self.assertFalse(self.d.exists())
        self.assertFalse((self.root/'mnt/a9-wifi/session/s2-attempted').exists())

    def test_power_limit_failure_has_rc_and_no_connect(self):
        r=self.run_worker(FAKE_LIMIT_RC='22');self.assertNotEqual(r.returncode,0);self.assertIn('iw_rc=22',r.stdout)
        self.assertNotIn('bridge-connect',self.calls.read_text());self.assertIn('session-stop',self.calls.read_text())

    def test_bad_seal_never_executes_bridge_but_scrubs_and_stops(self):
        (self.b/'bridge.jar').write_text('changed')
        r=self.run_worker();self.assertNotEqual(r.returncode,0)
        self.assertEqual(self.calls.read_text().splitlines(),['session-stop','link-down']);self.assertFalse((self.d/'input').exists())

    def test_running_logd_and_real_userdata_block_admission(self):
        self.assertNotEqual(self.run_worker(FAKE_LOGD='running').returncode,0);self.assertFalse(self.calls.exists())
        self.mounts.write_text(self.mounts.read_text().replace('/dev/block/ram0','/dev/block/by-name/userdata'))
        self.assertNotEqual(self.run_worker().returncode,0);self.assertFalse(self.calls.exists())

    def test_87h_capture_and_hardware_ram_user0_alias(self):
        self.assertEqual((FIX/'s2-87h-worker.txt').read_text(),
                         'S2_GATE phase=ram-data result=failed\nS2_ROOT_RC 1\n')
        # The raw mount command output is historical hardware evidence. Derive
        # /proc/mounts fields explicitly; 87h did not retain raw mount rows.
        rows=[]
        for line in (FIX/'s2-87h-data-mounts.txt').read_text().splitlines():
            parts=line.split();self.assertEqual(parts[1],'on');self.assertEqual(parts[3],'type')
            rows.append(f'{parts[0]} {parts[2]} {parts[4]} {parts[5][1:-1]} 0 0\n')
        mounts=''.join(rows)
        old=(Path('/home/user/A9-build/mainline/wifi-android/s2-87h/bundle/s2-target.sh')).read_text()
        block=old.split('phase=ram-data\n',1)[1].split(' || fail',1)[0]
        program=re.search(r'"\$AWK" \'(.*?)\' /proc/mounts',block,re.S).group(1)
        self.assertNotEqual(subprocess.run(AWK+[program],input=mounts,text=True,capture_output=True).returncode,0)
        evidence_row=self.mounts.read_text().splitlines(keepends=True)[1]
        self.mounts.write_text(mounts+evidence_row)
        shutil.rmtree(self.d)
        r=self.run_worker(mode='probe');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('phase=ram-data branch=accepted roots=1 children=1 user0=1',r.stdout)
        self.assertIn('public_preflight=accepted',r.stdout)
        self.assertEqual(self.calls.read_text().splitlines(),['bridge-probe','bridge-scan'])
        self.assertFalse(self.d.exists())

    def test_ram_alias_gate_rejects_persistent_overlay_duplicate_and_other_subtrees(self):
        original=self.mounts.read_text()
        root='/dev/block/ram0 /data ext4 rw 0 0\n'
        alias='/dev/block/ram0 /data/user/0 ext4 rw 0 0\n'
        cases=[('', 'root-count'),
               (root+root,'root-count'),
               (root.replace('/dev/block/ram0','/dev/block/by-name/userdata'),'root-backing'),
               (root+alias+alias,'user0-duplicate'),
               (root+alias.replace('/dev/block/ram0','/dev/block/by-name/userdata'),'user0-backing'),
               (root+alias.replace('ext4','overlay'),'user0-backing'),
               (root+alias.replace('/dev/block/ram0','tmpfs'),'user0-backing'),
               (root+alias.replace('/data/user/0','/data/misc/apexdata/com.android.wifi'),'unexpected-submount'),
               (root+alias.replace('/data/user/0','/data/local/tmp/a9-wifi-s2'),'unexpected-submount')]
        for mounts,branch in cases:
            with self.subTest(branch=branch,mounts=mounts):
                self.mounts.write_text(mounts+original.splitlines(keepends=True)[1])
                r=self.run_worker();self.assertNotEqual(r.returncode,0)
                self.assertIn('phase=ram-data branch='+branch,r.stdout)
                self.assertFalse(self.calls.exists())

    def test_short_remaining_session_never_connects(self):
        (self.root/'mnt/a9-wifi/trial/session-total').write_text('600\n')
        up=int(float(Path('/proc/uptime').read_text().split()[0]))
        (self.root/'mnt/a9-wifi/trial/started.uptime').write_text(str(up-500)+'\n')
        r=self.run_worker();self.assertNotEqual(r.returncode,0)
        self.assertIn('phase=remaining-budget',r.stdout)
        self.assertNotIn('bridge-connect',self.calls.read_text())

    def test_receive_stays_in_ram_and_never_associates(self):
        shutil.rmtree(self.d)
        r=self.run_worker(mode='receive',data='synthetic-private-marker')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertFalse(self.calls.exists())
        self.assertEqual(r.stdout,'S2_GATE phase=receive input=ready\n')
        self.assertEqual((self.d/'input').read_text(),'synthetic-private-marker');self.assertEqual((self.d/'input').stat().st_mode&0o777,0o600)
        self.assertNotEqual(self.run_worker(mode='receive',data='replacement').returncode,0)

    def test_public_preflight_needs_no_credential_and_keeps_session(self):
        shutil.rmtree(self.d)
        r=self.run_worker(mode='probe')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('public_preflight=accepted',r.stdout)
        self.assertIn('phase=power-before',r.stdout)
        self.assertIn('channel=accepted frequency=2427',r.stdout)
        self.assertEqual(self.calls.read_text().splitlines(),['bridge-probe','bridge-scan'])
        self.assertFalse(self.d.exists())
        self.assertFalse((self.root/'mnt/a9-wifi/session/s2-attempted').exists())
        blocked=self.run_worker(mode='probe',FAKE_LIMIT_RC='22')
        self.assertNotEqual(blocked.returncode,0)
        self.assertEqual(self.calls.read_text().splitlines().count('bridge-probe'),1)

    def test_private_root_stdin_envelope_to_receiver(self):
        shutil.rmtree(self.d)
        (self.root/'mnt/a9-wifi/trial').chmod(0o700)
        body=json.dumps({'ssid':'fixture network','passphrase':'fixturepass!'}).encode()
        frame=encoder.encode(body)
        payload=encoder.payload(frame).decode()
        import shlex
        payload=payload.replace('PATH=/system/bin:/system/xbin','PATH='+shlex.quote(str(self.bin)+os.pathsep+os.environ['PATH']))
        payload=payload.replace('[ "$(id -u)" = 0 ]','[ "$(id -u)" = '+str(os.getuid())+' ]')
        payload=payload.replace('= 0:700 ]','= '+str(os.getuid())+':700 ]')
        payload=payload.replace('/data/local/tmp/a9-wifi-s2/s2-target.sh',str(self.script))
        payload=payload.replace('/system/bin/sh',shlex.join(SHELL))
        payload=payload.replace('/mnt/a9-wifi/trial',str(self.root/'mnt/a9-wifi/trial'))
        payload=payload.replace('/proc/mounts',str(self.mounts))
        r=subprocess.run(SHELL,input=payload,text=True,capture_output=True,
            env=dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ['PATH']))
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.d/'input').read_bytes(),frame)
        self.assertEqual(r.stdout,'S2_GATE phase=receive input=ready\n')
        self.assertNotIn('fixturepass',r.stdout+r.stderr)
        self.assertNotIn('fixture network',r.stdout+r.stderr)
        self.assertFalse(self.calls.exists())

    def test_oversize_receive_scrubs_and_stops(self):
        shutil.rmtree(self.d)
        r=self.run_worker(mode='receive',data='x'*600)
        self.assertNotEqual(r.returncode,0);self.assertFalse((self.d/'input').exists());self.assertIn('session-stop',self.calls.read_text())


if __name__=='__main__':unittest.main(verbosity=2)
