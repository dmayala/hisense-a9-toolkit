#!/usr/bin/env python3
"""Host Play smoke ONB hook. Run only through the owning b-try session.

Credentials: op read -> Python memory -> ordinary adb shell stdin. Never put
credentials in argv, source files, onb-root.py input, exceptions, or output.
All subprocess output stays in memory; stdout contains fixed verdict lines only.
"""
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess as sp
import sys
import time
import xml.etree.ElementTree as ET

ROOT = str(Path(__file__).resolve().parents[1] / 'boot-deadline/onb-root.py')
CRITERIA = ('WIFI_CONNECTED', 'INTERNET_VALIDATED', 'GMS_CHECKIN',
            'PLAY_SIGNED_OUT_HOME', 'WIFI_DISCONNECTED', 'WIFI_FORGOTTEN',
            'BLUETOOTH_ENABLED', 'BLUETOOTH_NO_CRASH', 'NO_SYSTEM_CRASH_DIALOG')
BLUETOOTH_STATE = r'''
set -o pipefail
timeout 4 dumpsys bluetooth_manager 2>/dev/null | awk '
/^[[:space:]]*enabled:[[:space:]]*true[[:space:]]*$/ {enabled=1}
/^[[:space:]]*state:[[:space:]]*(STATE_)?ON[[:space:]]*$/ {on=1}
/^[[:space:]]*Bluetooth crashed [0-9]+ times/ {
    n=$0; sub(/^.*Bluetooth crashed /,"",n); sub(/ times.*$/,"",n); crashes=n+0; seen=1
}
END {print "BT_STATE",(enabled && on)+0; print "BT_FRAMEWORK_CRASHES",seen ? crashes : -1}' || exit 1
timeout 4 logcat -b events -b crash -d -v brief 2>/dev/null | awk '
/am_crash/ && /,[[:space:]]*com[.]android[.]bluetooth[[:space:]]*,/ {print}
/Fatal signal/ && /\(droid[.]bluetooth\)|\(com[.]android[.]bluetooth\)/ {print}' |
sha256sum | awk '{print "BT_LOG_SIGNATURE",$1}'
'''
START = r'''
state=$(getprop vendor.a9ml.wifi.session.state)
case "$state" in
    '') owner_state=0;; armed) owner_state=1;; starting) owner_state=2;; serving) owner_state=3;;
    ending) owner_state=4;; ended) owner_state=5;; failed) owner_state=6;; awaiting-s2-audit) owner_state=7;; *) owner_state=8;;
esac
printf 'owner_state %s\n' "$owner_state"
case "$state" in
    serving|armed|starting) ;;
    ''|awaiting-s2-audit)
        # Distinct exits: 11 = start already requested, 12 = owner session already
        # terminal (ended/failed/ending: one attempt per boot, cannot be restarted).
        [ "$(getprop vendor.a9ml.wifi.s2.start)" != 1 ] || exit 11
        setprop vendor.a9ml.wifi.s2.start 1 ;;
    *)
        # A terminal session cannot be restarted. Preserve its existing reason
        # now, before boot-local files are lost. No log text or identity escapes.
        reason=0
        if [ -f /mnt/a9-wifi/trial/session-end ] && [ ! -L /mnt/a9-wifi/trial/session-end ]; then
            read -r why < /mnt/a9-wifi/trial/session-end 2>/dev/null || why=
            case "$why" in requested) reason=1;; evidence-ram-low) reason=2;; session-length) reason=3;; esac
        fi
        printf 'owner_end_reason %s\n' "$reason"
        printf 'owner_start_attempted %s\n' "$([ -e /mnt/a9-wifi/trial/start-attempted ] && echo 1 || echo 0)"
        printf 'owner_fault_present %s\n' "$([ -s /mnt/a9-wifi/trial/fault ] && echo 1 || echo 0)"
        for file in /mnt/a9-wifi/session/prepare.out /mnt/a9-wifi/session/run.out; do
            [ -f "$file" ] && [ ! -L "$file" ] || continue
            tail -c 131072 "$file" 2>/dev/null || :
        done | awk '
          /STOP:/ && !code {
            if (/20-second readiness timeout/) code=1
            else if (/offline confinement\/trace audit|mutable companion gate/) code=2
            else if (/explicit MPSS start|MPSS startup budget/) code=3
            else if (/RMTFS|minimal MPSS-only release/) code=4
            else if (/Android HAL\/supplicant readiness timeout/) code=5
            else if (/actual QMI identity differs/) code=6
            else if (/factory MAC/) code=7
            else if (/regulatory/) code=8
            else if (/companion/) code=9
            else if (/monitor|watchdog/) code=10
            else if (/module load/) code=11
            else if (/firmware bridge/) code=12
          }
          END {print "owner_failure_code",code+0}'
        exit 12 ;;
esac
printf 'READY\n'
'''
CAP = r'''
cap_command_rc=0
cap_reply=$(timeout 3 /mnt/a9-wifi/tools/iw dev wlan0 set txpower limit 2000 2>&1) || cap_command_rc=$?
cap_netlink_error=0; cap_permission_error=0; cap_unsupported=0
case "$cap_reply" in *'netlink'*) cap_netlink_error=1;; esac
case "$cap_reply" in *'Operation not permitted'*|*'Permission denied'*) cap_permission_error=1;; esac
case "$cap_reply" in *'Operation not supported'*) cap_unsupported=1;; esac
unset cap_reply
printf 'cap_command_rc %s\ncap_netlink_error %s\ncap_permission_error %s\ncap_unsupported %s\n' \
    "$cap_command_rc" "$cap_netlink_error" "$cap_permission_error" "$cap_unsupported"
[ "$cap_command_rc" = 0 ] || exit "$cap_command_rc"
printf 'READY\n'
'''
GSF = r'''
# Native database read only. The identifier never leaves sqlite3.
for db in /data/user_de/0/com.google.android.gsf/databases/gservices.db /data/user/0/com.google.android.gsf/databases/gservices.db; do
    [ -r "$db" ] || continue
    for sql in /system/bin/sqlite3 /system/xbin/sqlite3 /vendor/bin/sqlite3; do
        [ -x "$sql" ] || continue
        n=$(timeout 4 "$sql" -readonly "$db" "SELECT length(value) FROM main WHERE name='android_id' AND value IS NOT NULL AND value NOT IN ('','0');" 2>/dev/null) || n=
        case "$n" in
            ''|*[!0-9]*) ;;
            *) if [ "$n" -gt 0 ]; then printf 'GSF_LEN %s\n' "$n"; exit 0; fi ;;
        esac
    done
done
printf 'GSF_LEN 0\n'
'''
CHECKIN = r'''
logcat -b main -b system -d -v brief 2>/dev/null | awk '
{s=tolower($0)}
# Match actual request/operation completion, not scheduling, binding or flags.
s ~ /checkinrequestprocessor/ && s ~ /checkin succeeded:/ {ok=1}
s ~ /checkinoperation/ && s ~ /checkin operation finished with result: success([[:space:].]|$)/ {ok=1}
END {print "CHECKIN",ok+0}'
'''
CHECKIN_REQUEST = ('am broadcast -a android.server.checkin.CHECKIN '
                   '-p com.google.android.gms --ez checkin_source_force true '
                   '--es checkin_source_package com.android.shell 2>/dev/null')
