#!/usr/bin/env python3
"""Five host-only groups. All transport/credential/clock operations are fakes."""
import contextlib
import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sweep', HERE/'onb.py')
sweep = importlib.util.module_from_spec(spec); spec.loader.exec_module(sweep)

class Clock:
    def __init__(self): self.now = 1000.
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += max(seconds, .001)

class Fake(sweep.Sweep):
    def __init__(self):
        super().__init__(); self.adb = 'FAKE_ADB'; self.calls = []; self.rebooted = False
        self.installed = False; self.tapped = False; self.bt = False; self.secret_inputs = []
        self.signed_out = False; self.policy_domains = ''; self.rotate = False; self.no_hardware = False
        self.existing_network = False; self.wifi_on = False; self.usb_mtp = False
        self.no_mtp = False; self.bad_mute = False; self.dialog = False; self.bt_signature = 'a'*64
        self.ram_data = False; self.broken_health = False; self.wifi_not_ready = False
        self.no_hevc = False
        image = Path(self.out)/'image'; probe = image/'encoder-probes/codec2-encode'
        if not probe.exists():
            probe.parent.mkdir(parents=True); probe.write_bytes(b'INERT HOST FIXTURE')
            (image/'encoder-stage.json').write_text(json.dumps({'probe_sha256': hashlib.sha256(probe.read_bytes()).hexdigest()}))
        os.environ['A9_SWEEP_IMAGE'] = str(image)
    def run(self, argv, data=None, timeout=20, env=None):
        argv = list(map(str, argv)); self.calls.append(argv)
        assert all('SECRET_TEST' not in a for a in argv)
        if len(argv) == 4 and argv[1] == 'push' and argv[2].endswith('/encoder-probes/codec2-encode'): return 0, ''
        if argv[1:] == ['reboot']: self.rebooted = True; return 0, ''
        if argv[1:] == ['get-state']: return 0, 'device\n'
        if argv[0].endswith('/permissive'): return 0, self.policy_domains
        if argv[0].endswith('mtp-session'): return (1, 'MTP_SESSION 0\n') if self.no_mtp else (0, 'MTP_SESSION 1\nMTP_RESETS_REFUSED 1\n')
        raise AssertionError(argv)
    def native(self, source, timeout=78):
        self.calls.append(['NATIVE', source]); assert 'SECRET_TEST' not in source
        if source == sweep.VULKAN_KERNEL: return 0, 'kernel_lines 100\ngpu_faults 0\niommu_faults 0\n'
        if source == sweep.ANR_TRACES: return 0, 'trace_files 0\ntrace_errors 0\ntrace_truncated 0\n'
        if source == sweep.SNAPSHOT:
            d = dict(tombstones=0, anrs=0, tombstones_signature=2 if self.rotate else 1, anrs_signature=1,
                     avcs=4, permissive_avcs=0)
            return 0, ''.join(f'{k} {v}\n' for k,v in d.items())
        if source == sweep.FP_NO_DUMPS: return 0, 'completed 1\nfiles 0\n'
        if source in (sweep.NATIVE, sweep.HEALTH):
            return 0, ('thermal_0_mc 32000\nthermal_zones 1\nice_wrapped 1\nwrapped_68 2\nnative_crypto 1\n'
                       f'data_f2fs {int(not self.ram_data)}\ndata_mounts 1\ndata_tmpfs {int(self.ram_data)}\n'
                       f'real_data_required 1\ndata_ready {int(not self.ram_data)}\ndata_rc {25 if self.ram_data else 0}\n'
                       f'sensor_ready {int(not self.broken_health)}\naudio_ready {int(not self.broken_health)}\n')
        if source == sweep.INPUTS: return 0, 'devices 5\ntouch 1\npower 1\nvolume 2\n'
        if source == sweep.WLAN_DEVICE: return 0, 'wlan_present 1\n'
        if source == 'printf "native_ready 1\\n"': return 0, 'native_ready 1\n'
        if source == sweep.play.EINK_CLOSE: return 0, 'EINK_CLOSED\n'
        if source == 'base64 /sys/fs/selinux/policy': return 0, 'ZmFrZQ==\n'
        if source == sweep.play.GSF: return 0, 'GSF_LEN 16\n'
        if source in (sweep.play.START, sweep.play.CAP): return 0, 'READY\n'
        raise AssertionError(source)
    def shell(self, source, data=None, timeout=20, capture_stderr=False, capture_partial=False):
        if source.startswith('set -o pipefail; timeout 5 dumpsys activity lastanr'): return 0, ''
        if source == 'mktemp -d /data/local/tmp/a9-encoder.XXXXXX': return 0, '/data/local/tmp/a9-encoder.ABCDEF\n'
        if source.startswith('chmod 700 /data/local/tmp/a9-encoder.'): return 0, ''
        if source.startswith('rm -r -- /data/local/tmp/a9-encoder.'): return 0, ''
        if source.startswith('timeout -k 2 30 /data/local/tmp/a9-encoder.'):
            self.calls.append(['ENCODER', source])
            hevc = ' hevc ' in source; number = 2 if hevc else 1
            failed = self.no_hardware or (hevc and self.no_hevc)
            return (1 if failed else 0), (f'ENCODE_STAGE codec={number} stage=4\n'
                f'ENCODE codec={number} stage=4 status={-2 if failed else 0} inputs=30 outputs=30 bytes=999 eos={0 if failed else 1}\n'
                'PRIVATE_TEST error detail\n')
        if source == sweep.USB_LOG_COUNTS:
            return 0, ''.join(k+' 0\n' for k in ('usb_descriptor_timeouts','usb_bind_errors','usb_apply_errors','usb_mtp_control_errors','usb_default_fallbacks','usb_mtp_service_errors'))
        self.calls.append(['SHELL', source]); assert 'SECRET_TEST' not in source
        if source == sweep.play.CONNECT:
            self.secret_inputs.append(data); assert data == b'SECRET_TEST_SSID\nSECRET_TEST_PASSWORD\n'; return 0, ''
        if 'cmd media_session volume' in source: raise AssertionError('obsolete volume command')
        if source == sweep.EVENTS: return 0, 'crash_events 0\n'
        if 'getprop vendor.a9ml.wifi.session.state' in source and 'getprop vendor.a9ml.wifi.android.ready' in source:
            return (1, '') if self.wifi_not_ready else (0, '')
        if source == 'getprop vendor.a9.sensors.ready; getprop vendor.a9.sensors.state': return 0, '1\nready\n'
        if source == sweep.play.BLUETOOTH_STATE:
            return 0, f'BT_STATE {int(self.bt)}\nBT_FRAMEWORK_CRASHES 0\nBT_LOG_SIGNATURE {self.bt_signature}\n'
        if source.startswith('cmd bluetooth_manager enable'): self.bt = True; return 0, ''
        if source.startswith('cmd bluetooth_manager disable'): self.bt = False; return 0, ''
        if source == 'dumpsys window windows 2>/dev/null':
            return 0, 'mCurrentFocus=Window{Application Error: foo}' if self.dialog else 'mCurrentFocus=Window{Launcher}'
        if source == 'getprop sys.boot_completed; getprop ro.boot.slot_suffix': return 0, '1\n_b\n'
        if source == 'getprop ro.boot.slot_suffix; getprop sys.boot_completed': return 0, '_b\n1\n'
        if source.startswith('pidof surfaceflinger;'): return 0, '123\nrunning\nDisplay 1 (HWC display 0)\nLauncher\n'
        if source.startswith('service check android.hardware.graphics.composer3'): return 0, 'Service android.hardware.graphics.composer3.IComposer/default: found\nService SurfaceFlinger: found\n'
        if source == 'getprop ro.crypto.state; getprop ro.crypto.type': return 0, 'encrypted\nfile\n'
        if source.startswith('dumpsys battery'): return 0, 'level: 83\nstatus: 2\nhealth: 2\nvoltage: 4000\ntemperature: 320\n'
        if source.startswith('getprop ro.build.type;'): return 0, 'user\nrelease-keys\n1\n1\n0\nuser\nrelease-keys\n'
        if source == 'svc usb getFunctions': raise AssertionError('getter result is on stderr')
        if source == 'svc usb getFunctions 2>&1': return 0, 'mtp\n' if self.usb_mtp else 'none\n'
        if source.startswith('umask 077; mkdir /data/local/tmp/a9-sweep-usb-'):
            self.usb_mtp = True; return 0, 'set_launch_ack 1\n'
        if source.startswith('cat /data/local/tmp/a9-sweep-usb-'):
            return 0, ('set_started 1\nset_completed 1\nset_command_rc 0\nset_exception 0\n'
                       'set_request_logged 1\nset_permission_denied 0\n')
        if source.startswith('rm -r -- /data/local/tmp/a9-sweep-usb-'): return 0, ''
        if source.startswith('svc usb setFunctions'): raise AssertionError('invalid framework USB request')
        if source == 'dumpsys usb':
            return 0, ('current_functions=[ADB, MTP]\ncurrent_functions_applied=true\nconnected=true\nconfigured=true\n'
                       if self.usb_mtp else 'current_functions=ADB\ncurrent_functions_applied=true\nconnected=true\nconfigured=true\n')
        if source == 'ip link show wlan0 >/dev/null 2>&1': return (0, '') if self.wifi_on else (1, '')
        if source.startswith('cmd wifi set-wifi-enabled enabled'): self.wifi_on = True; return 0, ''
        if source.startswith('cmd wifi set-wifi-enabled disabled'): self.wifi_on = False; return 0, ''
        if source.startswith('cmd wifi list-networks'):
            if not self.wifi_on: return 1, 'Wifi is disabled\n'
            return 0, 'Network Id  SSID  Security type\n4  SECRET_TEST_SSID  WPA2_PSK\n' if self.existing_network else 'No networks\n'
        if source.startswith('cmd wifi status'): return (255, '') if self.wifi_not_ready else (0, 'Wifi is connected to SECRET_TEST_SSID\n' if self.wifi_on else 'Wifi is disabled\n')
        if source.startswith('dumpsys connectivity'): return 0, 'NetworkAgentInfo{ network{100} WIFI CONNECTED Capabilities: INTERNET&VALIDATED }\n'
        if source == sweep.play.CHECKIN_REQUEST: return 0, 'Broadcast completed: result=0\n'
        if source == sweep.play.CHECKIN: return 0, 'CHECKIN 1\n'
        if source == 'getenforce': return 0, 'Enforcing\n'
        if source.startswith('cat /proc/sys/kernel/random/boot_id;'):
            return 0, ('new' if self.rebooted else 'old')+'\n_b\n'+('1\n' if 'sys.boot_completed' in source else '')
        if source.startswith('set -o pipefail; pm path '): return 0, '1000000\n'
        if source.startswith('pm path '):
            if self.tapped: self.installed = True
            return (0, 'package:/data/app/base.apk\n') if self.installed else (1, '')
        if source.startswith('pm list packages -i'): return 0, 'package:org.fossify.math installer=com.android.vending\n'
        if source.startswith('cmd package resolve-activity'): return 0, 'org.fossify.math/.MainActivity\n'
        if source == 'dumpsys activity activities': return 0, 'mResumedActivity: ActivityRecord{ org.fossify.math/.MainActivity }\n'
        if source.startswith('uiautomator dump'):
            label = 'Sign in' if self.signed_out else 'Install'
            return 0, f'<hierarchy><node package="com.android.vending" text="{label}" enabled="true" bounds="[0,0][100,100]" /></hierarchy>'
        if source.startswith('input tap'): self.tapped = True; return 0, ''
        if source.startswith(('am ', 'run-as ', 'input keyevent ', 'wm dismiss-keyguard ', 'cmd wifi ')): return 0, ''
        raise AssertionError(source)
    def image_context(self): self.put('00_image_context', True, boot_matches=1); return True
    def usb_descriptors(self): return 1, 1
    def prepare_assets(self):
        self.calls.append(['ASSETS']); self.assets_ok = True; return True
    def eink(self): self.eink_attempted = True; return True
    def app(self, package, activity, extras='', seconds=90):
        # Host fake emulates the numeric protocol, never loads/executes an APK.
        self.calls.append(['APP', package, extras])
        if 'mode mute' in extras:
            assert self.assets_ok
            return dict(completed=1, streams=[dict(stream=i, minimum=int(i == 4),
                        volume=(6 if i == 4 else 5) if self.bad_mute else int(i == 4))
                        for i in (1, 2, 3, 4, 5)])
        if 'mode health' in extras:
            return dict(completed=1, camera_count=int(not self.broken_health)*2, sensor_count=int(not self.broken_health)*4)
        if 'mode vulkan' in extras:
            return dict(completed=1,vk_load=1,vk_device=1,vk_a610=1,vk_turnip=1,vk_api=4194307,
                        vk_driver=18,vk_stage=8,vk_rc=0,vk_submitted=1,vk_fence=1,vk_words=64,vk_bad=0)
        if package == 'org.a9.cameraprobe': return dict(pass_=True, **{'pass': True}, jpeg_bytes=512, jpeg_width=640, jpeg_height=480, warmup_frames=20, ae_converged=True)
        if package == 'org.a9.videoprobe': return {'pass': True, 'decoded_frames': 60, 'drawn_frames': 60, 'output_eos': True, 'hardware_accelerated': True, 'software_only': False}
        if 'sensors' in extras: return dict(completed=1, listed=4, registered=4, window_ms=10000, sensors=[dict(type=t, events=10, nonmonotonic=0, wake=0) for t in (1,2,5,8)])
        if 'encoder' in extras:
            return dict(completed=1, hardware_available=0) if self.no_hardware else dict(completed=1, hardware_available=1, input_supported=1, inputs=30, outputs=30, bytes=999, eos=1)
        if 'mic' in extras: return dict(completed=1, window_ms=5000, samples=240000, nonzero=100, peak=42, rms=4, rate=48000)
        return dict(completed=1, available=1, duration_ms=10100, window_ms=10100, video_samples=150, video_bytes=9999, frames=151, af_available=0, torch_available=0, software_encoder=1, encoder_eos=1)

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'A9_BTRY_LOGS': self.tmp.name, 'A9_SWEEP_SECONDS': '3300', 'A9_SWEEP_PLAY_PACKAGE': 'org.fossify.math'}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.clock = Clock()
        for attr, value in [('monotonic', self.clock.monotonic), ('sleep', self.clock.sleep)]:
            p = patch.object(sweep.time, attr, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(sweep.play.Hook, 'credentials', lambda _: ['SECRET_TEST_SSID', 'SECRET_TEST_PASSWORD']); p.start(); self.addCleanup(p.stop)
        self.output = io.StringIO(); p = contextlib.redirect_stdout(self.output); p.__enter__(); self.addCleanup(p.__exit__, None, None, None)
    def test_1_context_refuses_before_transport(self):
        with patch.object(sweep.sp, 'run', side_effect=AssertionError('transport touched')):
            self.assertEqual(sweep.main(), 2)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
    def test_2_full_sweep_and_numeric_secret_free_report(self):
        s = Fake(); s.execute(); s.save()
        usb_position = next(i for i,c in enumerate(s.calls) if c[0].endswith('mtp-session'))
        encoder_position = next(i for i,c in enumerate(s.calls) if c[0] == 'ENCODER')
        self.assertLess(encoder_position, usb_position)
        expected_not_run = {'27_release', '29_pin_lifecycle', '10_hourly_drain'} | {f'19_{face}_{feature}' for face in ('front','rear') for feature in ('af','torch')}
        for k,v in s.results.items(): self.assertEqual(v['status'], 'NOT_RUN' if k in expected_not_run else 'PASS', (k,v))
        raw = (s.out/'acceptance.json').read_text()+self.output.getvalue()
        self.assertNotIn('SECRET_TEST', raw)
        self.assertEqual(s.secret_inputs, [b'SECRET_TEST_SSID\nSECRET_TEST_PASSWORD\n'])
        self.assertEqual(sum(c == ['FAKE_ADB', 'reboot'] for c in s.calls), 1)
        self.assertTrue(s.rebooted)
        self.assertEqual(s.results['01_restart']['evidence']['mute_verified'], 1)
        self.assertEqual(sum(c == ['ASSETS'] for c in s.calls), 2)
        mtp_call = next(i for i,c in enumerate(s.calls) if c[0].endswith('mtp-session'))
        self.assertLess(s.calls.index(['NATIVE', sweep.play.EINK_CLOSE]), mtp_call)
        self.assertLess(s.calls.index(['APP', sweep.PKG, '--es mode health']),
                        s.calls.index(['APP', 'org.a9.videoprobe', '']))
        self.assertEqual(json.loads((s.out/'service-inventory.json').read_text())['camera_count'], 2)
        enc = s.results['18_hardware_encoder']['evidence']
        for codec in ('avc', 'hevc'):
            self.assertEqual(enc[codec]['inputs'], 30); self.assertEqual(enc[codec]['eos'], 1)
            self.assertGreater(enc[codec]['bytes'], 0)
        self.assertEqual(len([c for c in s.calls if c[0] == 'ENCODER']), 2)
        self.assertNotIn('PRIVATE_TEST', raw)
        self.assertEqual(s.results['01_restart']['evidence']['health_verified'], 1)
        self.assertLess(s.calls.index(['ASSETS']), s.calls.index(['APP', sweep.PKG, '--es mode mute']))
        for v in s.results.values(): sweep.numeric_tree(v['evidence'])
    def test_3_missing_hardware_mtp_and_signed_out_do_not_pass(self):
        s = Fake(); s.assets_ok = True; s.no_hardware = True; s.no_mtp = True; s.signed_out = True
        s.sensors_media(); s.hardware_encoder(); s.usb(); s.play_install()
        self.assertEqual(s.results['18_hardware_encoder']['status'], 'FAIL')
        half = Fake(); half.no_hevc = True; half.hardware_encoder()
        self.assertEqual(half.results['18_hardware_encoder']['status'], 'FAIL')
        self.assertEqual(half.results['18_hardware_encoder']['evidence']['avc']['eos'], 1)
        self.assertEqual(half.results['18_hardware_encoder']['evidence']['hevc']['eos'], 0)
        for feature in ('af', 'torch'):
            self.assertEqual(s.results['19_front_'+feature]['evidence']['not_applicable'], 1)
            rear = s.results['19_rear_'+feature]['evidence']
            self.assertEqual(rear['stock_supported'], 1)
            self.assertEqual(rear['implementation_missing'], 1)
            self.assertNotIn('not_applicable', rear)
        self.assertEqual(s.results['11_adb_mtp']['status'], 'FAIL')
        # A function switch can disconnect the initiating adb shell. Do not
        # replay it or declare failure when the subsequent real state/session
        # proves that the single request succeeded.
        disconnected = Fake(); disconnected.assets_ok = True; original = disconnected.shell
        def disconnect_shell(source, data=None, timeout=20, capture_stderr=False):
            if source.startswith('umask 077; mkdir /data/local/tmp/a9-sweep-usb-'):
                disconnected.usb_mtp = True; return 255, 'error: closed PRIVATE_TEST\n'
            return original(source, data, timeout, capture_stderr)
        with patch.object(disconnected, 'shell', side_effect=disconnect_shell) as transport:
            self.assertTrue(disconnected.usb())
        self.assertEqual(disconnected.results['11_adb_mtp']['status'], 'PASS')
        self.assertEqual(disconnected.results['11_adb_mtp']['evidence']['set_rc'], 255)
        self.assertEqual(disconnected.results['11_adb_mtp']['evidence']['set_transport_closed'], 1)
        self.assertEqual(disconnected.results['11_adb_mtp']['evidence']['set_completed'], 1)
        self.assertEqual(sum(c.args[0].startswith('umask 077; mkdir /data/local/tmp/a9-sweep-usb-')
                             for c in transport.call_args_list), 1)
        self.assertNotIn('PRIVATE_TEST', (disconnected.out/'usb-set-evidence.json').read_text())
        missing = Fake(); missing.assets_ok = True; original_missing = missing.shell
        def missing_shell(source, data=None, timeout=20, capture_stderr=False):
            if source.startswith('umask 077; mkdir /data/local/tmp/a9-sweep-usb-'): return 255, 'error: closed\n'
            if source.startswith('cat /data/local/tmp/a9-sweep-usb-'): return 1, ''
            return original_missing(source, data, timeout, capture_stderr)
        with patch.object(missing, 'shell', side_effect=missing_shell) as transport:
            missing.usb()
        self.assertEqual(missing.results['11_adb_mtp']['status'], 'FAIL')
        self.assertEqual(missing.results['11_adb_mtp']['evidence']['set_completed'], 0)
        self.assertEqual(sum(c.args[0].startswith('umask 077; mkdir /data/local/tmp/a9-sweep-usb-')
                             for c in transport.call_args_list), 1)
        self.assertFalse(any(c.args[0].startswith('rm -r -- /data/local/tmp/a9-sweep-usb-') for c in transport.call_args_list))
        self.assertEqual(s.results['28_play_install_launch']['status'], 'NOT_RUN')
        self.assertEqual(s.results['28_play_install_launch']['reason'], 'needs_account')
        self.assertFalse(s.tapped)
        failed = Fake(); failed.assets_ok = True; original = failed.shell
        def install_fails(source, data=None, timeout=20):
            if source.startswith('input tap'): return 0, ''
            return original(source, data, timeout)
        with patch.object(failed, 'shell', side_effect=install_fails): failed.play_install()
        self.assertEqual(failed.results['28_play_install_launch']['status'], 'FAIL')
        self.assertNotIn('reason', failed.results['28_play_install_launch'])
        s = Fake(); s.existing_network = True; self.assertTrue(s.wifi())
        self.assertFalse(s.injected); self.assertEqual(s.secret_inputs, [])
        s.touched = True; s.cleanup()
        self.assertFalse(any('forget-network' in str(c) for c in s.calls))
        self.assertEqual(s.results['12_wifi']['evidence']['reused_existing'], 1)
        s = Fake(); s.wifi_not_ready = True
        self.assertFalse(s.wifi()); self.assertEqual(s.results['12_wifi']['evidence']['service_ready'], 0)
        self.assertEqual(s.secret_inputs, [])
        self.assertFalse(any('set-wifi-enabled enabled' in str(c) for c in s.calls))
        s = Fake(); s.ram_data = True; s.basic()
        self.assertEqual(s.results['29_wrapped_fbe']['status'], 'FAIL')
        self.assertEqual(s.results['29_wrapped_fbe']['evidence']['real_data_required'], 1)
        self.assertEqual(s.results['01_boot']['status'], 'FAIL')  # expected real data still fails
        self.assertEqual(s.results['01_boot']['evidence']['data_rc'], 25)
        s = Fake(); s.assets_ok = True; s.broken_health = True
        self.assertTrue(s.restart())  # changed boot ID alone is insufficient
        self.assertEqual(s.results['01_restart']['status'], 'FAIL')
        s.restart_health(True, True)
        self.assertEqual(s.results['01_restart']['status'], 'FAIL')
        self.assertEqual(s.results['01_restart']['evidence']['camera_count'], 0)
    def test_4_rotated_crash_dialog_bt_and_permissive_domain_fail(self):
        s = Fake()
        with patch.object(s, 'snapshot', return_value=dict(snapshot_ok=1, crash_events=31)):
            s.monitor(force=True)
        self.assertGreater(s.crashes, 0)  # startup ANRs cannot become the clean baseline
        s = Fake(); s.monitor(force=True); s.rotate = True; s.dialog = True; s.monitor(force=True)
        self.assertGreater(s.crashes, 0); self.assertGreater(s.dialogs, 0)
        changes = s.crashes
        for _ in range(5): s.monitor(force=True)
        self.assertEqual(s.crashes, changes)  # persistent difference is one transition
        self.assertEqual(s.signature_changes, dict(tombstones=1, anrs=0, crash_events=0))
        s.bluetooth_start(); s.bt_signature = 'b'*64; s.wait(65); s.bluetooth_end()
        self.assertEqual(s.results['13_bluetooth']['status'], 'FAIL')
        s.policy_domains = 'a9ml_rootsh\n'; s.policy()
        self.assertEqual(s.results['26_selinux']['status'], 'FAIL')
        self.assertEqual((s.out/'sweep-permissive-domains.txt').read_text(), 'a9ml_rootsh\n')
    def test_5_mute_and_transport_failures_are_bounded(self):
        # Replay INTEG16's first loss: AVC stage4/255, then all transports fail.
        # Independent feature results and closure must already be retained.
        class EncoderDisconnect(Fake):
            disconnected = False
            def shell(self, source, *args, **kwargs):
                if self.disconnected: return 1, ''
                if source.startswith('timeout -k 2 30 /data/local/tmp/a9-encoder.'):
                    self.disconnected = True
                    self.calls.append(['ENCODER', source])
                    return 255, 'ENCODE_STAGE codec=1 stage=4\n'
                return super().shell(source, *args, **kwargs)
            def native(self, source, *args, **kwargs):
                if self.disconnected: return 1, ''
                return super().native(source, *args, **kwargs)
        broken = EncoderDisconnect(); broken.execute()
        for check in ('19_front_photo', '19_rear_photo', '20_mic_amplitude',
                      '12_wifi', '26_selinux', 'cleanup'):
            self.assertEqual(broken.results[check]['status'], 'PASS', check)
        self.assertEqual(broken.results['18_hardware_encoder']['status'], 'FAIL')
        self.assertEqual(broken.results['18_hardware_encoder']['evidence']['avc']['command_rc'], 255)
        self.assertEqual(broken.results['A_stability']['status'], 'FAIL')
        self.assertFalse(broken.rebooted)
        self.assertLess(broken.calls.index(['NATIVE', sweep.play.EINK_CLOSE]),
                        next(i for i,c in enumerate(broken.calls) if c[0] == 'ENCODER'))
        s = Fake(); self.assertFalse(s.muted()); self.assertEqual(s.calls, [])
        s.bad_mute = True; s.execute()
        self.assertEqual(s.results['17_decoder_muted']['status'], 'FAIL')
        self.assertFalse(any(c[0] == 'APP' and 'mode mute' not in c[2] for c in s.calls))
        self.assertEqual(s.secret_inputs, []); self.assertFalse(s.rebooted)
        s.bad_mute = False; self.assertTrue(s.muted())  # Alarm minimum is 1.
        for result in (None, [1], {'completed': 0}, {'completed': 1, 'streams': []},
                       {'completed': 1, 'streams': [dict(stream=1, minimum=0, volume=0)]*5}):
            with patch.object(s, 'app', return_value=result): self.assertFalse(s.muted())
        s = Fake()
        def failed_assets(): s.assets_ok = False; return False
        with patch.object(s, 'prepare_assets', side_effect=failed_assets): s.execute()
        self.assertEqual(s.results['00_assets']['status'], 'FAIL')
        self.assertFalse(any(c[0] == 'APP' for c in s.calls))
        # Polling a fresh result must still work after the feature wait budget,
        # including the reserved reboot phase; exercise the real app() method.
        s = Fake(); s.wait_limit = self.clock.now - 1; polls = []
        original = s.shell
        def app_shell(source, data=None, timeout=20):
            if source.startswith('run-as ') and 'cat files/result.json' in source:
                polls.append(1)
                return (1, '') if len(polls) == 1 else (0, '{"completed":1}')
            return original(source, data, timeout)
        with patch.object(s, 'shell', side_effect=app_shell):
            self.assertEqual(sweep.Sweep.app(s, sweep.PKG, '.Probe', '--es mode mute', seconds=10), {'completed': 1})
        self.assertEqual(len(polls), 2)
        self.assertFalse(any('wm dismiss-keyguard' in str(c) for c in s.calls))
        # A reboot reinstall failure is recorded on restart, never on decoder.
        s = Fake(); installs = []
        def assets():
            installs.append(1); s.assets_ok = len(installs) == 1; return s.assets_ok
        with patch.object(s, 'prepare_assets', side_effect=assets): s.execute()
        self.assertEqual(s.results['17_decoder_muted']['status'], 'PASS')
        self.assertEqual(s.results['01_restart']['evidence']['probes_reinstalled'], 0)
        self.assertEqual(s.results['01_restart']['status'], 'FAIL')
        self.assertIn(s.results['A_stability']['status'], ('PASS', 'FAIL'))
        with patch.object(sweep.sp, 'run', side_effect=sweep.sp.TimeoutExpired(['fake'], 1, output=b'SECRET_TEST')):
            self.assertEqual(sweep.Sweep.run(s, ['fake']), (1, ''))
        with self.assertRaises(ValueError): sweep.numeric_tree({'bad': 'SECRET_TEST'})
        self.assertNotIn('SECRET_TEST', self.output.getvalue())

if __name__ == '__main__': unittest.main(verbosity=2)
