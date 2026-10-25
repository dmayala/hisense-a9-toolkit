#!/usr/bin/env python3
"""Opt-in standby measurement and independent charging-restoration watchdog.

Import is inert. Only the existing B owner may call run(); no lifecycle owner,
network credentials, raw register writes, forced suspend or battery simulation.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import select
import secrets
import signal
import subprocess as sp
import sys
import time

HERE = Path(__file__).resolve().parent
DURATION = 3600
RESERVE = DURATION + 300
MARGIN_PERCENT = 35                 # Restore before reaching the hard 30% floor.
ROW = '10_hourly_drain'
CLOCK = '/data/local/tmp/a9-drain-clock'
CONTROLS = {1: '/sys/class/power_supply/battery/charge_behaviour',
            2: '/sys/class/power_supply/a9-usb/input_suspend',
            3: '/sys/class/power_supply/usb/input_suspend',
            4: '/sys/class/power_supply/battery/input_suspend'}


def private_json(path, value):
    with open(path, 'w', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        json.dump(value, stream, sort_keys=True); stream.write('\n')
    os.chmod(path, 0o600)


def group_birth(pid):
    text = Path('/proc/'+str(pid)+'/stat').read_text()
    fields = text[text.rfind(')')+2:].split()
    if int(fields[2]) != pid: raise ValueError('owned process group')
    return int(fields[19])


def stop_groups(groups):
    stopped = 0; cleared = True
    for pid, (birth, marker) in groups.items():
        signalled = False
        try:
            if group_birth(pid) == birth:
                os.killpg(pid, signal.SIGKILL); signalled = True
        except (OSError, ValueError): pass
        # An exited group leader can leave adb/helper descendants behind. Only
        # signal exact pidfd identities inheriting our private command marker;
        # numeric PGID alone no longer proves ownership after leader death.
        for attempt in range(4):
            found = False; uncertain = False
            for proc in Path('/proc').iterdir():
                if not proc.name.isdecimal(): continue
                fd = None; candidate = False
                try:
                    fd = os.pidfd_open(int(proc.name))
                    stat = (proc/'stat').read_text(); f = stat[stat.rfind(')')+2:].split()
                    if int(f[2]) != pid or int(f[3]) != pid or int(f[19]) < birth: continue
                    if f[0] in ('Z', 'X'): continue  # Dead tasks cannot mutate charging.
                    candidate = True
                    if ('A9_DRAIN_COMMAND='+marker).encode() not in (proc/'environ').read_bytes().split(b'\0'): continue
                    found = True
                    if attempt < 3:
                        signal.pidfd_send_signal(fd, signal.SIGKILL); signalled = True
                except PermissionError:
                    uncertain = uncertain or candidate
                except (OSError, ValueError): pass
                finally:
                    if fd is not None: os.close(fd)
            if not found and not uncertain: break
            if attempt < 3: time.sleep(.02)
        cleared = cleared and not found and not uncertain
        stopped += int(signalled)
    return dict(stopped=stopped, cleared=int(cleared))


def command(argv, source=None, timeout=12, watcher=None):
    """Bound and reap this command's entire owned group, including adb children."""
    marker = secrets.token_hex(16)
    p = sp.Popen(list(map(str, argv)), stdin=sp.PIPE if source is not None else sp.DEVNULL,
                 stdout=sp.PIPE, stderr=sp.DEVNULL, start_new_session=True, close_fds=True,
                 env=dict(os.environ, A9_DRAIN_COMMAND=marker))
    birth = 0
    try:
        # onb-root reads its script from stdin. Register before sending any
        # mutation; EOF after parent death cannot orphan a late pause command.
        birth = group_birth(p.pid)
        if watcher is not None: watcher.track(p.pid, birth, marker)
        out, _ = p.communicate(source, timeout=timeout)
        if p.returncode: raise RuntimeError('transport')
        return out.decode(errors='replace').replace('\r', '')
    finally:
        # Do not poll/reap first: a zombie leader still reserves the PGID while
        # a descendant holds our output pipe. Kill before giving up that anchor.
        if p.returncode is None:
            try: os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        try:
            try: p.communicate(timeout=2)
            except sp.TimeoutExpired:
                p.stdout.close()
                p.wait(timeout=2)
        finally:
            # EOF/leader success does not prove that a child with redirected
            # stdio exited. Clear marked descendants on normal completion too.
            cleared = bool(stop_groups({p.pid:(birth,marker)})['cleared'])
            if watcher is not None and cleared: watcher.untrack(p.pid)
            if not cleared: raise RuntimeError('command cleanup uncertain')