CONNECT = r'''
set +x
IFS= read -r s || exit 1
IFS= read -r p || exit 1
cmd wifi connect-network "$s" wpa2 "$p" >/dev/null 2>&1
r=$?
unset s p
exit "$r"
'''
EINK_OPEN = r'''
# Existing vendor admission and fault watcher; no direct gate-opening bypass.
[ "$(getprop ro.boot.a9ml.eink)" = 1 ]
[ "$(getprop vendor.a9ml.eink.ready)" = 1 ]
# The integrated bridge already links hisense to the private display directory.
# Never replace the global path: MPSS/ADSP/Venus/BT also load firmware through it.
firmware_root=$(cat /sys/module/firmware_class/parameters/path) || exit 1
case "$firmware_root" in
    /mnt/a9-wifi/firmware)
        /vendor/bin/a9ml-wifi-firmware.sh >/dev/null || exit 1 ;;
    /data/local/tmp/a9fw) ;;
    *) exit 1 ;;
esac
mkdir -p /data/local/tmp/a9fw/hisense
chmod 755 /data/local/tmp/a9fw /data/local/tmp/a9fw/hisense
for n in a9-waveform.bin a9-ctrl-template.bin; do
    cp "/data/local/tmp/row28-$n" "/data/local/tmp/a9fw/hisense/$n"
    chmod 644 "/data/local/tmp/a9fw/hisense/$n"
    rm -f "/data/local/tmp/row28-$n"
done
# The vendor opener verifies stock PG telemetry in both kernel and module.
[ "$(cat /sys/module/a9_eink/parameters/enable)" = N ] || [ "$(cat /sys/module/a9_eink/parameters/enable)" = 0 ]
setprop vendor.a9ml.eink.bins 0-13
sh /vendor/bin/a9ml-eink.sh open >/dev/null 2>&1
[ "$(getprop vendor.a9ml.eink.gate)" = open ]
printf 'EINK_OPENED\n'
'''
EINK_CLOSE = r'''
setprop vendor.a9ml.eink.gate closed
sh /vendor/bin/a9ml-eink.sh close >/dev/null 2>&1
rm -f /data/local/tmp/row28-a9-waveform.bin /data/local/tmp/row28-a9-ctrl-template.bin
for f in /sys/module/a9_eink/parameters/enable /sys/module/fp9929a/parameters/allow_power_on /sys/module/panel_hisense_hlte556n_eink/parameters/enable; do
    case "$(cat "$f")" in N|0) ;; *) exit 1 ;; esac
done
printf 'EINK_CLOSED\n'
'''


