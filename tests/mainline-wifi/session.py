#!/usr/bin/env python3
"""Offline checks of the integrated Wi-Fi session payload: the stager and the boot-time starter.

Synthetic stage and fixture commands only; the starter runs against fixture paths with a FAKE
controller (the real controller's session stages are tested in coordinator.py). No device,
no adb/fastboot/edl, no partition, nothing under ~/a9-assets.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SH = os.environ.get('A9_WIFI_TEST_SH') or shutil.which('sh')   # e.g. the pinned mksh the phone uses
REPO = Path(__file__).resolve().parents[2]
WIFI = REPO / 'scripts/mainline/wifi'
SESSION = WIFI / 'session'
spec = importlib.util.spec_from_file_location('a9_stage_session', SESSION / 'stage-session.py')
stage = importlib.util.module_from_spec(spec); spec.loader.exec_module(stage)
START = 'I_AUTHORIZE_ONE_MPSS_START'
RISK = 'I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF'
ID = 'ad' * 32
PRE = 'b1' * 32


def sha(b): return hashlib.sha256(b).hexdigest()


class Stager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='a9-session-tools-')
        cls.tools = Path(cls.tmp.name) / 'tools'
        subprocess.run(['python3', '-I', str(WIFI / 'stage-tools.py'), '--out', str(cls.tools)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix='a9-session-stage-'); self.addCleanup(self.t.cleanup)
        self.stage = Path(self.t.name) / 'stage'
        (self.stage / 'etc/a9-wifi').mkdir(parents=True); (self.stage / 'etc/wifi').mkdir(); (self.stage / 'etc/selinux').mkdir(parents=True)
        (self.stage / 'build.prop').write_text('ro.a9ml.wifi=1\n')
        (self.stage / 'etc/a9-wifi/android-bundle.json').write_text('{}\n')
        shutil.copy(WIFI / 'android/config/wpa_supplicant_overlay.conf', self.stage / 'etc/wifi/wpa_supplicant_overlay.conf')
        (self.stage / 'etc/selinux/vendor_file_contexts').write_text('# base\n')

    def run_stager(self, *extra, tools=None, inert=False):
        args = ['python3', '-I', str(SESSION / 'stage-session.py'), '--stage', str(self.stage), '--tools', str(tools or self.tools),
                '--seconds', '3600', '--identity-sha256', ID, '--preliminary-sha256', PRE]
        args += ['--inert'] if inert else ['--authorize', START, '--accept-risk', RISK]
        if '--country' not in extra: args += ['--country', 'US']
        return subprocess.run(args + list(extra), capture_output=True, text=True)

    def test_authorised_stage_carries_pinned_tools_conf_labels_and_supplicant_policy(self):
        r = self.run_stager('--country', 'US'); self.assertEqual(r.returncode, 0, r.stderr)
        s = self.stage
        conf = (s / 'etc/a9-wifi/session/session.conf').read_text()
        self.assertIn(f'ack_start={START}', conf); self.assertIn(f'identity_sha256={ID}', conf)
        self.assertEqual((s / 'etc/a9-wifi/session/country').read_text(), 'US\n')
        self.assertEqual((s / 'etc/a9-wifi/session/tools/coordinator-target.sh').read_bytes(), (WIFI / 'coordinator-target.sh').read_bytes())
        sums = (s / 'etc/a9-wifi/session/tools/SHA256SUMS').read_text()
        for name in stage.TOOL_FILES:
            self.assertIn(sha((s / 'etc/a9-wifi/session/tools' / name).read_bytes()) + '  ' + name, sums)
        self.assertIn('ro.a9ml.wifi.session=1', (s / 'build.prop').read_text())
        self.assertIn('a9_wifi_radio_exec', (s / 'etc/selinux/vendor_file_contexts').read_text())
        overlay = (s / 'etc/wifi/wpa_supplicant_overlay.conf').read_text()
        self.assertIn('wowlan_triggers=disconnect magic_pkt', overlay)   # the A6L suspend mitigation is kept
        self.assertIn('p2p_disabled=1', overlay); self.assertIn('country=US', overlay)
        freqs = [int(x) for x in [l for l in overlay.splitlines() if l.startswith('freq_list=')][0][10:].split()]
        self.assertTrue(all(f < 2473 or f in (5180, 5200, 5220, 5240, 5745, 5765, 5785, 5805, 5825) for f in freqs))  # no DFS, no ch 12-14
        self.assertEqual(oct((s / 'bin/a9ml-wifi-session.sh').stat().st_mode & 0o777), '0o755')
        self.assertEqual(oct((s / 'etc/a9-wifi/session/session.conf').stat().st_mode & 0o777), '0o400')

    def test_country_is_mandatory_and_inert_stage_can_never_start(self):
        args = ['python3', '-I', str(SESSION / 'stage-session.py'), '--stage', str(self.stage), '--tools', str(self.tools),
                '--seconds', '3600', '--identity-sha256', ID, '--preliminary-sha256', PRE, '--inert']
        self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)   # no country: refused
        self.assertFalse((self.stage / 'etc/a9-wifi/session').exists())
        r = self.run_stager(inert=True); self.assertEqual(r.returncode, 0, r.stderr)
        conf = (self.stage / 'etc/a9-wifi/session/session.conf').read_text()
        self.assertIn('ack_start=' + stage.INERT, conf); self.assertNotIn(START, conf); self.assertNotIn(RISK, conf)
        self.assertEqual((self.stage / 'etc/a9-wifi/session/country').read_text(), 'US\n')

    def test_acknowledgements_must_be_named_exactly_and_inert_takes_none(self):
        for extra in (['--authorize', 'yes', '--accept-risk', RISK], ['--authorize', START]):
            args = ['python3', '-I', str(SESSION / 'stage-session.py'), '--stage', str(self.stage), '--tools', str(self.tools),
                    '--seconds', '3600', '--identity-sha256', ID, '--preliminary-sha256', PRE] + extra
            self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)
        r = self.run_stager('--authorize', START, inert=True); self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.stage / 'etc/a9-wifi/session').exists())

    def test_bad_arguments_are_refused_before_any_write(self):
        for extra in (['--country', 'fr'], ['--country', 'FRA'], ['--country', ''], ['--seconds', '599'], ['--seconds', '14401']):
            r = self.run_stager(*extra); self.assertNotEqual(r.returncode, 0, extra)
            self.assertFalse((self.stage / 'etc/a9-wifi/session').exists())
        bad = subprocess.run(['python3', '-I', str(SESSION / 'stage-session.py'), '--stage', str(self.stage), '--tools', str(self.tools),
                              '--seconds', '3600', '--identity-sha256', 'AD' * 32, '--preliminary-sha256', PRE, '--inert'], capture_output=True)
        self.assertNotEqual(bad.returncode, 0)

    def test_second_stage_overwrite_symlink_and_unsafe_stage_are_refused(self):
        self.assertEqual(self.run_stager().returncode, 0)
        before = (self.stage / 'build.prop').read_text()
        self.assertNotEqual(self.run_stager().returncode, 0)
        self.assertEqual((self.stage / 'build.prop').read_text(), before)
        link = Path(self.t.name) / 'link'; link.symlink_to(self.stage)
        r = subprocess.run(['python3', '-I', str(SESSION / 'stage-session.py'), '--stage', str(link), '--tools', str(self.tools), '--seconds', '3600',
                            '--identity-sha256', ID, '--preliminary-sha256', PRE, '--inert'], capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        r = subprocess.run(['python3', '-I', str(SESSION / 'stage-session.py'), '--stage', str(REPO / 'scripts'), '--tools', str(self.tools), '--seconds', '3600',
                            '--identity-sha256', ID, '--preliminary-sha256', PRE, '--inert'], capture_output=True)
        self.assertNotEqual(r.returncode, 0)

    def test_requires_an_android_stage_and_the_checkouts_own_controller_and_pinned_tools(self):
        (self.stage / 'etc/a9-wifi/android-bundle.json').unlink()
        self.assertNotEqual(self.run_stager().returncode, 0)
        (self.stage / 'etc/a9-wifi/android-bundle.json').write_text('{}\n')
        for victim, edit in (('coordinator-target.sh', b'# edited\n'), ('strace', b'x'), ('SHA256SUMS', b'')):
            copy = Path(self.t.name) / ('tools-' + victim); shutil.copytree(self.tools, copy)
            p = copy / victim; p.chmod(0o600)
            p.write_bytes(p.read_bytes() + edit if edit else b'')
            r = self.run_stager(tools=copy)
            self.assertNotEqual(r.returncode, 0, victim)
            self.assertFalse((self.stage / 'etc/a9-wifi/session').exists(), victim)
        extra = Path(self.t.name) / 'tools-extra'; shutil.copytree(self.tools, extra); (extra / 'unexpected').write_text('x')
        self.assertNotEqual(self.run_stager(tools=extra).returncode, 0)


class Starter(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix='a9-session-start-'); self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        (self.root / 'mnt/a9-wifi').mkdir(parents=True)
        (self.root / 'mnt/a9-wifi').chmod(0o700)
        self.conf = self.root / 'vendor/etc/a9-wifi/session'; (self.conf / 'tools').mkdir(parents=True)
        self.log = self.root / 'log'
        # fake controller records every invocation; prepare/run results come from env
        ctl = '''#!%s
echo "$*" >> "%s/calls"
case "$1" in
 prepare) exit ${FAKE_PREPARE_RC:-0};;
 run) echo "A9_WIFI_SESSION serving"; exit ${FAKE_RUN_RC:-0};;
esac
''' % (shutil.which('sh'), self.root)
        files = {'strace': b'strace', 'iw': b'iw', 'audit-opens.awk': b'awk', 'coordinator-target.sh': ctl.encode(), 'coordinator-tools.lock.json': b'{}'}
        for n, d in files.items(): (self.conf / 'tools' / n).write_bytes(d)
        (self.conf / 'tools/SHA256SUMS').write_text(''.join(f'{sha(d)}  {n}\n' for n, d in sorted(files.items())))
        (self.conf / 'country').write_text('US\n')
        self.write_conf()
        text = (SESSION / 'a9ml-wifi-session.sh').read_text()
        for old, new in (('/vendor/etc/a9-wifi/session', str(self.conf)), ('/mnt/a9-wifi', str(self.root / 'mnt/a9-wifi')),
                         ('/dev/kmsg', str(self.root / 'kmsg')), ('/system/bin/sh', shutil.which('sh')), ('/proc/uptime', str(self.root / 'uptime'))):
            text = text.replace(old, new)
        fake = '''
id() { echo 0; }
uname() { echo 7.2.fixture; }
getprop() {
  case "$1" in
    ro.a9ml.vendor|ro.a9ml.wifi|ro.a9ml.wifi.android) echo 1;;
    ro.a9ml.wifi.session) echo ${FAKE_SESSION_PROP-1};;
    ro.boot.slot_suffix) echo ${FAKE_SLOT-_b};;
    vendor.a9ml.wifi.session.stop|vendor.a9ml.wifi.session.state) command cat "%s/prop-$1" 2>/dev/null || :;;
  esac
}
setprop() { echo "$1=$2" >> "%s/props"; echo "$2" > "%s/prop-$1"; }
stat() { case "$*" in '-c %%u:%%a '*) echo 0:700;; *) command stat "$@";; esac; }
mount() { echo "mount $*" >> "%s/mounts"; }
''' % ((str(self.root),) * 4)
        text = text.replace('set -u\n', 'set -u\n' + fake, 1)
        self.script = self.root / 'a9ml-wifi-session.sh'; self.script.write_text(text)
        (self.root / 'uptime').write_text('100.0 0.0\n')

    def write_conf(self, **over):
        v = dict(seconds='3600', identity_sha256=ID, preliminary_sha256=PRE, ack_start=START, ack_risk=RISK); v.update(over)
        (self.conf / 'session.conf').write_text('# test\n' + ''.join(f'{k}={x}\n' for k, x in v.items()))

    def start(self, **env):
        return subprocess.run([SH, str(self.script), 'start'], env=os.environ | {k: str(x) for k, x in env.items()},
                              capture_output=True, text=True, timeout=30)

    def calls(self): return (self.root / 'calls').read_text().splitlines() if (self.root / 'calls').exists() else []
    def props(self): return (self.root / 'props').read_text().splitlines() if (self.root / 'props').exists() else []

    def test_authorised_start_runs_prepare_then_exactly_one_run_with_the_reviewed_arguments(self):
        r = self.start(); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.calls(); self.assertEqual(len(calls), 2, calls)
        self.assertTrue(calls[0].startswith('prepare '))
        self.assertEqual(calls[0].split()[1:], [sha(b'strace'), sha(b'iw'), sha(b'awk')])
        self.assertIn('run ' + START + ' ' + RISK + ' ' + PRE + ' --session 3600 --identity-reviewed ' + ID, calls[1])
        self.assertIn('vendor.a9ml.wifi.session.state=ended', self.props())
        mounts = (self.root / 'mounts').read_text()
        self.assertIn('-t tmpfs', mounts); self.assertNotIn('noexec', mounts)   # the tools mount must be executable

    def test_inert_or_incomplete_image_never_reaches_the_controller(self):
        for over in (dict(ack_start=stage.INERT, ack_risk=stage.INERT), dict(ack_risk='wrong'), dict(seconds='599'), dict(seconds='x'),
                     dict(identity_sha256='z' * 64), dict(preliminary_sha256='a' * 10)):
            with self.subTest(over=over):
                shutil.rmtree(self.root / 'mnt/a9-wifi/session', ignore_errors=True)
                (self.root / 'calls').unlink(missing_ok=True)
                self.write_conf(**over)
                r = self.start(); self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual(self.calls(), [])
                self.assertIn('vendor.a9ml.wifi.session.state=failed', self.props())

    def test_unknown_config_key_missing_tool_or_changed_tool_stops_before_prepare(self):
        (self.conf / 'session.conf').write_text((self.conf / 'session.conf').read_text() + 'extra=1\n')
        self.assertEqual(self.start().returncode, 1); self.assertEqual(self.calls(), [])
        self.write_conf(); shutil.rmtree(self.root / 'mnt/a9-wifi/session')
        (self.conf / 'tools/iw').write_bytes(b'tampered')
        self.assertEqual(self.start().returncode, 1); self.assertEqual(self.calls(), [])
        (self.conf / 'tools/iw').unlink(); shutil.rmtree(self.root / 'mnt/a9-wifi/session', ignore_errors=True)
        shutil.rmtree(self.root / 'mnt/a9-wifi/tools', ignore_errors=True)
        self.assertEqual(self.start().returncode, 1); self.assertEqual(self.calls(), [])

    def test_missing_or_malformed_country_stops_before_prepare(self):
        for content in (None, 'us\n', 'USA\n', 'US X\n'):
            with self.subTest(content=content):
                shutil.rmtree(self.root / 'mnt/a9-wifi/session', ignore_errors=True)
                (self.conf / 'country').unlink(missing_ok=True)
                if content is not None: (self.conf / 'country').write_text(content)
                self.assertEqual(self.start().returncode, 1); self.assertEqual(self.calls(), [])

    def test_wrong_environment_is_refused(self):
        for env in (dict(FAKE_SESSION_PROP=0), dict(FAKE_SLOT='_a')):
            with self.subTest(env=env):
                shutil.rmtree(self.root / 'mnt/a9-wifi/session', ignore_errors=True)
                self.assertEqual(self.start(**env).returncode, 1); self.assertEqual(self.calls(), [])

    def test_prepare_failure_means_no_run_and_no_retry_second_attempt_is_refused(self):
        r = self.start(FAKE_PREPARE_RC=3); self.assertEqual(r.returncode, 1)
        self.assertEqual([c.split()[0] for c in self.calls()], ['prepare'])
        self.assertIn('vendor.a9ml.wifi.session.state=failed', self.props())
        r = self.start(); self.assertEqual(r.returncode, 1)               # one attempt per boot
        self.assertEqual([c.split()[0] for c in self.calls()], ['prepare'])

    def test_failed_session_is_reported_failed_and_never_restarted(self):
        r = self.start(FAKE_RUN_RC=1); self.assertEqual(r.returncode, 1)
        self.assertEqual([c.split()[0] for c in self.calls()], ['prepare', 'run'])
        self.assertIn('vendor.a9ml.wifi.session.state=failed', self.props())
        self.assertNotIn('vendor.a9ml.wifi.session.state=ended', self.props())
        self.assertEqual(self.start().returncode, 1)
        self.assertEqual(len(self.calls()), 2)

    def test_init_rc_is_oneshot_disabled_critical_and_has_no_restart_or_persistence(self):
        rc = (SESSION / 'init.a9ml-wifi-session.rc').read_text()
        svc = rc.split('service a9ml_wifi_session', 1)[1]
        for word in ('disabled', 'oneshot', 'shutdown critical', 'seclabel u:r:a9_wifi_radio:s0'):
            self.assertIn(word, svc)
        self.assertNotIn('restart', svc.replace('Never', ''))
        self.assertNotIn('persist.', rc.replace('# ', '').replace('no property\n# persistence', ''))
        self.assertIn('sys.boot_completed=1 && property:ro.a9ml.wifi.session=1', rc)
        self.assertEqual(rc.count('\n    start a9ml_wifi_session\n'), 1)
        stop = rc.split('service a9ml_wifi_session_stop', 1)[1].split('service a9ml_wifi_session ', 1)[0]
        for word in ('disabled', 'oneshot', 'timeout_period 90', 'a9ml-wifi-session.sh stop'): self.assertIn(word, stop)
        self.assertIn('on shutdown\n    setprop vendor.a9ml.wifi.session.stop 1\n    exec_start a9ml_wifi_session_stop', rc)

    def test_script_names_no_partition_and_opens_no_block_device(self):
        text = (SESSION / 'a9ml-wifi-session.sh').read_text()
        for word in ('/dev/block', 'by-name', 'modemst', 'fsg', 'persist', 'devinfo', 'userdata', 'dd if=', 'sys/class/remoteproc'):
            self.assertNotIn(word, text)


class Verifier(unittest.TestCase):
    """host/session-verify.sh against a FAKE adb (explicit ADB path; the real adb is never run)."""
    MAC = 'aa:bb:cc:dd:ee:01'

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix='a9-session-verify-'); self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        fake = self.root / 'fake-adb'
        body = r"""#!@SH@
