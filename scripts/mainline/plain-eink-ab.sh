#!/usr/bin/env bash
# One OFF/ON optical comparison inside b-try's B hold. No SurfaceFlinger restart.
set -euo pipefail
P=/home/user/A9-build/mainline
PY=${A9_EINK_PYTHON:-/nix/store/ykripfs7ljz7i7ibwsfzy6d4hpjmcddk-python3-3.13.7-env/bin/python3}
umask 077
if [[ ${A9_BTRY_IN_HOOK:-0} != 1 ]]; then
    export A9_BTRY_ONB="$(realpath "$0")"
    export A9_BTRY_HOLD=${A9_BTRY_HOLD:-1200}
    exec "$P/b-try.sh" "${A9_PLAIN_BOOT:-$P/plain-deadline-logfix1-20261022/boot.img}" "${A9_PLAIN_VENDOR:-$P/row28-gapps-20261022/vendor-row28.img}"
fi
: "${ADB:?b-try supplies ADB}" "${A9_BTRY_LOGS:?b-try supplies logs directory}"
out="$A9_BTRY_LOGS/eink-ab"
mkdir -m 700 -p "$out"
"$PY" - "$out" <<'PY'
from pathlib import Path
import csv, io, os, re, shlex, signal, subprocess, sys, time, urllib.request
from PIL import Image, ImageDraw, ImageOps
out = Path(sys.argv[1])
def terminate(signum, frame):
    raise SystemExit(128+signum)
signal.signal(signal.SIGTERM, terminate)
adb = os.environ.get('ADB', 'adb')
def shell(cmd):
    guard = '[ "$(getprop ro.boot.slot_suffix)" = _b ] || exit 97; '
    return subprocess.check_output([adb, 'shell', guard+cmd], timeout=60, text=True).replace('\r', '')
def root(cmd):
    helper = Path(os.environ['A9_HOME'])/'scripts/mainline/boot-deadline/onb-root.py'
    return subprocess.check_output([sys.executable, str(helper)], input=cmd,
                                   text=True, timeout=105)
def save(name, text):
    (out/name).write_text(text)
# Framework commands need adbd's Android linker environment, not the root nc service.
shell('svc power stayon true && input keyevent KEYCODE_WAKEUP')
# The existing root shell installs the user's blobs; only ephemeral /data files.
for name in ['a9-waveform.bin', 'a9-ctrl-template.bin']:
    subprocess.run([adb, 'push', '/home/user/A9-build/mainline/firmware/hisense/'+name,
                    '/data/local/tmp/plain-'+name], check=True, timeout=60)