def fields(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r'[a-z_]+', parts[0]):
            if parts[0] in result: raise ValueError('duplicate field')
            result[parts[0]] = parts[1]
        elif parts and parts[0] == 'DRAIN_CLOCK' and len(parts) == 4:
            result.update(zip(('boot_ns', 'mono_ns', 'uncertainty_ns'), parts[1:]))
    return result


class Native:
    def __init__(self, context=None):
        if os.environ.get('A9_BTRY_IN_HOOK') != '1' or not os.environ.get('ADB'):
            raise RuntimeError('owning hook required')
        self.context = context

    def root(self, source):
        if self.context:
            boot = self.context['boot']
            if not re.fullmatch(r'[0-9a-f-]{36}', boot): raise ValueError('boot identity')
            source = '[ "$(cat /proc/sys/kernel/random/boot_id)" = "'+boot+'" ] || exit 90\n'+source
        return command([sys.executable, '-B', HERE.parent/'boot-deadline/onb-root.py'], source.encode(),
                       watcher=getattr(self, 'watchdog', None))

    def framework(self, source):
        # svc/input launch app_process; debug/framework commands belong to
        # ordinary shell. Keep the same bounded, tracked command ownership.
        return command([os.environ['ADB'], 'shell', '-T', 'set -e\n'+source],
                       watcher=getattr(self, 'watchdog', None))

    def inspect(self):
        text = self.root(r'''model=$(cat /sys/class/power_supply/battery/model_name) || exit 1
kind=0
if [ -r /sys/class/power_supply/battery/drain_counter_supported ] && [ "$(cat /sys/class/power_supply/battery/drain_counter_supported)" = 1 ]; then
    kind=1
    [ "$(cat /sys/class/power_supply/a9-usb/input_suspend)" = 0 ] || { echo control 0; exit 0; }
else
    case "$model" in A9-PMI632-sampled-CC-POC) echo unsupported_counter 1; exit 0;; esac
fi
echo unsupported_counter 0
printf 'counter_kind %s\n' "$kind"
printf 'boot %s\n' "$(cat /proc/sys/kernel/random/boot_id)"
if [ "$kind" = 1 ]; then
    printf 'capacity %s\n' "$(cat /sys/class/power_supply/battery/drain_capacity)"
    cat /sys/class/power_supply/battery/drain_snapshot | awk '$1=="charge_counter" { print "counter",$2 }'
else
    printf 'capacity %s\n' "$(cat /sys/class/power_supply/battery/capacity)"
    printf 'counter %s\n' "$(cat /sys/class/power_supply/battery/charge_counter)"
fi
if [ "$kind" = 1 ]; then echo control 2
elif [ -w /sys/class/power_supply/battery/charge_behaviour ] && grep -q '\[auto\].*force-discharge\|force-discharge.*\[auto\]' /sys/class/power_supply/battery/charge_behaviour; then echo control 1
elif [ -w /sys/class/power_supply/a9-usb/input_suspend ] && [ "$(cat /sys/class/power_supply/a9-usb/input_suspend)" = 0 ]; then echo control 2
elif [ -w /sys/class/power_supply/usb/input_suspend ] && [ "$(cat /sys/class/power_supply/usb/input_suspend)" = 0 ]; then echo control 3
elif [ -w /sys/class/power_supply/battery/input_suspend ] && [ "$(cat /sys/class/power_supply/battery/input_suspend)" = 0 ]; then echo control 4
else echo control 0; fi
''')
        d = fields(text)
        if d.get('unsupported_counter') == '1': return None, dict(unsupported_counter=1)
        d.update(fields(self.framework('printf \'stayon %s\\n\' "$(settings get global stay_on_while_plugged_in)"\ndumpsys power | grep -q \'mWakefulness=Awake\' && echo awake 1 || echo awake 0\n')))
        if d.get('counter_kind', '0') not in ('0', '1'): raise ValueError('counter kind')
        if d.get('counter_kind') == '1' and d.get('control') != '2': return None, dict(unsupported_control=1)
        if d.get('control') not in ('1', '2', '3', '4'): return None, dict(unsupported_control=1)
        if not re.fullmatch(r'[0-9a-f-]{36}', d.get('boot', '')): raise ValueError('boot')
        if not re.fullmatch(r'(?:null|[0-9]{1,2})', d.get('stayon', '')): raise ValueError('stayon')
        if d.get('awake') not in ('0', '1'): raise ValueError('wake state')
        if not re.fullmatch(r'-?[0-9]{1,12}', d.get('counter', '')): return None, dict(counter_unavailable=1)
        if not MARGIN_PERCENT < int(d['capacity']) <= 100: return None, dict(battery_margin=0)
        context = dict(boot=d['boot'], stayon=d['stayon'], awake=int(d['awake']), control=int(d['control']))
        if d.get('counter_kind') == '1': context['counter_kind'] = 1
        self.context = context
        return context, {}

    def install_clock(self, assets, shell):
        manifest = json.loads((assets/'assets.json').read_text())
        source = assets/'drain-clock'
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['assets']['drain-clock']:
            raise ValueError('clock asset hash')
        # Parent transport/deadline remains the existing sweep's.
        rc, _ = shell.run([shell.adb, 'push', source, CLOCK], timeout=20)
        if rc: raise RuntimeError('clock push')
        self.root('chmod 0755 '+CLOCK+'\n')

    def mode(self, paused):
        control = self.context['control']; path = CONTROLS[control]
        value = ('force-discharge' if paused else 'auto') if control == 1 else str(int(paused))
        expected = ('['+value+']') if control == 1 else value
        check = ('grep -Fq '+repr(expected)+' '+path) if control == 1 else '[ "$(cat '+path+')" = '+value+' ]'
        self.root('printf "%s\\n" '+value+' > '+path+' || exit 1\n'+check+' || exit 1\necho mode_ok 1\n')

    def prepare_screen(self):
        self.framework('svc power stayon false\ninput keyevent KEYCODE_SLEEP\n'
                  '[ "$(settings get global stay_on_while_plugged_in)" = 0 ] || exit 1\n')

    def snapshot(self):
        path = CONTROLS[self.context['control']]
        suspended = ('grep -Fq "[force-discharge]" '+path) if self.context['control'] == 1 else '[ "$(cat '+path+')" = 1 ]'
        metric_source = r'''
for entry in capacity charge_counter charge_full_design; do
    value=$(cat /sys/class/power_supply/battery/$entry) || exit 1
    printf '%s %s\n' "$entry" "$value"
done
'''
        if self.context.get('counter_kind') == 1:
            metric_source = 'cat /sys/class/power_supply/battery/drain_snapshot || exit 1\n'
        d = fields(self.root(CLOCK+'\n'+metric_source+r'''
for supply in a9-usb usb; do
    if [ -r /sys/class/power_supply/$supply/present ]; then
        printf 'usb_present %s\n' "$(cat /sys/class/power_supply/$supply/present)"; break
    fi
done
'''+suspended+' && echo input_paused 1 || echo input_paused 0\n'+
            '[ "$(cat /sys/class/power_supply/battery/status)" = Charging ] && echo charging 1 || echo charging 0\n'))
        d.update(fields(self.framework("cmd wifi status | grep -Eiq '^wi-?fi is connected([[:space:]]|$)' && echo wifi 1 || echo wifi 0\ndumpsys power | grep -Eq 'mWakefulness=(Asleep|Dozing)' && echo screen_off 1 || echo screen_off 0\n")))
        required = ('boot_ns', 'mono_ns', 'uncertainty_ns', 'capacity', 'charge_counter', 'charge_full_design', 'usb_present', 'wifi', 'screen_off', 'input_paused', 'charging')
        if self.context.get('counter_kind') == 1:
            required += ('counter_sample_ms', 'counter_elapsed_ms', 'counter_wakeups')
        if any(not re.fullmatch(r'-?[0-9]{1,20}', d.get(k, '')) for k in required): raise ValueError('snapshot')
        d = {k: int(d[k]) for k in required}
        if (not 0 <= d['capacity'] <= 100 or not 0 <= d['uncertainty_ns'] <= 10_000_000
                or not 100_000 <= d['charge_full_design'] <= 20_000_000
                or abs(d['charge_counter']) > 2**31-1): raise ValueError('sample')
        if self.context.get('counter_kind') == 1 and any(d[k] < 0 for k in
                ('counter_sample_ms', 'counter_elapsed_ms', 'counter_wakeups')):
            raise ValueError('counter metadata')
        return d

    def battery_safe(self):
        name = 'drain_capacity' if self.context.get('counter_kind') == 1 else 'capacity'
        value = self.root('cat /sys/class/power_supply/battery/'+name+'\n').strip()
        return bool(re.fullmatch(r'[0-9]{1,3}', value)) and MARGIN_PERCENT < int(value) <= 100

    def wakeups(self, path):
        # Wakeup attribution is diagnostic. An unavailable debugfs mount or
        # denied read must not prevent a real charge/residency measurement.
        source = r'''if table=$(head -c 262145 /sys/kernel/debug/wakeup_sources 2>/dev/null); then
    printf '%s\n' "$table"
    exit 0
fi
printf 'name active_count event_count wakeup_count total_time_ms prevent_suspend_time_ms\n'
count=0
for entry in /sys/class/wakeup/wakeup*; do
    [ -d "$entry" ] || continue
    name=$(cat "$entry/name" 2>/dev/null) || continue
    count=$((count + 1))
    [ "$count" -le 256 ] || exit 1
    printf '%s' "$name"
    for field in active_count event_count wakeup_count total_time_ms prevent_suspend_time_ms; do
        value=$(cat "$entry/$field" 2>/dev/null) || value=-
        printf ' %s' "$value"
    done
    printf '\n'
done
[ "$count" -gt 0 ]
'''
        try:
            text = self.root(source)
            if not text.startswith('name') or len(text.encode()) > 262144:
                raise ValueError('wakeup sources')
        except InterruptedError:
            raise
        except Exception:
            path.write_text('unavailable\n'); path.chmod(0o600)
            return False
        path.write_text(text); path.chmod(0o600)
        return True

    def restore(self):
        self.mode(False)                         # Charging first, before UI state.
        saved = self.context['stayon']
        if not re.fullmatch(r'(?:null|[0-9]{1,2})', saved): raise ValueError('stayon')
        setting = 'settings delete global stay_on_while_plugged_in' if saved == 'null' else 'settings put global stay_on_while_plugged_in '+saved
        key = 'KEYCODE_WAKEUP' if self.context['awake'] else 'KEYCODE_SLEEP'
        self.framework(setting+'\ninput keyevent '+key+'\n')


