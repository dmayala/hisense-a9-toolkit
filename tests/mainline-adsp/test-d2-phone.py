#!/usr/bin/env python3
"""Execute the production target script using Android mksh/toybox in qemu-user.

Only hardware/transport effects are simulated: sysfs state transitions, insmod,
kernel log streaming, uid, uname and ps. All parsing, globbing, hashing, ledger,
gate/identity checks, traps, start/stop and monitoring run in the real script.
"""
import argparse
import hashlib
import gzip
import os
import signal
import time
from pathlib import Path
import shutil
import subprocess as sp
import tempfile

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/mainline/adsp/d2-target.sh'
ap=argparse.ArgumentParser(); ap.add_argument('--sysroot',required=True,type=Path)
ap.add_argument('--qemu',required=True,type=Path); ap.add_argument('--case',action='append'); ap.add_argument('--shard',type=int,default=0); ap.add_argument('--shards',type=int,default=1); a=ap.parse_args()
assert 0 <= a.shard < a.shards
assert (a.sysroot/'system/bin/sh').is_file() and (a.sysroot/'system/bin/toybox').is_file()
PASSING=('owned-fd-observed','snd-vanish','pass','preloaded-provider','fallback-absent-nohelper','benign-template','benign-asoc','card-stays')
WHY={'prior-sound':'D2-FAIL prior-sound','prior-apr':'D2-FAIL prior-apr','no-apr':'D2-FAIL apr-timeout',
     'apr-wrong-channel':'D2-FAIL apr-timeout','apr-chan-unbound':'D2-FAIL apr-timeout','apr-extra-service':'D2-FAIL apr-unexpected-service',
     'apr-missing-service':'D2-FAIL apr-timeout','apr-unbound':'D2-FAIL apr-timeout','no-card':'D2-FAIL card-timeout',
     'card-wrong-id':'D2-FAIL card-id','pcm-extra':'D2-FAIL pcm-count','pcm-wrong':'D2-FAIL pcm-identity','extra-card':'D2-FAIL extra-card',
     'sound-node-extra':'D2-FAIL unexpected-sound-node','stream-open':'D2-FAIL stream-open','forbidden-module':'D2-FAIL unexpected-module',
     'soundwire-device':'D2-FAIL soundwire-identity','lpi-device':'D2-FAIL lpi-driver','apr-lost':'D2-FAIL apr-lost','card-lost':'D2-FAIL card-lost',
     'smmu-fault':'D2-FAIL kernel-fault','pdr-fault':'D2-FAIL kernel-fault','apr-fault-msg':'D2-FAIL kernel-fault','asoc-fail':'D2-FAIL kernel-fault',
     'q6-timeout':'D2-FAIL kernel-fault','audio-pd-down':'D2-FAIL kernel-fault','snd-opened':'D2-FAIL snd-device-open','snd-hidden':'D2-FAIL snd-scan-unreadable',**{c:'D2-FAIL kernel-fault' for c in ('glink-timeout','glink-channel-id','glink-nochannel','glink-intent-id','glink-timed-out-other')},'no-proc-scan':'D2-FAIL snd-scan', 'tone-fail':'D2-FAIL tone','tone-bad-terminal':'D2-FAIL tone','tone-hang':'D2-FAIL tone','tone-thermal':'D2-FAIL thermal-limit','tone-fault':'D2-FAIL codec-fault','tone-unknown':'D2-FAIL codec-state','tone-foreign':'D2-FAIL foreign-snd-owner','tone-wrong-exe':'D2-FAIL helper-executable','codec-not-ready':'D2-FAIL codec-not-safe','codec-fault':'D2-FAIL codec-not-safe','codec-unknown':'D2-FAIL codec-not-safe','missing-codec-attrs':'D2-FAIL codec-not-safe','kernel-stop-fail':'D2-FAIL codec-stop','reset-lost':'D2-FAIL codec-reset-lost','snd-grep-error':'D2-FAIL snd-scan','platform-driver':'D2-FAIL audio-platform-driver','platform-owner':'D2-FAIL audio-platform-owner','snd-pipe-grep-error':'D2-FAIL snd-scan'}
assert shutil.disk_usage('/').free >= 15_000_000_000, 'retain >=15 GB free on /'
THERMAL_REGRESSIONS = ('thermal-temp-missing', 'thermal-zone-missing', 'thermal-temp-unreadable',
    'thermal-baseline-missing', 'thermal-baseline-unreadable', 'thermal-current-missing', 'thermal-current-unreadable')
MATCHER_REGRESSIONS = ('matcher-first-grep-error', 'matcher-total-count-error', 'matcher-kept-count-error', 'matcher-benign-count-error')
for name in THERMAL_REGRESSIONS:
    WHY[name] = 'D2-FAIL '+('thermal-read' if name in ('thermal-temp-missing', 'thermal-temp-unreadable') else 'thermal-identity')
for name in MATCHER_REGRESSIONS:
    WHY[name] = 'D2-FAIL '+('matcher-grep' if name == 'matcher-first-grep-error' else 'matcher-count')