save('setup.txt', root('''
set -x
mkdir -p /data/local/tmp/a9fw/hisense
chmod 755 /data/local/tmp/a9fw /data/local/tmp/a9fw/hisense
for n in a9-waveform.bin a9-ctrl-template.bin; do
 cp /data/local/tmp/plain-$n /data/local/tmp/a9fw/hisense/$n
 chmod 644 /data/local/tmp/a9fw/hisense/$n
 rm /data/local/tmp/plain-$n
done
echo /data/local/tmp/a9fw > /sys/module/firmware_class/parameters/path
echo 1 > /proc/sys/kernel/printk
p=/sys/module/a9_eink/parameters
echo 0 > $p/skip_same_target
echo 0 > $p/prewarm
echo 1 > $p/input_diagnostics
echo 100 > $p/pg_settle_ms
echo 0 > $p/bin_min; echo 13 > $p/bin_max; echo 1 > $p/init_flash
echo Y > /sys/module/panel_hisense_hlte556n_eink/parameters/enable
echo Y > /sys/module/fp9929a/parameters/allow_power_on
echo Y > $p/enable
setprop vendor.a9ml.eink.gate open
for f in $p/enable $p/input_diagnostics $p/hv_hold_ms $p/hv_max_ms $p/hv_hold_interactive_ms $p/prewarm_gap_ms; do echo "$f=$(cat $f)"; done
'''))
# Same screen brightness and animation policy in both arms; leave modes/HV budgets alone.
# Restore is by returning to A; these Settings database values are restored explicitly.
brightness = shell('settings get system screen_brightness').strip()
brightness_mode = shell('settings get system screen_brightness_mode').strip()
try:
    shell('settings put system screen_brightness_mode 0 && settings put system screen_brightness 128')
    info = root('timeout 2 getevent -lp 2>&1 || true')
    save('input-device.txt', info)
    touch = None
    for block in re.split(r'(?=add device \d+:)', info):
        node = re.search(r'add device \d+:\s*(/dev/input/event\d+)', block)
        x = re.search(r'ABS_MT_POSITION_X[^\n]*min\s+(-?\d+),\s*max\s+(-?\d+)', block)
        y = re.search(r'ABS_MT_POSITION_Y[^\n]*min\s+(-?\d+),\s*max\s+(-?\d+)', block)
        if node and x and y and 'ABS_MT_SLOT' in block and 'ABS_MT_TRACKING_ID' in block:
            touch = (node[1], tuple(map(int, x.groups())), tuple(map(int, y.groups())), 'BTN_TOUCH' in block)
            break
    if touch is None:
        raise RuntimeError('No protocol-B touchscreen found; no synthetic fallback that would miss prewarm')
    node, xr, yr, btn = touch
    # Fractions of the touchscreen ABS ranges, portrait as on A9. Kernel input handler sees down.
    def swipe(up):
        x = round(xr[0]+.5*(xr[1]-xr[0]))
        a, b = (.78, .28) if up else (.28, .78)
        cmds = [f'sendevent {node} 3 47 0', f'sendevent {node} 3 57 42']
        if btn: cmds += [f'sendevent {node} 1 330 1']
        for i in range(17):
            y = round(yr[0]+(a+(b-a)*i/16)*(yr[1]-yr[0]))
            cmds += [f'sendevent {node} 3 53 {x}', f'sendevent {node} 3 54 {y}',
                     f'sendevent {node} 0 0 0', 'sleep 0.02']
        cmds += [f'sendevent {node} 3 57 -1']
        if btn: cmds += [f'sendevent {node} 1 330 0']
        cmds += [f'sendevent {node} 0 0 0']
        return '\n'.join(cmds)
    attrs = 'status input_stats damage_stats prewarm_stats mode'
    def stats():
        return root('for e in /sys/class/drm/card*-*/eink; do [ -d "$e" ] || continue; '
                    'for n in '+attrs+'; do echo "SECTION $n"; cat "$e/$n"; done; done')
    def section(s, name):
        return s.split('SECTION '+name+'\n', 1)[-1].split('SECTION ', 1)[0]
    def num(s, key):
        m = re.search(r'(?:^|\s)'+re.escape(key)+r'\s+(-?\d+)\b', s)
        return int(m[1]) if m else None
    def delta(before, after, sec, key):
        a,b = num(section(before, sec), key), num(section(after, sec), key)
        return b-a if a is not None and b is not None else None
    steps = [('01-settings-top', shell, 'am start -S -a android.settings.SETTINGS'),
             ('02-scroll-down', root, swipe(True)), ('03-scroll-down-again', root, swipe(True)),
             ('04-scroll-up', root, swipe(False)), ('05-scroll-up-again', root, swipe(False)),
             ('06-display-page', shell, 'am start -a android.settings.DISPLAY_SETTINGS'),
             ('07-back', shell, 'input keyevent KEYCODE_BACK'),
             ('08-same-page', shell, 'am start -a android.settings.SETTINGS')]
    rows=[]
    for arm, enabled in [('off', 0), ('on', 1)]:
        # Identical HOME + full clear history before each arm. No waveform/mode change.
        save(arm+'-params.txt', root(f'''p=/sys/module/a9_eink/parameters
echo {enabled} > $p/skip_same_target
echo {enabled} > $p/prewarm
cat $p/skip_same_target $p/prewarm $p/hv_hold_ms
'''))
        shell('input keyevent KEYCODE_HOME')
        time.sleep(2)
        root('for e in /sys/class/drm/card*-*/eink; do [ ! -f "$e/force_clear" ] || echo 1 > "$e/force_clear"; done')
        time.sleep(12)  # common reset and expired prewarm-gap/idle-hold
        for label, execute, cmd in steps:
            before=stats(); save(f'{arm}-{label}-before.txt', before)
            start=time.monotonic(); execute(cmd); action_ms=(time.monotonic()-start)*1000
            time.sleep(3)
            # Photograph first; dumpsys and stats are deliberately after the settled snapshot.
            with urllib.request.urlopen('http://127.0.0.1:8093/snapshot', timeout=15) as r:
                photo=Image.open(io.BytesIO(r.read())); photo.load()
            photo.save(out/f'{arm}-{label}.png')
            after=stats(); save(f'{arm}-{label}-after.txt', after)
            save(f'{arm}-{label}-hwc.txt', shell('dumpsys SurfaceFlinger'))
            samples=delta(before,after,'input_stats','samples')
            convert=delta(before,after,'input_stats','convert_total_ns')
            status=section(after,'status')
            last=next((line for line in status.splitlines() if line.startswith('last ')), '')
            row=dict(arm=arm, step=label, action_host_ms=round(action_ms,1),
                     samples=samples, convert_mean_us=round(convert/samples/1000,1) if samples and convert is not None else '',
                     engine_last_total_us=num(last,'total_us'), engine_last_begin_us=num(last,'begin_us'),
                     engine_last_prep_us=num(last,'prep_us'), compose_max_us=num(status,'compose_max_us'),
                     commit_max_us=num(status,'commit_max_us'),
                     updates=delta(before,after,'status','updates'),
                     frames=delta(before,after,'status','frames'), dry=num(last,'dry'),
                     prewarm_pokes=delta(before,after,'prewarm_stats','pokes'),
                     prewarm_fail=delta(before,after,'prewarm_stats','fail'),
                     same_checks=delta(before,after,'damage_stats','same_checks'),
                     same_skips=delta(before,after,'damage_stats','same_skips'),
                     prewarm_up=delta(before,after,'prewarm_stats','up'),
                     prewarm_unused=delta(before,after,'prewarm_stats','unused'))
            rows.append(row)
            with (out/'timing.tsv').open('w') as f:
                w=csv.DictWriter(f,fieldnames=list(row),delimiter='\t'); w.writeheader(); w.writerows(rows)
            print(arm,label,'updates',row['updates'],'frames',row['frames'],'dry',row['dry'],'skip',row['same_skips'],'prewarm',row['prewarm_up'],'fail',row['prewarm_fail'],flush=True)
            # Cold pipeline and gap between gestures in both arms; identical delay.
            time.sleep(10)