case "$1" in wait-for-device) exit 0;; shell) shift;; *) exit 1;; esac
cmd="$*"
payload=; case "$cmd" in "timeout 30 nc 127.0.0.1 2324") payload=$(cat);; esac
case "$cmd$payload" in
 *"getprop sys.boot_completed"*) echo 1;;
 "uname -r") echo 7.2.10;;
 *"getprop vendor.a9ml.wifi.session.state"*) echo ${FAKE_STATE:-serving};;
 *remoteproc*) echo "modem running recovery=disabled";;
 *dmesg*) echo "ath10k_snoc c800000.wifi: wlan0 mac @MAC@"; echo "a9wifi-session serving"; echo 'ssid SECRETNET leaked';;
 *run.out*) echo "A9_WIFI_SESSION serving";;
 *"iw reg get"*) echo global; echo "country 00: DFS-UNSET"; echo "addr @MAC@";;
 *"init.svc"*) echo running; echo running; echo stopped; echo running;;
 *"set-wifi-enabled"*) :;;
 "cmd wifi status") echo "Wifi is enabled";;
 *start-scan*) :;;
 *list-scan-results*) printf '@MAC@  2412  -50  [WPA2]  SECRETNET\n@MAC@  5180  -60  [WPA2]  OTHERNET\n';;
 *"dumpsys wifi"*) echo "Wi-Fi is enabled"; echo "owner mac @MAC@";;