def connected(text):
    return bool(re.search(r'^Wi-?fi is connected(?:\s|$)', text, re.I | re.M))


def target_connected(text, name):
    target = '(?:' + re.escape(name) + '|' + re.escape('"' + name + '"') + ')'
    return connected(text) and bool(re.search(
        r'^Wi-?fi is connected to ' + target + r'\s*$', text, re.I | re.M) or
        re.search(r'\bSSID:\s*' + target + r',\s*BSSID:', text))


def validated(text):
    # NetworkAgentInfo is one line per network, including its capabilities.
    # Require Wi-Fi and VALIDATED in the same live record, not a mobile network
    # or ConnectivityService's historical validation event log.
    return any(re.match(r'^\s*NetworkAgentInfo\{', line) and
               re.search(r'\bWIFI\b', line, re.I) and
               re.search(r'\bVALIDATED\b', line) and
               not re.search(r'\bDISCONNECTED\b', line)
               for line in text.splitlines())


def network_ids(text, name):
    # cmd wifi list-networks: network ID, padded printable SSID, security type.
    # Match only the exact supplied name (quoted or unquoted), never all IDs.
    pattern = re.compile(r'^\s*(\d+)\s+(?:' + re.escape(name) + r'|' +
                         re.escape('"' + name + '"') + r')\s+\S+\s*$')
    return {int(m.group(1)) for line in text.splitlines()
            if (m := pattern.fullmatch(line))}


def classify_play(output, activity):
    """Return source-defined enums only; never return text from a UI node."""
    start, end = output.find('<hierarchy'), output.rfind('</hierarchy>')
    if start < 0 or end < start:
        return 'UNKNOWN', 'NO_XML'
    try:
        tree = ET.fromstring(output[start:end + len('</hierarchy>')])
    except ET.ParseError:
        return 'UNKNOWN', 'INVALID_XML'
    nodes = list(tree.iter('node'))
    labels = [' '.join(n.get(k, '').casefold().split())
              for n in nodes for k in ('text', 'content-desc')]
    text = ' '.join(labels)
    # Error takes precedence over any sign-in control or store tab on the page.
    for phrase, label in (('server error', 'SERVER_ERROR'),
                          ('check your connection', 'CHECK_YOUR_CONNECTION'),
                          ('something went wrong', 'SOMETHING_WENT_WRONG'),
                          ('try again', 'TRY_AGAIN')):
        if phrase in text:
            return 'ERROR', label
    foreground = set(re.findall(
        r'(?:mResumedActivity|topResumedActivity)[^\n]*?\s(com\.[A-Za-z0-9_.]+)/', activity))
    allowed = {'com.android.vending', 'com.google.android.gms', 'com.google.android.gsf.login'}
    if not foreground.intersection(allowed):
        return 'UNKNOWN', 'NO_PLAY_FOREGROUND'
    active_labels = {' '.join(n.get(k, '').casefold().split())
                     for n in nodes if n.get('package') in foreground.intersection(allowed)
                     for k in ('text', 'content-desc')}
    if 'sign in' in active_labels or 'sign-in' in active_labels:
        return 'SIGN_IN', 'SIGN_IN_BUTTON'
    if active_labels.intersection({'use your google account', 'sign in to your google account'}):
        return 'SIGN_IN', 'GOOGLE_SIGN_IN'
    if 'com.android.vending' in foreground:
        for text, label in (('games', 'GAMES_TAB'), ('apps', 'APPS_TAB')):
            if text in active_labels:
                return 'HOME', label
    return 'UNKNOWN', 'NO_MATCH'


