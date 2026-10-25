#!/usr/bin/env python3
"""Five host fixture groups for the actual onb hook. Never invokes target tools."""
from contextlib import redirect_stdout
import importlib.util
import io
import os
import sys
import threading
from unittest.mock import patch
from pathlib import Path
import tempfile
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fp_hook', HERE / 'onb.py')
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
REAL_TIME = hook.time


class Clock:
    value = 0
    def monotonic(self): return self.value
    def sleep(self, seconds): self.value += seconds


class FixtureCapture(hook.StartupCapture):
    stopped = False
    def start(self, adb): pass
    def running(self): return self.ready.is_set() and not self.overflow and not self.stopped
    def stop(self): self.stopped = True


def startup(pid=1234, ready='ready'):
    return ('I/ 1234 1234 [fsfp-hal-device](' + str(pid) + '): (1439) device id = 0x8395\n'
            'I/ 1234 1234 [fsfp-hal-device](' + str(pid) + '): fingerprint device is ' + ready + '.\n')


class Fixture(hook.Hook):
    def __init__(self, root, case):
        self.adb = '/INVALID-no-device-tool'
        self.logs = root
        self.case = case
        self.result = dict(passed=False, enrollment_attempted=False, authentication_attempted=False)
        self.requested = False
        self.capture = None
        self.restarted = False
        self.commands = []
        self.clock = Clock()
        hook.time = SimpleNamespace(monotonic=self.clock.monotonic, sleep=self.clock.sleep)

    def start_capture(self):
        self.capture = FixtureCapture('FP_CAPTURE_fixture')
        if self.case != 'capture_unarmed':
            self.capture.feed('I/a9-fp-onb(99): FP_CAPTURE_fixture')
        return self.capture.running()

    def run(self, args, payload=None, timeout=20):
        text = payload if payload is not None else args[-1]
        self.commands.append(text)
        assert not any(call in text for call in ('enroll(', 'authenticate(', 'remove(', 'setActiveGroup(', 'lshal'))
        if 'getprop ro.boot.slot_suffix' == text: return 0, '_b\n'
        if 'getprop vendor.sys.listeners.registered' == text:
            raise AssertionError('private listener property must use native channel')
        if text.startswith('l=$(getprop vendor.sys.listeners.registered)'):
            if self.case == 'unreadable': return 1, ''
            value = 'false' if self.case == 'late_listeners' and self.clock.value < 6 else 'true'
            return 0, 'FP_QSEE listeners=' + value + ' daemon=running keymaster=running\n'
        if 'getprop init.svc.vendor.fps_hal' == text:
            raise AssertionError('private HAL state must use native channel')
        if text.startswith('s=$(getprop init.svc.vendor.fps_hal)'):
            if self.case == 'unreadable_hal': return 0, 'FP_HAL state=unset pid=1234 birth=9001\n'
            if not self.requested and self.case != 'prestarted':
                return 0, 'FP_HAL state=stopped pid=0 birth=0\n'
            birth = 9002 if self.restarted or (self.case == 'restart' and self.clock.value > 0) else 9001
            return 0, 'FP_HAL state=running pid=1234 birth=' + str(birth) + '\n'
        if text.startswith('dmesg | grep'): return 0, 'qseecom: LOAD sw_fp scm=0 result=0\n'
        if text.startswith('for p in /dev/sunwave_fp'):
            nodes = ['/dev/sunwave_fp', '/dev/qseecom', '/dev/ion', 'driver', '/vendor/firmware/sw_fp.mdt']
            nodes += [f'/vendor/firmware/sw_fp.b{i:02d}' for i in range(8)]
            values = {p: 'ready' for p in nodes}
            if self.case in ('missing_node', 'unreadable_node', 'wrong_node_type'):
                values['/dev/sunwave_fp'] = {'missing_node': 'missing', 'unreadable_node': 'unreadable',
                                             'wrong_node_type': 'not_character'}[self.case]
            assert 'stat -c %f' in text and 'unreadable' in text
            return 0, ''.join('FP_NODE ' + p + ' ' + v + '\n' for p, v in values.items())
        if text.startswith('setprop vendor.a9.fingerprint.enable'):
            if text.strip().endswith(' 1'):
                assert self.capture is not None and self.capture.running()
                if self.case not in ('null_device', 'prestarted'):
                    for line in startup(5678 if self.case == 'wrong_pid' else 1234,
                                        'initialized' if self.case == 'startup_only' else 'ready').splitlines():
                        self.capture.feed(line)
                self.capture.feed('D/UnrelatedHalTag(1234): registration stage reached')
                if self.case in ('startup_only', 'null_device'):
                    for _ in range(500):
                        self.capture.feed("I/fsfp-hal(1234): 'healthMonitoringThread' state:0001")
                if self.case == 'capture_lost': self.capture.stopped = True
            return 0, ''
        if text == 'setprop ctl.restart vendor.fps_hal\n':
            assert self.capture.running() and self.case == 'prestarted'
            self.restarted = True
            for line in startup().splitlines(): self.capture.feed(line)
            return 0, ''
        if text.startswith('p=$(pidof '):
            birth = 9002 if self.case == 'restart' and self.clock.value > 0 else 9001
            return 0, 'FP_PROCESS 1234 ' + str(birth) + '\n'
        if text == 'dumpsys -t 8 fingerprint':
            assert payload is None, 'framework dump uses ordinary shell, not enforcing native console'
            if self.case == 'timeout_framework': raise hook.sp.TimeoutExpired('fixture dumpsys', 12)
            if self.case == 'missing_framework': return 0, ''
            if self.case == 'decoy_framework': return 0, 'config_biometric_sensors=[0:2:15]\n'
            if self.case == 'late_framework' and self.clock.value < 6: return 0, ''
            logical = 1 if self.case == 'changed_framework' and self.clock.value >= 30 else 0
            row = f'Dumping for sensorId: {logical}, provider: FingerprintProvider\n'
            if self.case == 'multiple_framework': row += 'Dumping for sensorId: 1, provider: FingerprintProvider\n'
            if self.case == 'duplicate_framework': row += row
            if self.case == 'malformed_framework': row = 'Dumping for sensorId: invalid, provider: FingerprintProvider\n'
            if self.case == 'mixed_malformed_framework': row += 'Dumping for sensorId: invalid, provider: FingerprintProvider\n'
            if self.case == 'unreadable_framework': row += 'Permission Denial: fingerprint dump\n'
            if self.case == 'truncated_framework': row += '*** SERVICE DUMP TIMEOUT EXPIRED AFTER 8000 MS ***\n'
            # Per-user/private payloads must never escape the dump parser.
            row += 'Fps state: 0\n{"authenticator_id":"PRIVATE_ID", "enrolled":["PRIVATE_DATA"]}\n'
            return (139 if self.case == 'failed_framework' else 0), row
        if text.startswith('logcat -b crash'):
            return 0, 'Fatal signal 11\n' if self.case == 'crash' else ''
        raise AssertionError('unexpected fixture operation: ' + text)