with tempfile.TemporaryDirectory(prefix='adsp-phone-') as td:
    root=Path(td); sr=root/'sr'; shutil.copytree(a.sysroot,sr,symlinks=True)
    script=root/'d2-target.sh'; shutil.copy2(SCRIPT,script)
    x=root/'xbin'; x.mkdir()
    q=str(a.qemu); shell=str(sr/'system/bin/sh'); toy=str(sr/'system/bin/toybox'); linker=str(sr/'system/bin/linker64')
    bash=shutil.which('bash')
    # Pause real script commands at the vulnerable intervals. The hook itself
    # ignores group signals, allowing mksh's terminal handler to run on return.
    hook=x/'hook'
    hook.write_text("#!"+shutil.which('python3')+"\n"+r'''import os,signal,sys,time
from pathlib import Path
for sig in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM): signal.signal(sig,signal.SIG_IGN)
r=Path(os.environ['FIX']); phase=os.environ.get('PHASE','')
point=sys.argv[1]
if phase==point and not (r/'phase').exists():
    (r/'phase').write_text(point)
    while not (r/'release').exists(): time.sleep(.02)
''')
    hook.chmod(0o755)
    # D2 fixture: the audio stack appears ~0.8 s after a start (detached, like the
    # asynchronous APR/PDR/ASoC bring-up) and unregisters on stop. Cases perturb it.
    (x/'audiofix').write_text("#!"+shutil.which('python3')+"\n"+r'''import os,shutil,sys,time
from pathlib import Path
r=Path(os.environ['FIX']); case=os.environ['CASE']; mode=sys.argv[1]
def sysdir(p): d=r/p; d.mkdir(parents=True,exist_ok=True); return d
def rmtree(p):
    p=r/p
    if p.exists(): shutil.rmtree(p)
def remove_apr():
    for p in (r/'sys/bus/aprbus/devices').glob('aprsvc*'): p.unlink()
def remove_card():
    rmtree('proc/asound')
    for p in (r/'sys/class/sound').glob('*'): shutil.rmtree(p)
if mode=='down':
    if case!='card-stays': remove_card()
    remove_apr()
    p=r/'sys/bus/rpmsg/devices/apr-svc'
    if p.is_symlink(): p.unlink()
    sys.exit(0)
time.sleep(0.8)
edge=r/'sys/devices/platform/soc@0/a400000.remoteproc/glink-edge'
def build_apr():
    ch=edge/'rpmsg-apr'; ch.mkdir(parents=True,exist_ok=True)
    (ch/'name').write_text('apr_audio_svc\n' if case!='apr-wrong-channel' else 'other\n')
    drv=sysdir('sys/bus/rpmsg/drivers/qcom,apr')
    if case!='apr-chan-unbound': (ch/'driver').symlink_to(drv)
    bus=sysdir('sys/bus/rpmsg/devices'); (bus/'apr-svc').symlink_to(ch)
    names={3:'qcom-q6core',4:'qcom-q6afe',7:'qcom-q6asm',8:'qcom-q6adm'}
    if case=='apr-extra-service': names[9]='qcom-q6xxx'
    if case=='apr-missing-service': names.pop(8)
    sysdir('sys/bus/aprbus/devices')
    for svc,drvname in names.items():
        d=ch/f'aprsvc:service:4:{svc}'; d.mkdir(exist_ok=True)
        dd=sysdir('sys/bus/aprbus/drivers/'+drvname)
        if not (case=='apr-unbound' and svc==4): (d/'driver').symlink_to(dd)
        (r/'sys/bus/aprbus/devices'/d.name).symlink_to(d)
def build_card():
    cid={'card-wrong-id':'Other'}.get(case,'A9D2Earpiece')
    a=sysdir('proc/asound/card0'); (a/'id').write_text(cid+'\n')
    pcm=['00-00: MultiMedia1 (*) :  : playback 1']
    if case=='pcm-extra': pcm.append('00-02: MultiMedia3 (*) :  : playback 1 : capture 1')
    if case=='pcm-wrong': pcm[0]='00-00: Other (*) :  : playback 1'
    (r/'proc/asound/pcm').write_text('\n'.join(pcm)+'\n')
    (r/'proc/asound/cards').write_text(' 0 [A9D2Earpiece   ]: sm4250 - A9D2Earpiece\n')
    for n in ('pcm0p',):
        d=sysdir('proc/asound/card0/'+n+'/sub0'); (d/'status').write_text('running\n' if case=='stream-open' and n=='pcm0p' else 'closed\n')
    if case=='extra-card': sysdir('proc/asound/card1')
    names=['card0','controlC0','timer','pcmC0D0p']
    if case=='sound-node-extra': names.append('pcmC0D2p')
    for n in names: sysdir('sys/class/sound/'+n)
if case!='no-apr': build_apr()
if case not in ('no-card','no-apr'): build_card()
# Canonical fixture slave IDs are synthetic, never a phone identifier.
for sid in ('sdw:1:0:0217:010a:00:3','sdw:7:0:0217:010a:00:4'):
    d=sysdir('sys/bus/soundwire/devices/'+sid)
    (d/'driver').symlink_to(sysdir('sys/bus/soundwire/drivers/wcd9370-codec'))
    node='soundwire@a740000/codec@0,3' if sid.endswith(':3') else 'soundwire@a610000/codec@0,4'
    (d/'of_node').symlink_to(sysdir('sys/firmware/devicetree/base/soc@0/'+node))
for dev,drv,node in (
    ('a610000.soundwire','qcom-soundwire','soundwire@a610000'),
    ('a740000.soundwire','qcom-soundwire','soundwire@a740000'),
    ('a600000.codec','rx_macro','codec@a600000'),('a730000.codec','va_macro','codec@a730000'),
    ('a6a9000.clock-controller','lpasscc-sm6115','clock-controller@a6a9000'),
    ('a7ec000.clock-controller','lpasscc-sm6115','clock-controller@a7ec000')):
    d=sysdir('sys/bus/platform/devices/'+dev)
    (d/'driver').symlink_to(sysdir('sys/bus/platform/drivers/'+('wrong-driver' if case=='platform-driver' and dev=='a600000.codec' else drv)))
    (d/'of_node').symlink_to(sysdir('sys/firmware/devicetree/base/soc@0/'+('other-node' if case=='platform-owner' and dev=='a610000.soundwire' else node)))
d=sysdir('sys/bus/platform/devices/a7c0000.pinctrl')
(d/'driver').symlink_to(sysdir('sys/bus/platform/drivers/'+('wrong-lpi' if case=='lpi-device' else 'qcom-sm6115-lpass-lpi-pinctrl')))
k=sysdir('sys/bus/platform/devices/a9-d2-codec')
(k/'of_node').symlink_to(sysdir('sys/firmware/devicetree/base/a9-d2-codec'))
(k/'driver').symlink_to(sysdir('sys/bus/platform/drivers/wcd937x_codec'))
for n,v in {'d2_fault':'1' if case=='codec-fault' else '0','d2_ready':'0' if case=='codec-not-ready' else '1','d2_state':'UNKNOWN' if case=='codec-unknown' else 'SAFE','d2_stop':'0'}.items():
    if case!='missing-codec-attrs': (k/n).write_text(v)

if case=='forbidden-module':
    with (r/'proc/modules').open('a') as f: f.write('snd_soc_wcd938x 1 0 - Live 0x0\n')
if case in ('snd-opened','snd-hidden'): (r/'proc/1/fd/5').symlink_to('/dev/snd/pcmC0D0p')
if case=='soundwire-device': sysdir('sys/bus/soundwire/devices/sdw:0:0217:010d:00:4')

msgs={'smmu-fault':'3,900,1,-;arm-smmu c600000.iommu: Unhandled context fault: fsr=0x402, iova=0x1000, fsynr=0x1',
      'pdr-fault':'3,901,1,-;pdr_handle_alloc failed: -22',
      'apr-fault-msg':'3,902,1,-;qcom,apr 4000000.apr: Failed to add apr 8 svc',
      'asoc-fail':'3,903,1,-;ASoC: failed to instantiate card -22',
      'q6-timeout':'3,904,1,-;q6afe aprsvc:service:4:4: command timeout',
      'audio-pd-down':'3,905,1,-;apr: audio_pd service down',
      'glink-timeout':'3,907,1,-;glink-edge: intent request timed out',
      'glink-channel-id':'3,908,1,-;glink-edge: invalid channel id received',
      'glink-nochannel':'3,909,1,-;glink-edge: unable to find channel',
      'glink-intent-id':'3,910,1,-;glink-edge: invalid intent id received',
      'glink-timed-out-other':'3,911,1,-;qcom_glink: rx command timed out',
      'benign-asoc':'6,906,1,-;ASoC: Parent card not yet available, widget card binding deferred'}
if case in msgs:
    with (r/'dev/kmsg').open('a') as f: f.write(msgs[case]+'\n')
def after_ready():
    # Deterministic under load: act only once the target has printed that the card is up.
    end=time.time()+120
    while time.time()<end and not (r/'tone-started').exists(): time.sleep(0.05)
    time.sleep(0.1)
if case=='apr-lost': after_ready(); remove_apr()
if case=='card-lost': after_ready(); remove_card()
''')
    (x/'audiofix').chmod(0o755)
    for app in ('cat','tr','grep','readlink','wc','sed','cut','sort','cmp','mkdir','sync','sha256sum','timeout','sleep','printf','zcat','head','ls','tail','setsid','mv','cp'):
        p=x/app
        pre=''
        if app=='readlink':
            pre='if [ "$CASE" = owned-fd-observed ] && [[ "${!#}" = */proc/*/exe ]]; then\n'+q+' -L / '+linker+' '+toy+' readlink "$@"\nrc=$?\n[ "$rc" != 0 ] || '+shutil.which('touch')+' "$FIX/owned-observed"\nexit "$rc"\nfi\n'
        if app=='ls': pre='case "$*" in */proc/1/fd*) case "$CASE" in snd-hidden) [ -L "$FIX/proc/1/fd/5" ] && exit 1;; snd-vanish) '+shutil.which('rm')+' -rf "$FIX/proc/1"; exit 1;; esac;; esac\n'
        if app=='grep': pre='if [ "$CASE" = snd-grep-error ] && [ "${!#}" = snd.one ]; then exit 2; fi\nif [ "$CASE" = snd-pipe-grep-error ] && [ "$1" = /dev/snd/ ] && [ "${!#}" = snd.one ]; then exit 2; fi\n'
        if app=='grep': pre+='''if [ -f "$FIX/tone-started" ]; then
case "$CASE:$1:${!#}" in
 matcher-first-grep-error:-qiE:*.filtered) case "$2" in 'panic|Oops:'*) exit 2;; esac;;
 matcher-total-count-error:-c:*/kmsg.log|matcher-kept-count-error:-c:*.filtered|matcher-benign-count-error:-cxE:*/kmsg.log)
 '''+q+' -L / '+linker+' '+toy+''' grep "$@"; exit 2;;
esac
fi
'''
        if app=='cat': pre='case "$1" in */dev/kmsg) if [ "$CASE" = marker-lost ]; then exec '+shutil.which('sleep')+' 30; fi; exec '+shutil.which('tail')+' -n +1 -f "$1";; esac\n'
        if app=='cat': pre+='case "$1" in */thermal_zone0/temp) hook before-start;; esac\n'
        if app=='cat': pre+='if [ "$CASE" = explicit-stop ] && [ -f "$FIX/tone-started" ]; then case "$1" in */proc/sys/kernel/random/boot_id) hook tone-before-health;; esac; fi\n'
        if app=='cat': pre+='''if [ -f "$FIX/tone-started" ]; then
case "$CASE:$1" in
 thermal-temp-unreadable:*/thermal_zone1/temp) exit 2;;
 thermal-current-missing:*/thermal_zone1/temp) '''+shutil.which('rm')+''' -f "$FIX/data/local/tmp/adsp-d2/thermal.cur";;
esac
fi
'''
        if app=='cmp': pre+='''if [ -f "$FIX/tone-started" ]; then
case "$CASE:$*" in
 thermal-baseline-unreadable:*thermal.baseline*|thermal-current-unreadable:*thermal.cur*) exit 2;;
esac
fi
'''
        if app=='sed': pre+='if [ -f "$FIX/sys/class/remoteproc/remoteproc7/state" ] && [ "$("/run/current-system/sw/bin/cat" "$FIX/sys/class/remoteproc/remoteproc7/state")" = running ]; then hook observe; fi\n'
        p.write_text('#!'+bash+'\n'+pre+'exec '+q+' -L / '+linker+' '+toy+' '+app+' "$@"\n')
        p.chmod(0o755)
    for app,text in {'id':'echo 0','uname':'echo fixture-kernel','ps':'echo init'}.items():
        (x/app).write_text('#!'+bash+'\n'+text+'\n'); (x/app).chmod(0o755)
    # qemu-user cannot exec a second ARM ELF without binfmt. These wrappers
    # model kernel writes and re-enter qemu for ordinary shell expressions.
    (x/'sh').write_text('''#!'''+shutil.which('python3')+'''
import os,sys,time
from pathlib import Path
case=os.environ['CASE']; args=sys.argv[1:]
if args[:1]==['-c'] and args[1].startswith('printf 1 '):
    k=Path(args[-1]).parent; r=Path(os.environ['FIX'])
    with (r/'kernel-stops').open('a') as f: f.write('stop\\n')
    if case=='kernel-stop-fail': sys.exit(1)
    (k/'d2_state').write_text('RESET'); (k/'d2_ready').write_text('0')
    sys.exit(0)
if args[:1]==['-c'] and args[1].startswith('printf '):
    state=Path(args[-1]); action=args[1].split()[1]
    with (state.parent/'writes').open('a') as f: f.write(action+'\\n')
    if action=='start': os.spawnv(os.P_WAIT,os.environ['HOOK'],[os.environ['HOOK'],'start'])
    if case==action+'-hang': time.sleep(30)
    if case==action+'-fail': sys.exit(1)
    state.write_text('running' if action=='start' else 'offline')
    import subprocess
    subprocess.Popen([os.environ['AUDIOFIX'],'up' if action=='start' else 'down'],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if action=='start' and case=='crash':
        with (Path(os.environ['FIX'])/'dev/kmsg').open('a') as f: f.write('remoteproc crashed fatal\\n')
    if action=='start' and case=='reset': (Path(os.environ['FIX'])/'proc/sys/kernel/random/boot_id').write_text('22222222-2222-2222-2222-222222222222')
    if action=='start' and case=='pd-crash':
        with (Path(os.environ['FIX'])/'dev/kmsg').open('a') as f: f.write("PDM: service 'avs/audio' crash: 'fixture'\\n")
    if action=='start' and case in ('benign-template','eink-template-variant'):
        msg='a9-eink eink-display: Direct firmware load for hisense/a9-ctrl-template.bin failed with error -2'
        if case=='eink-template-variant': msg=msg.replace('template','other')
        with (Path(os.environ['FIX'])/'dev/kmsg').open('a') as f: f.write('4,1,300511233,-;'+msg+'\\n SUBSYSTEM=platform\\n DEVICE=+platform:eink-display\\n')
    if action=='start' and case=='thermal-rise': (Path(os.environ['FIX'])/'sys/class/thermal/thermal_zone0/temp').write_text('49000')
    if action=='stop' and case=='restart': state.write_text('running')
    if action=='stop' and case=='reset-lost': (Path(os.environ['FIX'])/'sys/bus/platform/devices/a9-d2-codec/d2_state').write_text('UNKNOWN')
    sys.exit(0)
os.execv('''+repr(q)+''',['''+repr(q)+''','-L','/','''+repr(linker)+''','''+repr(shell)+''']+args)
'''); (x/'sh').chmod(0o755)
    (x/'insmod').write_text('''#!'''+shutil.which('python3')+'''
import os,sys,time
from pathlib import Path
r=Path(os.environ['FIX']); case=os.environ['CASE']; ko=Path(sys.argv[1]).name
if case=='load-hang': time.sleep(30)
if case=='load-fail': sys.exit(1)
with (r/'binds').open('a') as f: f.write(ko+'\\n')
if ko!='qcom_q6v5_pas.ko':
    (r/'sys/module'/ko[:-3].replace('-','_')).mkdir(parents=True)
    if ko=='qcom_pd_mapper.ko': os.spawnv(os.P_WAIT,os.environ['HOOK'],[os.environ['HOOK'],'before-pas'])
    sys.exit(0)
assert sys.argv[2:]==['a9_adsp_manual_start=1']
p=r/'sys/module/qcom_q6v5_pas/parameters'; p.mkdir(parents=True)
if case!='gate-missing': (p/'a9_adsp_manual_start').write_text('N' if case=='gate-false' else 'Y')
n=r/'sys/firmware/devicetree/base/soc@0/remoteproc@a400000'; n.mkdir(parents=True,exist_ok=True)
(n/'compatible').write_bytes(b'qcom,sm6115-adsp-pas\\0'); (n/'status').write_bytes(b'okay\\0')
d=r/'sys/devices/platform/soc@0/a400000.remoteproc'; d.mkdir(parents=True)
(d/'of_node').symlink_to(n if case!='wrong-node' else n.parent)
driver=r/'sys/bus/platform/drivers/qcom_q6v5_pas'; driver.mkdir(parents=True)
(d/'driver').symlink_to(driver)
p=r/'sys/class/remoteproc/remoteproc7'; p.mkdir(parents=True); (p/'device').symlink_to(d)
for k,v in {'state':'offline','recovery':'disabled','coredump':'disabled','firmware':'qcom/sm6115/hisense/hlte556n/adsp.mdt'}.items(): (p/k).write_text(v)
if case=='recovery-enabled': (p/'recovery').write_text('enabled')
if case=='coredump-enabled': (p/'coredump').write_text('enabled')
if case=='prior-running': (p/'state').write_text('running')
if case=='duplicate': shutil=None; (p.parent/'remoteproc8').symlink_to(p)
rp=d/'glink-edge/rpmsg'; rp.mkdir(parents=True)
bus=r/'sys/bus/rpmsg/devices'; bus.mkdir(parents=True)
if case!='no-rpmsg': (bus/'adsp-service').symlink_to(rp)
qrtr=r/'sys/bus/rpmsg/drivers/qcom_smd_qrtr'; qrtr.mkdir(parents=True)
if case!='unbound-qrtr': (rp/'driver').symlink_to(qrtr)
aux=d/'qcom_common.pd-mapper.7'; aux.mkdir()
drv=r/'sys/bus/auxiliary/drivers/qcom_pd_mapper.qcom-pdm-mapper'; drv.mkdir(parents=True)
if case!='unbound-mapper': (aux/'driver').symlink_to(drv)
bus=r/'sys/bus/auxiliary/devices'; bus.mkdir(parents=True)
(bus/aux.name).symlink_to(aux)
with (r/'dev/kmsg').open('a') as f:
    if case!='diagnostic-missing': f.write('A9 ADSP manual-start: auto_boot=0 recovery=disabled coredump=disabled\\n')
'''); (x/'insmod').chmod(0o755)
    # Actual Android mksh negative controls (thermal-rc143-fix.md), with
    # sessions isolated from the test runner. Host mksh does not model this.
    probe_env={**os.environ,'PATH':str(x),'LD_LIBRARY_PATH':str(sr/'system/lib64')}
    def probe(source):
        return sp.run([q,'-L','/',linker,shell,'-c',source],env=probe_env,
                      capture_output=True,text=True,timeout=10,start_new_session=True)
    negative=probe("trap 'echo EXIT=$?' EXIT; (while :; do :; done) & n=$!; sleep .1; kill \"$n\"; sleep .1; echo RESUMED")
    assert 'EXIT=143' in negative.stdout and 'RESUMED' not in negative.stdout,(negative.stdout,negative.stderr)
    resumed=probe("trap 'echo CLEANED' TERM; kill -TERM $$; echo RESUMED")
    assert resumed.returncode==0 and 'RESUMED' in resumed.stdout
    terminal=probe("trap 'rc=$?; :; exit \"$rc\"' EXIT; trap 'exit 143' TERM; kill -TERM $$; echo RESUMED")
    assert terminal.returncode==143 and 'RESUMED' not in terminal.stdout,(terminal.returncode,terminal.stdout)
    print('PASS Android mksh group TERM, returning-trap negative control, preserved terminal status',flush=True)
    signals={f'{sig.name}-{phase}':(sig,phase) for sig in (signal.SIGHUP,signal.SIGINT,signal.SIGTERM)
             for phase in ('before-pas','before-start','start','observe')}
    races={'contain-before-start':(None,'before-start'), 'delayed-start':(None,'start'),
           'killed-executor':(signal.SIGKILL,'start'), 'group-hup':(signal.SIGHUP,'start'),
           'group-term':(signal.SIGTERM,'start')}
    for case_index,case in enumerate(('pass','gate-missing','gate-false','wrong-node','duplicate','stale-tuple',
        'autoload','firmware-missing','load-fail','start-fail','stop-fail','crash','reset',
        'recovery-enabled','coredump-enabled','prior-running','diagnostic-missing','no-rpmsg',
        'restart','thermal','display','load-hang','start-hang','stop-hang',
        'marker-lost','unbound-qrtr','unbound-mapper','deadman','live-boot','live-config','manifest-tamper','fallback-on','fallback-absent-helper','fallback-absent-nohelper','benign-template','eink-template-variant',
        'pd-crash','thermal-rise','display-fault','persistent-data','updated-apex','preloaded-provider','original-boot',
        'owned-fd-observed','snd-pipe-grep-error','platform-driver','platform-owner','explicit-stop','early-stop','early-contain','tone-fail','tone-bad-terminal','tone-hang','tone-thermal','tone-fault','tone-unknown','tone-foreign','tone-wrong-exe','codec-not-ready','codec-fault','codec-unknown','missing-codec-attrs','kernel-stop-fail','reset-lost','snd-grep-error',
        'prior-sound','prior-apr','no-apr','apr-wrong-channel','apr-chan-unbound','apr-extra-service','apr-missing-service','apr-unbound',
        'no-card','card-wrong-id','pcm-extra','pcm-wrong','extra-card','sound-node-extra','stream-open','forbidden-module',
        'soundwire-device','lpi-device','apr-lost','card-lost','smmu-fault','pdr-fault','apr-fault-msg','asoc-fail','q6-timeout',
        'audio-pd-down','benign-asoc','card-stays','snd-opened','no-proc-scan','snd-hidden','snd-vanish','glink-timeout','glink-channel-id','glink-nochannel','glink-intent-id','glink-timed-out-other',
        'thermal-temp-missing','thermal-zone-missing','thermal-temp-unreadable','thermal-baseline-missing','thermal-baseline-unreadable','thermal-current-missing','thermal-current-unreadable',
        'matcher-first-grep-error','matcher-total-count-error','matcher-kept-count-error','matcher-benign-count-error',*signals,*races)):
        if case_index % a.shards != a.shard: continue
        if a.case and case not in a.case: continue
        assert shutil.disk_usage('/').free >= 15_000_000_000, 'retain >=15 GB free on /'
        r=root/case; b=r/'data/local/tmp/adsp-d2'; b.mkdir(parents=True)
        def put(p,s): p=r/p; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(s)
        put(Path('proc/sys/kernel/random/boot_id'),'11111111-1111-1111-1111-111111111111')
        put(Path('proc/modules'),'')
        if case!='no-proc-scan': (r/'proc/1/fd').mkdir(parents=True); (r/'proc/2/fd').mkdir(parents=True)
        put(Path('proc/sys/kernel/modprobe'),'')
        (r/'proc/uptime').symlink_to('/proc/uptime')
        put(Path('proc/mounts'),('/dev/block/sda1' if case=='persistent-data' else '/dev/block/ram0')+' /data ext4 rw 0 0\n')
        if case=='prior-sound': put(Path('proc/asound/cards'),'x')
        if case=='prior-apr': (r/'sys/bus/aprbus').mkdir(parents=True)
        if case=='updated-apex': put(Path('data/apex/active/unreviewed.apex'),'fixture')
        put(Path('sys/module/firmware_class/parameters/path'),'/data/local/tmp/a9fw')
        if case!='fallback-absent-helper' and case!='fallback-absent-nohelper':
            put(Path('proc/sys/kernel/firmware_config/force_sysfs_fallback'),'1' if case=='fallback-on' else '0')
        put(Path('sys/module/a9_eink/parameters/enable'),'Y' if case=='display' else 'N')
        put(Path('sys/module/panel_hisense_hlte556n_eink/parameters/enable'),'N')
        put(Path('sys/module/fp9929a/parameters/allow_power_on'),'N')
        put(Path('sys/class/drm/card9-DSI-1/eink/status'),'rail_fault 1\n' if case=='display-fault' else 'rail_fault 0 stall_fault 0 commit_fail 0 aborts 0\n')
        put(Path('sys/class/thermal/thermal_zone0/temp'),'71000' if case=='thermal' else '32000')
        if case in THERMAL_REGRESSIONS: put(Path('sys/class/thermal/thermal_zone1/temp'),'32000')
        put(Path('dev/kmsg'),'baseline\n')
        put(Path('dev/block/by-name/boot_b'),'k'*8192)
        cfg=b'CONFIG_FIXTURE=y\n'+(b'# CONFIG_FW_LOADER_USER_HELPER is not set\n' if case=='fallback-absent-nohelper' else b'')
        (r/'proc/config.gz').write_bytes(gzip.compress(cfg))
        (b/'config.sha256').write_text(hashlib.sha256(cfg).hexdigest())
        (b/'boot.bytes').write_text('8192')
        (b/'boot.sha256').write_text(hashlib.sha256(b'k'*8192).hexdigest())
        (b/'live.symlinks').write_text('')
        if case=='live-boot': (r/'dev/block/by-name/boot_b').write_text('wrong')
        if case=='live-config': (r/'proc/config.gz').write_bytes(gzip.compress(b'wrong'))
        if case=='autoload': (r/'sys/module/qcom_q6v5_pas').mkdir()
        (b/'kernel.release').write_text('fixture-kernel')
        (b/'baseline.modules').write_text('')
        if case=='preloaded-provider':
            (b/'baseline.modules').write_text('qrtr_smd\n')
            (r/'proc/modules').write_text('qrtr_smd 1 0 - Live 0x0\n')
            (r/'sys/module/qrtr_smd').mkdir()
        (b/'modules.d2').write_text('apr.ko\nq6core.ko\nsnd-soc-sm8250.ko\nqrtr-smd.ko\nqcom_pd_mapper.ko\nqcom_q6v5_pas.ko\n')
        (b/'soundwire.devices').write_text('0217:010a:00:3\n0217:010a:00:4\n')
        (b/'d2-tone').write_text('#!'+shutil.which('python3')+'\n'+r'''import os,sys,time,shutil
from pathlib import Path
r=Path(os.environ['FIX']); case=os.environ['CASE']; k=r/'sys/bus/platform/devices/a9-d2-codec'
stop=Path(sys.argv[2]); assert sys.argv[1]=='--run'
if case=='tone-hang': time.sleep(35)
if stop.exists() or case=='tone-fail': sys.exit(1)
p=r/'proc'/str(os.getpid()); (p/'fd').mkdir(parents=True)
(p/'exe').symlink_to('/wrong' if case=='tone-wrong-exe' else Path(sys.argv[0]))
(p/'fd/3').symlink_to('/dev/snd/controlC0'); (p/'fd/4').symlink_to('/dev/snd/pcmC0D0p')
(k/'d2_state').write_text('EAR')
(r/'proc/asound/card0/pcm0p/sub0/status').write_text('state: RUNNING\n')
with (r/'tone-actions').open('a') as f: f.write('START\n')
(r/'tone-started').touch()
if case=='thermal-temp-missing': (r/'sys/class/thermal/thermal_zone1/temp').unlink()
if case=='thermal-zone-missing': shutil.rmtree(r/'sys/class/thermal/thermal_zone1')
if case=='thermal-baseline-missing': (r/'data/local/tmp/adsp-d2/thermal.baseline').unlink(missing_ok=True)
if case=='tone-thermal': (r/'sys/class/thermal/thermal_zone0/temp').write_text('71000')
if case=='tone-fault': (k/'d2_fault').write_text('1')
if case=='tone-unknown': (k/'d2_state').write_text('UNKNOWN')
if case=='tone-foreign':
    d=r/'proc/3/fd'; d.mkdir(parents=True); (d/'4').symlink_to('/dev/snd/pcmC0D0p')
must_stop=case in ('tone-thermal','tone-fault','tone-unknown','tone-foreign','tone-wrong-exe','snd-pipe-grep-error','apr-lost','card-lost','explicit-stop')
must_stop=must_stop or case.startswith(('thermal-temp-', 'thermal-zone-', 'thermal-baseline-', 'thermal-current-', 'matcher-'))
end=time.monotonic()+(100 if must_stop or case=='owned-fd-observed' else 1.5)
cancel=False
while time.monotonic()<end:
    if stop.exists(): cancel=True; break
    if case=='owned-fd-observed' and (r/'owned-observed').exists(): break
    time.sleep(.01)
with (r/'tone-actions').open('a') as f: f.write('DROP\nCLOSE\n')
shutil.rmtree(p)
if (r/'proc/3').exists(): shutil.rmtree(r/'proc/3')
(r/'proc/asound/card0/pcm0p/sub0/status').write_text('closed\n')
(k/'d2_state').write_text('SAFE')
print('D2-TONE-CLOSED-SAFE' if case!='tone-bad-terminal' else 'missing completion')
sys.exit(1 if cancel or must_stop or (case=='owned-fd-observed' and not (r/'owned-observed').exists()) else 0)
''')
        (b/'d2-tone').chmod(0o755)
        (b/'dummy').write_text('firmware')
        digest=hashlib.sha256(b'firmware').hexdigest()
        (b/'SHA256SUMS').write_text(digest+'  dummy\n')
        tuple_id=hashlib.sha256((b/'SHA256SUMS').read_bytes()).hexdigest()
        (b/'tuple').write_text('b'*64 if case=='stale-tuple' else tuple_id)
        if case=='manifest-tamper': (b/'SHA256SUMS').write_text('changed')
        (b/'live.sha256').write_text(digest+'  '+str(b/'dummy')+'\n')
        if case=='firmware-missing': (b/'dummy').unlink()
        env={**os.environ,'PATH':str(x),'ADSP_D2_REHEARSAL':'1','CASE':case,'FIX':str(r),'LD_LIBRARY_PATH':str(sr/'system/lib64'),'HOOK':str(hook),'AUDIOFIX':str(x/'audiofix')}
        if case=='deadman': env['ADSP_D2_REHEARSAL_DEADLINE']='2'
        argv=[q,'-L','/',linker,shell,str(script),'--rehearse',str(r),'run',tuple_id,
              '22222222-2222-2222-2222-222222222222' if case=='original-boot' else '11111111-1111-1111-1111-111111111111']
        owner=r/'data/local/tmp/adsp-d2-ledger/11111111-1111-1111-1111-111111111111'
        def until(predicate,seconds=180):
            end=time.monotonic()+seconds
            while not predicate():
                assert time.monotonic()<end,(case,'fixture wait expired',(r/'stderr').read_text()[-2000:])
                time.sleep(.02)
        with (r/'stdout').open('w') as stdout, (r/'stderr').open('w') as stderr:
            if case in signals or case in races:
                sig,phase={**signals,**races}[case]; env['PHASE']=phase
                child=sp.Popen(argv,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
                until(lambda:(r/'phase').exists())
                if sig:
                    if case.startswith('group-'): os.killpg(child.pid,sig)
                    else: child.send_signal(sig)
                # Separate containment must not certify while executor is
                # paused before start, or while the issued writer is pending.
                with (r/'contain.out').open('w') as co, (r/'contain.err').open('w') as ce:
                    contain=sp.Popen(argv[:-3]+['contain',tuple_id,argv[-1]],env=env,stdout=co,stderr=ce)
                    until(lambda:len(list(b.glob('abort.*')))==1)
                    time.sleep(.15)
                    assert contain.poll() is None,(case,'premature containment')
                    assert not (owner/'result').exists(),(case,'OFFLINE certified with a future start possible')
                    os.kill(int((owner/'guard.pid').read_text()),0)
                    if case in ('delayed-start','killed-executor'):
                        # Keep the syscall pending beyond guardian's handoff
                        # deadline, even though sysfs continues to say OFFLINE.
                        until(lambda:(owner/'result').exists(),45)
                        assert (owner/'result').read_text().strip()=='CONTAINMENT-UNVERIFIED'
                        assert (owner/'fallback').exists()
                        os.kill(int((owner/'guard.pid').read_text()),0)
                        assert 'OFFLINE' not in (r/'contain.out').read_text()
                    (r/'release').touch()
                    child.wait(timeout=120); contain.wait(timeout=30)
                p=sp.CompletedProcess(argv,child.returncode)
                if case in ('delayed-start','killed-executor'):
                    until(lambda:any(j.name=='op.7' and (j/'done').exists() for j in owner.glob('op.*')),20)
                    assert (owner/'result').read_text().strip()=='CONTAINMENT-UNVERIFIED'
                    assert 'D2-CONTAINED-OFFLINE' not in (r/'contain.out').read_text()
                    if case=='delayed-start':
                        until(lambda:(owner/'late-result').exists(),30)
                        assert (owner/'result').read_text().strip()=='CONTAINMENT-UNVERIFIED'
                if sig and sig!=signal.SIGKILL: assert child.returncode==128+int(sig),(case,child.returncode)
            elif case=='explicit-stop':
                env['PHASE']='tone-before-health'
                child=sp.Popen(argv,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
                until(lambda:(r/'phase').exists())
                stopper=sp.Popen(argv[:-3]+['stop',tuple_id,argv[-1]],env=env,stdout=sp.PIPE,stderr=sp.PIPE,text=True)
                until(lambda:len(list(b.glob('abort.*')))==1)
                (r/'release').touch()
                stopout,stoperr=stopper.communicate(timeout=60)
                child.wait(timeout=60); p=sp.CompletedProcess(argv,child.returncode)
                assert stopper.returncode==0 and 'D2-CONTAINED-OFFLINE' in stopout,(stopout,stoperr)
            elif case in ('early-stop','early-contain'):
                stopped=sp.run(argv[:-3]+['contain' if case=='early-contain' else 'stop',tuple_id,argv[-1]],env=env,capture_output=True,text=True,timeout=10)
                assert stopped.returncode and (b/('stop.'+argv[-1]+'.'+tuple_id)).exists()
                p=sp.run(argv,env=env,stdout=stdout,stderr=stderr,text=True,timeout=20)
            else:
                p=sp.run(argv,env=env,stdout=stdout,stderr=stderr,text=True,timeout=300)
        p.stdout=(r/'stdout').read_text(); p.stderr=(r/'stderr').read_text()
        writes=list(r.rglob('writes'))
        actions=writes[0].read_text().splitlines() if writes else []
        if case in signals or case in races:
            assert 'D2-PASS-' not in p.stdout,(case,p.stdout)
            phase={**signals,**races}[case][1]
            if phase in ('before-pas','before-start'):
                assert 'start' not in actions,(case,actions)
            if phase=='before-pas': assert 'qcom_q6v5_pas.ko' not in (r/'binds').read_text(),case
        if case=='original-boot': assert not (r/'binds').exists(),case
        if case in WHY:
            try:
                assert WHY[case] in p.stderr,(case,WHY[case],[l for l in p.stderr.splitlines() if 'linker' not in l][-20:],p.stdout[-600:])
            except AssertionError:
                # A before-fix regression is expected to assert here. Reap only
                # this offline fixture's guardian; this is never containment.
                if case in THERMAL_REGRESSIONS+MATCHER_REGRESSIONS and (owner/'guard.pid').exists():
                    try: os.kill(int((owner/'guard.pid').read_text()),signal.SIGKILL)
                    except ProcessLookupError: pass
                raise
        if case in ('prior-sound','prior-apr'): assert not (r/'binds').exists() and 'start' not in actions,case
        if case=='eink-template-variant': assert 'D2-FAIL kernel-fault' in p.stderr,case
        if case in ('fallback-on','fallback-absent-helper'): assert 'D2-FAIL firmware-fallback' in p.stderr and not (r/'binds').exists(),case
        if case in PASSING: assert p.returncode==0,(case,p.stdout,[l for l in p.stderr.splitlines() if 'linker' not in l][-30:])
        tone_actions=(r/'tone-actions').read_text().splitlines() if (r/'tone-actions').exists() else []
        assert tone_actions.count('START')<=1,(case,tone_actions)
        if case=='owned-fd-observed': assert (r/'owned-observed').exists()
        if case in PASSING: assert tone_actions==['START','DROP','CLOSE'],(case,tone_actions)
        if case in ('early-stop','early-contain'): assert not actions and not tone_actions
        if case=='explicit-stop': assert tone_actions==['START','DROP','CLOSE'] and (owner/'result').read_text().strip()=='OFFLINE'
        assert actions.count('start')<=1,(case,actions)
        assert (p.returncode==0)==(case in PASSING),(case,p.stdout,p.stderr[-3000:])
        if case in PASSING:
            assert actions==['start','stop'],actions
            assert 'D2-EVIDENCE up' in p.stdout and 'MultiMedia1' in p.stdout and 'D2-STEP card-ready' in p.stdout,(case,p.stdout)
            assert ('D2-AFTER-STOP card=present' if case=='card-stays' else 'D2-AFTER-STOP card=gone') in p.stdout,(case,p.stdout)
            assert p.stdout.rstrip().endswith('D2-PASS-ONE-TONE-CLOSED-RESET-OFFLINE')
            shutil.rmtree(r/'sys/module/qcom_q6v5_pas')
            shutil.rmtree(r/'sys/class/remoteproc/remoteproc7')
            # Re-entry must be refused by the LEDGER itself: also drop the audio residue
            # (a real second attempt would already stop earlier at prior-apr/loaded-modules).
            for residue in ('sys/bus/aprbus','proc/asound'):
                if (r/residue).exists(): shutil.rmtree(r/residue)
            for stopfile in b.glob('stop.*'): stopfile.unlink()
            again=sp.run([q,'-L','/',linker,shell,str(script),'--rehearse',str(r),'run',tuple_id,'11111111-1111-1111-1111-111111111111'],
                env=env,capture_output=True,text=True,timeout=60)
            assert again.returncode and 'consumed-attempt' in again.stderr
        if case in ('gate-missing','gate-false','wrong-node','duplicate','recovery-enabled','coredump-enabled','diagnostic-missing','prior-running'):
            assert 'start' not in actions,(case,actions)
        if case in ('load-hang','start-hang','stop-hang','tone-hang'):
            until(lambda:all((j/'done').exists() for j in owner.glob('op.*')),40)
        # Reap only this fixture's detached guardian with KILL after checking
        # evidence; TERM itself can re-raise to an Android mksh process group.
        if (owner/'guard.pid').exists():
            try: os.kill(int((owner/'guard.pid').read_text()),signal.SIGKILL)
            except ProcessLookupError: pass
        print('PASS Android mksh/toybox',case,flush=True)
print('ADSP-D2-PHONE TEST PASS')
