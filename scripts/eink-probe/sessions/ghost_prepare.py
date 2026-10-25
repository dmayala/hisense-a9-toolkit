"""Ghost boot coordinator using review-88d safe3's production pipe barrier.

Only the pipe reader reaches the boot child. The shell owns this entire process
 group and all restore/accounting; this helper never restores or steals a lock.
Preparation scripts and verifiers are ported from the reviewed safe3 parent.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess as sp
import sys
import time

FAIL = re.compile(r'^FAIL:|FAILED|ERROR|ABL fell back|E Ink session stopped', re.M)

PREP_SLOT = r"""
[ "$(getprop ro.boot.slot_suffix)" = _b ]
[ "$(getprop sys.boot_completed)" = 1 ]
awk '$2=="/data" {n++;if($1!="/dev/block/ram0" || $3!="ext4")bad=1} END {exit !(n==1 && !bad)}' /proc/mounts
"""
PREP_SETUP = '# SAFE3_PREP_SETUP\nset -eu\n' + PREP_SLOT + r"""
settings --user 0 put global device_provisioned 1
settings --user 0 put secure user_setup_complete 1
pm disable-user --user 0 org.lineageos.setupwizard
pm disable-user --user 0 com.google.android.setupwizard
svc power stayon true
cmd uimode night no
am start -W -a android.intent.action.MAIN -c android.intent.category.HOME
"""
PREP_HOME = '# SAFE3_PREP_HOME\nset -eu\n' + PREP_SLOT + r"""
input keyevent BACK
am start -W -a android.intent.action.MAIN -c android.intent.category.HOME
"""
PREP_VERIFY = '# SAFE3_PREP_VERIFY\n' + PREP_SLOT + r"""
[ "$(settings --user 0 get global device_provisioned)" = 1 ]
[ "$(settings --user 0 get secure user_setup_complete)" = 1 ]
for p in org.lineageos.setupwizard com.google.android.setupwizard; do
    pm list packages -d --user 0 "$p" | grep -qxF "package:$p"
    echo "SAFE3_DISABLED $p"