def signed_out_page(output, activity):
    return classify_play(output, activity)[0] in ('SIGN_IN', 'HOME')


def crash_dialog(output, windows):
    """Only classify current system error windows or explicit error UI controls."""
    focus = '\n'.join(line for line in windows.splitlines() if 'mCurrentFocus=' in line)
    window_error = bool(re.search(r'Application (?:Error|Not Responding):', focus))
    start, end = output.find('<hierarchy'), output.rfind('</hierarchy>')
    nodes = []
    if start >= 0 and end >= start:
        try:
            nodes = list(ET.fromstring(output[start:end + len('</hierarchy>')]).iter('node'))
        except ET.ParseError:
            pass
    labels = ' '.join(n.get('text', '').casefold() for n in nodes)
    error_control = any(n.get('resource-id') in ('android:id/aerr_close', 'android:id/aerr_restart')
                        for n in nodes)
    if not (window_error or error_control):
        return None
    return 'BLUETOOTH_CRASH' if 'com.android.bluetooth' in focus or 'bluetooth' in labels else 'APPLICATION_CRASH'


def disable_wifi(shell, seconds=15, deadline=None):
    """Request once, then require bounded positive framework off readback.

    Command acceptance isn't completion, and an already-off service can reject
    the request. Keep raw framework output (possibly network names) in memory.
    """
    stop = time.monotonic() + seconds
    if deadline is not None: stop = min(stop, deadline)
    rc, _ = shell('cmd wifi set-wifi-enabled disabled >/dev/null 2>&1', timeout=5)
    evidence = dict(wifi_disable_rc=rc, wifi_status_rc=-1, wifi_disable_polls=0)
    while time.monotonic() < stop:
        sr, output = shell('cmd wifi status 2>/dev/null', timeout=min(5, max(.1, stop-time.monotonic())))
        evidence.update(wifi_status_rc=sr, wifi_disable_polls=evidence['wifi_disable_polls']+1)
        if sr == 0 and re.search(r'^Wi-?fi is disabled\s*$', output, re.I | re.M): return True, evidence
        time.sleep(min(1, max(0, stop-time.monotonic())))
    return False, evidence