def restore_attempts(native, sleep=time.sleep):
    for attempt in range(3):
        try:
            native.restore()
            return dict(restored=1, attempts=attempt+1)
        except Exception:
            if attempt < 2: sleep(1)
    return dict(restored=0, attempts=3)


def read_control(stream, limit=128, timeout=5):
    # No TextIO read-ahead: a buffered REAPED/PING must not be hidden from the
    # next select(). Partial or oversized control messages also stay bounded.
    try: fd = stream.fileno()
    except (AttributeError, OSError): return stream.readline()
    end = time.monotonic()+timeout; line = bytearray()
    while len(line) <= limit:
        if not select.select([fd], [], [], max(0, end-time.monotonic()))[0]: raise TimeoutError('control pipe')
        part = os.read(fd, 1)
        if not part or part == b'\n': return line.decode('ascii')
        line.extend(part)
    raise ValueError('control length')


def watch_loop(stream, native, deadline, report, ready, now=time.monotonic, wait=select.select):
    """EOF survives parent SIGKILL; deadline survives a live but hung parent."""
    ready(); reason = 1; heartbeat = now(); groups = {}
    try:
        while now() < deadline:
            readable, _, _ = wait([stream], [], [], max(0, min(30, deadline-now(), heartbeat+75-now())))
            if readable:
                line = read_control(stream).strip()
                if line == 'PING': heartbeat = now(); continue
                match = re.fullmatch(r'GROUP ([0-9]+) ([0-9]+) ([0-9a-f]{32})', line)
                if match:
                    if len(groups) >= 128: reason = 6; break
                    groups[int(match[1])] = (int(match[2]), match[3]); continue
                match = re.fullmatch(r'REAPED ([0-9]+)', line)
                if match: groups.pop(int(match[1]), None); continue
                reason = 2 if line == 'DONE' else 3; break
            if now()-heartbeat >= 75: reason = 4; break
            try:
                if not native.battery_safe(): reason = 5; break
            except Exception: reason = 6; break
    except Exception: reason = 6
    finally:
        killed = stop_groups(groups)
        result = restore_attempts(native)
        result['reason'] = reason
        result['stopped_command_groups'] = killed['stopped']
        result['command_groups_cleared'] = killed['cleared']
        private_json(report, result)
    return result


