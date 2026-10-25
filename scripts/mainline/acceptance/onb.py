#!/usr/bin/env python3
"""One owning b-try acceptance sweep. Import/self-tests never access a device.

Raw subprocess output, UI, account labels and Wi-Fi credentials stay in memory.
Only numeric measurements reach the result. No host camera, audio playback,
credential argv, screen/audio/photo export, or independent lifecycle owner.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import subprocess as sp
import struct
import sys
import time
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
MAINLINE = HERE.parent
spec = importlib.util.spec_from_file_location('sweep_play', MAINLINE/'play/onb.py')
play = importlib.util.module_from_spec(spec); spec.loader.exec_module(play)
PKG = 'org.a9.acceptance'
ASSETS = Path(os.environ.get('A9_SWEEP_IMAGE', os.environ.get('A9_INTEG_IMAGE', '.')))/'acceptance-assets'
# These are measurement labels, not a renumbering of the parity matrix.
CHECKS = ('00_image_context', '00_assets', '01_boot', '01_restart', '02_03_display_hwc', '04_gpu_vulkan', '06_07_input_devices',
          '08_22_sensors', '10_hourly_drain', '11_adb_mtp', '12_wifi', '13_bluetooth',
          '19_front_photo', '19_rear_photo', '19_front_video', '19_rear_video',
          '19_front_af', '19_rear_af', '19_front_torch', '19_rear_torch',
          '18_hardware_encoder', '17_decoder_muted', '20_mic_amplitude',
          '25_thermal', '24_battery', '26_selinux', '27_release', '28_checkin',
          '28_play_install_launch', '29_wrapped_fbe', 'A_stability', '29_pin_lifecycle', '30_fp_no_dumps', 'cleanup')

FP_NO_DUMPS = (HERE/'fp-no-dumps.sh').read_text()

VULKAN_KERNEL = r'''set -o pipefail
dmesg 2>/dev/null | awk '
{lines++; s=tolower($0)}
s ~ /(adreno|gpu|ringbuffer|msm.*drm)/ && s ~ /(fault|hang|recover|timeout|stalled)/ {gpu++}
s ~ /unhandled context fault|arm-smmu.*fault/ {iommu++}
END {print "kernel_lines",lines+0; print "gpu_faults",gpu+0; print "iommu_faults",iommu+0}'
'''

# Secret arrives on stdin, never in the host argv, script source, or receipts.
# locksettings set-pin prints the PIN on some releases: suppress it at source.
PIN_READ = r'''set +x
IFS= read -r pin || exit 1
case "$pin" in ''|*[!0-9]*) exit 1;; esac
[ "${#pin}" -eq 6 ] || exit 1
'''
PIN_SET = PIN_READ + 'locksettings set-pin --user 0 "$pin" >/dev/null 2>&1\n'
PIN_CLEAR = PIN_READ + 'locksettings clear --user 0 --old "$pin" >/dev/null 2>&1\n'
PIN_VERIFY = PIN_READ + r'''
reply=$(locksettings verify --user 0 --old "$pin" 2>&1)
rc=$?
unset pin
[ "$rc" -eq 0 ] || exit 1
[ "$reply" = 'Lock credential verified successfully' ]
'''
PIN_UNLOCK = PIN_READ + r'''
input text "$pin" >/dev/null 2>&1 || exit 1
unset pin
input keyevent KEYCODE_ENTER >/dev/null 2>&1
'''

# Run as the ordinary framework shell, in a separate session with no ADB FDs.
# A function switch destroys its own initiating USB transport. Keep one bounded
# request and its numeric receipt alive; never retry an uncertain mutation.
USB_SET_WORKER = r'''
umask 077
dir=$1
printf 'set_started 1\n' >"$dir/started" || exit 1
reply=$(timeout -k 2 20 svc usb setFunctions mtp 2>&1)
rc=$?
exception=0; logged=0; permission=0
case "$reply" in *Error*|*Exception*) exception=1;; esac
case "$reply" in *'setCurrentFunctions opId:'*) logged=1;; esac
case "$reply" in *'Permission Denial'*|*'Permission denied'*|*SecurityException*) permission=1;; esac
printf 'set_completed 1\nset_command_rc %d\nset_exception %d\nset_request_logged %d\nset_permission_denied %d\n' \
    "$rc" "$exception" "$logged" "$permission" >"$dir/result.tmp" || exit 1
mv "$dir/result.tmp" "$dir/result"
'''

# Native commands contain no credentials, media payload or device identifiers.
# Numeric-only kernel evidence. The logger retains early crypto messages after
# the live ring wraps; bind the saved file to this boot ID before reading it.
KERNEL_LOG = r'''
current_kernel() {
    file=
    for pid in $(timeout 2 pidof a9-mainline-logger 2>/dev/null); do
        case "$pid" in ''|*[!0-9]*) continue;; esac
        root=/proc/$pid/root/kdebuginfo/mainline
        n=$(cat "$root/counter" 2>/dev/null) || continue
        case "$n" in ''|*[!0-9]*) continue;; esac
        dir=$(printf '%s/boot-%010d' "$root" "$n")
        header=$(head -n 1 "$dir/info.txt" 2>/dev/null) || continue
        [ "$header" = "A9_MAINLINE_LOG boot=$(printf 'boot-%010d' "$n") pid=$pid" ] || continue
        bid=$(cat /proc/sys/kernel/random/boot_id) || return 1
        if grep -qx "boot_id=$bid" "$dir/info.txt" 2>/dev/null && [ -r "$dir/kernel.log" ]; then
            file=$dir/kernel.log
            break
        fi
    done
    if [ -z "$file" ]; then
    n=$(cat /kdebuginfo/mainline/counter 2>/dev/null) || n=
    case "$n" in ''|*[!0-9]*) ;;
      *) dir=$(printf '/kdebuginfo/mainline/boot-%010d' "$n")
         bid=$(cat /proc/sys/kernel/random/boot_id) || return 1
         if grep -qx "boot_id=$bid" "$dir/info.txt" 2>/dev/null && [ -r "$dir/kernel.log" ]; then
             file=$dir/kernel.log
         fi ;;
    esac
    fi
    if [ -n "$file" ]; then
        cat "$file" || return 1
        if [ -r "$file.prev" ]; then cat "$file.prev" || return 1; fi
    else
        dmesg
    fi
}
'''
SNAPSHOT = KERNEL_LOG + r'''
set -o pipefail
for name in tombstones anrs; do
    case "$name" in tombstones) dir=/data/tombstones;; anrs) dir=/data/anr;; esac
    if [ -d "$dir" ] && [ -r "$dir" ]; then
        n=$(find "$dir" -maxdepth 1 -type f | wc -l) || n=-1
        sig=$(find "$dir" -maxdepth 1 -type f -exec stat -c '%n %s %Y' {} \; | sort | cksum | awk '{print $1}') || sig=-1
    elif [ ! -e "$dir" ]; then
        # An absent crash directory is an empty set, not a failed snapshot.
        n=0; sig=$(printf '' | cksum | awk '{print $1}')
    else
        n=-1; sig=-1
    fi
    printf '%s %s\n%s_signature %s\n' "$name" "$n" "$name" "$sig"
done
current_kernel | awk '/avc: *denied/ {n++} /avc: *denied/ && /permissive=1/ {p++} END {print "avcs",n+0; print "permissive_avcs",p+0}'
'''
EVENTS = r'''
set -o pipefail
# logcat belongs to the ordinary framework shell, not the native root domain.
# Stream selected lines to the host's private memory. Do not turn a whole log
# buffer into an external printf argument, or depend on a second device hash
# pipeline. Only the numeric count/digest is retained by snapshot().
logcat -b events -b crash -d -v epoch 2>/dev/null | awk '
/am_crash|am_anr|Fatal signal|FATAL EXCEPTION IN SYSTEM PROCESS/ {print "event " $0; n++}
END {print "crash_events",n+0}'
'''
ANR_TRACES = r'''
set -o pipefail
files=0; errors=0; truncated=0
for file in /data/anr/* /data/tombstones/tombstone_*; do
    [ ! -L "$file" ] && [ -f "$file" ] || continue
    case "$file" in *.pb) continue;; esac
    files=$((files+1)); [ "$files" -le 128 ] || { truncated=1; break; }
    # Diagnostic category only: no path, timestamp, PID or command arguments.
    case "${file##*/}" in
        pre_watchdog*) kind=1;; watchdog*) kind=2;; anr_temp*) kind=3;;
        anr*) kind=4;; tombstone_*) kind=5;; *) kind=0;;
    esac
    size=$(stat -c %s "$file" 2>/dev/null) || { errors=$((errors+1)); continue; }
    case "$size" in ''|*[!0-9]*) errors=$((errors+1)); continue;; esac
    cut=0; [ "$size" -le 262144 ] || cut=1
    printf 'trace_begin %d %d %s %d\n' "$files" "$kind" "$size" "$cut"
    if head -c 262144 "$file" 2>/dev/null | awk '
      function valid(s) {return s ~ /^[A-Za-z_][A-Za-z0-9_.:$<>-]*$/ && length(s)<=192}
      # HIDL executables contain @version. Keep this process-name allowance
      # separate from the Java/native symbol alphabet; never export argv.
      function process_name(s) {return s ~ /^[A-Za-z_][A-Za-z0-9_.:@$<>-]*$/ && length(s)<=192}
      /^Cmd ?line:/ {
        name=$0; sub(/^Cmd ?line:[[:space:]]*/,"",name); sub(/[[:space:]].*$/,"",name); sub(/^.*\//,"",name)
        if(process_name(name)) {print "trace_section",name; if(primary=="") primary=name}
        thread=0
      }
      /^pid: .*>>> .* <<</ {
        name=$0; sub(/^.*>>> /,"",name); sub(/ <<<.*$/,"",name); sub(/[[:space:]].*$/,"",name); sub(/^.*\//,"",name)
        if(process_name(name)) {print "trace_section",name; if(primary=="") primary=name}
      }
      /^"/ {
        # Fixed thread categories; arbitrary thread names can contain app data.
        thread=0
        if(/^"main"/) thread=1
        else if(/^"android.fg"/) thread=2
        else if(/^"android.ui"/) thread=3
        else if(/^"android.display"/) thread=4
        else if(/^"Binder:/) thread=5
        else if(/^"watchdog"/) thread=6
      }
      /^[[:space:]]+at / && frames<128 {
        method=$0; sub(/^[[:space:]]+at /,"",method); sub(/[(].*$/,"",method)
        if(process_name(name) && valid(method)) {print "trace_frame",thread,method; frames++}
      }
      /^[[:space:]]*#[0-9][0-9] pc / && frames<128 {
        lib=$4; sub(/^.*\//,"",lib)
        method=$5; sub(/^[(]/,"",method); sub(/[+)].*$/,"",method)
        if(process_name(name) && valid(lib) && valid(method)) {print "trace_native",lib,method; frames++}
      }
      /getRadioProxy|IRadio.*getService|HidlServiceManagement|waitForHwService/ {radio=1}
      /failed to complete startup/ {startup=1}
      /IPowerStats|PowerStatsHALWrapper/ {powerstats=1}
      /Cannot access credential-protected data from direct boot/ {directboot=1}
      END {
        if(process_name(primary)) print "trace_process",primary,radio+0,startup+0
        print "trace_flags",radio+0,startup+0,powerstats+0,directboot+0
        print "trace_frame_limit",(frames>=128)+0
      }'
    then :; else errors=$((errors+1)); fi
    printf 'trace_end\n'
done
printf 'trace_files %s\ntrace_errors %s\ntrace_truncated %s\n' "$files" "$errors" "$truncated"
'''
NATIVE = KERNEL_LOG + r'''
set -o pipefail
n=0
for z in /sys/class/thermal/thermal_zone*; do
    [ -d "$z" ] || continue
    t=$(cat "$z/temp") || exit 1
    case "$t" in ''|*[!0-9-]*) exit 1;; esac
    printf 'thermal_%s_mc %s\n' "$n" "$t"; n=$((n+1))
done
printf 'thermal_zones %s\n' "$n"
current_kernel | awk '
/ICE.*wrapped keys selected/ {ice++}
/Android wrappedkey_v0 add raw_size=68/ {wrapped++}
/AES-256-XTS using blk-crypto [(]native[)]/ {native++}
END {print "ice_wrapped",ice+0; print "wrapped_68",wrapped+0; print "native_crypto",native+0}'
awk '$2=="/data" {mounts++; if ($3=="f2fs") f2fs++; if ($3=="tmpfs") ram++}
END {print "data_mounts",mounts+0; print "data_f2fs",f2fs+0; print "data_tmpfs",ram+0}' /proc/mounts
printf 'real_data_required %s\n' "$([ "$(getprop ro.vendor.a9.data.mode)" = real-wrapped ] && echo 1 || echo 0)"
printf 'data_ready %s\n' "$([ "$(getprop vendor.a9.data.ready)" = 1 ] && echo 1 || echo 0)"
data_rc=$(getprop vendor.a9.trace.data_rc)
case "$data_rc" in ''|*[!0-9]*) data_rc=-1;; esac
printf 'data_rc %s\n' "$data_rc"
'''
HEALTH = NATIVE + r'''
printf 'sensor_ready %s\n' "$([ "$(getprop vendor.a9.sensors.ready)" = 1 ] && echo 1 || echo 0)"
printf 'audio_ready %s\n' "$([ "$(getprop vendor.a9.audio.ready)" = 1 ] && echo 1 || echo 0)"
'''
INPUTS = r'''
set -o pipefail
getevent -lp 2>/dev/null | awk '
/add device/ {devices++}
/ABS_MT_POSITION_X/ {touch++}
/KEY_POWER/ {power++}
/KEY_VOLUMEUP|KEY_VOLUMEDOWN/ {volume++}
END {print "devices",devices+0; print "touch",touch+0; print "power",power+0; print "volume",volume+0}'
'''
WLAN_DEVICE = r'''
# Query the actual netdevice without requiring the framework shell's rtnetlink
# socket. No addresses, network names or credentials leave this command.
if [ -d /sys/class/net/wlan0 ]; then
    index=$(cat /sys/class/net/wlan0/ifindex) || exit 1
    case "$index" in ''|*[!0-9]*) exit 1;; esac
    [ "$index" -gt 0 ] || exit 1
    printf 'wlan_present 1\n'
else
    printf 'wlan_present 0\n'
fi
'''



def policy_for_host(blob):
    """Remove functionfs_seclabel bit12 ONLY from a host libsepol parsing copy.

    libsepol 3.8 supports capabilities 0..9. Our v30 policy also sets bit12.
    Permissive/type bytes remain unchanged; this copy never goes to a device.
    Other unsupported capability bits still fail normal libsepol validation.
    """
    if len(blob) < 56: return blob, 0
    magic, length = struct.unpack_from('<II', blob)
    if magic != 0xf97cff8c or length != 8 or blob[8:16] != b'SE Linux': return blob, 0
    version = struct.unpack_from('<I', blob, 16)[0]
    if version != 30: return blob, 0
    unit, high, count = struct.unpack_from('<III', blob, 32)
    if unit != 64 or count > 8 or len(blob) < 44 + 12*count: return blob, 0
    blocks = [struct.unpack_from('<IQ', blob, 44+12*i) for i in range(count)]
    if not any(start == 0 and bits & (1 << 12) for start,bits in blocks): return blob, 0
    kept = [(start, bits & ~(1 << 12) if start == 0 else bits) for start,bits in blocks]
    kept = [(start,bits) for start,bits in kept if bits]
    bitmap = struct.pack('<III', 64, max((start+64 for start,bits in kept), default=0), len(kept))
    bitmap += b''.join(struct.pack('<IQ', start, bits) for start,bits in kept)
    return blob[:32] + bitmap + blob[44+12*count:], 1


def numbers(text):
    result = {}
    for line in text.splitlines():
        m = re.fullmatch(r'([a-z][a-z0-9_]*) (-?[0-9]+)', line.strip())
        if m:
            if m[1] in result: raise ValueError('duplicate measurement')
            result[m[1]] = int(m[2])
    return result


def numeric_tree(value):
    if type(value) in (int, float): return value
    if isinstance(value, dict): return {k: numeric_tree(v) for k, v in value.items()}
    if isinstance(value, list): return [numeric_tree(v) for v in value]
    raise ValueError('non-numeric evidence')


def nodes(xml):
    start, end = xml.find('<hierarchy'), xml.rfind('</hierarchy>')
    if start < 0 or end < start: return []
    try: return list(ET.fromstring(xml[start:end+len('</hierarchy>')]).iter('node'))
    except ET.ParseError: return []


USB_LOG_COUNTS = r'''
set -o pipefail
logcat -b all -d -v brief -s android.hardware.usb.gadget-service.a9:I UsbDeviceManager:D MtpServer:D MtpService:D '*:S' 2>/dev/null | awk '
/USB descriptors\/bind timed out/ {timeout++}
/bind: UDC/ {bind_error++}
/apply:/ {apply_error++}
/Failed to open control for mtp/ {control_error++}
/Setting default fu[nc]*tions/ {fallback++}
/Binding to MtpService failed|Unable to bind to MtpService/ {mtp_error++}
END {print "usb_descriptor_timeouts",timeout+0; print "usb_bind_errors",bind_error+0;
     print "usb_apply_errors",apply_error+0; print "usb_mtp_control_errors",control_error+0;
     print "usb_default_fallbacks",fallback+0; print "usb_mtp_service_errors",mtp_error+0}'
'''


class Sweep:
    def __init__(self):
        self.results = {k: {'status': 'NOT_RUN', 'evidence': {'attempted': 0}} for k in CHECKS}
        self.adb = os.environ.get('ADB', '')
        self.out = Path(os.environ.get('A9_BTRY_LOGS', '/nonexistent'))
        self.assets = Path(os.environ.get('A9_SWEEP_ASSETS', str(ASSETS)))
        self.end = time.monotonic() + int(os.environ.get('A9_SWEEP_SECONDS', str(int(os.environ.get('A9_BTRY_HOLD', '3600'))-30)))
        self.hvenc_qualify = os.environ.get('A9_SWEEP_HVENC_QUALIFY','0') == '1'
        self.hvenc_variants = self.hvenc_qualify and os.environ.get('A9_SWEEP_HVENC_VARIANTS','0') == '1'
        self.hvenc_extra_reserve = 480 if self.hvenc_variants else 0
        self.drain_requested = os.environ.get('A9_SWEEP_DRAIN','0') == '1'
        self.drain_cleanup_ok = True
        self.hvenc_owner_end = os.environ.get('A9_BTRY_OWNER_END')
        if (self.hvenc_qualify or self.drain_requested) and self.hvenc_owner_end:
            self.end = min(self.end, float(self.hvenc_owner_end)-15)
        # Reserve a final PIN window; other measurements and optional codec
        # qualification must finish before a credential can be installed.
        self.pin_requested = os.environ.get('A9_SWEEP_PIN_LIFECYCLE') == 'fresh-disposable-formatted-b'
        self.pin_end = self.end
        if self.pin_requested: self.end -= min(240, max(0, self.end-time.monotonic()-300))
        self.qualification_end = self.end
        # A short existing hold still runs its ordinary rows. Only a long,
        # owner-bounded hold reserves the hour plus setup/restoration margin.
        self.drain_reserved = 3900 if (self.drain_requested and self.hvenc_owner_end
                                      and self.end-time.monotonic() >= 4800) else 0
        self.end -= self.drain_reserved
        self.hvenc_qualification_attempted = False
        self.hvenc_finalizing = False
        self.command_end = self.end - 300 - self.hvenc_extra_reserve
        self.wait_limit = self.end - 300 - self.hvenc_extra_reserve
        self.name = None; self.network_before = None; self.injected = False
        self.eink_attempted = False; self.bt_before = None
        self.baseline = None; self.dialogs = 0; self.probes = 0; self.lost = 0; self.crashes = 0
        self.previous_snapshot = None
        self.signature_changes = {k: 0 for k in ('tombstones', 'anrs', 'crash_events')}
        self.losses = dict(native=0, events=0, window=0, incomplete=0, usb=0, diagnostic=0)
        self.event_seen = set(); self.event_records = 0; self.event_unparsed = 0
        self.system_server_fatals = 0
        self.trace_seen = set(); self.trace_records = 0
        self.evidence_boot = 0; self.last_anr_evidence = -1e30
        self.anr_evidence = dict(samples=0, processes={}, event_records=0)
        self.started = time.monotonic(); self.last_sample = -1e9
        self.bt_start = None; self.bt_crash = False; self.bt_seen = False; self.bt_dropped = False
        self.bt_probes = 0; self.assets_ok = False
        self.restart_attempted = False; self.cleaned = False; self.touched = False
        self.cleanup_ok = False; self.native_failures = 0; self.app_stage = 0
        self.pin_cleanup_ok = True
        self.row_errors = set()

    def operation_end(self):
        # Protect LAST-stage qualification from slow snapshots, cleanup and
        # repeated native evidence collection (integ24 exhausted its reserve).
        end = self.command_end
        if self.hvenc_qualify and not self.hvenc_qualification_attempted:
            reserve = (155 if self.hvenc_finalizing else 240) + self.hvenc_extra_reserve
            end = min(end, self.qualification_end-reserve)
        return end

    def run(self, argv, data=None, timeout=20, env=None, capture_stderr=False, capture_partial=False):
        try:
            left = self.operation_end() - time.monotonic()
            if left <= 0: return 124, ''
            r = sp.run(list(map(str, argv)), input=data, stdout=sp.PIPE, stderr=sp.STDOUT if capture_stderr else sp.DEVNULL,
                       timeout=max(.1, min(timeout, left)), env=env)
            return r.returncode, r.stdout.decode(errors='replace')
        except sp.TimeoutExpired as error:
            # Only the public synthetic encoder opts into partial stdout.
            # Keep the existing discard behavior for credential/UI commands.
            if not capture_partial: return 1, ''
            output = error.stdout or b''
            return 124, output if isinstance(output, str) else output.decode(errors='replace')
        except OSError: return 1, ''

    def shell(self, source, data=None, timeout=20, capture_stderr=False, capture_partial=False):
        return self.run([self.adb, 'shell', '-T', source], data, timeout,
                        capture_stderr=capture_stderr, capture_partial=capture_partial)

    def native(self, source, timeout=78):
        rc, text = self.run(['python3', '-B', MAINLINE/'boot-deadline/onb-root.py'], source.encode(), timeout)
        if rc:
            self.native_failures += 1
            match = re.search(r'A9_ONB_NATIVE_EXIT rc=([0-9]+) line=([0-9]+)', text)
            remote_rc, line = map(int, match.groups()) if match else (-1, -1)
            self.last_remote_rc = remote_rc
            helper = re.search(r'A9_ONB_TRANSPORT phase=([0-9]+) rc=(-?[0-9]+)', text)
            helper_phase, helper_rc = map(int, helper.groups()) if helper else (-1, -1)
            error = re.search(r'A9_ONB_ERROR kind=([0-9]+)', text)
            error_kind = int(error[1]) if error else -1
            step = {SNAPSHOT: 1, NATIVE: 2, INPUTS: 3, 'base64 /sys/fs/selinux/policy': 4,
                    play.EINK_OPEN: 5, play.EINK_CLOSE: 6, play.START: 7, play.CAP: 8, play.GSF: 9}.get(source, 0)
            print('NATIVE_DETAIL step=%d rc=%d remote_rc=%d line=%d helper_phase=%d helper_rc=%d error_kind=%d failures=%d' %
                  (step, rc, remote_rc, line, helper_phase, helper_rc, error_kind, self.native_failures), flush=True)
        return rc, text

    def put(self, check, passed, **evidence):
        if check in self.row_errors: passed = False; evidence['row_error'] = 1
        self.results[check] = {'status': 'PASS' if passed else 'FAIL', 'evidence': numeric_tree(evidence)}
        self.persist()
        print(check, self.results[check]['status'], json.dumps(evidence, sort_keys=True), flush=True)

    def not_run(self, check, reason=None, **evidence):
        self.results[check] = {'status': 'NOT_RUN', 'evidence': numeric_tree(evidence)}
        if reason is not None:
            if reason != 'needs_account': raise ValueError('unknown prerequisite reason')
            self.results[check]['reason'] = reason
        self.persist()

    def wait(self, seconds, predicate=None):
        stop = min(self.wait_limit, self.operation_end(), time.monotonic() + seconds)
        while time.monotonic() < stop:
            self.monitor()
            if predicate is not None and predicate(): return True
            time.sleep(min(3, max(0, stop-time.monotonic())))
        return False

    def snapshot(self):
        rc, output = self.native(SNAPSHOT)
        d = numbers(output)
        expected = {'tombstones', 'anrs', 'tombstones_signature', 'anrs_signature', 'crash_events', 'crash_signature', 'avcs', 'permissive_avcs'}
        # Keep AVC telemetry even when crash evidence is unavailable.
        er, events = self.shell(EVENTS)
        d.update(numbers(events))
        records = [line[6:] for line in events.splitlines() if line.startswith('event ')]
        # Keep valid process-name evidence from a partial log read while the
        # nonzero reader status still makes this observation fail closed.
        if records: self.record_crash_events(records)
        if er == 0 and d.get('crash_events') == len(records):
            d['crash_signature'] = int.from_bytes(hashlib.sha256('\n'.join(records).encode()).digest(), 'big')
        d['native_rc'] = rc; d['events_rc'] = er
        if rc: d.update(avcs=-1, permissive_avcs=-1)
        d['missing_fields'] = len(expected - d.keys())
        d['invalid_fields'] = sum(d.get(k, -1) < 0 for k in expected if k in d)
        d['snapshot_ok'] = int(rc == 0 and er == 0 and expected <= d.keys()
                               and all(d[k] >= 0 for k in expected))
        return d

    @staticmethod
    def process_name(value):
        # Only a process token; never a command line, PID, UID or free text.
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*(?:@[0-9]+(?:\.[0-9]+)*(?:-[A-Za-z0-9_.-]+)?)?(?::[A-Za-z0-9_.-]+)?', value) and len(value) <= 128:
            if not re.search(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-', value): return value
        return None

    def record_crash_events(self, records):
        additions = []
        for line in records:
            identity = hashlib.sha256(line.encode()).digest()  # private memory only
            if identity in self.event_seen: continue
            if len(self.event_seen) >= 8192: break
            self.event_seen.add(identity)
            if 'FATAL EXCEPTION IN SYSTEM PROCESS' in line:
                self.system_server_fatals += 1
                additions.append(dict(event='system_server_fatal',process='system_server',
                                      boot=self.evidence_boot,cause_code=0))
                continue
            match = re.search(r'\b(am_anr|am_crash)\s*(?:\([^)]*\))?\s*:\s*\[\s*\d+\s*,\s*\d+\s*,\s*([^,\]]+)', line)
            name = self.process_name(match[2].strip()) if match else None
            if not name:
                self.event_unparsed += 1; continue
            # Fixed categories retain the reason without broadcast extras,
            # exception messages, source paths, account labels or IDs.
            cause = 0
            if 'failed to complete startup' in line: cause = 1
            elif 'android.safetycenter.action.REFRESH_SAFETY_SOURCES' in line: cause = 2
            elif 'Cannot access credential-protected data from direct boot' in line: cause = 3
            additions.append(dict(event=match[1], process=name, boot=self.evidence_boot, cause_code=cause))
        if additions:
            dest = self.out/'stability-events.jsonl'
            with dest.open('a') as f:
                for record in additions: f.write(json.dumps(record, sort_keys=True)+'\n')
            dest.chmod(0o600); self.event_records += len(additions)

    def record_trace_details(self, text, rc):
        """Save only the typed device summary; reject arbitrary text twice.

        Ordinals describe files in a sample, not unique incidents. A changing
        file produces updated evidence; identical summaries are deduplicated.
        """
        record = None; section = None; additions = []
        symbol = r'[A-Za-z_][A-Za-z0-9_.:$<>-]{0,191}'
        def keep(complete):
            if record is None: return
            record.update(complete=int(complete), reader_rc=rc, boot=self.evidence_boot)
            identity = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).digest()
            if identity not in self.trace_seen and len(self.trace_seen) < 8192:
                self.trace_seen.add(identity)
                additions.append(dict(record, sample=self.anr_evidence['samples']))
        for line in text.splitlines():
            begin = re.fullmatch(r'trace_begin ([0-9]+) ([0-5]) ([0-9]+) ([01])', line)
            if begin:
                keep(False)
                record = dict(slot=int(begin[1]), kind=int(begin[2]), bytes=int(begin[3]),
                              prefix_truncated=int(begin[4]), sections=[])
                section = None
            elif line == 'trace_end':
                keep(True); record = None; section = None
            elif record is not None:
                match = re.fullmatch(r'trace_section (\S+)', line)
                if line.startswith('trace_section '): section = None
                name = self.process_name(match[1]) if match else None
                if name:
                    section = dict(process=name, frames=[], native=[])
                    record['sections'].append(section)
                frame = re.fullmatch(r'trace_frame ([0-6]) ('+symbol+')', line)
                native = re.fullmatch(r'trace_native ('+symbol+') ('+symbol+')', line)
                if section is not None and frame:
                    section['frames'].append(dict(thread=int(frame[1]), method=frame[2]))
                if section is not None and native:
                    section['native'].append(dict(library=native[1], method=native[2]))
                flags = re.fullmatch(r'trace_flags ([01]) ([01]) ([01]) ([01])', line)
                if flags:
                    record['hints'] = dict(zip(('radio_wait','startup_timeout','powerstats_wait','direct_boot'),map(int,flags.groups())))
                limit = re.fullmatch(r'trace_frame_limit ([01])', line)
                if limit: record['frame_limit_reached'] = int(limit[1])
        keep(False)
        if additions:
            dest = self.out/'stability-traces.jsonl'
            with dest.open('a') as f:
                for record in additions: f.write(json.dumps(record, sort_keys=True)+'\n')
            dest.chmod(0o600); self.trace_records += len(additions)

    def collect_anr_evidence(self, force=False):
        if not force and time.monotonic()-self.last_anr_evidence < 60: return
        self.last_anr_evidence = time.monotonic()
        # Raw activity output stays in memory. The trace reader retains only
        # sanitized methods/diagnostic categories, before transport.
        ar, activity = self.shell('set -o pipefail; timeout 5 dumpsys activity lastanr 2>/dev/null | head -c 131072', timeout=8)
        nr, traces = self.native(ANR_TRACES, timeout=18)
        self.record_trace_details(traces, nr)
        counts = numbers('\n'.join(line for line in traces.splitlines()
                                  if re.fullmatch(r'trace_(files|errors|truncated) [0-9]+', line)))
        sample = dict(boot=self.evidence_boot, activity_rc=ar, trace_rc=nr, **counts)
        if ar or nr or counts.get('trace_errors', 1) or counts.get('trace_truncated', 1):
            self.lost += 1; self.losses['diagnostic'] += 1
            print('STABILITY_LOSS', json.dumps(dict(diagnostic=1, activity_rc=ar, trace_rc=nr)), flush=True)
        trace_names = []
        for line in traces.splitlines():
            match = re.fullmatch(r'trace_process (\S+) ([01]) ([01])', line)
            name = self.process_name(match[1]) if match else None
            if name: trace_names.append((name, int(match[2]), int(match[3])))
        activity_names = []
        for name in re.findall(r'^\s*(?:ANR in|Process:)\s+(\S+)', activity, re.M):
            name = self.process_name(name)
            if name: activity_names.append(name)
        radio = int(bool(re.search(r'getRadioProxy|IRadio.*getService|HidlServiceManagement|waitForHwService', activity)))
        startup = int('failed to complete startup' in activity)
        activity = None
        for name, r, s in trace_names + [(n,radio,startup) for n in activity_names]:
            value = self.anr_evidence['processes'].setdefault(name, dict(observations=0, radio_wait=0, startup_timeout=0))
            value['observations'] += 1; value['radio_wait'] |= r; value['startup_timeout'] |= s
        self.anr_evidence.update(samples=self.anr_evidence['samples']+1, last=sample,
                                 event_records=self.event_records, event_unparsed=self.event_unparsed,
                                 trace_records=self.trace_records, trace_limit_reached=int(len(self.trace_seen)>=8192),
                                 event_limit_reached=int(len(self.event_seen)>=8192))
        dest = self.out/'stability-evidence.json.tmp'
        dest.write_text(json.dumps(self.anr_evidence, indent=2, sort_keys=True)+'\n')
        dest.chmod(0o600); dest.replace(self.out/'stability-evidence.json')

    def monitor(self, force=False):
        if not force and time.monotonic()-self.last_sample < 12: return
        self.last_sample = time.monotonic()
        d = self.snapshot()
        if not d.get('snapshot_ok'):
            self.lost += 1
            loss = dict(native=int(d.get('native_rc', 0) != 0), events=int(d.get('events_rc', 0) != 0),
                        incomplete=int(bool(d.get('missing_fields', 0) or d.get('invalid_fields', 0))))
            for k,v in loss.items(): self.losses[k] += v
            print('STABILITY_LOSS', json.dumps(loss, sort_keys=True), flush=True)
        elif self.baseline is None:
            self.baseline = d
            # Existing events from this boot are failures too; do not baseline
            # away startup ANRs that happened before the first sampling point.
            if any(d.get(k, 0) > 0 for k in ('crash_events', 'anrs', 'tombstones')): self.crashes += 1
        elif self.previous_snapshot is not None:
            changed = {name: int(d[key] != self.previous_snapshot[key]) for name,key in
                       [('tombstones','tombstones_signature'), ('anrs','anrs_signature'), ('crash_events','crash_signature')]}
            if any(changed.values()):
                self.crashes += 1
                for name,n in changed.items(): self.signature_changes[name] += n
                print('STABILITY_CHANGE', json.dumps(changed, sort_keys=True), flush=True)
        if d.get('snapshot_ok'): self.previous_snapshot = d
        if any(d.get(k, 0) > 0 for k in ('anrs', 'crash_events', 'tombstones')): self.collect_anr_evidence()
        rc, window = self.shell('dumpsys window windows 2>/dev/null', timeout=10)
        if rc:
            self.lost += 1; self.losses['window'] += 1
            print('STABILITY_LOSS', json.dumps(dict(window=1, rc=rc)), flush=True)
        else:
            self.probes += 1
            self.dialogs += int(play.crash_dialog('', window) is not None)
        if self.bt_start is not None:
            rc, text = self.shell(play.BLUETOOTH_STATE, timeout=12)
            match = re.fullmatch(r'BT_STATE ([01])\s+BT_FRAMEWORK_CRASHES (-1|[0-9]+)\s+BT_LOG_SIGNATURE ([a-f0-9]{64})\s*', text)
            if rc or not match: self.bt_crash = True
            else:
                state = (int(match[1]), int(match[2]), match[3]); self.bt_probes += 1
                if self.bt_before is not None and state[1:] != self.bt_before[1:]: self.bt_crash = True
                if self.bt_seen and not state[0]: self.bt_dropped = True
                self.bt_seen |= bool(state[0]); self.bt_before = state

    def muted(self):
        # Use our installed package identity and MODIFY_AUDIO_SETTINGS. Shell
        # command success is not proof of a volume change. Never restore volume.
        if not self.assets_ok: return False
        d = self.app(PKG, '.Probe', '--es mode mute', 15)
        if not isinstance(d, dict): d = {}
        streams = d.get('streams')
        valid = (type(d.get('completed')) is int and d['completed'] == 1
                 and isinstance(streams, list) and len(streams) == 5)
        if valid:
            valid = all(isinstance(r, dict)
                        and all(type(r.get(k)) is int for k in ('stream', 'minimum', 'volume'))
                        and r['stream'] == stream and r['minimum'] >= 0
                        and r['volume'] == r['minimum']
                        for stream, r in zip((1, 2, 3, 4, 5), streams))
        if not valid:
            # Fixed keys/numbers only; malformed/absent/partial results fail closed.
            levels = []
            if isinstance(streams, list):
                for r in streams[:5]:
                    if isinstance(r, dict):
                        levels.append('/'.join(str(r[k]) if type(r.get(k)) is int else '-1'
                                               for k in ('stream', 'volume', 'minimum')))
            print('MUTE_DETAIL completed=%d records=%d levels=%s' %
                  (int(d.get('completed') == 1), len(streams) if isinstance(streams, list) else 0,
                   ','.join(levels)), flush=True)
        return valid

    def prepare_assets(self):
        try:
            manifest = json.loads((self.assets/'assets.json').read_text())
            if 'mtp-session' not in manifest['assets'] or 'acceptance/mtp-session.c' not in manifest['sources']: return False
            if not all('firmware/'+name in manifest['assets'] for name in ('a9-waveform.bin', 'a9-ctrl-template.bin')): return False
            for p, h in manifest['assets'].items():
                if hashlib.sha256((self.assets/p).read_bytes()).hexdigest() != h: return False
            for p, h in manifest['sources'].items():
                if hashlib.sha256((MAINLINE/p).read_bytes()).hexdigest() != h: return False
        except (OSError, ValueError, KeyError): return False
        for apk in ('acceptance.apk', 'photo/a9-camera2-probe.apk', 'decoder/a9-codec2-playback.apk'):
            rc, text = self.run([self.adb, 'install', '-r', '-g', self.assets/apk], timeout=60)
            if rc or not re.search(r'^Success\s*$', text, re.M): return False
        self.assets_ok = True
        return True

    def app(self, package, activity, extras='', seconds=90):
        self.app_stage = 1
        self.shell('am force-stop '+package+' >/dev/null 2>&1')
        # Wake only; dismiss-keyguard cannot bypass a credential-protected lock.
        self.shell('input keyevent KEYCODE_WAKEUP >/dev/null 2>&1')
        # The mute activity can show over keyguard. Dismiss only after mute has
        # succeeded, so an unlock sound is never requested at default volume.
        if not (package == PKG and extras in ('--es mode mute', '--es mode keyguard')):
            self.shell('wm dismiss-keyguard >/dev/null 2>&1')
        rc, _ = self.shell('run-as '+package+' rm -f files/result.json')
        if rc: return {'probe_result': 0, 'app_stage': 1, 'command_rc': rc}
        self.app_stage = 2
        rc, text = self.shell('am start -W --user 0 -n '+package+'/'+activity+' '+extras+' 2>/dev/null', timeout=25)
        if rc or re.search(r'Error|Exception', text):
            return {'probe_result': 0, 'app_stage': 2, 'command_rc': rc}
        result = None; partial = {}
        self.app_stage = 3
        # A fresh per-invocation deadline also works after the global test phase
        # expires; reboot observation has its own reserved budget.
        stop = min(self.operation_end(), time.monotonic()+seconds)
        try:
            while time.monotonic() < stop:
                rc, text = self.shell('run-as '+package+' cat files/result.json 2>/dev/null', timeout=8)
                if rc == 0:
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            # Camera writes atomic capability/stage evidence
                            # before touching the encoder. Partial output is
                            # evidence, never a completed recording or PASS.
                            if (((package == PKG and '--es mode camera' in extras)
                                    or package == 'org.a9.cameraprobe')
                                    and parsed.get('completed') == 0 and not parsed.get('error')):
                                partial = parsed
                            else:
                                result = parsed; break
                    except ValueError: pass
                self.monitor()
                time.sleep(min(2, max(0, stop-time.monotonic())))
        finally: self.shell('am force-stop '+package+' >/dev/null 2>&1')
        return result if result is not None else dict(partial, probe_result=0, app_stage=3, timed_out=1)

    def eink(self):
        self.eink_attempted = True
        for name in ('a9-waveform.bin', 'a9-ctrl-template.bin'):
            rc, _ = self.run([self.adb, 'push', self.assets/'firmware'/name, '/data/local/tmp/row28-'+name], timeout=30)
            if rc: return False
        rc, text = self.native(play.EINK_OPEN)
        return rc == 0 and text.strip() == 'EINK_OPENED'

    def keyguard_state(self):
        d = self.app(PKG, '.Probe', '--es mode keyguard', 8)
        keys = ('device_secure', 'device_locked', 'keyguard_locked')
        if d.get('completed') != 1 or any(d.get(k) not in (0, 1) for k in keys):
            return None
        return {k: d[k] for k in keys}

    def pin_state_wait(self, secure, locked=None):
        stop = min(self.operation_end(), time.monotonic()+15)
        while time.monotonic() < stop:
            state = self.keyguard_state()
            if state is None: return False
            if state['device_secure'] == secure and (locked is None or
                    state['device_locked'] == locked == state['keyguard_locked']):
                return True
            time.sleep(.5)
        return False

    def pin_lifecycle(self):
        check = '29_pin_lifecycle'
        if os.environ.get('A9_SWEEP_PIN_LIFECYCLE') != 'fresh-disposable-formatted-b':
            self.not_run(check, opt_in_required=1); return
        evidence = dict(attempted=0, disposable_data_attested=1, preconditions=0,
                        initially_unsecured=0, capabilities=0, set_completed=0, set=0, verified=0,
                        locked=0, unlocked=0, removed=0, finally_unsecured=0,
                        cleanup_attempted=0, cleanup_ok=1, interrupted_or_exception=0)
        dispatched = False; pin = None
        try:
            # /data is shared across slots. The opt-in attests a NEW format;
            # slot B alone says nothing about whether the data is disposable.
            rc, slot = self.shell('getprop ro.boot.slot_suffix')
            if rc or slot.strip() != '_b' or self.command_end-time.monotonic() < 180: return
            evidence['preconditions'] = 1
            state = self.keyguard_state()
            if state is None or state['device_secure'] != 0: return
            evidence['initially_unsecured'] = 1
            rc, help_text = self.shell('locksettings help 2>/dev/null')
            if rc or not all(token in help_text for token in ('set-pin', 'verify', 'clear', '--old', '--user')): return
            evidence['capabilities'] = 1
            # Fresh six-digit disposable test PIN; never saved or printed.
            pin = (str(secrets.randbelow(900000)+100000)+'\n').encode()
            evidence['attempted'] = 1
            dispatched = True  # Uncertain transport also requires cleanup.
            rc, _ = self.shell(PIN_SET, data=pin, timeout=30)
            evidence['set_completed'] = int(rc == 0)
            if rc or not self.pin_state_wait(1): return
            evidence['set'] = 1
            rc, _ = self.shell(PIN_VERIFY, data=pin, timeout=30)
            if rc: return
            evidence['verified'] = 1
            rc, _ = self.shell('input keyevent KEYCODE_SLEEP >/dev/null 2>&1')
            if rc or not self.pin_state_wait(1, 1): return
            evidence['locked'] = 1
            # Show the credential bouncer using ordinary UI input. There is no
            # dismiss-keyguard or trust-agent substitute for authentication.
            rc, _ = self.shell('input keyevent KEYCODE_WAKEUP >/dev/null 2>&1; '
                               'input swipe 412 1400 412 300 300 >/dev/null 2>&1')
            if rc: return
            time.sleep(1)
            rc, _ = self.shell(PIN_UNLOCK, data=pin)
            if rc or not self.pin_state_wait(1, 0): return
            evidence['unlocked'] = 1
        except Exception:
            evidence['interrupted_or_exception'] = 1
        finally:
            if dispatched:
                evidence['cleanup_attempted'] = 1
                self.pin_cleanup_ok = False
                # Keep cleanup possible when the ordinary feature budget ends.
                self.command_end = max(self.command_end, min(self.end, time.monotonic()+35))
                try:
                    rc, _ = self.shell(PIN_CLEAR, data=pin, timeout=25)
                    evidence['removed'] = int(rc == 0)
                    evidence['finally_unsecured'] = int(self.pin_state_wait(0))
                    # A lost setter transport may leave a still-running remote
                    # mutation. An unsecured observation cannot certify its end.
                    self.pin_cleanup_ok = bool(evidence['set_completed'] and evidence['removed']
                                               and evidence['finally_unsecured'])
                except Exception:
                    pass
                evidence['cleanup_ok'] = int(self.pin_cleanup_ok)
            pin = None
            passed = all(evidence[k] == 1 for k in ('set', 'verified', 'locked', 'unlocked',
                                                    'removed', 'finally_unsecured', 'cleanup_ok'))
            self.put(check, passed, **evidence)

    def independent(self, checks, action, *args, **kwargs):
        """A row verdict/error is data, not permission to abandon later rows."""
        try: return action(*args, **kwargs)
        except InterruptedError: raise
        except Exception:
            # Exception text can contain credentials or raw subprocess output.
            for check in checks:
                self.row_errors.add(check)
                self.put(check, False, **dict(self.results[check]['evidence'], row_error=1))
            print('ROW_ERROR '+json.dumps(dict(rows=len(checks))), flush=True)
            return False

    def final_pin(self):
        if not self.pin_requested or not self.touched: return
        self.end = self.pin_end
        self.command_end = self.end-35
        self.wait_limit = self.command_end
        try: self.independent(('29_pin_lifecycle',), self.pin_lifecycle)
        finally:
            # pin_lifecycle always attempts clear in its own finally, even if
            # set/verify/transport was uncertain. Reflect that final verdict.
            self.cleaned = False
            self.command_end = max(self.command_end, min(self.end, time.monotonic()+35))
            self.independent(('cleanup',), self.cleanup)

    def basic(self):
        self.independent(('01_boot',), self._basic_boot)
        self.independent(('02_03_display_hwc',), self._basic_display)
        self.independent(('06_07_input_devices',), self._basic_inputs)
        self.independent(('25_thermal', '29_wrapped_fbe', '01_boot'), self._basic_storage)
        self.independent(('24_battery',), self._basic_battery)
        self.independent(('27_release',), self._basic_release)

    def vulkan(self):
        # Always attempt the real dispatch; diagnostic loss cannot veto it.
        d = self.app(PKG, '.Probe', '--es mode vulkan', seconds=20)
        rc, text = self.native(VULKAN_KERNEL, timeout=8)
        kernel = numbers(text)
        keys = ('completed', 'vk_load', 'vk_device', 'vk_a610', 'vk_turnip', 'vk_api',
                'vk_driver', 'vk_stage', 'vk_rc', 'vk_submitted', 'vk_fence', 'vk_words', 'vk_bad')
        evidence = {k: d.get(k, -1) if type(d.get(k)) is int else -1 for k in keys}
        evidence.update(kernel_rc=rc, kernel_lines=kernel.get('kernel_lines', -1),
                        gpu_faults=kernel.get('gpu_faults', -1), iommu_faults=kernel.get('iommu_faults', -1),
                        timed_out=int(d.get('timed_out') == 1))
        compute = all(evidence[k] == 1 for k in ('completed', 'vk_load', 'vk_device', 'vk_a610', 'vk_turnip', 'vk_submitted', 'vk_fence'))
        compute = compute and evidence['vk_driver'] == 18 and evidence['vk_api'] >= 4194304 and evidence['vk_stage'] == 8
        compute = compute and evidence['vk_rc'] == evidence['vk_bad'] == 0 and evidence['vk_words'] == 64 and not d.get('error') and not evidence['timed_out']
        self.put('04_gpu_vulkan', compute and rc == 0 and evidence['kernel_lines'] > 0
                 and evidence['gpu_faults'] == evidence['iommu_faults'] == 0, **evidence)

    def _basic_boot(self):
        rc, text = self.shell('getprop sys.boot_completed; getprop ro.boot.slot_suffix')
        self.put('01_boot', rc == 0 and text.strip().splitlines() == ['1', '_b'], completed=int(rc == 0 and text.startswith('1')), slot_b=int('_b' in text))

    def _basic_display(self):
        opened = self.eink()
        rc, text = self.shell('pidof surfaceflinger; dumpsys SurfaceFlinger --display-id; dumpsys SurfaceFlinger --list', timeout=15)
        lines = text.splitlines(); sf = int(bool(lines and re.fullmatch(r'[0-9 ]+', lines[0])))
        display = int(bool(re.search(r'Display [0-9]+', text)))
        # Query the actual AIDL service: ps NAME is truncated on this build.
        cr, cp = self.shell('service check android.hardware.graphics.composer3.IComposer/default; service check SurfaceFlinger', timeout=12)
        composer = int(bool(re.search(r'^Service android[.]hardware[.]graphics[.]composer3[.]IComposer/default: found$', cp, re.M)))
        binder = int(bool(re.search(r'^Service SurfaceFlinger: found$', cp, re.M)))
        self.put('02_03_display_hwc', opened and rc == 0 and cr == 0 and all((sf, display, composer, binder)), eink_open=int(opened), sf=sf, display=display, composer=composer, binder=binder, layers=max(0, len(lines)-2))

    def _basic_inputs(self):
        rc, text = self.native(INPUTS); d = numbers(text)
        self.put('06_07_input_devices', rc == 0 and all(d.get(k, 0) > 0 for k in ('touch', 'power', 'volume')), **d)

    def _basic_storage(self):
        rc, text = self.native(NATIVE); d = numbers(text)
        zones = {k: v for k, v in d.items() if re.fullmatch('thermal_[0-9]+_mc', k)}
        self.put('25_thermal', rc == 0 and len(zones) > 0 and len(zones) == d.get('thermal_zones') and all(-40000 <= v <= 150000 for v in zones.values()), zones=len(zones), **zones)
        rc2, props = self.shell('getprop ro.crypto.state; getprop ro.crypto.type')
        fbe = int(rc2 == 0 and props.strip().splitlines() == ['encrypted', 'file'])
        evidence = {k: d.get(k, -1) for k in ('ice_wrapped', 'wrapped_68', 'native_crypto', 'data_f2fs')}
        storage = dict(encrypted_file=fbe, data_mounts=d.get('data_mounts', -1), data_tmpfs=d.get('data_tmpfs', -1), **evidence)
        if rc == 0 and d.get('real_data_required') == 0 and d.get('data_mounts') == 1 and d.get('data_tmpfs') == 1:
            self.not_run('29_wrapped_fbe', not_applicable=1, **storage)
        else:
            self.put('29_wrapped_fbe', rc == 0 and fbe == 1 and self.data_healthy(d)
                     and all(v > 0 for v in evidence.values()), real_data_required=d.get('real_data_required', -1), **storage)
        data_ok = self.data_healthy(d)
        boot = self.results['01_boot']
        storage_health = {k: d.get(k, -1) for k in ('real_data_required', 'data_ready', 'data_rc', 'data_mounts', 'data_f2fs', 'data_tmpfs')}
        self.put('01_boot', boot['status'] == 'PASS' and data_ok, **boot['evidence'], data_healthy=int(data_ok), **storage_health)

    def _basic_battery(self):
        rc, text = self.shell('dumpsys battery 2>/dev/null')
        battery = {k: int(v) for k, v in re.findall(r'^\s*(level|status|health|voltage|temperature):\s*(-?\d+)\s*$', text, re.M)}
        self.put('24_battery', rc == 0 and len(battery) == 5 and 0 <= battery.get('level', -1) <= 100 and battery.get('voltage', 0) > 0, **battery)

    def _basic_release(self):
        rc, text = self.shell('getprop ro.build.type; getprop ro.build.tags; getprop ro.secure; getprop ro.adb.secure; getprop ro.debuggable; getprop ro.vendor.build.type; getprop ro.vendor.build.tags; getprop ro.vendor.a9ml.release')
        p = text.strip().splitlines()
        release = dict(zip(('user', 'release_keys', 'secure', 'adb_secure', 'not_debuggable', 'vendor_user', 'vendor_release_keys'), [int(a == b) for a,b in zip(p, ('user', 'release-keys', '1', '1', '0', 'user', 'release-keys'))]))
        requested = len(p) == 8 and p[7] == '1'
        readable = rc == 0 and len(p) == (8 if requested else 7)
        configured = readable and all(release.values())
        # This row measures configuration. The host release receipt separately
        # verifies real signing; tags alone never certify APK/APEX signatures.
        if requested:
            self.put('27_release', configured, informational=0, readable=int(readable),
                     release_configuration=int(configured), **release)
        else:
            self.not_run('27_release', informational=1, readable=int(readable),
                         release_configuration=int(configured), **release)

    def usb_descriptors(self):
        # Bind host enumeration to this adb transport without emitting its serial.
        rc, serial = self.run([self.adb, 'get-serialno'], timeout=8)
        if rc or not serial.strip() or serial.strip() == 'unknown': return 0, 0
        serial = serial.strip(); mtp_total = 0; same_device = 0
        try:
            for device in Path('/sys/bus/usb/devices').iterdir():
                if ':' in device.name or not (device/'idVendor').exists(): continue
                interfaces = []
                for interface in device.glob(device.name+':*'):
                    try: interfaces.append(tuple(int((interface/n).read_text().strip(), 16) for n in ('bInterfaceClass', 'bInterfaceSubClass', 'bInterfaceProtocol')))
                    except (OSError, ValueError): continue
                mtp = (6, 1, 1) in interfaces
                mtp_total += int(mtp)
                if mtp and (255, 66, 1) in interfaces and (device/'serial').read_text().strip() == serial: same_device += 1
        except OSError: return 0, 0
        return mtp_total, same_device

    def transport_ready(self, seconds=40):
        # Read-only probes only. Never replay a mutation with unknown completion
        # or restart adbd/rootsh/server. Do not monitor recursively while offline.
        stop = min(self.operation_end(), time.monotonic()+seconds)
        while time.monotonic() < stop:
            rc, state = self.shell('getprop ro.boot.slot_suffix; getprop sys.boot_completed', timeout=5)
            if rc == 0 and state.strip().splitlines() == ['_b', '1']: return True
            time.sleep(min(2, max(0, stop-time.monotonic())))
        return False

    def usb(self):
        # USB descriptor enumeration and a real PTP/MTP session are different checks.
        # mtp-folders has no serial/address arguments and raw object names stay in memory.
        # This check follows a long hold and display closure. Android suppresses
        # MTP while locked. Wake/dismiss again only after verified minimum volume;
        # dismiss-keyguard cannot unlock a credential-protected device.
        if not self.muted():
            self.put('11_adb_mtp', False, mute_verified=0, mtp_attempted=0, prerequisite_failed=1); return False
        self.shell('input keyevent KEYCODE_WAKEUP >/dev/null 2>&1')
        self.shell('wm dismiss-keyguard >/dev/null 2>&1')
        log_rc, before_text = self.shell(USB_LOG_COUNTS)
        before = numbers(before_text)
        usb_diag = {}
        def applied():
            # AOSP UsbCommand prints this getter's normal result to System.err.
            r, request = self.shell('svc usb getFunctions 2>&1')
            # Ordinary shell can dump UsbService; direct vendor HAL binder calls
            # are denied by enforcing SELinux. Require framework applied state
            # AND the host descriptors/session below, never just the request.
            h, state = self.shell('dumpsys usb')
            functions = re.findall(r'^\s*current_functions\s*[=:]\s*(.*?)\s*$', state, re.M)
            has_mtp = any('mtp' in re.findall(r'[a-z_]+', value.lower()) for value in functions)
            flags = {key: int(re.search(r'^\s*'+key+r'\s*[=:]\s*true\s*$', state, re.M) is not None)
                     for key in ('current_functions_applied', 'configured', 'connected')}
            usb_diag.update(request_rc=r, dump_rc=h, framework_readable=int(r == h == 0),
                            request_mtp=int('mtp' in request.strip().split(',')) if r == 0 else -1,
                            framework_mtp=int(has_mtp) if h == 0 else -1,
                            applied_flag=flags['current_functions_applied'] if h == 0 else -1,
                            configured_flag=flags['configured'] if h == 0 else -1,
                            connected_flag=flags['connected'] if h == 0 else -1,
                            screen_locked=int(re.search(r'^\s*screen_locked\s*[=:]\s*true\s*$', state, re.M) is not None) if h == 0 else -1)
            return r == 0 and usb_diag['request_mtp'] == 1 and h == 0 and has_mtp and all(flags.values())
        configured = int(applied())
        if not configured:
            usb_diag.update(self.usb_set_mtp())
            configured = int(self.wait_restart(25, applied))
        ar, state = self.run([self.adb, 'get-state'], timeout=10)
        mtp_total, same_device = self.usb_descriptors()
        # Do not open unrelated MTP devices. Use the pinned probe: stock
        # mtp-folders forcibly resets composite Android USB on successful close.
        mr, mtp = self.run([self.assets/'mtp-session'], timeout=35) if mtp_total == same_device == 1 else (1, '')
        # The probe's uppercase protocol is deliberately small and numeric.
        session = int(mr == 0 and re.search(r'^MTP_SESSION 1$', mtp, re.M) is not None)
        resets = re.findall(r'^MTP_RESETS_REFUSED ([0-9]+)$', mtp, re.M)
        ready = self.transport_ready()
        if not ready:
            self.lost += 1; self.losses['usb'] += 1
            print('STABILITY_LOSS', json.dumps(dict(usb=1)), flush=True)
        # Opening/closing the MTP session can overlap FunctionFS rebind. One
        # successful adb probe is not the final framework observation. Retry
        # these read-only getters within the existing reconnect allowance.
        if ready: configured = int(self.wait_restart(40, applied))
        after_rc, after_text = self.shell(USB_LOG_COUNTS)
        after = numbers(after_text)
        counters_ok = log_rc == after_rc == 0 and len(before) == len(after) == 6 and before.keys() == after.keys()
        rotated = counters_ok and any(after[k] < before[k] for k in before)
        usb_diag.update(usb_log_readable=int(counters_ok), usb_log_rotated=int(rotated))
        if counters_ok:
            usb_diag.update({k:v for k,v in after.items()})
            if not rotated: usb_diag.update({k+'_during_request':after[k]-v for k,v in before.items()})
        self.put('11_adb_mtp', ar == 0 and state.strip() == 'device' and configured == 1 and session == 1 and mtp_total == 1 and same_device == 1 and ready, mtp_devices=mtp_total, same_adb_mtp_device=same_device, adb=int(ar == 0 and state.strip() == 'device'), configured=configured, mtp_session=session, mtp_rc=mr, resets_refused=int(resets[0]) if len(resets) == 1 else -1, transport_ready=int(ready), **usb_diag)
        return ready

    def usb_set_mtp(self):
        # A fresh shell-owned directory prevents stale results or symlinks from
        # providing evidence. This is the same MANAGE_USB shell caller as svc.
        directory = '/data/local/tmp/a9-sweep-usb-'+os.urandom(12).hex()
        q = shlex.quote(directory)
        launch = ('umask 077; mkdir '+q+' || exit 1; printf %s '+shlex.quote(USB_SET_WORKER)+
                  ' >'+q+'/worker || exit 1; '
                  'nohup setsid timeout -k 2 25 sh '+q+'/worker '+q+
                  ' </dev/null >/dev/null 2>&1 &\n'
                  # Do not let adbd tear down the launching process group
                  # before nohup/setsid have actually detached the worker.
                  'n=0; while [ "$n" -lt 50 ]; do '
                  '[ ! -r '+q+'/started ] || { printf "set_launch_ack 1\\n"; exit 0; }; '
                  'sleep .1; n=$((n+1)); done; exit 1')
        rc, reply = self.shell(launch, timeout=10, capture_stderr=True)
        diagnostic = dict(set_rc=rc, set_launch_ack=int(numbers(reply).get('set_launch_ack') == 1),
                          set_transport_closed=int(bool(re.search(r'closed|disconnected|connection reset', reply, re.I))),
                          set_transport_offline=int(bool(re.search(r'offline|no devices|device .*not found', reply, re.I))),
                          set_transport_permission=int('permission' in reply.lower()),
                          set_started=0, set_completed=0, set_result_readable=0,
                          set_command_rc=-1, set_exception=-1, set_request_logged=-1, set_permission_denied=-1)
        # Read-only recovery even when launch returned 255: never replay launch.
        def finished():
            r, text = self.shell('cat '+q+'/started '+q+'/result 2>/dev/null', timeout=4)
            values = numbers(text)
            diagnostic['set_started'] = int(values.get('set_started') == 1)
            expected = {'set_started', 'set_completed', 'set_command_rc', 'set_exception',
                        'set_request_logged', 'set_permission_denied'}
            if (r or values.keys() != expected or values['set_started'] != 1 or values['set_completed'] != 1
                    or not 0 <= values['set_command_rc'] <= 255
                    or any(values[k] not in (0, 1) for k in ('set_exception', 'set_request_logged', 'set_permission_denied'))):
                return False
            diagnostic.update(values, set_result_readable=1)
            return True
        complete = self.wait_restart(30, finished)
        # Missing completion is uncertain: retain the tiny receipt directory,
        # rather than remove a possibly running worker's files or retry it.
        cleanup = self.shell('rm -r -- '+q+' 2>/dev/null')[0] == 0 if complete else False
        diagnostic['set_receipt_cleaned'] = int(cleanup)
        path = self.out/'usb-set-evidence.json'
        path.write_text(json.dumps(diagnostic, sort_keys=True)+'\n'); path.chmod(0o600)
        return diagnostic

    def hardware_encoder(self):
        # Exact-name synthetic probes use the ordinary framework shell. Stage
        # into shell-owned data; vendor_file execution is deliberately denied.
        evidence = dict(qualification=0, bundle_verified=0, staged=0, cleanup=0,
                        avc=dict(attempted=0), hevc=dict(attempted=0))
        remote = None
        try:
            image = Path(os.environ.get('A9_SWEEP_IMAGE', str(self.assets.parent)))
            probe = image/'encoder-probes/codec2-encode'
            receipt = json.loads((image/'encoder-stage.json').read_text())
            if hashlib.sha256(probe.read_bytes()).hexdigest() != receipt['probe_sha256']:
                return
            evidence['bundle_verified'] = 1
            rc, text = self.shell('mktemp -d /data/local/tmp/a9-encoder.XXXXXX')
            if rc or not re.fullmatch(r'/data/local/tmp/a9-encoder\.[A-Za-z0-9]{6,}\s*', text): return
            remote = text.strip()
            rc, _ = self.run([self.adb, 'push', probe, remote+'/probe'], timeout=20)
            if rc: return
            rc, _ = self.shell('chmod 700 '+shlex.quote(remote+'/probe'))
            if rc: return
            evidence['staged'] = 1
            for label, argument, number in (('avc', 'h264', 1), ('hevc', 'hevc', 2)):
                rc, text = self.shell('timeout -k 2 30 '+shlex.quote(remote+'/probe')+' '+argument+' 2>&1',
                                      timeout=35, capture_partial=True)
                d = dict(attempted=1, command_rc=rc, completed=0, stage=0)
                stages = re.findall(r'^ENCODE_STAGE codec='+str(number)+r' stage=([1-4])$', text, re.M)
                if stages: d['stage'] = int(stages[-1])
                points = {name:i+1 for i,name in enumerate(('first_input.begin', 'first_input.end',
                    'first_output', 'eos.begin', 'eos.end', 'stop.begin', 'stop.end', 'delete.begin', 'delete.end'))}
                for point,inputs,outputs,status in re.findall(r'^ENCODE_IO codec='+str(number)+
                        r' op=([a-z_.]+) inputs=([0-9]+) outputs=([0-9]+) status=(-?[0-9]+)$', text, re.M):
                    if point in points:
                        d.update(io_point=points[point], io_inputs=int(inputs), io_outputs=int(outputs), io_status=int(status))
                records = re.findall(r'^ENCODE codec='+str(number)+r' stage=([1-4]) status=(-?[0-9]+) inputs=([0-9]+) outputs=([0-9]+) bytes=([0-9]+) eos=([01])$', text, re.M)
                if len(records) == 1:
                    d.update(zip(('stage', 'status', 'inputs', 'outputs', 'bytes', 'eos'), map(int, records[0])))
                    d['completed'] = 1
                evidence[label] = d
        except (OSError, ValueError, KeyError):
            pass
        finally:
            if remote is not None:
                evidence['cleanup'] = int(self.shell('rm -r -- '+shlex.quote(remote))[0] == 0)
            ok = evidence['staged'] == 1 and evidence['cleanup'] == 1
            for label in ('avc', 'hevc'):
                d = evidence[label]
                ok &= (d.get('completed') == 1 and d.get('command_rc') == 0 and d.get('status') == 0
                       and d.get('stage') == 4 and d.get('inputs') == 30
                       and d.get('outputs', 0) > 0 and d.get('bytes', 0) > 0 and d.get('eos') == 1)
            self.put('18_hardware_encoder', bool(ok), **evidence)

    def sensors_media(self):
        if not self.assets_ok: return
        self.independent(('08_22_sensors',), self._sensors)
        self.independent((), self._service_inventory)
        self.independent(('17_decoder_muted',), self._decoder)
        self.independent(('20_mic_amplitude',), self._mic)
        for facing, value in (('front', 0), ('rear', 1)):
            self.independent(('19_'+facing+'_photo',), self._photo, facing, value)
        # Preserve both still-photo attempts before any encoder is opened.
        for facing, value in (('front', 0), ('rear', 1)):
            self.independent(('19_'+facing+'_af','19_'+facing+'_torch'), self._controls, facing, value)
        # Finish both cameras' independent controls before a video stall can
        # occupy CameraService's connection slot.
        for facing, value in (('front', 0), ('rear', 1)):
            self.independent(('19_'+facing+'_video',), self._video, facing, value)

    def _sensors(self):
        _, setup = self.shell('getprop vendor.a9.sensors.ready; getprop vendor.a9.sensors.state')
        setup_ready = int(setup.splitlines()[:1] == ['1'])
        setup_failed = int(any(x.startswith('fail:') for x in setup.splitlines()))
        d = self.app(PKG, '.Probe', '--es mode sensors', 35)
        if isinstance(d, dict): d.update(setup_ready=setup_ready, setup_failed=setup_failed)
        if d is None: self.put('08_22_sensors', False, result=0)
        else:
            records = d.get('sensors', []); events = sum(r.get('events', 0) for r in records)
            self.put('08_22_sensors', d.get('completed') == 1 and d.get('registered', 0) > 0 and events > 0 and not any(r.get('nonmonotonic') for r in records), **d)

    def _service_inventory(self):
        # Preserve a quick service enumeration before a slow codec can consume
        # the remaining transport window. This is diagnostic, never a photo PASS.
        inventory = self.app(PKG, '.Probe', '--es mode health', 20) or {}
        inventory = {k: inventory.get(k, -1) if type(inventory.get(k)) is int else -1
                     for k in ('completed', 'camera_count', 'sensor_count', 'app_stage', 'command_rc')}
        inventory_file = self.out/'service-inventory.json'
        inventory_file.write_text(json.dumps(inventory, sort_keys=True)+'\n'); inventory_file.chmod(0o600)
        print('SERVICE_INVENTORY', json.dumps(inventory, sort_keys=True), flush=True)

    def _decoder(self):
        if self.muted():
            d = self.app('org.a9.videoprobe', '.PlaybackActivity', seconds=65) or {}
            evidence = {k: int(d.get(k, 0)) for k in ('decoded_frames', 'drawn_frames', 'output_eos', 'hardware_accelerated', 'software_only', 'probe_result', 'app_stage', 'timed_out')}
            self.put('17_decoder_muted', d.get('pass') is True and evidence['decoded_frames'] == 60 and evidence['drawn_frames'] == 60 and evidence['output_eos'] == 1 and evidence['hardware_accelerated'] == 1 and evidence['software_only'] == 0, mute_verified=1, **evidence)
        else: self.put('17_decoder_muted', False, mute_verified=0)

    def _mic(self):
        d = self.app(PKG, '.Probe', '--es mode mic', 20) or {}
        self.put('20_mic_amplitude', d.get('completed') == 1 and d.get('window_ms', 0) >= 5000 and d.get('samples', 0) >= 200000 and d.get('peak', 0) > 0, **d)

    def _photo(self, facing, value):
        d = self.app('org.a9.cameraprobe', '.CaptureActivity', '--es facing '+('front' if value == 0 else 'back'), 95) or {}
        # JPEG sanity is checked by the existing app; never pull the photo.
        evidence = {k: int(d.get(k, 0)) for k in ('jpeg_bytes', 'jpeg_width', 'jpeg_height', 'ae_converged', 'camera_error_code', 'paused_during_capture', 'probe_result', 'app_stage', 'command_rc', 'timed_out')}
        evidence.update({k: int(d.get(k, -1)) for k in ('warmup_frames', 'camera_phase', 'activity_resumed', 'interactive', 'keyguard_locked')})
        error = str(d.get('reason', ''))
        evidence.update(permission_missing=int(error == 'camera_permission_missing'),
                        no_camera=int(error in ('no_front_camera', 'no_back_camera')))
        self.put('19_'+facing+'_photo', d.get('pass') is True, **evidence)
        self.shell('run-as org.a9.cameraprobe rm -f files/picture.jpg files/result.json')

    def _controls(self, facing, value):
        d = self.app(PKG, '.Probe', '--es mode camera_controls --ei facing '+str(value), 80) or {}
        for feature, avail, count in (('af', 'af_available', 'af_locked'), ('torch', 'torch_available', 'torch_fired')):
            check = '19_'+facing+'_'+feature
            self.independent((check,), self._control_result, check, d, facing, feature, avail, count)

    def _control_result(self, check, d, facing, feature, avail, count):
        if d.get('available') == 0 or d.get(avail) not in (0, 1):
            self.not_run(check, prerequisite_failed=1, exposed=-1)
        elif d.get(avail) == 0:
            # Exact A9 stock modules: the HI556 front has neither an
            # actuator nor flash; the S5K3L6 rear has both. See
            # cameras/STOCK-CAPABILITIES.md. Missing rear controls are
            # implementation gaps, never hardware N/A or PASS.
            if facing == 'front': self.not_run(check, exposed=0, not_applicable=1, stock_supported=0)
            else: self.not_run(check, exposed=0, stock_supported=1, implementation_missing=1)
        else:
            if feature == 'af':
                keys = ('af_locked', 'af_unfocused', 'af_scanning', 'lens_moving', 'lens_span_millidioptres')
            else:
                keys = ('torch_fired', 'torch_off_result', 'torch_manager_on', 'torch_manager_off', 'torch_on_frames', 'torch_off_frames')
            proof = {k: int(d.get(k, 0)) for k in keys}
            required = ('af_locked',) if feature == 'af' else ('torch_fired', 'torch_off_result', 'torch_manager_on', 'torch_manager_off')
            self.put(check, d.get('completed') == 1 and all(proof[k] > 0 for k in required),
                     exposed=d.get(avail, -1), observed=d.get(count, 0),
                     **{k: int(d.get(k, 0)) for k in ('completed', 'frames', 'camera_error',
                         'camera_stage', 'camera_close_phase', 'camera_closed', 'error', 'error_kind',
                         'error_line', 'app_stage', 'timed_out')}, **proof)

    def _video(self, facing, value):
        d = self.app(PKG, '.Probe', '--es mode camera --ei facing '+str(value), 75) or {}
        self.put('19_'+facing+'_video', d.get('completed') == 1 and d.get('software_encoder') == 1
                 and d.get('encoder_eos') == 1 and d.get('duration_ms', 0) >= 10000
                 and d.get('video_samples', 0) > 0 and d.get('frames', 0) > 0, **d)

    def saved_networks(self):
        rc, text = self.shell('cmd wifi list-networks 2>/dev/null')
        if rc == 0 and text.strip() == 'No networks': return set()
        if rc == 0 and re.search(r'Network\s+Id\s+SSID', text, re.I):
            return play.network_ids(text, self.name)
        return None

    def wifi(self):
        # list-networks can fail while Wi-Fi is disabled. Start the existing
        # controller/enable path before interpreting that output as saved state.
        self.last_remote_rc = 0
        sr, owner_text = self.native(play.START)
        owner = {k:v for k,v in numbers(owner_text).items() if k in
                 ('owner_state','owner_end_reason','owner_start_attempted','owner_fault_present','owner_failure_code')}
        owner_exit = self.last_remote_rc if sr else 0  # 11 start already requested, 12 owner session terminal
        # sys.boot_completed alone does not imply WifiService finished startup.
        # Bounded read-only readiness; a permanently incomplete boot still fails.
        service_ready = sr == 0 and self.wait(45, lambda: self.shell('cmd wifi status 2>/dev/null')[0] == 0)
        if not service_ready:
            self.put('12_wifi', False, controller_rc=sr, owner_exit=owner_exit, service_ready=0, service_checked=int(sr == 0), **owner); return False
        # START queues an asynchronous owner. Framework startup does not prove
        # that MPSS/ath10k admission has finished. Wait for its existing release
        # before asking the Android HAL to create an interface; never restart it.
        owner_ready = self.wait(120, lambda: self.shell(
            '[ "$(getprop vendor.a9ml.wifi.session.state)" = serving ] && '
            '[ "$(getprop vendor.a9ml.wifi.android.ready)" = 1 ]')[0] == 0)
        if not owner_ready:
            self.put('12_wifi', False, controller_rc=sr, service_ready=1, owner_ready=0); return False
        er, _ = self.shell('cmd wifi set-wifi-enabled enabled >/dev/null 2>&1')
        def netdevice():
            rc, text = self.native(WLAN_DEVICE)
            return rc == 0 and numbers(text).get('wlan_present') == 1
        device_ready = sr == 0 and er == 0 and self.wait(60, netdevice)
        cr, cap_text = self.native(play.CAP) if device_ready else (1, '')
        cap_diag = {k:v for k,v in numbers(cap_text).items() if k in
                    ('cap_command_rc','cap_netlink_error','cap_permission_error','cap_unsupported')}
        if sr or er or cr:
            self.put('12_wifi', False, controller_rc=sr, service_ready=1, owner_ready=1,
                     enable_rc=er, device_ready=int(device_ready), cap_attempted=int(device_ready), cap_rc=cr, **cap_diag); return False
        try: self.name, password = play.Hook.credentials(self)
        except Exception: self.put('12_wifi', False, credentials=0); return False
        def known():
            self.network_before = self.saved_networks()
            return self.network_before is not None
        if not self.wait(25, known):
            password = None; self.put('12_wifi', False, credentials=1, saved_state_unknown=1); return False
        reused = bool(self.network_before)
        if not reused:
            self.injected = True
            rc, _ = self.shell(play.CONNECT, (self.name+'\n'+password+'\n').encode())
        else:
            # Do not replace stored credentials or delete a pre-existing entry.
            # Let framework auto-connect, and accept only exact live validation.
            rc = 0
        password = None
        def valid():
            r, status = self.shell('cmd wifi status 2>/dev/null')
            v, text = self.shell('dumpsys connectivity 2>/dev/null')
            return r == 0 and v == 0 and play.target_connected(status, self.name) and play.validated(text)
        connected = rc == 0 and self.wait(120, valid)
        self.put('12_wifi', connected, connected_validated=int(connected), credentials=1,
                 reused_existing=int(reused))
        return connected

    def checkin(self):
        # Exact run5 forced-check-in request and success/GSF-length predicates.
        rc, text = self.shell(play.CHECKIN_REQUEST)
        sent = rc == 0 and not re.search('SecurityException|Permission Denial', text, re.I)
        source = 0
        def checked():
            nonlocal source
            r, s = self.shell(play.CHECKIN)
            if r == 0 and s.strip() == 'CHECKIN 1': source = 1; return True
            r, s = self.native(play.GSF)
            if r == 0 and re.fullmatch(r'GSF_LEN [1-9][0-9]*\s*', s): source = 2; return True
            return False
        ok = self.wait(900, checked) if sent else False
        self.put('28_checkin', ok, requested=int(sent), success_source=source)

    def play_install(self):
        # Default tiny free app has no permissions or automatic audible playback.
        package = os.environ.get('A9_SWEEP_PLAY_PACKAGE', 'org.fossify.math')
        if not re.fullmatch(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+', package):
            self.put('28_play_install_launch', False, package_valid=0); return
        rc, text = self.shell('pm path '+package)
        if rc == 0 and 'package:' in text:
            self.not_run('28_play_install_launch', already_installed=1); return
        if not self.muted(): self.put('28_play_install_launch', False, mute_verified=0); return
        self.shell('input keyevent KEYCODE_WAKEUP >/dev/null 2>&1')
        rc, text = self.shell('am start -W -a android.intent.action.VIEW -d '+shlex.quote('market://details?id='+package)+' -p com.android.vending', timeout=25)
        if rc or re.search('Error|Exception', text): self.put('28_play_install_launch', False, play_open=0); return
        clicked = False; signed_out = False
        def installed():
            nonlocal clicked, signed_out
            rc, text = self.shell('pm path '+package)
            if rc == 0 and 'package:' in text: return True
            rc, xml = self.shell('uiautomator dump --compressed /dev/stdout 2>/dev/null', timeout=25)
            if '<hierarchy' not in xml:
                rc, xml = self.run([self.adb, 'shell', '-tt', 'uiautomator dump --compressed /dev/tty 2>/dev/null'], timeout=25)
            page = [n for n in nodes(xml) if n.get('package') == 'com.android.vending']
            labels = [n.get('text', '').strip().casefold() for n in page]
            if any(label in ('sign in', 'sign-in') for label in labels):
                signed_out = True
                # Account setup is outside the unattended sweep. An install
                # already attempted still follows the normal failure path.
                if not clicked and not any(n.get('text', '').strip().casefold() == 'install'
                                          and n.get('enabled') == 'true' for n in page):
                    return True
            for n in page:
                if n.get('package') != 'com.android.vending': continue
                label = n.get('text', '').strip().casefold()
                if not clicked and label == 'install' and n.get('enabled') == 'true':
                    m = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', n.get('bounds', ''))
                    if m:
                        x1,y1,x2,y2 = map(int, m.groups())
                        self.shell('input tap '+str((x1+x2)//2)+' '+str((y1+y2)//2)); clicked = True
            return False
        ok = self.wait(240, installed)
        if signed_out and not clicked:
            self.not_run('28_play_install_launch', reason='needs_account', signed_out=1, install_tapped=0)
            return
        # PM installer attribution is required: a side load is never Play evidence.
        rc, details = self.shell('pm list packages -i '+package)
        installer = int(rc == 0 and bool(re.search(r'^package:'+re.escape(package)+r'\s+installer=com.android.vending\s*$', details, re.M)))
        launched = 0
        if ok and installer:
            rc, text = self.shell('cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER '+package)
            components = [s.strip() for s in text.splitlines() if re.fullmatch(re.escape(package)+r'/[A-Za-z0-9_.$]+', s.strip())]
            if rc == 0 and len(components) == 1:
                r, text = self.shell('am start -W -n '+components[0], timeout=25)
                r2, foreground = self.shell('dumpsys activity activities')
                launched = int(r == 0 and not re.search('Error|Exception', text) and r2 == 0 and bool(re.search(r'(?:mResumedActivity|topResumedActivity)[^\n]*\s'+re.escape(package)+'/', foreground)))
        sr, sizes = self.shell("set -o pipefail; pm path "+package+" | while IFS= read -r f; do case \"$f\" in package:/*) stat -c %s \"${f#package:}\";; *) exit 1;; esac; done")
        size_values = sizes.splitlines()
        size_ok = sr == 0 and bool(size_values) and all(re.fullmatch(r'[0-9]+', v) for v in size_values)
        apk_bytes = sum(map(int, size_values)) if size_ok else -1
        self.put('28_play_install_launch', ok and installer == 1 and launched == 1 and clicked and 0 < apk_bytes <= 20*1024*1024, apk_bytes=apk_bytes, install_tapped=int(clicked), signed_out=int(signed_out), installed=int(ok), installer_play=installer, launched=launched)

    def bluetooth_start(self):
        rc, text = self.shell(play.BLUETOOTH_STATE, timeout=12)
        m = re.fullmatch(r'BT_STATE ([01])\s+BT_FRAMEWORK_CRASHES (-1|[0-9]+)\s+BT_LOG_SIGNATURE ([a-f0-9]{64})\s*', text)
        self.bt_before = (int(m[1]), int(m[2]), m[3]) if rc == 0 and m else None
        self.bt_crash = self.bt_before is None
        self.bt_start = time.monotonic()
        self.shell('cmd bluetooth_manager enable >/dev/null 2>&1')
        self.wait(60, lambda: self.bt_seen)

    def bluetooth_end(self):
        if self.bt_start is None: return
        self.wait(max(0, 61 - (time.monotonic()-self.bt_start)))
        self.monitor(force=True)
        seconds = int(time.monotonic()-self.bt_start)
        self.bt_start = None
        self.shell('cmd bluetooth_manager disable >/dev/null 2>&1')
        def off():
            r, s = self.shell(play.BLUETOOTH_STATE, timeout=12)
            return r == 0 and s.startswith('BT_STATE 0\n')
        disabled = self.wait(25, off)
        self.put('13_bluetooth', self.bt_seen and not self.bt_crash and not self.bt_dropped and disabled and seconds >= 60, seen_on=int(self.bt_seen), disabled=int(disabled), crash_or_evidence_loss=int(self.bt_crash), dropped=int(self.bt_dropped), window_s=seconds, probes=self.bt_probes)

    def policy(self):
        rc, enforcing = self.shell('getenforce')
        er = int(rc == 0 and enforcing.strip() == 'Enforcing')
        rc, encoded = self.native('base64 /sys/fs/selinux/policy')
        domains = None; compatibility = 0
        policy = self.out/'sweep-loaded-policy.bin'
        try:
            if rc == 0:
                blob, compatibility = policy_for_host(base64.b64decode(encoded, validate=False))
                policy.write_bytes(blob); policy.chmod(0o600)
                r, text = self.run([self.assets/'permissive', policy], timeout=15)
                if r == 0 and all(re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', d) for d in text.splitlines()): domains = sorted(set(text.splitlines()))
        except (ValueError, OSError): pass
        finally: policy.unlink(missing_ok=True)
        if domains is not None:
            # Explicitly requested domain-name list, separate from numeric verdict evidence.
            (self.out/'sweep-permissive-domains.txt').write_text(''.join(d+'\n' for d in domains))
            (self.out/'sweep-permissive-domains.txt').chmod(0o600)
        d = self.snapshot() or {}
        self.put('26_selinux', er == 1 and domains == [] and d.get('permissive_avcs') == 0, enforcing=er, functionfs_cap_compat=compatibility, permissive_domains=-1 if domains is None else len(domains), avcs=d.get('avcs', -1), permissive_avcs=d.get('permissive_avcs', -1), native_rc=d.get('native_rc', -1), events_rc=d.get('events_rc', -1))

    def restart(self):
        self.command_end = self.end
        self.restart_attempted = True
        before = ''
        def baseline():
            nonlocal before
            rc, before = self.shell('cat /proc/sys/kernel/random/boot_id; getprop ro.boot.slot_suffix')
            return rc == 0 and len(before.strip().splitlines()) == 2 and before.strip().endswith('_b')
        # USB/MTP may have just re-enumerated. Retrying a read is harmless;
        # never label that expected outage as a failed reboot or retry reboot.
        if not self.wait_restart(40, baseline):
            self.put('01_restart', False, baseline=0, request_count=0); return False
        # Exactly one Android warm reboot; no bootloader/slot/flash/recovery commands.
        self.cleaned = False
        self.run([self.adb, 'reboot'], timeout=12)
        observed = False
        def booted():
            nonlocal observed
            rc, after = self.shell('cat /proc/sys/kernel/random/boot_id; getprop ro.boot.slot_suffix; getprop sys.boot_completed')
            p = after.strip().splitlines()
            if rc == 0 and len(p) == 3 and p[0] != before.splitlines()[0]: observed = True
            return observed and p[1:] == ['_b', '1'] if rc == 0 and len(p) == 3 else False
        ok = self.wait_restart(180, booted)
        # Transport boot is provisional. Post-reinstall health must complete it.
        self.put('01_restart', False, request_count=1, boot_id_changed=int(observed), slot_b_completed=int(ok), health_verified=0)
        # Ring buffers reset; persistent tombstones/ANRs must still match across reboot.
        previous = self.baseline
        self.baseline = None
        self.previous_snapshot = None
        self.evidence_boot += 1; self.event_seen.clear()
        self.monitor(force=True)
        if previous and self.baseline and any(previous[k] != self.baseline[k] for k in ('tombstones_signature', 'anrs_signature')):
            self.crashes += 1
        if self.baseline and self.baseline['crash_events'] > 0:
            self.crashes += 1
        return ok

    @staticmethod
    def data_healthy(d):
        mounted = d.get('data_mounts') == 1
        if d.get('real_data_required') == 1:
            return mounted and d.get('data_f2fs') == 1 and d.get('data_tmpfs') == 0 and d.get('data_ready') == 1
        return mounted and d.get('real_data_required') == 0

    def restart_health(self, ready, mute_ok):
        evidence = self.results['01_restart']['evidence']
        evidence.update(probes_reinstalled=int(ready), mute_verified=int(mute_ok))
        if mute_ok:
            rc, text = self.native(HEALTH)
            d = numbers(text)
            app = self.app(PKG, '.Probe', '--es mode health', 20)
            evidence.update(sensor_ready=d.get('sensor_ready', -1), audio_ready=d.get('audio_ready', -1),
                            camera_count=app.get('camera_count', -1), sensor_count=app.get('sensor_count', -1),
                            data_healthy=int(rc == 0 and self.data_healthy(d)))
            ok = (rc == 0 and app.get('completed') == 1 and app.get('camera_count', 0) > 0
                  and app.get('sensor_count', 0) > 0 and d.get('sensor_ready') == 1
                  and d.get('audio_ready') == 1 and evidence['data_healthy'] == 1)
        else: ok = False
        evidence['health_verified'] = int(ok)
        self.put('01_restart', ok, **evidence)

    def wait_restart(self, seconds, predicate):
        stop = min(self.end-(240 if self.hvenc_qualify else 60), time.monotonic()+seconds)
        # Expected transport outage is not a stability evidence failure.
        while time.monotonic() < stop:
            if predicate(): return True
            time.sleep(3)
        return False

    def cleanup(self):
        if self.cleaned: return
        self.cleaned = True; ok = True
        stopped = True; wifi_removed = True; eink_closed = True
        if not self.touched:
            self.not_run('cleanup', attempted=0); return
        for p in (PKG, 'org.a9.cameraprobe', 'org.a9.videoprobe'):
            rc, _ = self.shell('am force-stop '+p+' >/dev/null 2>&1'); stopped &= rc == 0
        if self.injected and self.name:
            self.shell('cmd wifi disconnect >/dev/null 2>&1')
            ids = self.saved_networks()
            wifi_removed &= ids is not None
            ids = ids or set()
            for i in ids:
                rc, _ = self.shell('cmd wifi forget-network '+str(i)+' >/dev/null 2>&1'); wifi_removed &= rc == 0
            remaining = self.saved_networks()
            wifi_removed &= remaining is not None and not remaining
        self.name = None
        wifi_disabled, wifi_off = play.disable_wifi(self.shell, deadline=self.operation_end())
        rc, _ = self.shell('cmd bluetooth_manager disable >/dev/null 2>&1'); bt_disabled = rc == 0
        if self.eink_attempted:
            rc, text = self.native(play.EINK_CLOSE); eink_closed = rc == 0 and text.strip() == 'EINK_CLOSED'
        ok = all((stopped, wifi_removed, wifi_disabled, bt_disabled, eink_closed, self.pin_cleanup_ok,
                  self.drain_cleanup_ok))
        self.cleanup_ok = ok
        self.put('cleanup', ok, completed=int(ok), apps_stopped=int(stopped), wifi_removed=int(wifi_removed),
                 wifi_disabled=int(wifi_disabled), bt_disabled=int(bt_disabled), eink_closed=int(eink_closed),
                 pin_cleanup_ok=int(self.pin_cleanup_ok), drain_cleanup_ok=int(self.drain_cleanup_ok), **wifi_off)

    def standby_drain(self):
        spec=importlib.util.spec_from_file_location('standby_drain',HERE/'drain.py')
        drain=importlib.util.module_from_spec(spec); spec.loader.exec_module(drain)
        drain.run(self)

    def image_context(self):
        try:
            image = Path(os.environ.get('A9_SWEEP_IMAGE', os.environ.get('A9_INTEG_IMAGE', Path(os.environ['A9_BTRY_BOOT']).parent)))
            wanted = image/'boot.img'
            supplied = Path(os.environ['A9_BTRY_BOOT'])
            match = hashlib.sha256(wanted.read_bytes()).digest() == hashlib.sha256(supplied.read_bytes()).digest()
            self.put('00_image_context', match, boot_matches=int(match), boot_bytes=supplied.stat().st_size)
            return match
        except (OSError, KeyError):
            self.put('00_image_context', False, boot_matches=0); return False

    def execute(self):
        if not self.image_context(): return
        self.touched = True
        self.independent(('30_fp_no_dumps',), self.fingerprint_privacy)
        self.independent(('A_stability',), self.monitor, force=True)
        ready = self.prepare_assets()
        self.put('00_assets', ready, installed_verified=int(ready))
        if not ready: return
        if not self.muted():
            self.put('17_decoder_muted', False, mute_verified=0)
            return
        # Only after verified minimum volumes; never bypasses a secure keyguard.
        self.independent((), self.shell, 'wm dismiss-keyguard >/dev/null 2>&1')
        self.basic()
        self.independent(('04_gpu_vulkan',), self.vulkan)
        self.independent(('13_bluetooth',), self.bluetooth_start)
        self.sensors_media()
        if self.independent(('12_wifi',), self.wifi):
            self.independent(('28_checkin',), self.checkin)
            self.independent(('28_play_install_launch',), self.play_install)
        self.independent(('13_bluetooth',), self.bluetooth_end)
        self.independent(('26_selinux',), self.policy)
        self.independent(('A_stability',), self.monitor, force=True)
        if self.drain_requested:
            self.independent(('10_hourly_drain',), self.standby_drain)
        # Close E Ink admission and remove credentials before restarting.
        self.independent(('cleanup',), self.cleanup)
        # USB re-enumeration can remove adb. Requested real tests run first.
        if not self.hvenc_qualify:
            self.independent(('18_hardware_encoder',), self.hardware_encoder)
        if not (self.hvenc_qualify or self.pin_requested): self.usb_restart()
        self.hvenc_finalizing = True
        if not (self.hvenc_qualify or self.pin_requested): self.finish_stability()

    def fingerprint_privacy(self):
        rc, text = self.native(FP_NO_DUMPS, timeout=10)
        d = numbers(text)
        old = self.results['30_fp_no_dumps']['evidence']
        files = max(old.get('files', 0), d.get('files', -1))
        errors = old.get('scan_errors', 0) + int(old.get('row_error', 0) > 0) + int(rc != 0 or d.get('completed') != 1 or d.get('files', -1) < 0)
        self.put('30_fp_no_dumps', errors == 0 and files == 0,
                 files=files, scan_errors=errors, scans=old.get('scans', 0) + 1)

    def finish_stability(self):
        final = self.independent(('A_stability',), self.snapshot) or {}
        self.independent(('A_stability',), self.collect_anr_evidence, force=True)
        evidence = dict(window_s=int(time.monotonic()-self.started), probes=self.probes, lost=self.lost,
                        crash_or_rotation_observations=self.crashes, dialogs=self.dialogs,
                        signature_changes=self.signature_changes,
                        loss_reasons=self.losses, saved_event_records=self.event_records,
                        system_server_fatals=self.system_server_fatals,
                        system_server_crash_loop=int(self.system_server_fatals>=2),
                        tombstones=final.get('tombstones', -1), anrs=final.get('anrs', -1))
        self.put('A_stability', bool(final.get('snapshot_ok')) and self.probes > 1 and self.lost == 0 and self.crashes == 0 and self.dialogs == 0 and self.system_server_fatals == 0, **evidence)

    def usb_restart(self):
        self.command_end = self.end
        self.wait_limit = self.end-12
        # USB re-enumeration/reboot run after requested PIN and qualification.
        # Their verdicts and temporary transport loss cannot veto those rows.
        # Cleanup and MTP verdicts are data, not prerequisites for a different
        # row. restart() independently waits for its live slot-B baseline.
        self.independent(('11_adb_mtp',), self.usb)
        if self.independent(('01_restart',), self.restart):
            self.wait_limit = self.end-12
            # Some failed-data boots use ephemeral /data: confirm/reinstall
            # pinned probes instead of assuming they survived warm reboot.
            self.assets_ok = False
            ready = self.independent(('01_restart',), self.prepare_assets)
            mute_ok = ready and self.independent(('01_restart',), self.muted)
            self.independent(('01_restart',), self.restart_health, ready, mute_ok)
            # Preserve the original decoder result; this is a reboot observation.
            if mute_ok: self.independent(('A_stability',), self.wait, max(0, self.end-time.monotonic()-12))

    def persist(self):
        staged = self.out/'acceptance.json.tmp'
        staged.write_text(json.dumps(self.results, indent=2, sort_keys=True)+'\n')
        staged.chmod(0o600); staged.replace(self.out/'acceptance.json')

    def save(self):
        self.persist()
        for k, v in self.results.items():
            reason = 'reason='+v['reason']+' ' if 'reason' in v else ''
            print(k, v['status'], reason+json.dumps(v['evidence'], sort_keys=True), flush=True)


def main():
    # Guard before credentials, USB, executable lookup, assets or output writes.
    if os.environ.get('A9_BTRY_IN_HOOK') != '1' or not os.environ.get('ADB') or not os.environ.get('A9_BTRY_LOGS'):
        print('CONTEXT FAIL {"owning_hook":0}'); return 2
    if os.environ.get('A9_SWEEP_HVENC_QUALIFY','0') not in ('0','1'):
        print('CONTEXT FAIL {"hvenc_option":0}'); return 2
    if os.environ.get('A9_SWEEP_HVENC_VARIANTS','0') not in ('0','1'):
        print('CONTEXT FAIL {"hvenc_variants_option":0}'); return 2
    if os.environ.get('A9_SWEEP_DRAIN','0') not in ('0','1'):
        print('CONTEXT FAIL {"drain_option":0}'); return 2
    s = Sweep()
    def stopped(signum, frame): raise InterruptedError()
    signal.signal(signal.SIGTERM, stopped); signal.signal(signal.SIGINT, stopped)
    rows_complete = False
    try:
        s.execute()
        rows_complete = True
    except Exception:
        # Do not print exceptions/tracebacks carrying subprocess data or credentials.
        print('SWEEP FAIL {"interrupted_or_exception":1}', flush=True)
    finally:
        s.end = max(s.end, time.monotonic()+35)
        s.command_end = s.end
        s.hvenc_finalizing = True
        try: s.cleanup()
        except Exception: s.put('cleanup', False, completed=0)
        # Persist ordinary rows before real tests. Diagnostic/cleanup verdicts
        # do not gate PIN or encoding. USB re-enumeration and warm reboot come
        # afterwards, so their transport loss cannot prevent either test.
        s.persist()
        if rows_complete and s.hvenc_qualify:
            try:
                runner = 'variants.py' if s.hvenc_variants else 'run.py'
                spec=importlib.util.spec_from_file_location('last_hvenc',MAINLINE/'video/qualification'/runner)
                qualifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(qualifier)
                qualifier.run(s)
            except InterruptedError:
                rows_complete = False
                s.put('18_hardware_encoder',False,qualification=1,attempted=0,interrupted=1)
            except Exception: s.put('18_hardware_encoder',False,qualification=1,attempted=0,internal_error=1)
        # An owner interruption must clean up, never admit a new credential.
        if rows_complete: s.final_pin()
        if rows_complete and (s.hvenc_qualify or s.pin_requested):
            s.independent(('11_adb_mtp', '01_restart'), s.usb_restart)
            s.independent(('cleanup',), s.cleanup)
            s.independent(('A_stability',), s.finish_stability)
        try:
            if s.touched: s.collect_anr_evidence(force=not rows_complete)
        except Exception: print('STABILITY_EVIDENCE {"completed":0}', flush=True)
        if s.touched: s.independent(('30_fp_no_dumps',), s.fingerprint_privacy)
        s.save()
    # Qualification FAIL is data; b-try should still own its full hold/return.
    qualified = not s.hvenc_qualify or s.results['18_hardware_encoder']['status']=='PASS'
    return 0 if rows_complete and s.results['cleanup']['status'] == 'PASS' and qualified else 1

if __name__ == '__main__': sys.exit(main())