done
[ "$(settings --user 0 get secure ui_night_mode)" = 1 ]
stay=$(settings --user 0 get global stay_on_while_plugged_in)
case "$stay" in ''|*[!0-9]*) exit 1;; esac
[ "$stay" -gt 0 ] && [ "$stay" -le 15 ]
echo "SAFE3_PROVISIONED $(settings --user 0 get global device_provisioned)"
echo "SAFE3_USER_SETUP $(settings --user 0 get secure user_setup_complete)"
echo "SAFE3_NIGHT $(settings --user 0 get secure ui_night_mode)"
echo "SAFE3_STAY $stay"
[ -z "$(getprop vendor.a9ml.gpu.launcher)" ]
home=$(cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME | tail -n1)
case "$home" in com.android.launcher3/?*) ;; *) exit 1;; esac
echo "SAFE3_HOME $home"
pids=$(pidof com.android.launcher3); set -- $pids
[ "$#" = 1 ]; case "$1" in ''|*[!0-9]*) exit 1;; esac
echo "SAFE3_PID $1"
stat=$(cat "/proc/$1/stat"); stat=${stat##*) }; set -- $stat
[ "$#" -ge 20 ]; shift 19
echo "SAFE3_START $1"
resumed=$(dumpsys activity activities | grep -E 'topResumedActivity|mResumedActivity' | tail -n1)
component=$(printf '%s\n' "$resumed" | grep -oE 'com\.android\.launcher3/[A-Za-z0-9_.$]+' | tail -n1)
echo "SAFE3_RESUMED $component"
echo 'SAFE3_SETUP verified=yes'
"""
PREP_CLOSED = '# SAFE3_PREP_CLOSED\n' + PREP_SLOT + r"""
printf 'SAFE3_GATE %s\n' "$(getprop vendor.a9ml.eink.gate)"
for g in a9_eink/parameters/enable fp9929a/parameters/allow_power_on panel_hisense_hlte556n_eink/parameters/enable; do
    v=$(cat /sys/module/$g)
    printf 'SAFE3_BOOL %s %s\n' "$g" "$v"
done
c=$(getprop vendor.a9ml.eink.card)
case "$c" in card[0-9]*) ;; *) exit 1;; esac
cat /sys/class/drm/$c-*/eink/status
cat /sys/kernel/debug/gpio
"""


class Stop(Exception):
    pass


def require_closed_readback(txt, preparing=True):
    # The property is an init request, unset on a fresh boot, not a gate readback.
    gates = re.findall(r'^SAFE3_BOOL ([\w/-]+) ([YN01])$', txt, re.M)
    expected = {'a9_eink/parameters/enable', 'fp9929a/parameters/allow_power_on',
                'panel_hisense_hlte556n_eink/parameters/enable'}
    # msm_gpio_dbg_show_one reports chip-local gpio36/gpio86, with no consumer
    # labels. Restrict to the board's TLMM chip; these offsets are in its DTS.
    chip = None
    pins = []
    for line in txt.splitlines():
        if re.match(r'^gpiochip[0-9]+:', line):
            chip = 'parent: platform/500000.pinctrl, 500000.pinctrl:' in line
        elif chip:
            m = re.match(r'^\s*gpio(36|86)\s*:\s*(.*)$', line)
            if m:
                pins.append(m.groups())
    requests = re.findall(r'^SAFE3_GATE ([^\n]*)$', txt, re.M)
    if (len(requests) != 1 or (preparing and requests[0] not in ('', 'closed')) or
            len(gates) != 3 or {k for k, v in gates} != expected or
            any(v not in ('N', '0') for k, v in gates) or
            len(re.findall(r'^enable 0\b[^\n]*\bhv_on 0\b', txt, re.M)) != 1 or
            len(pins) != 2 or {n for n, v in pins} != {'36', '86'} or
            any(not re.match(r'out\s+low\s+func0\b', v) for n, v in pins)):
        raise Stop('readback requires closed gates and HV/PWRUP/VCOM_EN low')


class Preparation:
    def __init__(self, args):
        self.a = args
        self.out = Path(args.out)
        self.adb = json.loads(args.adb_json)
        if not self.adb or not all(isinstance(x,str) and x for x in self.adb):
            raise Stop('invalid preparation transport')
        self.launcher = args.launcher_component
        self.prep_write = None
        self.child = None
        self.deadline = None
        self.poll = args.poll
        self.rehearsal = args.setup_wait != 5
        self.t = {'command': args.command_s}

    def log(self, text):
        print(text, flush=True)

    def boot_ok(self):
        txt = (self.out/'boot.log').read_text(errors='replace')
        if FAIL.search(txt) or self.child.poll() is not None:
            raise Stop('boot child failed/exited before verified preparation/HOLD')
        return txt

    def command(self, cmd, filename):
        self.boot_ok()
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            raise Stop('preparation/HOLD shared deadline exceeded')
        with (self.out/filename).open('w') as log:
            child = sp.Popen(cmd,stdin=sp.DEVNULL,stdout=log,stderr=sp.STDOUT)
            try:
                rc = child.wait(timeout=min(self.t['command'],remaining))
            finally:
                if child.poll() is None:
                    child.terminate()
                    try: child.wait(timeout=2)
                    except sp.TimeoutExpired: child.kill(); child.wait(timeout=2)
        self.boot_ok()
        return rc,(self.out/filename).read_text(errors='replace').replace('\r','')

    def root(self, text, filename):
        return self.command(self.adb+['shell','echo '+shlex.quote(text)+' | nc 127.0.0.1 2324'],filename)

    def wait_boot(self, pattern, deadline):
        while True:
            txt = self.boot_ok()
            if time.monotonic() >= deadline:
                raise Stop('preparation/HOLD shared deadline exceeded')
            if pattern in txt:
                return
            time.sleep(min(self.poll,max(0,deadline-time.monotonic())))

    def run(self):
        read_fd,self.prep_write = os.pipe()
        try:
            self.child = sp.Popen(['bash',self.a.boot_script,*self.a.boot_args,
                                  '--require-preparation','--latency-prepared-fd',str(read_fd)],
                                 pass_fds=(read_fd,),stdin=sp.DEVNULL,
                                 env=dict(os.environ,GHOST_ATTEMPT_OUT=str(self.out)))
        finally:
            os.close(read_fd)
        try:
            # Outer wrapper enforces reboot/slot-B deadlines independently. Its
            # 600-second HOLD deadline includes our barrier/preparation work.
            self.wait_boot('-- SAFE3 PREPARATION BARRIER:',time.monotonic()+self.a.arrival_s)
            self.deadline = time.monotonic()+self.a.hold_s
            self.android_prepare(self.deadline)
            if self.a.probe:
                self.wait_boot('-- GHOST CLOSED PROBE ADMITTED',self.deadline)
                self.closed_preparation('probe-after-admission.txt')
                self.camera_check()
                self.closed_preparation('probe-after-camera.txt')
                print('-- GHOST CLOSED PROBE VERIFIED',flush=True)
                # The outer wrapper always uses its proven restore path.
                return self.child.wait()
            self.wait_boot('-- HOLD',self.deadline)
            print('-- GHOST PREPARATION AND HOLD VERIFIED',flush=True)
            return self.child.wait()
        finally:
            if self.prep_write is not None:
                os.close(self.prep_write); self.prep_write = None
            if self.child.poll() is None:
                self.child.terminate()
                try: self.child.wait(timeout=20)
                except sp.TimeoutExpired: self.child.kill(); self.child.wait(timeout=5)

    def camera_check(self):
        import cv2
        import numpy as np
        sys.path.insert(0,str(Path(self.a.boot_script).resolve().parents[3]/'scripts/eink-probe'))
        import probe_lib as L
        cal=L.Calib.load(self.a.calib)
        if cal.H.shape!=(3,3) or not np.isfinite(cal.H).all() or abs(np.linalg.det(cal.H))<1e-12:
            raise Stop('camera calibration matrix invalid')
        cap=L.Capture(self.a.url,str(self.out/'camera-probe'),max_seconds=4,stale_reconnect_s=3,max_reconnects=0).start()
        try:
            if not cap.wait_first(min(8,max(0,self.deadline-time.monotonic()))):
                raise Stop('camera probe received no frame')
            end=min(time.monotonic()+4,self.deadline)
            while time.monotonic()<end:
                if len(cap.index)>=3 and cap.index[-1]['mono']>cap.index[0]['mono']:break
                time.sleep(.05)
        finally:
            rows=cap.stop()
        if len(rows)<3 or cap.error or rows[-1]['mono']<=rows[0]['mono']:
            raise Stop('camera probe stream/freshness failed')
        image=cv2.imread(str(self.out/'camera-probe'/('f%05d.jpg'%rows[-1]['i'])))
        if image is None or cal.quad.shape!=(4,2) or not np.isfinite(cal.quad).all():
            raise Stop('camera frame/calibration quad invalid')
        h,w=image.shape[:2]
        if not ((cal.quad[:,0]>=0).all() and (cal.quad[:,0]<w).all() and (cal.quad[:,1]>=0).all() and (cal.quad[:,1]<h).all()):
            raise Stop('camera calibration quad outside live frame')
        warped=cal.warp_gray(image)
        if warped.shape!=(1648,824) or not np.isfinite(warped).all():
            raise Stop('camera calibration warp failed')
        preview=image.copy();cv2.polylines(preview,[cal.quad.astype(np.int32)],True,(0,255,0),3)
        cv2.imwrite(str(self.out/'camera-quad.png'),preview)
        cv2.imwrite(str(self.out/'camera-warp.png'),np.clip(warped*255,0,255).astype(np.uint8))
        from ghost_recovery import atomic_json
        atomic_json(self.out/'camera-check.json',dict(frames=len(rows),frame_shape=[h,w],warp_shape=list(warped.shape),
                    geometry_and_stream=True,pattern_registration='not measured with gates closed'))

    def checked_root(self, script, filename):
        rc, txt = self.root("(\nset -eu\n" + script +
                            "\n)\nr=$?; printf 'SAFE3_ROOT_RC %s\n' \"$r\"", filename)
        markers = re.findall(r'^SAFE3_ROOT_RC ([0-9]+)$', txt, re.M)
        if rc or markers != ['0']:
            raise Stop(filename + ' root readback failed')
        return txt

    def closed_preparation(self, filename):
        txt = self.checked_root(PREP_CLOSED, filename)
        try:
            require_closed_readback(txt)
        except Stop:
            from ghost_attempt import breach
            breach(self.out,txt)
            raise
        return txt

    def android_prepare(self, deadline):
        self.closed_preparation('preparation-before.txt')
        rc, _ = self.command(self.adb + ['shell', PREP_SETUP], 'setup.log')
        if rc:
            raise Stop('Android setup failed')
        time.sleep(self.a.setup_wait)
        rc, _ = self.command(self.adb + ['shell', PREP_HOME], 'home.log')
        if rc:
            raise Stop('Android HOME failed')
        txt = self.checked_root(PREP_VERIFY, 'preparation-readback.txt')
        if (any(re.findall(r'^SAFE3_' + key + r' (\S+)$', txt, re.M) != ['1']
                for key in ('PROVISIONED', 'USER_SETUP', 'NIGHT')) or
                sorted(re.findall(r'^SAFE3_DISABLED (\S+)$', txt, re.M)) !=
                ['com.google.android.setupwizard', 'org.lineageos.setupwizard']):
            raise Stop('preparation readback: setup values not confirmed')
        stay = re.findall(r'^SAFE3_STAY ([0-9]+)$', txt, re.M)
        if len(stay) != 1 or not 0 < int(stay[0]) <= 15:
            raise Stop('preparation readback: stay-awake value not confirmed')
        home = re.findall(r'^SAFE3_HOME (com\.android\.launcher3/[A-Za-z0-9_.$]+)$', txt, re.M)
        resumed = re.findall(r'^SAFE3_RESUMED (com\.android\.launcher3/[A-Za-z0-9_.$]+)$', txt, re.M)
        def canonical(component):
            pkg, activity = component.split('/', 1)
            return pkg + '/' + (pkg + activity if activity.startswith('.') else activity)
        pid = re.findall(r'^SAFE3_PID ([1-9][0-9]*)$', txt, re.M)
        start = re.findall(r'^SAFE3_START ([1-9][0-9]*)$', txt, re.M)
        if (len(home) != 1 or len(resumed) != 1 or canonical(home[0]) != canonical(resumed[0])
                or len(pid) != 1 or len(start) != 1 or
                re.findall(r'^SAFE3_SETUP verified=yes$', txt, re.M) != ['SAFE3_SETUP verified=yes']):
            raise Stop('preparation readback: intended foreground Launcher3 identity not proved')
        if self.launcher and canonical(home[0]) != canonical(self.launcher):
            raise Stop('preparation readback: common launcher component mismatch')
        self.closed_preparation('preparation-after.txt')
        self.boot_ok()
        if time.monotonic() >= deadline:
            raise Stop('preparation/HOLD shared deadline exceeded')
        receipt = dict(home=home[0], resumed=resumed[0], pid=int(pid[0]), start_ticks=int(start[0]),
                       setup_verified=True, closed_before=True, closed_after=True)
        (self.out / 'preparation.json').write_text(json.dumps(receipt, indent=2) + '\n')
        self.log('preparation verified: intended Launcher3, setup readbacks, gates/HV/GPIO closed; releasing admission')
        os.write(self.prep_write, b'PREPARED_LAUNCHER3_VERIFIED_V1\n')
        os.close(self.prep_write)
        self.prep_write = None


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--out',required=True)
    p.add_argument('--boot-script',required=True)
    p.add_argument('--adb-json',default='["adb"]')
    p.add_argument('--launcher-component',default='')
    p.add_argument('--hold-s',type=float,default=600)
    p.add_argument('--command-s',type=float,default=30)
    p.add_argument('--arrival-s',type=float,default=3000)
    p.add_argument('--poll',type=float,default=3)
    p.add_argument('--setup-wait',type=float,default=5)
    p.add_argument('--probe',action='store_true')
    p.add_argument('--calib')
    p.add_argument('--url',default='http://127.0.0.1:8093/stream')
    p.add_argument('boot_args',nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    if a.boot_args[:1] == ['--']: a.boot_args.pop(0)
    if not (0<a.hold_s<=600 and 0<a.command_s<=30 and 0<a.arrival_s<=3000 and a.poll>0 and 0<=a.setup_wait<=5):
        p.error('preparation deadline exceeds reviewed bounds')
    if a.probe and ('--open-gates' in a.boot_args or '--ghost-closed-probe' not in a.boot_args or not a.calib):
        p.error('closed probe requires calibration and forbids open gates')
    if '--write' not in a.boot_args or (not a.probe and '--open-gates' not in a.boot_args):
        p.error('coordinator is only for the bounded write/open path')
    def interrupted(signum,_frame):
        raise Stop('signal '+str(signum))
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
        signal.signal(sig,interrupted)
    try:
        return Preparation(a).run()
    except Exception as e:
        print('FAIL: ghost preparation: '+str(e),flush=True)
        return 9


if __name__ == '__main__':
    raise SystemExit(main())