class Watchdog:
    def __init__(self, context, deadline, report):
        self.report = report
        # No owner lock descriptors are inherited; this is only a restoration
        # child of the existing hook, never a second phone lifecycle owner.
        self.p = sp.Popen([sys.executable, '-B', __file__, '--watchdog'], stdin=sp.PIPE,
                          stdout=sp.PIPE, stderr=sp.DEVNULL, text=True,
                          start_new_session=True, close_fds=True)
        self.p.stdin.write(json.dumps(dict(context=context, deadline=deadline, report=str(report)))+'\n')
        self.p.stdin.flush()
        if not select.select([self.p.stdout], [], [], 10)[0] or self.p.stdout.readline().strip() != 'ARMED':
            self.p.stdin.close(); self.p.wait(timeout=90)
            raise RuntimeError('watchdog not armed')

    def finish(self):
        try:
            self.p.stdin.write('DONE\n'); self.p.stdin.flush()
        except (OSError, BrokenPipeError): pass
        self.p.stdin.close()
        self.p.wait(timeout=90)
        return self.p.returncode == 0 and json.loads(self.report.read_text()).get('restored') == 1

    def ping(self):
        self.p.stdin.write('PING\n'); self.p.stdin.flush()

    def track(self, pid, birth, marker):
        self.p.stdin.write('GROUP '+str(pid)+' '+str(birth)+' '+marker+'\n'); self.p.stdin.flush()

    def untrack(self, pid):
        try:
            self.p.stdin.write('REAPED '+str(pid)+'\n'); self.p.stdin.flush()
        except (OSError, BrokenPipeError): pass