class Hook:
    def __init__(self):
        self.results = dict.fromkeys(CRITERIA, False)
        self.name = None
        self.injected = False
        self.ids = set()
        self.adb = os.environ.get('ADB', '')
        self.phase = 'context'
        self.checkin_triggered = False
        self.eink_attempted = False
        self.bt_active = False
        self.bt_previous = None
        self.bt_seen_on = False
        self.bt_dropped = False
        self.bt_proof = False
        self.bt_crashed = False
        self.bt_last_probe = float('-inf')
        self.bt_begin = None
        self.dialog_seen = False
        self.dialog_attempts = 0

    def mark(self, phase):
        # Callers supply fixed source constants only, never a value from a
        # command, property, exception, UI, credential or account.
        self.phase = phase
        print('ROW28_STAGE ' + phase, file=sys.stderr, flush=True)

    def run(self, args, data=None, timeout=20, env=None):
        try:
            r = sp.run(args, input=data, stdout=sp.PIPE, stderr=sp.DEVNULL,
                       timeout=timeout, env=env)
            return r.returncode, r.stdout.decode(errors='replace')
        except (OSError, sp.TimeoutExpired):
            # Never expose subprocess exceptions: they may contain stdin/stdout.
            return 1, ''

    def shell(self, source, data=None, timeout=20):
        return self.run([self.adb, 'shell', '-T', source], data, timeout)

    def native(self, source, timeout=78):
        return self.run(['python3', ROOT], source.encode(), timeout)

    def until(self, seconds, predicate, interval=5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.monitor_bluetooth()
            if predicate():
                return True
            time.sleep(min(interval, max(0, end - time.monotonic())))
        return False

    def bluetooth_snapshot(self):
        rc, output = self.shell(BLUETOOTH_STATE, timeout=12)
        match = re.fullmatch(r'BT_STATE ([01])\s+BT_FRAMEWORK_CRASHES (-1|[0-9]+)\s+BT_LOG_SIGNATURE ([a-f0-9]{64})\s*', output)
        return (int(match[1]), int(match[2]), match[3]) if rc == 0 and match else None

    def monitor_bluetooth(self, force=False):
        if not self.bt_active or (not force and time.monotonic() - self.bt_last_probe < 15):
            return
        current = self.bluetooth_snapshot()
        self.bt_last_probe = time.monotonic()
        if current is None:
            self.bt_proof = False
            self.results['BLUETOOTH_ENABLED'] = False
            self.results['BLUETOOTH_NO_CRASH'] = False
            print('WARN BLUETOOTH_MONITOR label=SNAPSHOT_FAILED', file=sys.stderr, flush=True)
            return
        on, framework, logs = current
        if self.bt_previous is not None:
            _, old_framework, old_logs = self.bt_previous
            # A new event or buffer rollover changes the signature even when
            # event counts stay equal. Lost evidence never becomes a clean pass.
            if logs != old_logs or (framework >= 0 and old_framework >= 0 and framework != old_framework):
                self.bt_crashed = True
                print('WARN BLUETOOTH_MONITOR label=CRASH_OR_COUNTER_RESET', file=sys.stderr, flush=True)
        if self.bt_seen_on and not on:
            self.bt_dropped = True
        self.bt_seen_on = self.bt_seen_on or bool(on)
        self.bt_previous = current
        self.results['BLUETOOTH_ENABLED'] = bool(on) and not self.bt_dropped
        self.results['BLUETOOTH_NO_CRASH'] = self.bt_proof and not self.bt_crashed

    def start_bluetooth(self):
        self.mark('bluetooth_enable')
        self.bt_previous = self.bluetooth_snapshot()
        self.bt_proof = self.bt_previous is not None
        self.bt_begin = time.monotonic()
        self.bt_active = True
        rc, _ = self.shell('cmd bluetooth_manager enable >/dev/null 2>&1')
        print('BLUETOOTH_ENABLE ' + ('REQUESTED' if rc == 0 else 'FAILED'), file=sys.stderr, flush=True)
        self.until(60, lambda: self.results['BLUETOOTH_ENABLED'])

    def credentials(self):
        # Reuse the established POC op binary and authentication path. Nothing
        # is fetched before this method is reached during the real hook.
        op = os.environ.get('A9_OP') or shutil.which('op')
        if not op: raise RuntimeError('credential tool unavailable')
        env = {k: os.environ[k] for k in ('PATH', 'HOME') if k in os.environ}
        env.update(OP_CACHE='false', OP_BIOMETRIC_UNLOCK_ENABLED='false',
                   OP_SERVICE_ACCOUNT_TOKEN=Path('/run/agenix/op-token').read_text().strip())
        values = []
        for field in ('network name', 'wireless network password'):
            rc, value = self.run([op, 'read', '--no-newline',
                                  'op://vault/item/field' + field], timeout=25, env=env)
            if rc or not value or any(c in value for c in '\n\r\0'):
                raise RuntimeError('credential unavailable')
            values.append(value)
        return values

    def status(self):
        rc, output = self.shell('cmd wifi status 2>/dev/null')
        return rc == 0 and target_connected(output, self.name)

    def internet(self):
        rc, output = self.shell('dumpsys connectivity 2>/dev/null')
        return rc == 0 and validated(output) and self.status()

    def saved(self):
        rc, output = self.shell('cmd wifi list-networks 2>/dev/null')
        # A failed/unsupported command is not proof that a network was forgotten.
        if rc:
            return None
        # AOSP emits this exact successful empty-list response WITHOUT a header.
        # It occurs both before the first connection and after the last forget.
        if output.strip() == 'No networks':
            return set()
        if not re.search(r'Network\s+Id\s+SSID', output, re.I):
            return None
        return network_ids(output, self.name)

    def checkin(self, seconds=900):
        started = time.monotonic()
        end = time.monotonic() + seconds
        if not self.checkin_triggered:
            self.checkin_triggered = True  # Exactly one request, no retry loop.
            # Shipped GMS maps checkin_source_force to its forced-check-in
            # configuration. The old unforced request honored a ~2h backoff.
            # Attribute this one request to its actual Android shell caller.
            rc, response = self.shell(CHECKIN_REQUEST, timeout=20)
            sent = rc == 0 and not re.search(r'SecurityException|Permission Denial', response, re.I)
            print('CHECKIN_TRIGGER ' + ('SENT' if sent else 'FAILED') + ' force=1 source=SHELL', file=sys.stderr, flush=True)
        print('CHECKIN_WAIT START budget_s=' + str(seconds), file=sys.stderr, flush=True)
        # Native sqlite is optional; log success covers builds without sqlite3.
        while time.monotonic() < end:
            self.monitor_bluetooth()
            remaining = end - time.monotonic()
            rc, output = self.shell(CHECKIN, timeout=min(15, remaining))
            if rc == 0 and output.strip() == 'CHECKIN 1':
                print('CHECKIN_WAIT SUCCESS evidence=LOG elapsed_s=' + str(int(time.monotonic() - started)), file=sys.stderr, flush=True)
                return True
            remaining = end - time.monotonic()
            if remaining >= 78:
                rc, output = self.native(GSF)
                if rc == 0 and re.fullmatch(r'GSF_LEN [1-9][0-9]*\s*', output):
                    print('CHECKIN_WAIT SUCCESS evidence=GSF_LENGTH elapsed_s=' + str(int(time.monotonic() - started)), file=sys.stderr, flush=True)
                    return True
            time.sleep(min(15, max(0, end - time.monotonic())))
        print('CHECKIN_WAIT TIMEOUT elapsed_s=' + str(int(time.monotonic() - started)), file=sys.stderr, flush=True)
        return False

    def prepare_eink(self):
        self.eink_attempted = True  # A failed partial admission still closes.
        for name in ('a9-waveform.bin', 'a9-ctrl-template.bin'):
            image = Path(os.environ['A9_BTRY_BOOT']).parent
            source = Path(os.environ.get('A9_SWEEP_ASSETS', str(image/'acceptance-assets')))/'firmware'/name
            rc, _ = self.run([self.adb, 'push', source, '/data/local/tmp/row28-' + name], timeout=30)
            if rc:
                print('EINK_GLASS SETUP_FAILED', file=sys.stderr, flush=True)
                return False
        rc, out = self.native(EINK_OPEN)
        ready = rc == 0 and out.strip() == 'EINK_OPENED'
        print('EINK_GLASS ' + ('OPENED' if ready else 'SETUP_FAILED'), file=sys.stderr, flush=True)
        return ready

    def play(self):
        print('PLAY_STEP REACHED', file=sys.stderr, flush=True)
        rc, _ = self.shell('input keyevent KEYCODE_WAKEUP >/dev/null 2>&1')
        print('PLAY_WAKE ' + ('REQUESTED' if rc == 0 else 'FAILED'), file=sys.stderr, flush=True)
        self.prepare_eink()
        rc, _ = self.shell('am start -W -n com.android.vending/com.google.android.finsky.activities.MainActivity >/dev/null 2>&1', timeout=30)
        if rc:
            print('PLAY_SCREEN class=UNKNOWN label=LAUNCH_FAILED', file=sys.stderr, flush=True)
            return False

        screen = ('UNKNOWN', 'NO_XML')
        def page():
            nonlocal screen
            # The check-in wait can outlast the display timeout. Keep only this
            # short inspection awake; do not persist a stay-on setting.
            self.shell('input keyevent KEYCODE_WAKEUP >/dev/null 2>&1')
            wr, windows = self.shell('dumpsys window windows 2>/dev/null', timeout=10)
            if wr:
                self.results['NO_SYSTEM_CRASH_DIALOG'] = False
                print('WARN PLAY_WINDOW label=PROBE_FAILED', file=sys.stderr, flush=True)
            elif not self.dialog_seen:
                self.results['NO_SYSTEM_CRASH_DIALOG'] = True
            label = crash_dialog('', windows) if wr == 0 else None
            if label:
                self.dismiss_crash_dialog(label)
                screen = ('ERROR', 'SYSTEM_CRASH_DIALOG')
                return False
            # /dev/stdout avoids writing a UI dump containing account labels.
            rc, xml = self.shell('uiautomator dump --compressed /dev/stdout 2>/dev/null', timeout=25)
            if '<hierarchy' not in xml:
                # Some app_process/FileOutputStream combinations cannot dump
                # onto /dev/stdout. Force a PTY and use its controlling terminal;
                # XML stays in the captured pipe, never a device/host XML file.
                rc, xml = self.run([self.adb, 'shell', '-tt',
                                   'uiautomator dump --compressed /dev/tty 2>/dev/null'], timeout=25)
                print('PLAY_DUMP transport=PTY xml=' + str(int('<hierarchy' in xml)), file=sys.stderr, flush=True)
            else:
                print('PLAY_DUMP transport=STDOUT xml=1', file=sys.stderr, flush=True)
            label = crash_dialog(xml, windows) if wr == 0 else crash_dialog(xml, '')
            if label:
                self.dismiss_crash_dialog(label)
                screen = ('ERROR', 'SYSTEM_CRASH_DIALOG')
                return False
            ar, activity = self.shell('dumpsys activity activities 2>/dev/null')
            if rc:
                screen = ('UNKNOWN', 'DUMP_FAILED')
            elif ar:
                screen = ('UNKNOWN', 'ACTIVITY_FAILED')
            else:
                screen = classify_play(xml, activity)
            return screen[0] in ('SIGN_IN', 'HOME')
        try:
            return self.until(120, page)
        finally:
            # These are only allowlisted constants, never XML or account labels.
            print('PLAY_SCREEN class=' + screen[0] + ' label=' + screen[1],
                  file=sys.stderr, flush=True)
            # Still before Wi-Fi cleanup/return-to-A, while Play remains open.
            self.camera_frame()

    def dismiss_crash_dialog(self, label):
        self.results['NO_SYSTEM_CRASH_DIALOG'] = False
        if label == 'BLUETOOTH_CRASH':
            self.bt_crashed = True
            self.results['BLUETOOTH_NO_CRASH'] = False
        if not self.dialog_seen:
            print('WARN PLAY_SYSTEM_CRASH_DIALOG label=' + label, file=sys.stderr, flush=True)
            self.dialog_seen = True
        if self.dialog_attempts < 2:
            self.dialog_attempts += 1
            # Only act after identifying a current system crash dialog. Retry
            # the UI dump on the next poll; dismissal never clears the failure.
            rc, _ = self.shell('input keyevent KEYCODE_BACK >/dev/null 2>&1')
            print('PLAY_DIALOG_DISMISS ' + ('REQUESTED' if rc == 0 else 'FAILED'), file=sys.stderr, flush=True)

    def camera_frame(self):
        """Runtime-only host desk-camera capture; never a device screenshot."""
        saved = False
        try:
            output = Path(os.environ['A9_BTRY_LOGS']) / 'play.jpg'
            rc, _ = self.run(['curl', '-s', '-m', '10',
                              'http://127.0.0.1:8093/snapshot', '-o', str(output)], timeout=12)
            if output.is_file():
                output.chmod(0o600)
                with output.open('rb') as frame:
                    saved = rc == 0 and frame.read(2) == b'\xff\xd8'
        except (OSError, KeyError):
            pass
        print('PLAY_CAMERA ' + ('SAVED' if saved else 'FAILED'), file=sys.stderr, flush=True)

    def cleanup(self):
        print('ROW28_CLEANUP begin', file=sys.stderr, flush=True)
        # execute() samples BT at the end of its observation window. Keep
        # diagnostic subprocess waits out of the signal-driven teardown path.
        if self.eink_attempted:
            rc, out = self.native(EINK_CLOSE)
            print('EINK_GLASS ' + ('CLOSED' if rc == 0 and out.strip() == 'EINK_CLOSED' else 'CLOSE_FAILED'), file=sys.stderr, flush=True)
        # Do not wait for network requests to release: disable Wi-Fi as well as
        # disconnect so GMS cannot reconnect during targeted forget operations.
        self.shell('cmd wifi disconnect >/dev/null 2>&1')
        disabled, _ = disable_wifi(self.shell, seconds=20)
        self.results['WIFI_DISCONNECTED'] = disabled
        if not self.injected:
            self.results['WIFI_FORGOTTEN'] = True
            return
        current = self.saved()
        if current is None:
            return
        self.ids.update(current)
        # An unreadable or unrecognized list cannot silently authorize a broad
        # forget. Require at least one exact target ID once injection was tried.
        if not self.ids:
            return
        ok = True
        for net in self.ids:
            rc, _ = self.shell('cmd wifi forget-network ' + str(net) + ' >/dev/null 2>&1')
            ok = ok and rc == 0
        remaining = self.saved()
        self.results['WIFI_FORGOTTEN'] = ok and remaining == set()

    def execute(self):
        self.mark('owner_request')
        rc, _ = self.native(START)
        if rc:
            return

        def serving():
            rc, out = self.shell('getprop vendor.a9ml.wifi.session.state')
            if rc == 0 and out.strip().startswith(('failed', 'ended', 'ending')):
                raise RuntimeError('owner unavailable')
            return rc == 0 and out.strip() == 'serving'
        self.mark('owner_wait')
        if not self.until(300, serving):
            return
        self.mark('owner_serving')
        rc, _ = self.native(CAP)
        if rc:
            return
        self.start_bluetooth()
        self.mark('framework_enable')
        rc, _ = self.shell('cmd wifi set-wifi-enabled enabled >/dev/null 2>&1')
        if rc:
            return
        self.mark('runtime_credentials')
        self.name, password = self.credentials()
        self.mark('saved_list')
        previous = self.saved()
        if previous is None:
            return
        self.ids.update(previous)
        self.mark('connect_request')
        self.injected = True  # Set BEFORE sending, including timeout/signal cases.
        payload = (self.name + '\n' + password + '\n').encode()
        password = None
        rc, _ = self.shell(CONNECT, payload, timeout=30)
        payload = None
        if rc:
            return
        current = self.saved()
        if current is not None:
            self.ids.update(current)
        self.mark('connection_wait')
        self.results['WIFI_CONNECTED'] = self.until(120, self.status)
        if not self.results['WIFI_CONNECTED']:
            return
        self.mark('validation_wait')
        self.results['INTERNET_VALIDATED'] = self.until(120, self.internet)
        if not self.results['INTERNET_VALIDATED']:
            return
        self.mark('checkin_wait')
        self.results['GMS_CHECKIN'] = self.checkin()
        # Still inspect Play after a check-in timeout so both gaps are reported.
        self.mark('play_page')
        self.results['PLAY_SIGNED_OUT_HOME'] = self.play()
        if self.bt_active:
            self.mark('bluetooth_observation')
            self.until(61, lambda: time.monotonic() - self.bt_begin >= 60)
            self.monitor_bluetooth(force=True)
        self.mark('observation_complete')


def main():
    hook = Hook()
    def interrupted(signum, frame):
        raise InterruptedError('hook interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if os.environ.get('A9_BTRY_IN_HOOK') != '1' or not hook.adb or not os.environ.get('A9_BTRY_LOGS'):
            return 2
        rc, context = hook.shell('printf "%s\\n" "$(getprop ro.boot.slot_suffix)" "$(getprop sys.boot_completed)"')
        if rc or context.replace('\r', '').strip() != '_b\n1':
            return 2
        try:
            hook.execute()
        finally:
            # b-try's hook timeout leaves only ten seconds before SIGKILL. The
            # supplied 2100s hold leaves ample room for this normal cleanup.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            hook.cleanup()
    except BaseException:
        # Never print traceback, exception text, raw XML, logs or shell output.
        print('ROW28_EXCEPTION suppressed', file=sys.stderr, flush=True)
    finally:
        print('ROW28_END phase=' + hook.phase + ' injection_attempted=' + str(int(hook.injected)),
              file=sys.stderr, flush=True)
        for criterion in CRITERIA:
            print(('PASS' if hook.results[criterion] else 'FAIL') + ' ' + criterion, flush=True)
    return 0 if all(hook.results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
