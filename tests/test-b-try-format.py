#!/usr/bin/env python3
"""Offline FORMAT=1 lifecycle regression. No real transports or USB locks.

Run: python3 -B tests/test-b-try-format.py
The real acceptance launcher and b-try run against an isolated fake phone.
Only vendor preparation and the acceptance hook are stubbed. Bash sleep
advances a virtual clock, so a 108-second boot and deadline expiry are fast.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''#!PYTHON
import hashlib, json, os, pathlib, shlex, shutil, sys
r = pathlib.Path(os.environ['FAKE_ROOT'])
s = json.loads((r/'state.json').read_text())
now = int((r/'clock').read_text())
tool, *a = sys.argv[1:]
with (r/'calls.jsonl').open('a') as f:
    f.write(json.dumps([now, tool, *a])+'\n')
def done(rc=0, text=None):
    (r/'state.json').write_text(json.dumps(s))
    if text is not None: print(text)
    sys.exit(rc)
def elapsed(): return now-s['b_start']
def fastboot():
    if s['mode'] in ('fastboot', 'fastbootd'): return True
    if s['mode'] != 'b': return False
    return (elapsed() < s['linger'] or s['scenario'] == 'stuck-fastboot'
            or (s['scenario'] == 'return-fastboot' and elapsed() >= 40))
if tool == 'timeout':
    while a[0].startswith('--'): a.pop(0)
    os.environ['FAKE_TIMEOUT'] = a.pop(0)
    os.execvp(a[0], a)
if tool == 'lsusb': done(1)  # No USB discovery is ever delegated to the host.
if tool == 'hook':
    assert s['mode'] == 'b' and elapsed() >= s['boot_after']
    s['hook'] = True
    if s['scenario'].startswith('hvenc-'):
        out=pathlib.Path(os.environ['A9_BTRY_LOGS'])
        (out/'hvenc-qualification.json').write_text('{"host_mp4_ok":1,"native_pass":1,"cleanup":1}')
    done(text='fake acceptance hook')
if tool == 'fastboot':
    if a == ['devices']: done(text='fake-only\tfastboot' if fastboot() else '')
    if a == ['getvar', 'current-slot']: done(0 if fastboot() else 1, 'current-slot: '+s['slot'])
    if a == ['getvar', 'is-userspace']: done(0 if fastboot() else 1, 'is-userspace: '+('yes' if s['mode']=='fastbootd' else 'no'))
    assert fastboot(), a
    if a == ['reboot','fastboot']:
        s['mode']='fastbootd'
        if s.get('system_case')=='fastbootd-slot': s['slot']='b'
        done()
    if a == ['reboot','recovery']:
        assert s['mode']=='fastbootd'
        s['mode']='recovery';done()
    if a[:1] == ['flash']:
        if a[1]=='system_b':
            assert s['mode']=='fastbootd' and s['slot']=='a'
            if s.get('system_case')=='flash-failed': done(1)
            s['system_hash']=hashlib.sha256(pathlib.Path(a[2]).read_bytes()).hexdigest()
            s.setdefault('written',[]).append('system_b');done(text='OKAY')
        assert a[1] == 'boot_b'
        s.setdefault('written',[]).append('boot_b')
        s['flashed'] = True
    elif a[:1] == ['set_active']:
        assert a[1] in ('a', 'b')
        s['slot'] = a[1]
    elif a == ['reboot']:
        if s['slot'] == 'b':
            assert (s.get('formatted') or s.get('system_case')=='ram') and s.get('flashed') and s.get('vendor')
            s['mode'] = 'b'; s['b_start'] = now
        else: s['mode'] = 'device'
    else: raise AssertionError(a)
    done(text='Rebooting OKAY' if a == ['reboot'] else 'OKAY')
assert tool == 'adb', (tool, a)
if a == ['devices']:
    done(text='List of devices attached\n'+('fake-only\tdevice' if s['mode']=='device' else ''))
if a == ['get-state']:
    if fastboot() or (s['mode'] == 'b' and elapsed() < 8): done(1)
    done(text='device' if s['mode'] == 'b' else s['mode'])
if a[:1] == ['reboot']:
    s['mode'] = 'fastboot' if a[1:] == ['bootloader'] else 'recovery'
    if s.get('system_case')=='recovery-slot' and s['mode']=='recovery': s['slot']='b'
    done()
if a[:1] == ['push']:
    kind='system' if a[2]=='/tmp/b-try-system.img' else 'vendor'
    if kind=='system' and s.get('system_case')=='push-failed': done(1)
    s[kind+'_hash'] = hashlib.sha256(pathlib.Path(a[1]).read_bytes()).hexdigest()
    done()
if a[:1] == ['pull']:
    shutil.copyfile(r/'phone-files'/pathlib.Path(a[1]).name,a[2]);done()
if a[:1] == ['exec-out']:
    import io, tarfile
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode='w') as t:
        d = tarfile.TarInfo('pstore' if 'pstore' in a[1] else 'boot-0000000001')
        d.type = tarfile.DIRTYPE; d.mode=0o700; t.addfile(d)
        if 'pstore' not in a[1] and s['scenario']=='crash-loop':
            d2=tarfile.TarInfo('boot-0000000002');d2.type=tarfile.DIRTYPE;d2.mode=0o700;t.addfile(d2)
            text=(b'10-24 01:02:03.000 1 1 E AndroidRuntime: FATAL EXCEPTION IN SYSTEM PROCESS: main\n'
                  b'10-24 01:02:04.000 2 2 E AndroidRuntime: FATAL EXCEPTION IN SYSTEM PROCESS: main\n')
            f=tarfile.TarInfo('boot-0000000001/logcat.log');f.size=len(text);t.addfile(f,io.BytesIO(text))
        if 'pstore' not in a[1] and s['scenario'].startswith('hvenc-'):
            d2=tarfile.TarInfo('boot-0000000002');d2.type=tarfile.DIRTYPE;d2.mode=0o700;t.addfile(d2)
            text=(b'HVENC_RESULT status=0 avc_frames=20 avc_eos=1 avc_named=1 hevc_frames=20 hevc_eos=1 hevc_named=1 encoder_named=1 surface_frames=30 encoded_frames=30 encoder_eos=1 muxer_ok=1 mp4_frames=30 duration_us=3000000 pts_span_us=2900000 roundtrip_frames=30 roundtrip_eos=1 roundtrip_named=1 bytes=40000\n')
            if s['scenario']=='hvenc-partial': text=b'HVENC_QUAL op=6 phase=0 rc=0 frames=0\n'
            f=tarfile.TarInfo('boot-0000000002/logcat.log');f.size=len(text);t.addfile(f,io.BytesIO(text))
            text=(b'A9_VENUS_ENC op=session_start phase=response rc=0 state=3 codec=0x2\n'
                  b'A9_VENUS_ENC op=buffer.done type=0x2 used=999 offset=0 flags=0 hfi_flags=0 tag=1\n')
            f=tarfile.TarInfo('boot-0000000002/kernel.log');f.size=len(text);t.addfile(f,io.BytesIO(text))
    sys.stdout.buffer.write(b.getvalue()); done()
assert a[:1] == ['shell'], a
c = ' '.join(a[2:] if a[1:2] == ['-T'] else a[1:])
if c.startswith('su -c ') and s.get('system_case')=='unrooted': done(1)
if c.startswith('su -c '): c = shlex.split(c)[2]
if 'echo "slot=' in c:
    done(text='slot=_'+s['slot']+'\nboot_completed=1\nlineage=fake\nwakefulness=Awake')
if c=='id': done(text='uid=0(root) gid=0(root)')
if 'service call SurfaceFlinger 1010' in c: done(text='0x00000010: 00000001')
if 'dmesg | grep' in c: done()
if 'plat_mac_permissions' in c: done(text=(r/'phone-files/plat_mac_permissions.xml').read_text())
if 'ro.build.version.sdk_full' in c:
    done(text='ro.build.version.sdk_full=36.1\nro.build.version.sdk=36\nro.build.version.security_patch=2026-05-01\nro.build.fingerprint=fake')
if c=='getprop ro.boot.slot_suffix': done(text='_'+s['slot'])
if c=='getprop vendor.gralloc.disable_ubwc': done(text='1')
if 'blockdev' in c and 'by-name/super' in c:
    done(text='super '+str((64 if s.get('system_case')=='super-full' else 128)*1024**2)+'\nsystem_a 33554432\nsystem_b 16777216\nvendor_a 8388608\nvendor_b 8388608')
if c.startswith('head -c ') and '/dev/block/mapper/system_b | sha256sum' in c:
    if s.get('system_case')=='readback-failed': done(1)
    done(text=('0'*64 if s.get('system_case')=='mismatch' else s['system_hash'])+'  -')
if c == 'twrp format data':
    assert s['mode'] == 'recovery'
    s['formatted'] = True; done(text='Done processing script file')
if c.startswith('blockdev --setrw'):
    assert s['mode']=='recovery' and s['slot']=='a'
    if '/dev/block/mapper/system_b' in c:
        if s.get('system_case')=='flash-failed': done(1)
        s.setdefault('written',[]).append('system_b');done()
    s.setdefault('written',[]).append('vendor_b')
    s['vendor'] = True; done(text=s['vendor_hash']+'  /dev/block/mapper/vendor_b')
if c == 'sh /system/bin/rebootsystem.sh': done()
if c.startswith('printf "A9_BTRY_BOOT_STATUS '):
    if fastboot() or (s['mode'] == 'b' and elapsed() < 8): done(1)
    complete = s['mode'] == 'device' or (s['mode'] == 'b'
        and s['scenario'] != 'incomplete' and elapsed() >= s['boot_after'])
    record = 'A9_BTRY_BOOT_STATUS slot=_'+s['slot']+' completed='+str(int(complete))
    if s['mode'] == 'b':
        if s['scenario'] == 'boot-noisy':
            # First shell request fails; later shells take 15 seconds (a ten
            # second wrapper would lose these too). No real waiting needed.
            if not s.get('probe_failed'):
                s['probe_failed'] = True
                done(124, record)
            if int(os.environ['FAKE_TIMEOUT']) < 15: done(124)
            done(text='unrelated shell startup output\n'+record+'\r\n')
        if s['scenario'] == 'incomplete':
            # Failed and duplicate complete replies must never dispatch hook.
            n = s.get('probe_count', 0); s['probe_count'] = n+1
            record = 'A9_BTRY_BOOT_STATUS slot=_b completed=1'
            if n%3 == 0: done(124, record)
            if n%3 == 1: done(text=record+'\n'+record)
            done(text=record+'extra')
    done(text=record+'\r\n')
if c.startswith('su -c '): c = shlex.split(c)[2]
if c == 'id -u': done(text='2000' if s.get('system_case')=='unrooted' and s['mode']=='device' else '0')
if c.startswith('ls -1 '):
    done(text='boot-0000000002' if (s['scenario']=='crash-loop' or s['scenario'].startswith('hvenc-')) and 'tail -2' not in c else 'boot-0000000001')
if c.startswith(('mkdir -p ', 'tar -C ', 'rm -f ')): done()
raise AssertionError(('unexpected fake command', a))
'''


class FormatPath(unittest.TestCase):
    def run_phone(self, scenario='boot', linger=4, system_case=None, format_override=None):
        with tempfile.TemporaryDirectory(prefix='b-try-format-') as tmp:
            r = Path(tmp)
            scripts = r/'scripts'
            deadline = scripts/'mainline/boot-deadline'
            acceptance = scripts/'mainline/acceptance'
            deadline.mkdir(parents=True)
            acceptance.mkdir()
            for source, target in (
                (ROOT/'scripts/mainline/boot-deadline/b-try.sh', deadline/'b-try.sh'),
                (ROOT/'scripts/mainline/acceptance/run.sh', acceptance/'run.sh'),
                (ROOT/'scripts/mainline/acceptance/crash-evidence.py', acceptance/'crash-evidence.py'),
            ):
                shutil.copyfile(source, target)
                target.chmod(0o755)
            qualification=scripts/'mainline/video/qualification'
            qualification.mkdir(parents=True)
            for name in ('extract.py','validation.py'):
                shutil.copyfile(ROOT/'scripts/mainline/video/qualification'/name,qualification/name)
            (deadline/'prepare-vendor.py').write_text(
                'import shutil, sys\nshutil.copyfile(sys.argv[2], sys.argv[3])\n')
            # Nothing sources the real environment, registry or lock code.
            (scripts/'a9-env.sh').write_text(
                'a9_usb_lock_inherit() { :; }\nexport ADB FB A9_SERIAL\n')
            if system_case is not None:
                # Production frozen checks use the actual environment helpers;
                # only the owner lock and wait clock are inert in this fixture.
                (scripts/'a9-env.sh').write_text(
                    f'. "{ROOT}/scripts/a9-env.sh"\n'
                    'a9_usb_lock_inherit() { :; }\n'
                    'a9_wait_fastboot() { [ "$(a9_fbvar is-userspace)" = "$1" ]; }\n')
                import importlib.util
                spec=importlib.util.spec_from_file_location('format_system_bundle',ROOT/'scripts/mainline/build-integrated.py')
                builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
                builder.bundle_system_delivery(scripts/'mainline')
            fake = r/'fake.py'
            fake.write_text(FAKE.replace('#!PYTHON', '#!'+sys.executable))
            fake.chmod(0o755)
            bin_dir = r/'bin'
            bin_dir.mkdir()
            for tool in ('adb', 'fastboot', 'lsusb', 'edl', 'timeout'):
                p = bin_dir/tool
                p.write_text(f'#!/bin/sh\nexec "{fake}" {tool} "$@"\n')
                p.chmod(0o755)
            hook = acceptance/'onb.py'
            hook.write_text(f'#!/bin/sh\nexec "{fake}" hook\n')
            hook.chmod(0o755)
            # Keep the production runner unmodified, including its SECONDS
            # arithmetic. Only sleep is virtualized in this child bash.
            bash_env = r/'bash-env'
            bash_env.write_text('''unset SECONDS
SECONDS=0
sleep() {
  local now
  read -r now < "$FAKE_ROOT/clock"
  now=$((now+$1))
  printf '%s\\n' "$now" > "$FAKE_ROOT/clock"
  SECONDS=$now
}
''')
            (r/'clock').write_text('0\n')
            image = r/'image'
            image.mkdir()
            boot = bytearray(4096)
            boot[:8] = b'ANDROID!'
            cmd = b'a9_deadline=300'
            boot[64:64+len(cmd)] = cmd
            (image/'boot.img').write_bytes(boot)
            (image/'vendor.img').write_bytes(b'fake vendor\n')
            if system_case is not None:
                import hashlib,zipfile
                tree=r/'system-tree';(tree/'system/etc/selinux').mkdir(parents=True)
                (tree/'system/framework').mkdir()
                (r/'phone-files').mkdir()
                def cert_files(cert,directory):
                    with zipfile.ZipFile(directory/'framework-res.apk','w') as z:z.writestr('META-INF/CERT.RSA',cert)
                    (directory/'plat_mac_permissions.xml').write_text('<policy><signer signature="'+cert.hex()+'"><seinfo value="platform"/></signer></policy>')
                cert_files(b'synthetic-platform-cert',r/'phone-files')
                cert_files(b'other-platform-cert' if system_case=='key-mismatch' else b'synthetic-platform-cert',tree/'system/framework')
                shutil.move(tree/'system/framework/plat_mac_permissions.xml',tree/'system/etc/selinux/plat_mac_permissions.xml')
                (tree/'system/build.prop').write_text('ro.build.version.sdk_full=36.1\nro.build.version.sdk=36\nro.build.version.security_patch=2026-05-01\nro.build.fingerprint=fake\n')
                subprocess.run(['mke2fs','-q','-t','ext4','-d',tree,image/'system.img','16M'],check=True,capture_output=True)
                digest=hashlib.sha256((image/'system.img').read_bytes()).hexdigest()
                (image/'integrated-build.json').write_text(json.dumps({'sha256': {'system.img': digest}}))
                (image/'system.img.sha256').write_text(('0'*64 if system_case=='sha-mismatch' else digest)+'  system.img\n')
            (r/'state.json').write_text(json.dumps(dict(
                mode='device', slot='b' if system_case=='initial-b' else 'a', scenario=scenario, linger=linger, system_case=system_case,
                boot_after=108 if system_case is None else 16)))
            # No inherited A9 configuration or hook can reach a real phone.
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('A9_', 'ANDROID_', 'BASH_', 'BASH_FUNC_'))}
            env.update(A9_HOME=str(r), A9_SERIAL='fake-only',
                       ADB=str(bin_dir/'adb'), FB=str(bin_dir/'fastboot'),
                       EDL=str(bin_dir/'edl'), A9_BTRY_FORMAT='1',
                       A9_BTRY_HOLD='12', A9_BTRY_LOGS=str(r/'logs'),
                       A9_SWEEP_HVENC_QUALIFY='1' if scenario.startswith('hvenc-') else '0',
                       BASH_ENV=str(bash_env), FAKE_ROOT=str(r),
                       PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
            if system_case is not None:
                env['A9_BTRY_SYSTEM']='/stale/other-system.img'  # launcher must bind the manifest
                if system_case=='ram':env['A9_BTRY_FORMAT']='0'
                if format_override is not None:env['A9_BTRY_FORMAT']=format_override
            p = subprocess.run(['bash', str(acceptance/'run.sh'), str(image)],
                               env=env, capture_output=True, text=True, timeout=180)
            calls = [json.loads(x) for x in (r/'calls.jsonl').read_text().splitlines()]
            state = json.loads((r/'state.json').read_text())
            if system_case is not None:
                state['system_check']=(r/'logs/system-check.log').read_text()
                state['system_verified']=(r/'logs/system-delivery.log').exists()
                if system_case not in ('pass','ram') or format_override is not None:
                    return p,state,calls
            self.assertEqual(bool(state.get('formatted')),system_case!='ram',p.stdout+p.stderr)
            self.assertEqual((state['slot'], state['mode']), ('a', 'device'))
            self.assertEqual(sum(c[1:] == ['adb', 'shell', 'twrp', 'format', 'data']
                                 for c in calls),0 if system_case=='ram' else 1)
            self.assertFalse(any(c[1] == 'edl' for c in calls))
            self.assertTrue((r/'logs/mainline.tar').is_file(), p.stdout+p.stderr)
            state['probe_log'] = (r/'logs/boot-probe.log').read_text()
            if scenario.startswith('hvenc-'):
                state['hvenc_boot1']=json.loads((r/'logs/boot-0000000001/hvenc-diagnostics.json').read_text())
                state['hvenc_boot2']=json.loads((r/'logs/boot-0000000002/hvenc-diagnostics.json').read_text())
                state['hvenc_host_copied']=(r/'logs/boot-0000000002/hvenc-qualification.json').exists()
            return p, state, calls

    def test_format_lingering_fastboot_slow_boot_and_hook(self):
        p, state, calls = self.run_phone('boot-noisy')
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertIn('PASS boot_completed=1 slot_A=1', p.stdout)
        self.assertTrue(state.get('hook'))
        hook = next(c for c in calls if c[1] == 'hook')
        self.assertGreaterEqual(hook[0]-state['b_start'], 108)
        self.assertIn('B observation:', p.stderr)
        self.assertIn('phase=observe_b rc=124 timeout=20', state['probe_log'])
        self.assertIn('records=1 valid=1', state['probe_log'])
        self.assertTrue(any(c[1] == 'timeout' and '20' in c[2:] for c in calls))

    def test_saved_system_server_loop_overrides_boot_success(self):
        p,state,calls=self.run_phone('crash-loop')
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('system_server_fatals=2 system_server_crash_loop=1',p.stdout)
        self.assertIn('HANG boot_completed=1 slot_A=1 detail=system_server_crash_loop',p.stdout)

    def test_hvenc_saved_boots_qualify_only_final_native_completion(self):
        p,state,calls=self.run_phone('hvenc-pass')
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(state['hvenc_boot1']['probe_seen'],0)
        self.assertEqual(state['hvenc_boot2']['native_pass'],1)
        self.assertEqual(state['hvenc_boot2']['diagnostics_ok'],1)
        self.assertTrue(state['hvenc_host_copied'])
        p,state,calls=self.run_phone('hvenc-partial')
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertEqual(state['hvenc_boot2']['native_pass'],0)
        self.assertIn('detail=hvenc_qualification_diagnostics',p.stdout)

    def test_format_direct_disconnect_slow_boot(self):
        p, state, calls = self.run_phone(linger=0)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertIn('PASS boot_completed=1 slot_A=1', p.stdout)

    def test_format_real_fastboot_return_still_fails(self):
        p, state, calls = self.run_phone('return-fastboot')
        self.assertEqual(p.returncode, 1, p.stdout+p.stderr)
        self.assertIn('HANG boot_completed=0 slot_A=1 detail=returned_fastboot', p.stdout)
        self.assertIn('B observation: result=HANG boot_completed=0 detail=returned_fastboot', p.stderr)
        self.assertFalse(state.get('hook'))
        return_a = next(c for c in calls if c[1:] == ['fastboot', 'set_active', 'a'])
        self.assertLess(return_a[0]-state['b_start'], 60)

    def test_format_incomplete_boot_retains_full_deadline(self):
        p, state, calls = self.run_phone('incomplete')
        self.assertEqual(p.returncode, 1, p.stdout+p.stderr)
        self.assertIn('HANG boot_completed=0 slot_A=1 detail=B_boot_incomplete', p.stdout)
        self.assertFalse(state.get('hook'))
        self.assertIn('rc=124', state['probe_log'])
        self.assertIn('records=2 valid=0', state['probe_log'])
        self.assertIn('records=1 valid=0', state['probe_log'])
        return_a = next(c for c in calls if c[1:] == ['fastboot', 'set_active', 'a'])
        self.assertGreaterEqual(return_a[0]-state['b_start'], 420)

    def test_format_fastboot_never_departs_is_bounded(self):
        p, state, calls = self.run_phone('stuck-fastboot')
        self.assertEqual(p.returncode, 1, p.stdout+p.stderr)
        self.assertIn('HANG boot_completed=0 slot_A=1 detail=returned_fastboot', p.stdout)
        self.assertFalse(state.get('hook'))
        return_a = next(c for c in calls if c[1:] == ['fastboot', 'set_active', 'a'])
        self.assertGreaterEqual(return_a[0]-state['b_start'], 15)
        self.assertLess(return_a[0]-state['b_start'], 30)


if __name__ == '__main__':
    unittest.main()
