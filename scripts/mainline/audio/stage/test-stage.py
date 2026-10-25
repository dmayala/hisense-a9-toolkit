#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline tests of the attended audio stage scripts against a fake phone.

The fake /sys/firmware/devicetree is generated from the BUILT stage DTB (the
one inside the boot image), so node paths and enabled LEDs follow the real
image (review 51 R51-3/R51-4). The phone script runs under mksh (Android's
/system/bin/sh) when the host has one; its 'kernel' is the hook below. The host
wrapper runs against a fake adb that executes the phone script locally. No
device, adb or network is used.

  test-stage.py [--dtb <stage dtb>]"""
import argparse, glob, hashlib, os, pathlib, shutil, struct, subprocess, sys, tempfile, textwrap

HERE = pathlib.Path(__file__).resolve().parent
PHONE = HERE / 'a9-audio-stage.sh'
HOST = HERE / 'audio-stage.sh'
SH = shutil.which('mksh') or next(iter(sorted(glob.glob('/nix/store/*-mksh-*/bin/mksh'))), None) or 'sh'
DEF_DTB = pathlib.Path.home() / ('A9-build/mainline/sol-audio/stage-a/build/out-audio-ess-enabled/'
                                 'sm6115-hisense-hlte556n-android-audio-ess-enabled.dtb')
ap = argparse.ArgumentParser(); ap.add_argument('--dtb', type=pathlib.Path, default=DEF_DTB)
ap.add_argument('--shell', type=pathlib.Path, help='shell wrapper (e.g. qemu Android sh)')
args = ap.parse_args(); DTB = args.dtb
if args.shell: SH = str(args.shell.resolve())
if not DTB.is_file(): sys.exit(f'stage DTB {DTB} missing (build-stage-a.sh kernel step)')
ORDER = ['snd.ko', 'snd-timer.ko', 'snd-pcm.ko', 'snd-pcm-dmaengine.ko', 'snd-compress.ko',
         'snd-soc-core.ko', 'snd-soc-es9218-a9.ko']
KREL = '7.2.10-a9'

def fdt_nodes(blob):
    """Minimal FDT walker: yields (path, {prop: bytes})."""
    magic, _, off_struct, off_strings = struct.unpack_from('>4I', blob)
    assert magic == 0xd00dfeed
    i, stack, out = off_struct, [], []
    def name(o): return blob[off_strings + o:blob.index(b'\0', off_strings + o)].decode()
    while True:
        tok, = struct.unpack_from('>I', blob, i); i += 4
        if tok == 1:
            e = blob.index(b'\0', i); n = blob[i:e].decode(); i = (e + 4) & ~3
            stack.append(n); out.append(('/'.join(stack) or '/', {}))
        elif tok == 3:
            ln, no = struct.unpack_from('>2I', blob, i); i += 8
            out[[p for p, _ in out].index('/'.join(stack) or '/')][1][name(no)] = blob[i:i + ln]; i = (i + ln + 3) & ~3
        elif tok == 2: stack.pop()
        elif tok == 4: continue
        elif tok == 9: return out
NODES = fdt_nodes(DTB.read_bytes())

HOOK = r'''
import os, pathlib, sys, time
R = pathlib.Path(os.environ['A9_AUDIO_ROOT']); sc = os.environ.get('FAKE_SCENARIO', 'nominal')
ev, args = sys.argv[1], sys.argv[2:]
def log(s):
    with open(R/'dmesg.txt', 'a') as f: f.write('[ 1.0] ' + s + '\n')
def pins(a, b, c, d):
    (R/'sys/kernel/debug/gpio').write_text(
        ' gpio64  : in  low  func0 2mA pull down\n'
        f' gpio65  : out {a} func0 2mA no pull\n gpio66  : out {b} func0 2mA no pull\n'
        ' gpio67  : in  low  func0 2mA pull down\n'
        f' gpio68  : out {c} func0 2mA no pull\n gpio69  : out {d} func0 2mA no pull\n')
dev = R/'sys/bus/i2c/drivers/a9-es9218/1-0048'
if ev == 'kmsg':
    if sc != 'nodmesg': log(args[0])
elif ev == 'insmod':
    name = args[0][:-3].replace('-', '_')
    with open(R/'proc/modules', 'a') as f: f.write(f'{name} 16384 0 - Live 0x0\n')
    if name == 'snd_soc_es9218_a9' and sc != 'nobind':
        bp = 'Y' if 'bringup=1' in args else 'N'
        dev.mkdir(parents=True, exist_ok=True); (dev/'selftest').write_text('level=none\n')
        p = R/'sys/module/snd_soc_es9218_a9/parameters'; p.mkdir(parents=True, exist_ok=True); p.joinpath('bringup').write_text(bp + '\n')
        log(f'a9-es9218 1-0048: standby, no I2C at probe (bringup={int(bp == "Y")})')
        pins('low', 'low', 'high' if sc == 'pa_raised' else 'low', 'high')
        if sc == 'i2c_err': log('geni_i2c 4a84000.i2c: i2c txn timed out')
elif ev == 'rmmod':
    if sc == 'rmmod_stuck': sys.exit(0)
    m = R/'proc/modules'; m.write_text(''.join(l for l in m.read_text().splitlines(True) if not l.startswith(args[0] + ' ')))
    if dev.exists():
        for f in dev.iterdir(): f.unlink()
        dev.rmdir()
    pins('low', 'low', 'low', 'high')
elif ev == 'write' and args[0].endswith('/selftest'):
    level = args[1]
    cnt = R/'selftest-count'; n = int(cnt.read_text()) + 1 if cnt.exists() else 1; cnt.write_text(str(n))
    with open(R/'selftest-events', 'a') as f: f.write(level + '\n')
    if sc == 'store_hang': time.sleep(30)
    rid = 0x12 if sc == 'wrong_id' else (0xc0 + n % 4)
    ret = -121 if sc == 'ret_negative' else 0
    match = int((rid & 0xfc) == 0xc0)
    vals = [0,128,52,112,0,104,7,65,221,0,2,0,119,0,37,255,255,255,255,255,98,0,144,0,54,0,98,196,240,5,0,0,3,60] + [0]*13
    if sc == 'bad_dump': vals[7] = 64
    result = f'level={level} ret={ret} id=0x{rid:x} match={match} mode=0\n'
    if level == 'init' and ret == 0:
        result += 'regs=' + ' '.join(f'{i:02x}:{v:02x}' for i,v in enumerate(vals)) + '\n'
    dev.joinpath('selftest').write_text(result)
    log(f'a9-es9218 1-0048: selftest {level}: ret={ret} id=0x{rid:x}')
    if sc == 'kbad': log('a9-es9218 1-0048: selftest id: ret=-5 id=0xc0')
    if sc == 'pins_high': pins('low', 'high', 'high', 'high')
    if sc == 'ret_negative': sys.exit(1)
elif ev == 'write' and 'brightness'  in args[0] and sc == 'fl_stuck':
    pathlib.Path(args[0]).write_text('7\n')
'''

def fake_root(tmp, scenario, adsp=None):
    R = tmp / 'root'
    base = R / 'sys/firmware/devicetree/base'
    for path, props in NODES:
        d = base / path.lstrip('/'); d.mkdir(parents=True, exist_ok=True)
        for k, v in props.items(): (d / k).write_bytes(v)
    if adsp:
        (next(base.rglob('remoteproc@a400000')) / 'status').write_bytes(adsp.encode() + b'\0')
    for d in ('sys/bus/i2c/drivers/a9-es9218', 'sys/kernel/debug', 'proc/sys/kernel/random', 'dev', 'bin',
              'sys/class/remoteproc', 'sys/class/leds', 'data'):
        (R / d).mkdir(parents=True, exist_ok=True)
    # LED class devices exactly for the ENABLED labelled nodes on i2c0/i2c1
    leds = []
    for path, props in NODES:
        if ('/i2c@4a84000/' in path or '/i2c@4a80000/' in path) and 'label' in props \
                and props.get('status', b'okay\0') != b'disabled\0':
            l = props['label'].rstrip(b'\0').decode(); leds.append(l)
            (R/'sys/class/leds'/l).mkdir(); (R/'sys/class/leds'/l/'brightness').write_text('40\n')
            (R/'sys/class/leds'/l/'max_brightness').write_text('255\n')
    (R/'leds.txt').write_text('\n'.join(leds))
    (R/'proc/sys/kernel/random/boot_id').write_text('11111111-boot\n')
    (R/'proc/modules').write_text('a9_eink 16384 0 - Live 0x0\n')
    (R/'dmesg.txt').write_text('[ 0.0] boot\n')
    (R/'sys/kernel/debug/gpio').write_text(' gpio65  : out low func0 2mA no pull\n gpio66  : out high func0 2mA no pull\n'
                                          ' gpio68  : out high func0 2mA no pull\n gpio69  : out high func0 2mA no pull\n')
    (R/'hook.py').write_text(HOOK)
    hook = R/'bin/hook'; hook.write_text(f'#!/bin/sh\nexec {sys.executable} -I {R}/hook.py "$@"\n'); hook.chmod(0o755)
    fakes = {
        'getprop': 'echo 1', 'id': 'echo 0', 'uname': f'echo {KREL}', 'mount': 'exit 0',
        'dmesg': f'cat {R}/dmesg.txt',
        'insmod': '[ "$FAKE_SCENARIO" = insmod_fail ] && exit 1; [ "$FAKE_SCENARIO" = insmod_hang ] && exec /bin/sleep 30; exit 0',
        'rmmod': '[ "$FAKE_SCENARIO" = rmmod_stuck ] && exec /bin/sleep 30; exit 0',
    }
    for k, v in fakes.items():
        p = R/'bin'/k; p.write_text('#!/bin/sh\n' + v.replace('/bin/sleep', shutil.which('sleep')) + '\n'); p.chmod(0o755)
    md = R/'data/a9audio'; md.mkdir()
    for k in ORDER: (md/k).write_bytes(hashlib.sha256(k.encode()).digest())
    (md/'modules.order').write_text('\n'.join(ORDER) + '\n')
    (md/'kernel.release').write_text(KREL + '\n')
    (md/'kernel-image.sha256').write_text('ab' * 32 + '\n')
    (md/'SHA256SUMS').write_text(''.join(f'{hashlib.sha256((md/k).read_bytes()).hexdigest()}  {k}\n'
                                         for k in ORDER + ['modules.order', 'kernel.release', 'kernel-image.sha256']))
    return R, md

def env_for(R, scenario, **extra):
    e = dict(os.environ, A9_AUDIO_ROOT=str(R), A9_AUDIO_TEST_HOOK=str(R/'bin/hook'), FAKE_SCENARIO=scenario,
             A9_AUDIO_OBSERVE='3', TMPDIR=str(R), PATH=f'{R}/bin:' + os.environ['PATH'])
    e.update(extra); return e

def phone(R, md, stage, scenario='nominal', **extra):
    # qemu forked children can retain its original stdio descriptors even after
    # the guest redirects them. A regular file avoids waiting for orphan EOF.
    log = R/'phone-output'
    with log.open('w') as f:
        r = subprocess.run([SH, str(PHONE), stage, str(md)], env=env_for(R, scenario, **extra),
                           stdout=f, stderr=subprocess.STDOUT, timeout=120, start_new_session=True)
    return r.returncode, log.read_text()

passed = 0
def case(name, cond, out=''):
    global passed
    if not cond:
        print('FAIL', name); print(out); sys.exit(1)
    passed += 1
def mods(R): return (R/'proc/modules').read_text()

with tempfile.TemporaryDirectory(prefix='a9-audio-stage-') as t:
    t = pathlib.Path(t); k = [0]
    def fresh(sc='nominal', adsp=None):
        k[0] += 1; d = t / f'{sc}-{k[0]}'; d.mkdir(); return fake_root(d, sc, adsp)
    R, md = fresh()
    leds = (R/'leds.txt').read_text().split()
    case('built DT: warm center (AW99703 @36) enabled, warm edge (KTD @35) not', 'a9:warm:center' in leds and 'a9:warm:edge' not in leds, leds)
    case('built DT: codec node lives under geniqup', any(p.endswith('geniqup@4ac0000/i2c@4a84000/audio-codec@48') for p, _ in NODES))
    rc, out = phone(R, md, 'pre')
    case('pre passes on the real DT layout and writes nothing', rc == 0 and 'AUDIO-STAGE-pre PASS' in out
         and 'A9AUDIO' not in (R/'dmesg.txt').read_text(), out)
    R, md = fresh(adsp='okay'); rc, out = phone(R, md, 'A')
    case('A refuses an ADSP-enabled DT', rc == 2 and 'ADSP remoteproc is' in out and 'insmod' not in out, out)
    R, md = fresh(); (md/'snd.ko').write_bytes(b'tampered'); rc, out = phone(R, md, 'A')
    case('A refuses a tampered bundle', rc == 2 and 'hash mismatch' in out, out)
    R, md = fresh(); rc, out = phone(R, md, 'A')
    case('A nominal PASS (probe/Standby only)', rc == 0 and 'AUDIO-STAGE-A PASS probe/Standby only' in out, out)
    case('A loads the codec last with bringup=0', 'insmod snd-soc-es9218-a9.ko bringup=0' in out
         and out.index('insmod snd-soc-core.ko') < out.index('insmod snd-soc-es9218-a9.ko'), out)
    case('A never touches the selftest attribute', (R/'sys/bus/i2c/drivers/a9-es9218/1-0048/selftest').read_text() == 'level=none\n', out)
    case('A checks frontlight before and after', 'i2c1 0x36 before' in out and 'i2c1 0x36 after' in out, out)
    rc, out = phone(R, md, 'A'); case('A once per boot', rc == 2 and 'already passed this boot' in out, out)
    rc, out = phone(R, md, 'C'); case('C is NOGO on this image', rc == 3 and 'needs the ADSP' in out, out)
    rc, out = phone(R, md, 'cleanup')
    case('cleanup removes and verifies', rc == 0 and 'snd_soc_es9218_a9' not in mods(R) and 'PASS codec removed' in out, out)
    (R/'proc/sys/kernel/random/boot_id').write_text('22222222-boot\n')
    rc, out = phone(R, md, 'A'); case('A allowed again after a reboot (new boot_id)', rc == 0, out)
    for sc, want in [('pa_raised', 'GPIO not in Standby'), ('i2c_err', 'kernel log: '), ('fl_stuck', 'frontlight readback'),
                     ('nobind', 'did not bind'), ('insmod_fail', 'insmod snd.ko failed'),
                     ('insmod_hang', 'insmod snd.ko failed or blocked'), ('nodmesg', 'marker not readable')]:
        R, md = fresh(sc); rc, out = phone(R, md, 'A', sc)
        case(f'A {sc} FAILs', rc == 1 and want in out and 'AUDIO-STAGE-A FAIL' in out, out)
        case(f'A {sc} contained', 'snd_soc_es9218_a9' not in mods(R), out)
        rc, out = phone(R, md, 'A'); case(f'A {sc}: failure latched, no retry this boot', rc == 2 and 'failure is latched' in out, out)
    R, md = fresh(); rc, out = phone(R, md, 'A', A9_AUDIO_DEADLINE='2', A9_AUDIO_OBSERVE='30')
    case('phone deadline ends the stage and contains', rc == 1 and 'deadline' in out and 'snd_soc_es9218_a9' not in mods(R), out)
    R, md = fresh('rmmod_stuck'); rc, out = phone(R, md, 'A', 'pa_raised')
    case('containment failure is reported, not hidden', rc == 1, out)
    R, md = fresh(); rc, out = phone(R, md, 'A'); case('A ok before stuck-rmmod case', rc == 0, out)
    rc, out = phone(R, md, 'cleanup', 'rmmod_stuck')
    case('cleanup with a blocked rmmod -> CONTAINMENT-UNVERIFIED + Power fallback', rc == 6 and 'hold Power 10-15 s' in out, out)
    rc, out = phone(R, md, 'A'); case('after unverified containment nothing runs', rc == 2 and 'latched' in out, out)

    # Powered stages: P1 always means 3 ID stores, P2 exactly one INIT store.
    for st in ('P1', 'P2'):
        R, md = fresh()
        if st == 'P2':
            rc, out = phone(R, md, 'P1'); case('P2 prerequisite P1 passes', rc == 0, out)
        else:
            rc, out = phone(R, md, 'A'); case('A before P1 reload passes', rc == 0, out)
        rc, out = phone(R, md, st)
        case(f'{st} nominal PASS', rc == 0 and f'AUDIO-STAGE-{st} PASS' in out, out)
        case(f'{st} bringup=1 reload checked', 'insmod snd-soc-es9218-a9.ko bringup=1' in out, out)
        case(f'{st} contains even on PASS', 'snd_soc_es9218_a9' not in mods(R) and '65 out low, 66 out low, 68 out low' in out, out)
        case(f'{st} frontlight before and after', 'i2c1 0x36 before' in out and 'i2c1 0x36 after' in out, out)
        events = (R/'selftest-events').read_text().splitlines()
        case(f'{st} exact selftest count', events == (['id']*3 if st == 'P1' else ['id']*3 + ['init']), events)
        if st == 'P1': case('P1 raw ID status bits may vary, masked ID stable', 'raw: 0xc1 0xc2 0xc3' in out, out)
        rc, out = phone(R, md, st); case(f'{st} refuses second run same boot', rc == 2 and 'already attempted this boot' in out, out)
        for sc, want in [('wrong_id', 'bad selftest status'), ('ret_negative', 'selftest store failed'),
                         ('store_hang', 'timed out (124)'), ('pins_high', 'GPIO not in Standby'),
                         ('kbad', 'kernel log:')]+([('bad_dump', 'dump differs')] if st == 'P2' else []):
            R, md = fresh()
            if st == 'P2':
                rc, out = phone(R, md, 'P1'); case(f'{st}/{sc} prerequisite P1', rc == 0, out)
            rc, out = phone(R, md, st, sc)
            case(f'{st} {sc} FAIL', rc == 1 and want in out, out)
            case(f'{st} {sc} contained', 'snd_soc_es9218_a9' not in mods(R), out)
            for blocked in ('P1', 'P2'):
                rc, out = phone(R, md, blocked)
                case(f'{st} {sc} latches failure for {blocked}', rc == 2 and 'failure is latched' in out, out)
        R, md = fresh()
        if st == 'P2':
            rc, out = phone(R, md, 'P1'); case('P2 stuck remove prerequisite', rc == 0, out)
        # rmmod returns without removing the codec: PASS must be impossible.
        rc, out = phone(R, md, st, 'rmmod_stuck')
        case(f'{st} PASS requires verified rmmod', rc == 6 and 'CONTAINMENT-UNVERIFIED' in out, out)
    R, md = fresh(); rc, out = phone(R, md, 'P2')
    case('P2 without P1 PASS refuses before loading', rc == 2 and 'requires P1 PASS' in out and 'insmod' not in out, out)
    rc, out = phone(R, md, 'P1'); case('P1 before boot change PASS', rc == 0, out)
    (R/'proc/sys/kernel/random/boot_id').write_text('another-boot\n')
    rc, out = phone(R, md, 'P2'); case('P2 prior boot P1 PASS insufficient', rc == 2 and 'requires P1 PASS' in out, out)
    rc, out = phone(R, md, 'P1'); case('P1 can run in new boot', rc == 0, out)
    # host wrapper against a fake adb that runs the phone script locally
    R, md = fresh()
    bundle = t / 'bundle'; shutil.copytree(md, bundle)
    pdata = R / 'data/local/tmp/a9audio'
    fake_adb = t / 'adb'
    fake_adb.write_text(textwrap.dedent(f'''\
        #!{sys.executable} -I
        import os, subprocess, sys, hashlib, pathlib
        R = pathlib.Path({str(R)!r}); a = sys.argv[1:]
        m = lambda p: R / p.lstrip('/')
        if a[0] == 'devices': print('List of devices attached\\nFAKE01\\tdevice'); sys.exit(0)
        if a[0] == 'push': m(a[2]).parent.mkdir(parents=True, exist_ok=True); m(a[2]).write_bytes(open(a[1], 'rb').read()); sys.exit(0)
        c = a[1]
        if c == 'getprop ro.a9ml.vendor': print(os.environ.get('FAKE_VENDOR', '1'))
        elif c == 'cat /vendor/lib/modules/kernel-image.sha256': print(os.environ.get('FAKE_KIMG', 'ab' * 32))
        elif c == 'id -u': print(0)
        elif c.startswith('mkdir -p '): m(c[9:]).mkdir(parents=True, exist_ok=True)
        elif c.startswith('sha256sum '): p = m(c[10:]); print(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + c[10:])
        elif c.startswith('sh /data/local/tmp/a9audio/a9-audio-stage.sh '):
            st = c.split()[2]
            if st != 'cleanup' and os.environ.get('FAKE_TRANSPORT') == 'lost': sys.exit(255)
            env = dict(os.environ, A9_AUDIO_ROOT=str(R), A9_AUDIO_TEST_HOOK=str(R / 'bin/hook'), A9_AUDIO_OBSERVE='3',
                       PATH=str(R / 'bin') + ':' + os.environ['PATH'])
            log = R/'adb-phone-output'
            with log.open('w') as f:
                rc = subprocess.run([{SH!r}, str(m('/data/local/tmp/a9audio/a9-audio-stage.sh')), st, str(m('/data/local/tmp/a9audio'))], env=env, stdout=f, stderr=subprocess.STDOUT, start_new_session=True).returncode
            print(log.read_text(), end=''); sys.exit(rc)
        else: print('fake adb: unhandled', c); sys.exit(9)
        '''))
    fake_adb.chmod(0o755)
    def host(stage, **env):
        e = dict(os.environ, ADB=str(fake_adb), FAKE_SCENARIO='nominal', **env)
        r = subprocess.run(['bash', str(HOST), stage, '--bundle', str(bundle), '--run-dir', str(t / f'run-{passed}')],
                           env=e, capture_output=True, text=True, timeout=200)
        return r.returncode, r.stdout + r.stderr
    rc, out = host('A', FAKE_VENDOR='0'); case('host refuses a non-mainline vendor', rc == 4 and 'not on the mainline vendor' in out, out)
    rc, out = host('A', FAKE_KIMG='cd' * 32); case('host refuses another image pair', rc == 4 and 'not the audio stage image pair' in out, out)
    rc, out = host('A'); case('host A PASS end to end', rc == 0 and 'verdict: AUDIO-STAGE-A PASS' in out, out)
    case('host pushed the bundle', (pdata / 'SHA256SUMS').exists(), out)
    # Remove its bundle files to prove P1 pushes them independently of pre/A.
    for f in ORDER + ['modules.order','SHA256SUMS','kernel.release','kernel-image.sha256']: (pdata/f).unlink()
    rc, out = host('P1'); case('host P1 PASS end to end, bundle repushed', rc == 0 and 'verdict: AUDIO-STAGE-P1 PASS' in out and (pdata/'SHA256SUMS').exists(), out)
    for f in ORDER + ['modules.order','SHA256SUMS','kernel.release','kernel-image.sha256']: (pdata/f).unlink()
    rc, out = host('P2'); case('host P2 PASS end to end, bundle repushed', rc == 0 and 'verdict: AUDIO-STAGE-P2 PASS' in out and (pdata/'SHA256SUMS').exists(), out)
    rc, out = host('cleanup'); case('host cleanup PASS', rc == 0, out)
    (R/'proc/sys/kernel/random/boot_id').write_text('33333333-boot\n')
    rc, out = host('A', FAKE_TRANSPORT='lost')
    case('host: transport loss -> no verdict, automatic verified cleanup', rc == 5 and 'containment verified' in out, out)
    (bundle/'snd.ko').write_bytes(b'x'); rc, out = host('A'); case('host refuses a tampered bundle', rc == 4, out)

print(f'PASS {passed} stage-script checks (shell: {SH}; DT from {DTB.name})')