def measure(native, watchdog_factory, out, deadline, now=time.monotonic, sleep=time.sleep,
            restored_callback=lambda okay: None):
    """Fixed 60-minute sample; injected clock/backend are host-test seams only."""
    result = dict(attempted=0, measured=0, restored=0, below_floor=0)
    if deadline-now() < RESERVE: return dict(result, insufficient_time=1), True
    watchdog = None; restoration = False; phase = 1
    try:
        before = native.wakeups(out/'drain-wakeup-before.txt') is not False
        result['wakeup_sources_before'] = int(before)
        phase = 2
        watchdog = watchdog_factory(native.context, min(deadline-90, now()+DURATION+180), out/'drain-watchdog.json')
        native.watchdog = watchdog
        result['attempted'] = 1
        phase = 3
        native.mode(True)
        phase = 4
        native.prepare_screen()
        phase = 5
        start = native.snapshot()
        result['start_capacity'] = start['capacity']
        if start['capacity'] <= MARGIN_PERCENT: raise ValueError('battery margin')
        end = now()+DURATION; samples = 0
        phase = 6
        while True:
            sample = native.snapshot(); samples += 1
            watchdog.ping()
            if sample['capacity'] <= MARGIN_PERCENT:
                result.update(battery_margin=0, below_floor=int(sample['capacity'] < 30)); raise ValueError('battery margin')
            if not all(sample[k] == 1 for k in ('usb_present', 'wifi', 'screen_off', 'input_paused')) or sample['charging'] != 0:
                raise ValueError('standby conditions')
            if now() >= end: break
            sleep(min(60, end-now()))
        after = native.wakeups(out/'drain-wakeup-after.txt') is not False
        result['wakeup_sources_after'] = int(after)
        phase = 7
        elapsed = sample['boot_ns']-start['boot_ns']; awake = sample['mono_ns']-start['mono_ns']
        uncertainty = sample['uncertainty_ns']+start['uncertainty_ns']
        if not DURATION*10**9 <= elapsed <= (DURATION+60)*10**9 or awake < 0 or not -uncertainty <= elapsed-awake <= elapsed+uncertainty:
            raise ValueError('clock interval')
        delta = start['charge_counter']-sample['charge_counter']
        if not 0 <= delta <= start['charge_full_design']: raise ValueError('charging or counter reset')
        rate_ns = elapsed
        if native.context.get('counter_kind') == 1:
            covered = sample['counter_sample_ms']-start['counter_sample_ms']
            counter_elapsed = sample['counter_elapsed_ms']-start['counter_elapsed_ms']
            if (counter_elapsed <= 0 or sample['counter_wakeups'] < start['counter_wakeups']
                    or abs(counter_elapsed-elapsed//10**6) > 1000
                    or not 990*counter_elapsed <= 1000*covered <= 1010*counter_elapsed):
                raise ValueError('incomplete QG sample coverage')
            rate_ns = covered*10**6
            result.update(counter_kind=1, counter_covered_ms=covered,
                          counter_coverage_permille=covered*1000//counter_elapsed,
                          counter_wakeups=sample['counter_wakeups']-start['counter_wakeups'])
        result.update(measured=1, elapsed_ms=elapsed//10**6, suspend_ms=max(0, elapsed-awake)//10**6,
                      awake_ms=awake//10**6, clock_uncertainty_ns=uncertainty,
                      suspend_permille=max(0, elapsed-awake)*1000//elapsed, charge_delta_uah=delta,
                      drain_uah_per_hour=delta*3600*10**9//rate_ns, end_capacity=sample['capacity'],
                      drain_millipercent_per_hour=delta*3600*100000*10**9//(rate_ns*start['charge_full_design']),
                      samples=samples, wakeup_sources_recorded=int(before and after))
    except InterruptedError:
        result['interrupted'] = 1
        raise
    except Exception:
        result.update(stage_failed=1, failed_phase=phase)
    finally:
        if watchdog is not None:
            primary = restore_attempts(native)
            try: restoration = watchdog.finish()
            except Exception: restoration = False
            result.update(primary_restored=primary['restored'], restored=int(restoration))
        else: restoration = True                # No charging mutation was admitted.
        restored_callback(restoration)
        private_json(out/'drain-result.json', result)
    return result, restoration


def run(sweep):
    if not sweep.drain_requested: return
    # Return unused reservation even when a precondition refuses the stage.
    sweep.end += sweep.drain_reserved
    sweep.command_end = sweep.end
    sweep.wait_limit = sweep.end-300
    if not sweep.drain_reserved:
        sweep.put(ROW, False, attempted=0, insufficient_time=1); return
    if sweep.results['12_wifi']['status'] != 'PASS':
        sweep.not_run(ROW, attempted=0, wifi_prerequisite=0); return
    native = Native()
    context, unavailable = native.inspect()
    if context is None:
        sweep.not_run(ROW, attempted=0, measured=0, **unavailable); return
    native.install_clock(sweep.assets, sweep)
    deadline = min(sweep.end-300, float(os.environ['A9_BTRY_OWNER_END'])-120)
    result, restored = measure(native, Watchdog, sweep.out, deadline,
                               restored_callback=lambda okay: setattr(sweep, 'drain_cleanup_ok', okay))
    sweep.drain_cleanup_ok = restored
    sweep.put(ROW, result.get('measured') == 1 and restored, **result)


def main():
    if sys.argv[1:] != ['--watchdog']: return 2
    try:
        message = json.loads(read_control(sys.stdin, 4096, 10))
        result = watch_loop(sys.stdin, Native(message['context']), float(message['deadline']),
                            Path(message['report']), lambda: print('ARMED', flush=True))
        return 0 if result['restored'] and result['command_groups_cleared'] else 1
    except Exception: return 1


if __name__ == '__main__': sys.exit(main())