finally:
    def setting(key, value):
        return ('settings delete system '+key) if value == 'null' else ('settings put system '+key+' '+shlex.quote(value))
    try:
        shell(setting('screen_brightness',brightness)+' && '+setting('screen_brightness_mode',brightness_mode))
    finally:
        root('echo 0 > /sys/module/a9_eink/parameters/prewarm\necho 0 > /sys/module/a9_eink/parameters/skip_same_target')
# Full camera frame, no crop/alignment/contrast adjustment. Labels stay outside photos.
thumbw=640; thumbh=360; pad=12; header=48; rowh=thumbh+36
sheet=Image.new('RGB',(2*thumbw+3*pad,header+len(steps)*rowh+pad),'white'); d=ImageDraw.Draw(sheet)
d.text((pad,10),'OFF: same-target N / prewarm 0',fill='black')
d.text((2*pad+thumbw,10),'ON: same-target Y / prewarm 1',fill='black')
for i,(label,_,_) in enumerate(steps):
    for j,arm in enumerate(['off','on']):
        im=ImageOps.contain(Image.open(out/f'{arm}-{label}.png').convert('RGB'),(thumbw,thumbh))
        x=pad+j*(thumbw+pad); y=header+i*rowh
        d.text((x,y),label,fill='black'); sheet.paste(im,(x+(thumbw-im.width)//2,y+24))
sheet.save(out/'contact-sheet.png')
save('notes.txt','Boot: '+os.environ['A9_BTRY_BOOT']+'; OFF then ON, bundled skip/prewarm.\n'
     'Portrait kernel protocol-B scrolls; verify same Settings content in photos. Current mode/HV budgets unchanged.\n'
     'Stats last_* describe last completed update, max_* are cumulative maxima, not per-action latency.\n'
     'Conversion mean is a counter delta; action_host_ms includes transport/injection, not optical latency.\n'
     'No optical score/pass. Zero skips/prewarm_up means that feature was not exercised.\n'
     'Contact sheet shows entire camera frame; full-resolution originals and raw SurfaceFlinger/HWC dumps saved.\n')
print('Contact sheet:',out/'contact-sheet.png'); print('Timing table:',out/'timing.tsv')
PY
