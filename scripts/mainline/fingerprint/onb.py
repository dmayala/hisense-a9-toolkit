#!/usr/bin/env python3
"""B-try fingerprint registration/chip-ID observation. Never enrolls or authenticates."""
from pathlib import Path
from collections import deque
import json
import os
import re
import signal
import secrets
import threading
import subprocess as sp
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT_HELPER = HERE.parent / 'boot-deadline/onb-root.py'
SERVICE = 'android.hardware.biometrics.fingerprint@2.1-service'


def chip_id(text):
    values = re.findall(r'\bdevice id = 0x([0-9a-fA-F]{4})\b', text)
    ids = {int(v, 16) for v in values if int(v, 16) not in (0, 0xffff)}
    ready = any(message in text for message in (
        'fingerprint device is ready.', 'fingerprint device is initialized.'))
    return next(iter(ids)) if len(ids) == 1 and ready else None


def same_process(first, current):
    return first is not None and first == current


class StartupCapture:
    """Keep strict sensor proof plus bounded PID/manager diagnostics."""
    BRIEF = re.compile(r'^[VDIWEF]/(.*?)\(\s*(\d+)\):\s?(.*)$')

    def __init__(self, marker):
        self.marker = marker
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.records = {}
        self.overflow = False
        self.process = None
        self.thread = None
        self.tracked = set()
        self.anchored = set()
        self.manager_pids = set()
        self.details = {}
        self.initial = {}
        self.totals = {}

    @staticmethod
    def redact(body):
        # Keep every tag/stage, but never persist device identifiers or secret
        # token/template/calibration payloads from the vendor startup logs.
        body = re.sub(r'(?i)\b(sn_code|serial(?:[_ ]?(?:number|no))?|imei|meid|imsi|iccid|android_id|uuid)\b.*',
                      lambda m: m[1] + '=<redacted-id>', body)
        body = re.sub(r'(?i)\b(challenge|operation_id|fp_id|finger_id|'
                      r'auth(?:entication)?[_ -]?(?:key|token)|authenticator[_ ]?id|key|'
                      r'(?:template|calibration)(?:[_ ]?(?:id|data|bytes|buffer|dump))?)\s*[:=].*',
                      lambda m: m[1] + '=<redacted>', body)
        body = re.sub(r'(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', '<redacted-id>', body)
        body = re.sub(r'(?i)\b(?:0x)?[0-9a-f]{12,}\b', '<redacted-hex>', body)
        return re.sub(r'(?i)(?:\b[0-9a-f]{2}[ ,:-]){8,}[0-9a-f]{2}\b', '<redacted-bytes>', body)

    def track(self, pid):
        with self.lock:
            self.tracked.add(pid)

    def _append(self, key, line):
        if key not in self.details:
            if len(self.details) >= 17:
                # Prefer a positively observed HAL over unselected QSEE users.
                other = next((k for k in self.details if k != 'managers' and k not in self.tracked), None)
                if other is None: return
                for mapping in (self.details, self.initial, self.totals): mapping.pop(other)
            self.details[key] = deque(maxlen=4096)
            self.initial[key] = []
            self.totals[key] = 0
        if len(line) > 2048: line = line[:2048] + ' [line truncated]'
        if len(self.initial[key]) < 128: self.initial[key].append(line)
        self.details[key].append(line)
        self.totals[key] += 1

    def _render(self, key):
        tail = list(self.details.get(key, []))
        dropped = self.totals.get(key, 0) - len(tail)
        prefix = self.initial.get(key, [])[:min(128, dropped)]
        if dropped > len(prefix): prefix += ['# diagnostic middle records omitted: ' + str(dropped - len(prefix))]
        return prefix + tail

    def full_snapshot(self):
        with self.lock:
            lines = []
            for pid in sorted(self.tracked): lines += self._render(pid)
            return '\n'.join(lines)

    def manager_snapshot(self):
        with self.lock:
            return '\n'.join(self._render('managers'))

    def diagnostic_summary(self):
        with self.lock:
            keys = [*self.tracked, 'managers']
            return dict(hal_records=sum(self.totals.get(p, 0) for p in self.tracked),
                        manager_records=self.totals.get('managers', 0),
                        middle_records_omitted=sum(max(0, self.totals.get(k, 0) - 4224) for k in keys))

    def feed(self, line):
        match = self.BRIEF.match(line.rstrip('\r\n'))
        if not match:
            return
        tag, pid, body = match.groups()
        pid = int(pid)
        with self.lock:
            if tag.strip() == 'a9-fp-onb' and body == self.marker:
                if not self.ready.is_set():
                    self.records.clear()
                    self.ready.set()
                return
            safe_line = line[0] + '/' + tag + '(' + str(pid) + '): ' + self.redact(body)
            if tag.strip() in ('hwservicemanager', 'servicemanager', 'vndservicemanager'):
                self.manager_pids.add(pid)
            manager = (pid in self.manager_pids or tag.strip().lower() in ('libvintf', 'hidlservicemanagement', 'servicemanagement')
                       or ('avc:' in body and 'hwservice_manager' in body))
            if manager: self._append('managers', safe_line)
            if not self.ready.is_set(): return
            anchor = 'fsfp-' in tag or 'QSEECOMAPI' in tag or 'fingerprint' in tag.lower()
            if anchor and len(self.anchored) < 16: self.anchored.add(pid)
            if pid in self.tracked or pid in self.anchored: self._append(pid, safe_line)
            if not ('fsfp-' in tag or 'QSEECOMAPI' in tag): return
            # Diagnostics cannot qualify a sensor: keep the strict proof parser
            # separate from the broader, redacted process log.
            safe = []
            for value in re.findall(r'\bdevice id = 0x([0-9a-fA-F]{4})\b', body):
                safe.append('device id = 0x' + value.lower())
            for message in ('fingerprint device is ready.', 'fingerprint device is initialized.',
                            'Load image request failed', 'failed to probe device id.',
                            'failed to init fingerprint device.', 'failed to init hal.'):
                if message in body:
                    safe.append(message)
            state = re.search(r"'(sf_get_device_state|healthMonitoringThread)' state:([0-9a-fA-F]{4})\b", body)
            if state:
                safe.append(state[1] + ' state:' + state[2].lower())
            if not safe:
                return
            if pid not in self.records:
                if len(self.records) >= 16:
                    self.overflow = True
                    return
                self.records[pid] = []
            records = self.records[pid]
            for message in safe:
                text = 'I/fsfp-onb(' + str(pid) + '): ' + message
                if text not in records:
                    if len(records) >= 128:
                        self.overflow = True
                        return
                    records.append(text)

    def snapshot(self, pid):
        with self.lock:
            return '\n'.join(self.records.get(pid, []))

    def start(self, adb):
        # -T 1 streams onward; no log-buffer clearing or log-level change.
        self.process = sp.Popen([adb, 'shell', '-T', 'logcat -b all -v brief -T 1 "*:V"'],
                                stdout=sp.PIPE, stderr=sp.STDOUT, text=True, errors='replace')
        def read():
            for line in self.process.stdout:
                self.feed(line)
        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()

    def running(self):
        return self.ready.is_set() and not self.overflow and self.process.poll() is None and self.thread.is_alive()

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except sp.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            if self.thread is not None:
                self.thread.join(timeout=3)
            self.process.stdout.close()


