#!/usr/bin/env python3
"""E Ink + Wi-Fi POC on slot B: integrated R4 images. The wrapper only flashes and boots
(gates closed); then it is stopped, keeper.sh feeds the dead-man, dev-open.sh opens the
E Ink gates (bins 0-13, pg_settle 100, no GPU guard) and the wifi-poc scripts connect.
usage: run.py --offline | --check | --write CODE   (output dir: ./run-<UTC>)"""
import importlib.util, os, re, subprocess as sp, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
R4 = Path('/home/user/A9-build/mainline/integrated-latency-v3-runtime-probe-r4-20261019')
FW = Path('/home/user/A9-build/mainline/firmware/hisense')
POC = Path('/home/user/A9-build/mainline/wifi-poc')
HOLD = os.environ.get('POC_HOLD', '5400')

spec = importlib.util.spec_from_file_location('r4probe', R4/'probe.py')
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)

def main():
    mode = sys.argv[1]
    out = HERE/('run-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + mode.lstrip('-')[0])
    if os.environ.get('POC_R4'): P.HERE = Path(os.environ['POC_R4'])
    pr = P.Probe(out)
    if os.environ.get('POC_R4'): pr.env['A9_WIFI_SNAPSHOT'] = str(P.HERE/'snapshot')
    if os.environ.get('POC_BOOT'):
        pr.boot = Path(os.environ['POC_BOOT']); pr.args[pr.args.index('--boot')+1] = str(pr.boot)
    if os.environ.get('POC_VENDOR'):
        pr.vendor = Path(os.environ['POC_VENDOR'])
        if '--vendor' in pr.args: pr.args[pr.args.index('--vendor')+1] = str(pr.vendor)
    pr.prepare()                      # dirs, symlinks, offline wrapper check
    if mode == '--offline':
        print('OFFLINE PASS', out); return 0
    args = ['--wifi', '--eink',
            '--waveform', str(FW/'a9-waveform.bin'), '--template', str(FW/'a9-ctrl-template.bin'),
            '--hold', HOLD, '--watch', '900',
            '--boot', str(pr.boot), '--vendor', str(pr.vendor), '--no-restore']
    cmd = pr.bootcmd + args
    if mode == '--check':
        r = sp.run(cmd, cwd=pr.wt, env=pr.env, stdout=sp.PIPE, stderr=sp.STDOUT, text=True)
        (out/'check.log').write_text(r.stdout)
        print('\n'.join(r.stdout.splitlines()[-4:])); return r.returncode
    code = sys.argv[2]
    log = (out/'boot.log').open('w')
    bp = sp.Popen(cmd + ['--write', '--code', code], cwd=pr.wt, env=pr.env,
                  stdin=sp.DEVNULL, stdout=log, stderr=sp.STDOUT, start_new_session=True)
    adb = pr.adb
    def sh(c, inp=None, t=60):
        return sp.run(adb + ['shell', c], input=inp, stdout=sp.PIPE, stderr=sp.STDOUT, text=True, timeout=t).stdout
    def root(script, t=90):
        return sh('timeout %d nc 127.0.0.1 2324' % t, script, t + 10)
    def flat(s, n=600):
        return s.strip().replace('\n', ' | ')[-n:]
    def say(m):
        m = re.sub(r'([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', '<mac>', re.sub(r'\b\d{1,3}(\.\d{1,3}){3}\b', '<ip>', m))
        line = time.strftime('%H:%M:%S ') + m; print(line, flush=True)
        with (out/'poc.log').open('a') as f: f.write(line + '\n')
    say('boot wrapper pid %d' % bp.pid)
    while '-- HOLD' not in (out/'boot.log').read_text(errors='replace'):
        if bp.poll() is not None: say('wrapper exited rc=%s before hold' % bp.returncode); return 1
        time.sleep(5)
    # Take over: stop the wrapper (no fault-driven reboot); keeper.sh feeds the dead-man.
    os.killpg(bp.pid, 15); time.sleep(3)
    keeper = sp.Popen(['bash', str(HERE/'keeper.sh'), adb[0], adb[2]], start_new_session=True,
                      stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    (out/'keeper.pid').write_text(str(keeper.pid))
    say('wrapper stopped; keeper pid %d' % keeper.pid)
    say(flat(root(P.S.PREP_SETUP + '\necho SETUP_OK\n'), 80))
    say('eink open: ' + flat(root((HERE/'dev-open.sh').read_text())))
    say('eink direct: ' + flat(root((HERE/'dev-gates-direct.sh').read_text())))
    if os.environ.get('POC_KEEP_ADSP') != '1': say('adsp stop: ' + flat(root('setprop ctl.stop vendor.sensors-a9; setprop ctl.stop vendor.a9-sensors; sleep 2\nfor p in /sys/class/remoteproc/remoteproc*; do [ "$(cat $p/name)" = adsp ] && echo stop > $p/state; done; sleep 2\nfor p in /sys/class/remoteproc/remoteproc*; do echo "$(cat $p/name)=$(cat $p/state)"; done\n')))
    say('Wi-Fi start: ' + flat(sp.run([str(POC/'start.sh')], stdout=sp.PIPE, stderr=sp.STDOUT, text=True).stdout))
    say('wifi kmsg: ' + flat(root('dmesg | grep a9wifi-session | tail -n 25 | cut -c1-200\n'), 3000))
    sh('cmd wifi start-scan'); time.sleep(8)
    say(sp.run([sys.executable, '-I', str(POC/'connect.py')], stdout=sp.PIPE, stderr=sp.STDOUT, text=True).stdout.strip())
    time.sleep(20)
    say(flat(sp.run([str(POC/'status.sh')], stdout=sp.PIPE, stderr=sp.STDOUT, text=True).stdout))
    say('READY')
    return 0

if __name__ == '__main__':
    sys.exit(main())