esac
"""
        fake.write_text(body.replace('@SH@', shutil.which('sh')).replace('@MAC@', self.MAC))
        fake.chmod(0o700); self.fake = fake

    def run_verify(self, *extra, **env):
        out = self.root / 'out'
        r = subprocess.run(['bash', str(WIFI / 'host/session-verify.sh'), str(out), *extra],
                           env=os.environ | {'ADB': str(self.fake)} | env, capture_output=True, text=True, timeout=120)
        return r, out

    def test_pass_path_enable_never_leaks_a_mac_or_an_ssid(self):
        r, out = self.run_verify('--enable', '--wait', '30')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('S1 VERIFY PASS', (out / 'verdict.txt').read_text())
        for f in out.iterdir():
            text = f.read_text()
            self.assertNotIn(self.MAC, text, f.name); self.assertNotIn('SECRETNET', text, f.name); self.assertNotIn('OTHERNET', text, f.name)
        self.assertEqual(sorted(l.split()[1] for l in (out / 'scan-freq-histogram.txt').read_text().splitlines()), ['2412', '5180'])
        self.assertIn('scan results: 2 rows', (out / 'verdict.txt').read_text())

    def test_not_serving_fails_and_without_enable_it_never_touches_wifi_settings(self):
        r, out = self.run_verify('--wait', '30', FAKE_STATE='failed')
        self.assertEqual(r.returncode, 1)
        self.assertIn('S1 VERIFY FAIL', (out / 'verdict.txt').read_text())
        self.assertNotIn('set-wifi-enabled', (WIFI / 'host/session-verify.sh').read_text().split('if [ "$enable" = 1 ]')[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
