#!/usr/bin/env python3
"""Kernel-fault matcher of d0-target.sh, extracted between BEGIN/END-MATCHER and run
in Android mksh + real toybox awk/grep under QEMU. No device. Optional --replay takes a
real kmsg dump (e.g. attempt-4 d0/run.log) and replays its marked window."""
import argparse,re,shutil,subprocess as sp,tempfile
from pathlib import Path
ap=argparse.ArgumentParser()
ap.add_argument('--sysroot',required=True,type=Path); ap.add_argument('--qemu',required=True,type=Path)
ap.add_argument('--replay',type=Path); ap.add_argument('--marker-prefix',default='ADSP-D0-')
a=ap.parse_args()
src=(Path(__file__).resolve().parents[2]/'scripts/mainline/adsp/d0-target.sh').read_text()
fn=re.search(r'# BEGIN-MATCHER\n(.*?)# END-MATCHER',src,re.S).group(1)
BENIGN='a9-eink eink-display: Direct firmware load for hisense/a9-ctrl-template.bin failed with error -2'
CONT=' SUBSYSTEM=platform\n DEVICE=+platform:eink-display\n'
def rec(msg,pri=4,seq=1,cont=CONT): return f'{pri},{seq},300511233,-;{msg}\n{cont}'
with tempfile.TemporaryDirectory() as td:
    r=Path(td); sr=r/'sr'; shutil.copytree(a.sysroot,sr,symlinks=True); x=r/'x'; x.mkdir()
    linker=str(sr/'system/bin/linker64'); toy=str(sr/'system/bin/toybox'); shell=str(sr/'system/bin/sh')
    for app in ('grep','wc'):
        (x/app).write_text('#!'+shutil.which('bash')+'\nexec '+str(a.qemu)+' -L / '+linker+' '+toy+' '+app+' "$@"\n'); (x/app).chmod(0o755)
    (r/'m.sh').write_text('fail() { echo "D0-FAIL $*" >&2; echo FAIL-CLOSED; exit 1; }\n'+fn+'if kernel_fault_in "$1"; then echo FAULT; else echo CLEAN; fi\n')
    env={'PATH':str(x)+':/run/current-system/sw/bin','LD_LIBRARY_PATH':str(sr/'system/lib64')}
    def run(text,tool=None):
        f=r/'k.log'; f.write_text(text)
        o=sp.run([str(a.qemu),'-L','/',linker,shell,str(r/'m.sh'),str(f)],env=env,capture_output=True,text=True,timeout=60)
        out=[l for l in o.stdout.split() if l in ('FAULT','CLEAN','FAIL-CLOSED')]
        assert len(out)==1,(o.stdout,o.stderr[-500:]); return out[0]
    ok=lambda name,text,want: (run(text)==want or (_ for _ in ()).throw(AssertionError((name,want)))) and print('PASS matcher',name,want,flush=True)
    ok('benign-with-continuation',rec(BENIGN),'CLEAN')
    ok('benign-repeated-between-clean',rec(BENIGN)+'6,2,3,-;healthd: battery l=99\n'+rec(BENIGN,seq=3),'CLEAN')
    ok('benign-without-continuation',f'4,1,2,-;{BENIGN}\n','CLEAN')
    ok('adsp-quirk-line-clean','3,1,2,-;qcom_q6v5_pas a400000.remoteproc: Handover signaled, but it already happened\n'+CONT,'CLEAN')
    for name,msg in {
      'eink-variant-other-file':'a9-eink eink-display: Direct firmware load for hisense/other.bin failed with error -2',
      'eink-variant-suffix':BENIGN+' retrying',
      'eink-variant-prefix':'x '+BENIGN,
      'eink-variant-error-3':BENIGN.replace('-2','-3'),
      'eink-rail-failed':'a9-eink eink-display: rail failed',
      'eink-fault':'a9-eink eink-display: rail_fault 1',
      'drm-check-failed':'a9-eink eink-display: [drm:drm_atomic_helper_check_planes] atomic driver check failed',
      'crash':'remoteproc remoteproc0: crash detected in adsp: type fatal error',
      'ssr':'qcom_q6v5_pas: SSR event',
      'pdr':"PDM: service 'avs/audio' crash: 'x'",
      'watchdog':'watchdog: bite reset',
      'smmu':'arm-smmu 1c40000.iommu: SMMU fault at 0x0',
      'smem':'qcom-smem: SMEM error -22',
      'glink':'qcom_glink_smem: glink failed to open channel',
      'auth':'qcom_scm: authentication failed',
      'thermal':'thermal thermal_zone5: critical temperature reached',
      'fp9929a':'fp9929a 0-0048: fault',
      'panic':'Kernel panic - not syncing','reboot':'reboot: Restarting system'}.items():
        ok(name,rec(msg),'FAULT')
    # A benign record followed by a real fault must still fault, incl. fault hidden in a continuation-looking line.
    ok('benign-then-real-fault',rec(BENIGN)+rec('remoteproc remoteproc0: crash detected in adsp'),'FAULT')
    ok('fault-after-benign-no-continuation',f'4,1,2,-;{BENIGN}\n6,2,3,-;panic now\n','FAULT')
    ok('benign-header-with-fault-continuation',f'4,1,2,-;{BENIGN}\n SUBSYSTEM=platform\n DEVICE=+x\n6,2,3,-;fatal x\n','FAULT')
    ok('unterminated-last-line-benign',rec(BENIGN)+'6,2,3,-;healthd: x','CLEAN')
    ok('unterminated-last-line-fault',rec(BENIGN)+'6,2,3,-;panic now'.rstrip('\n'),'FAULT')
    # Broken grep must fail closed, never report CLEAN: shadow grep with a failing stub.
    good=(x/'grep').read_text()
    (x/'grep').write_text('#!'+shutil.which('bash')+'\nexit 2\n')
    assert run(rec('panic'))=='FAIL-CLOSED' and run(rec(BENIGN))=='FAIL-CLOSED'
    (x/'grep').write_text(good); print('PASS matcher fails closed when grep is broken',flush=True)
    if a.replay:
        lines=a.replay.read_text().splitlines(keepends=True)
        i=next(n for n,l in enumerate(lines) if a.marker_prefix in l and l.split(';',1)[-1].startswith(a.marker_prefix))
        window=''.join(lines[i:])
        assert BENIGN in window and 'D0-FAIL' in window
        window=''.join(l for l in window.splitlines(keepends=True) if not l.startswith(('D0-FAIL','D0-FAILED')))
        ok('attempt4-window-replay',window,'CLEAN')
        # the same replay with the filter disabled must fault (proves the replay exercises the benign line)
        r2=run(window.replace(BENIGN,BENIGN+' FAKE'))
        assert r2=='FAULT'; print('PASS matcher attempt4-window-replay-with-altered-line FAULT',flush=True)
print('ADSP-D0-MATCHER TEST PASS')