class Hook:
    def __init__(self):
        self.adb = os.environ['ADB']
        self.logs = Path(os.environ['A9_BTRY_LOGS'])
        self.result = dict(row=30, host_only_preparation=True, enrollment_attempted=False,
                           authentication_attempted=False, passed=False)
        self.requested = False
        self.capture = None

    def run(self, args, payload=None, timeout=20):
        r = sp.run(args, input=payload, text=True, stdout=sp.PIPE, stderr=sp.STDOUT, timeout=timeout)
        return r.returncode, r.stdout.replace('\r', '')

    def shell(self, text, timeout=20):
        return self.run([self.adb, 'shell', '-T', text], timeout=timeout)

    def native(self, text):
        return self.run([sys.executable, str(ROOT_HELPER)], text + '\n', timeout=80)

    def need(self, condition, name):
        print(('PASS ' if condition else 'FAIL ') + name, flush=True)
        if not condition: raise RuntimeError(name)

    def status(self):
        # Like listener readiness, this vendor service property is private.
        # Empty ordinary-shell reads must not look like a stable HAL.
        rc, text = self.native('s=$(getprop init.svc.vendor.fps_hal)\n'
            'p=$(pidof ' + SERVICE + ' || true)\n'
            'case "$p" in ""|*[!0-9]*) p=0; b=0;; *)\n'
            '  b=$(sed "s/^.*) //" /proc/$p/stat | cut -d " " -f20);; esac\n'
            'printf "FP_HAL state=%s pid=%s birth=%s\\n" "${s:-unset}" "$p" "${b:-0}"')
        match = re.search(r'^FP_HAL state=(running|stopped|restarting) pid=(\d+) birth=(\d+)$', text, re.M)
        if rc != 0 or match is None:
            return None, None
        state, pid, birth = match.groups()
        identity = (int(pid), int(birth)) if state == 'running' and int(pid) and int(birth) else None
        self.result['hal_state'] = state
        if identity is not None and self.capture is not None: self.capture.track(identity[0])
        return state, identity

    def framework(self):
        # Query only system_server's fingerprint service through ordinary shell.
        # lshal's whole-HAL-table traversal segfaulted in FP13 (exit139). Do not
        # invoke it, fall back to its output, or mistake a feature flag for a
        # registered framework sensor. This dump does not enroll/authenticate.
        self.result['registration_query'] = 'dumpsys_fingerprint'
        try:
            rc, text = self.shell('dumpsys -t 8 fingerprint', timeout=12)
        except sp.TimeoutExpired:
            rc, text = None, ''
        raw = re.findall(r'^Dumping for sensorId:.*$', text, re.M)
        rows = re.findall(r'^Dumping for sensorId: ([0-9]+), provider: ([A-Za-z0-9_.$]+) *$', text, re.M)
        malformed = len(raw) != len(rows)
        errors = (('permission_denied', r'Permission Denial|SecurityException'),
                  ('dump_timeout', r'DUMP TIMEOUT'),
                  ('service_missing', r"Can't find service|SERVICE DUMP DISABLED"),
                  ('dead_object', r'DeadObjectException'),
                  ('transaction_failed', r'Error dumping service info|FAILED_TRANSACTION|Transaction failed'),
                  ('query_crash', r'Segmentation fault'))
        error = next((name for name, pattern in errors if re.search(pattern, text, re.I)), None)
        readable = rc == 0 and not error and not malformed
        state = ('timeout' if rc is None else 'query_failed' if rc != 0 else
                 'dump_error' if error else 'malformed' if malformed else
                 'visible' if len(rows) == 1 else 'missing' if not rows else 'multiple')
        # Sensor IDs here are logical framework indices; zero is valid. Keep
        # the identity private and compare it again after the stability window.
        identity = (int(rows[0][0]), rows[0][1]) if readable and len(rows) == 1 else None
        self.result.update(registration_query_rc=rc, registration_state=state,
                           framework_sensor_count=len(rows), framework_parse_error=malformed,
                           framework_query_error=error)
        self.logs.mkdir(parents=True, exist_ok=True)
        # Never persist the raw dump: provider diagnostics contain per-user
        # enrollment, authenticator IDs and scheduler/client details.
        summary = dict(query='dumpsys_fingerprint', rc=rc, state=state,
                       sensor_count=len(rows), parse_error=malformed, error=error)
        with (self.logs / 'fingerprint-framework.json').open('w') as file:
            os.fchmod(file.fileno(), 0o600)
            json.dump(summary, file, indent=2)
            file.write('\n')
        return identity

    def start_capture(self):
        self.capture = StartupCapture('FP_CAPTURE_' + secrets.token_hex(12))
        self.capture.start(self.adb)
        rc, history = self.shell('logcat -b all -d -v brief -s hwservicemanager:V servicemanager:V vndservicemanager:V libvintf:V HidlServiceManagement:V ServiceManagement:V "*:S"', timeout=12)
        self.result['manager_history_readable'] = rc == 0
        if rc == 0:
            for line in history.splitlines(): self.capture.feed(line)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            # logcat may still be attaching when the first marker is emitted.
            # Repeated markers never erase proof after the first acknowledgment.
            rc, _ = self.shell('log -p i -t a9-fp-onb ' + self.capture.marker, timeout=10)
            if rc != 0:
                return False
            if self.capture.ready.wait(timeout=0.5):
                return self.capture.running()
        return False

    def sensor(self, pid):
        self.need(self.capture.running(), 'STARTUP_CAPTURE_LIVE')
        # Keep only post-marker stream messages from the observed HAL PID.
        # Startup proof survives logd rollover and the final stability check.
        messages = self.capture.snapshot(pid)
        self.logs.mkdir(parents=True, exist_ok=True)
        (self.logs / 'fingerprint-hal.log').write_text(messages + '\n')
        self.save_diagnostics()
        if 'Load image request failed' in messages:
            self.result['ta_load_error'] = 'QSEECom LOAD_APP failed; see fingerprint-hal.log'
        return chip_id(messages)

    def save_diagnostics(self):
        if self.capture is None: return
        self.logs.mkdir(parents=True, exist_ok=True)
        for name, text in [('fingerprint-hal-full.log', self.capture.full_snapshot()),
                           ('fingerprint-service-managers.log', self.capture.manager_snapshot())]:
            fd = os.open(self.logs / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(text + '\n')
        self.result['diagnostics'] = self.capture.diagnostic_summary()

    def qsee_ready(self):
        # fp1's ordinary shell was denied this private listener property. Read
        # through the existing owned native channel; do not expose it to shell.
        rc, text = self.native(
            'l=$(getprop vendor.sys.listeners.registered)\n'
            'd=$(getprop init.svc.vendor.qseecomd)\n'
            'k=$(getprop init.svc.vendor.keymaster-4-1)\n'
            'printf "FP_QSEE listeners=%s daemon=%s keymaster=%s\\n" "${l:-unset}" "${d:-unset}" "${k:-unset}"')
        match = re.search(r'^FP_QSEE listeners=(true|false|unset) daemon=(running|stopped|restarting|unset) keymaster=(running|stopped|restarting|unset)$', text, re.M)
        if rc != 0 or match is None:
            self.result['qsee_state'] = 'unreadable'
            return False
        listener, daemon, keymaster = match.groups()
        self.result.update(listeners_registered=listener, qseecomd_state=daemon, keymaster_state=keymaster)
        print('FP_QSEE listeners=' + listener + ' daemon=' + daemon + ' keymaster=' + keymaster, flush=True)
        return listener == 'true' and daemon == 'running' and keymaster == 'running'

    def execute(self):
        rc, text = self.shell('getprop ro.boot.slot_suffix')
        self.need(rc == 0 and text.strip() == '_b', 'SLOT_B')
        deadline = time.monotonic() + 30
        ready = self.qsee_ready()
        while not ready and time.monotonic() < deadline:
            time.sleep(3)
            ready = self.qsee_ready()
        self.need(ready, 'STOCK_QSEECOMD_LISTENERS')
        # Report every prerequisite even when one is absent; fp2's set-e
        # command hid which node failed. No TA load is claimed by readability.
        rc, text = self.native(
            'for p in /dev/sunwave_fp /dev/qseecom /dev/ion; do\n'
            '  if e=$(stat -c %f "$p" 2>&1); then\n'
            '    if test -c "$p"; then v=ready; else v=not_character; fi\n'
            '  else\n'
            '    case "$e" in *"No such file"*) v=missing;; *) v=unreadable;; esac\n'
            '  fi\n'
            '  printf "FP_NODE %s %s\\n" "$p" "$v"\n'
            'done\n'
            'for p in /vendor/firmware/sw_fp.mdt /vendor/firmware/sw_fp.b0[0-7]; do\n'
            '  if test -r "$p"; then v=ready; else v=missing; fi\n'
            '  printf "FP_NODE %s %s\\n" "$p" "$v"\n'
            'done\n'
            'if test -L /sys/bus/platform/devices/fingerprint/driver; then v=ready; else v=missing; fi\n'
            'printf "FP_NODE driver %s\\n" "$v"')
        nodes = dict(re.findall(r'^FP_NODE (\S+) (ready|missing|unreadable|not_character)$', text, re.M))
        self.result['prerequisites'] = nodes
        print(text.strip(), flush=True)
        self.need(rc == 0 and 'unreadable' not in nodes.values(), 'STOCK_PREREQUISITES_READABLE')
        for path, name in [('/dev/sunwave_fp', 'SUNWAVE_CHAR_DEVICE'),
                           ('driver', 'SUNWAVE_DRIVER_BOUND'),
                           ('/dev/qseecom', 'QSEECOM_CHAR_DEVICE'),
                           ('/dev/ion', 'ION_CHAR_DEVICE')]:
            self.need(nodes.get(path) == 'ready', name)
        files = ['/vendor/firmware/sw_fp.mdt'] + [f'/vendor/firmware/sw_fp.b{i:02d}' for i in range(8)]
        self.need(all(nodes.get(path) == 'ready' for path in files), 'SW_FP_FILES_READABLE')
        # Attach before requesting startup or a single pre-measurement restart.
        self.need(self.start_capture(), 'STARTUP_CAPTURE_ARMED')
        self.result['startup_capture_armed'] = True
        state, before = self.status()
        self.need(state is not None, 'HAL_STATE_READABLE')
        # Init starts the unmodified stock HAL only after actual listener readiness.
        self.requested = True
        rc, _ = self.native('setprop vendor.a9.fingerprint.enable 1')
        self.need(rc == 0, 'HAL_REQUESTED')
        if before is not None and chip_id(self.capture.snapshot(before[0])) is None:
            # Restored interface lazy-start can precede the hook. Restart once
            # under the armed stream, before fixing the measured PID/birth.
            # Never retry a measured crash or restart to obtain a pass.
            rc, _ = self.native('setprop ctl.restart vendor.fps_hal')
            self.need(rc == 0, 'HAL_CAPTURE_RESTART_REQUESTED')
            self.result['premeasurement_restart'] = True
            deadline = time.monotonic() + 20
            current = before
            while time.monotonic() < deadline:
                state, current = self.status()
                self.need(state is not None, 'HAL_STATE_READABLE')
                if current is not None and not same_process(before, current): break
                time.sleep(1)
            self.need(current is not None and not same_process(before, current), 'HAL_CAPTURE_RESTART_COMPLETE')
        end = time.monotonic() + 90
        first, sensor, framework_sensor = None, None, None
        while time.monotonic() < end:
            state, current = self.status()
            self.need(state is not None, 'HAL_STATE_READABLE')
            self.need(state != 'restarting', 'HAL_NOT_RESTARTING')
            if first is not None:
                self.need(same_process(first, current), 'HAL_PROCESS_UNCHANGED')
            elif current is not None:
                first = current
            if first is not None:
                sensor = self.sensor(first[0])
                if sensor is not None:
                    framework_sensor = self.framework()
                    if framework_sensor is not None: break
            time.sleep(3)
        self.need(first is not None and sensor is not None, 'REAL_SENSOR_ID_AND_INITIALIZATION')
        self.need(framework_sensor is not None, 'FRAMEWORK_ONE_FINGERPRINT_SENSOR')
        # A framework provider alone can outlive its HAL. Require the
        # TA-backed chip ID/ready message plus stable process identity for 30s.
        until = time.monotonic() + 30
        while time.monotonic() < until:
            time.sleep(5)
            state, current = self.status()
            self.need(state == 'running' and same_process(first, current), 'HAL_STABLE')
        rc, crash = self.shell('logcat -b crash -d -v brief --pid=' + str(first[0]), timeout=12)
        self.need(rc == 0 and not re.search(r'Fatal signal|FATAL EXCEPTION|backtrace:|Abort message:', crash), 'NO_HAL_CRASH')
        self.need(self.framework() == framework_sensor and self.sensor(first[0]) == sensor, 'FINAL_REGISTRATION_AND_SENSOR_ID')
        self.result.update(passed=True, sensor_id=hex(sensor), stable_seconds=30, framework_sensor_unchanged=True,
                           interface='android.hardware.biometrics.fingerprint@2.1::IBiometricsFingerprint/default')
        print('FP_REGISTRATION PASS sensor_id=' + hex(sensor) + ' framework_sensors=1 stable_s=30 enrollment=NOT_RUN', flush=True)

    def cleanup(self):
        # Close our host stream even if the native stop/diagnostic call fails.
        if self.capture is not None:
            self.capture.stop()
            self.save_diagnostics()
        self.logs.mkdir(parents=True, exist_ok=True)
        try:
            rc, text = self.native('dmesg | grep -E "qseecom: (LOAD|SHM)|a9-sunwave.*stock control|Sunwave control probe" || true')
            (self.logs / 'fingerprint-kernel.log').write_text(text)
            self.result['kernel_diagnostics_readable'] = rc == 0
        except Exception:
            self.result['kernel_diagnostics_readable'] = False
        if self.requested:
            rc, _ = self.native('setprop vendor.a9.fingerprint.enable 0')
            self.result['stop_requested'] = rc == 0
            if rc != 0: self.result['passed'] = False
        self.logs.mkdir(parents=True, exist_ok=True)
        (self.logs / 'fingerprint-result.json').write_text(json.dumps(self.result, indent=2) + '\n')


def main():
    if os.environ.get('A9_BTRY_IN_HOOK') != '1' or not os.environ.get('ADB') or not os.environ.get('A9_BTRY_LOGS'):
        print('FAIL HOOK_CONTEXT'); return 2
    hook = Hook()
    def interrupted(*_): raise InterruptedError()
    signal.signal(signal.SIGTERM, interrupted); signal.signal(signal.SIGINT, interrupted)
    try:
        hook.execute()
    except Exception as error:
        print('FAIL FP_REGISTRATION ' + type(error).__name__, flush=True)
    finally:
        try: hook.cleanup()
        except Exception:
            hook.result['passed'] = False
            print('FAIL FP_CLEANUP', flush=True)
    return 0 if hook.result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