def run_case(root, case, expected, failure=None):
    test = Fixture(root / case, case)
    error = None
    with redirect_stdout(io.StringIO()):
        try: test.execute()
        except RuntimeError as e: error = str(e)
        finally: test.cleanup()
    assert test.result['passed'] == expected, (case, error, test.result)
    if failure: assert error == failure, (case, error)
    if test.capture is not None:
        assert test.capture.stopped
        assert (test.logs / 'fingerprint-hal-full.log').stat().st_mode & 0o777 == 0o600
        assert (test.logs / 'fingerprint-service-managers.log').exists()
    assert test.restarted == (case == 'prestarted')
    if test.requested:
        assert test.commands[-1] == 'setprop vendor.a9.fingerprint.enable 0\n'
    if expected:
        assert test.clock.value >= 30 and test.result['sensor_id'] == '0x8395'
        assert test.result['registration_state'] == 'visible'
        assert test.result['framework_sensor_count'] == 1
    if (test.logs / 'fingerprint-framework.json').exists():
        saved = (test.logs / 'fingerprint-framework.json').read_text()
        assert 'PRIVATE' not in saved and 'sensorId' not in saved
        assert (test.logs / 'fingerprint-framework.json').stat().st_mode & 0o777 == 0o600


def main():
    with tempfile.TemporaryDirectory(prefix='fp-host-smoke-') as temp:
        root = Path(temp)
        run_case(root, 'valid', True)
        run_case(root, 'startup_only', True)
        run_case(root, 'prestarted', True)
        run_case(root, 'late_framework', True)
        for case in ('missing_framework', 'multiple_framework', 'duplicate_framework',
                     'malformed_framework', 'mixed_malformed_framework', 'unreadable_framework',
                     'failed_framework', 'timeout_framework', 'truncated_framework', 'decoy_framework'):
            run_case(root, case, False, 'FRAMEWORK_ONE_FINGERPRINT_SENSOR')
        run_case(root, 'changed_framework', False, 'FINAL_REGISTRATION_AND_SENSOR_ID')
        run_case(root, 'wrong_pid', False, 'REAL_SENSOR_ID_AND_INITIALIZATION')
        run_case(root, 'capture_unarmed', False, 'STARTUP_CAPTURE_ARMED')
        run_case(root, 'capture_lost', False, 'STARTUP_CAPTURE_LIVE')
        run_case(root, 'null_device', False, 'REAL_SENSOR_ID_AND_INITIALIZATION')
        run_case(root, 'restart', False, 'HAL_STABLE')
        run_case(root, 'unreadable_hal', False, 'HAL_STATE_READABLE')
        run_case(root, 'crash', False, 'NO_HAL_CRASH')
        run_case(root, 'missing_node', False, 'SUNWAVE_CHAR_DEVICE')
        run_case(root, 'unreadable_node', False, 'STOCK_PREREQUISITES_READABLE')
        run_case(root, 'wrong_node_type', False, 'SUNWAVE_CHAR_DEVICE')
        run_case(root, 'late_listeners', True)
        run_case(root, 'unreadable', False, 'STOCK_QSEECOMD_LISTENERS')
        assert hook.chip_id('module id = 0x8395\nfingerprint device is ready.') is None
        for invalid in ['0000', 'ffff']:
            assert hook.chip_id('device id = 0x' + invalid + '\nfingerprint device is ready.') is None
        assert hook.chip_id('device id = 0x8395') is None
        assert hook.chip_id('device id = 0x8395\ndevice id = 0x8263\nfingerprint device is ready.') is None
        # Exercise the actual parser: pre-marker/wrong PID/secret/health-only
        # messages cannot manufacture real chip-ID plus initialization proof.
        capture = FixtureCapture('FP_CAPTURE_fixture')
        capture.feed('W/hwservicemanager(99): not in manifest before capture')
        for line in startup().splitlines(): capture.feed(line)
        assert not capture.snapshot(1234)
        capture.feed('I/a9-fp-onb(99): FP_CAPTURE_fixture')
        for line in startup().splitlines(): capture.feed(line)
        for _ in range(500):
            capture.feed("I/fsfp-hal(1234): 'healthMonitoringThread' state:0001")
        capture.feed('I/fsfp-hal(1234): sn_code: PRIVATE_serial')
        capture.feed('I/fsfp-hal(1234): auth_key: PRIVATE_key')
        capture.feed('I/fsfp-hal(5678): device id = 0x8263')
        capture.feed('I/a9-fp-onb(99): FP_CAPTURE_fixture')
        assert hook.chip_id(capture.snapshot(1234)) == 0x8395
        assert hook.chip_id(capture.snapshot(5678)) is None
        assert 'PRIVATE' not in capture.snapshot(1234)
        assert len(capture.records[1234]) == 3
        capture.track(1234)
        capture.feed('D/libhidl:ServiceManagement(1234): registerAsService stage reached')
        capture.feed('E/AnyHalTag(1234): failure reason preserved')
        capture.feed('I/AnyHalTag(1234): serialno is PRIVATE_serial')
        capture.feed('I/AnyHalTag(1234): key=PRIVATE_secret')
        capture.feed('I/UnrelatedApp(9876): PRIVATE_other_process')
        capture.feed('W/servicemanager(99): not allowed to add requested interface')
        capture.feed('E/libvintf(101): fingerprint entry not in manifest')
        capture.feed('D/AnotherManagerTag(99): manager process detail')
        full = capture.full_snapshot()
        assert 'libhidl:ServiceManagement' in full and 'failure reason preserved' in full
        assert 'PRIVATE' not in full and '<redacted' in full
        assert 'before capture' in capture.manager_snapshot() and 'not allowed' in capture.manager_snapshot()
        assert 'UnrelatedApp' not in full
        assert 'entry not in manifest' in capture.manager_snapshot()
        assert 'AnotherManagerTag' in capture.manager_snapshot()
        for i in range(5000): capture.feed(f'D/AnyHalTag(1234): line={i}')
        assert 'middle records omitted' in capture.full_snapshot()
        assert 'line=4999' in capture.full_snapshot() and capture.diagnostic_summary()['middle_records_omitted'] > 0
        assert hook.chip_id('sf_get_device_state state:0001') is None
        assert hook.chip_id('device id = 0x8395\nfingerprint device is initialized.') == 0x8395
        rc = (HERE / 'vendor/etc/init/init.a9-fingerprint.rc').read_text()
        assert '\n    interface android.hardware.biometrics.fingerprint@2.1::IBiometricsFingerprint default\n' in rc
        assert '\n    disabled\n' in rc
        import xml.etree.ElementTree as ET
        manifest = ET.parse(HERE / 'vendor/etc/vintf/manifest/fingerprint-a9.xml').getroot()
        hal = [n for n in manifest.findall('hal') if n.findtext('name') == 'android.hardware.biometrics.fingerprint']
        assert len(hal) == 1 and hal[0].findtext('transport') == 'hwbinder' and hal[0].findtext('version') == '2.1'
        assert hal[0].findtext('interface/name') == 'IBiometricsFingerprint' and hal[0].findtext('interface/instance') == 'default'
        assert 'vendor.sys.listeners.registered=true && property:vendor.a9.fingerprint.enable=1' in rc
        # Execute the actual framework dump command with a host-only stub.
        # Check command/transport failures fail even with a valid header, while
        # private provider payloads and a logical zero ID never leave the parser.
        stub_bin = root / 'bin'
        stub_bin.mkdir()
        stub = stub_bin / 'dumpsys'
        stub.write_text('#!/bin/sh\n[ "$*" = "-t 8 fingerprint" ] || exit 2\nprintf "%s\\n" "Dumping for sensorId: 0, provider: FingerprintProvider" "PRIVATE_ID"\n')
        stub.chmod(0o755)
        class PipelineHook(hook.Hook):
            def __init__(self):
                self.result = {}
                self.logs = root / 'pipeline'
            def shell(self, text, timeout=20):
                assert timeout == 12
                env = dict(os.environ, PATH=str(stub_bin) + os.pathsep + os.environ['PATH'])
                r = hook.sp.run(['sh', '-c', text], env=env, text=True, stdout=hook.sp.PIPE,
                                stderr=hook.sp.STDOUT, timeout=5)
                return r.returncode, r.stdout
            def native(self, text): raise AssertionError('framework query must not use native channel')
        query = PipelineHook()
        assert query.framework() == (0, 'FingerprintProvider')
        assert 'PRIVATE_ID' not in (query.logs / 'fingerprint-framework.json').read_text()
        stub.write_text(stub.read_text() + 'exit 139\n')
        assert query.framework() is None and query.result['registration_query_rc'] == 139
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "Dumping for sensorId: 0, provider: FingerprintProvider" "DUMP TIMEOUT"\n')
        assert query.framework() is None and query.result['registration_state'] == 'dump_error'
        hook.time = REAL_TIME
        class MarkerHook(hook.Hook):
            def __init__(self):
                self.adb = '/INVALID-fixture'
                self.attempts = 0
                self.result = {}
            def shell(self, text, timeout=20):
                if text.startswith('logcat '): return 0, 'W/hwservicemanager(99): manifest history\n'
                self.attempts += 1
                if self.attempts == 2:
                    self.capture.feed('I/a9-fp-onb(99): ' + self.capture.marker)
                return 0, ''
        marker_hook = MarkerHook()
        with patch.object(hook, 'StartupCapture', FixtureCapture):
            assert marker_hook.start_capture() and marker_hook.attempts == 2
        marker_hook.capture.stop()
        # Real host pipe/thread lifecycle with a synthetic producer; no adb.
        captured = threading.Event()
        class PipeCapture(hook.StartupCapture):
            def feed(self, line):
                super().feed(line)
                if hook.chip_id(self.snapshot(1234)) == 0x8395: captured.set()
        pipe = PipeCapture('FP_CAPTURE_pipe')
        producer = "import time; print(" + repr('I/a9-fp-onb(99): FP_CAPTURE_pipe\n' + startup()) + ", flush=True); time.sleep(30)"
        real_popen = hook.sp.Popen
        def fake_transport(args, **kwargs):
            assert args[0] == '/INVALID-fixture' and 'logcat -b all' in args[-1] and '"*:V"' in args[-1]
            return real_popen([sys.executable, '-u', '-c', producer], **kwargs)
        try:
            with patch.object(hook.sp, 'Popen', fake_transport): pipe.start('/INVALID-fixture')
            assert captured.wait(3) and pipe.running()
        finally:
            pipe.stop()
        assert pipe.process.poll() is not None and not pipe.thread.is_alive()
        old = os.environ.pop('A9_BTRY_IN_HOOK', None)
        try:
            with redirect_stdout(io.StringIO()): assert hook.main() == 2
        finally:
            if old is not None: os.environ['A9_BTRY_IN_HOOK'] = old
    print('PASS 5 fingerprint host fixture groups; no device tools or target execution')


if __name__ == '__main__':
    main()
