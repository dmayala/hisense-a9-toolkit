#!/usr/bin/env python3
"""Stage verified host artifacts into the existing combined fix3 launcher."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')
B = ROOT/'gnss-outdoor-fix3-20261023'
OUT = ROOT/'integ-20261023-5/fix4'


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    # No edits to an executable that a current owner is running.
    owned = {str(B/n) for n in ['run.sh','onb.py','b-try-outdoor.sh','sleep.sh','mirror.sh']}
    for p in Path('/proc').glob('[0-9]*/cmdline'):
        try: args=p.read_bytes().split(b'\0')
        except (OSError,PermissionError): continue
        assert not any(a.decode(errors='replace') in owned for a in args), 'candidate is executing; stage later'
    if sys.argv[1:]==['--fix5']:
        stage_fix5()
        return
    if sys.argv[1:]==['--fix6']:
        stage_fix6()
        return
    if sys.argv[1:]==['--fix6-outing']:
        stage_fix6_outing()
        return
    assert not sys.argv[1:], 'usage: stage.py [--fix5|--fix6|--fix6-outing]'
    result=json.loads((OUT/'result.json').read_text())
    assert sha(result['vendor'])==result['vendor_sha256']
    assert sha(result['boot'])==result['boot_sha256']
    assert 'PASS 5 FIX4 smoke groups' in (OUT/'smoke.log').read_text()
    # Preserve the combined XTRA baseline and its prior manifests for handoff.
    backup=OUT/'before-fix3'
    if not backup.exists():
        backup.mkdir()
        for n in ['run.sh','onb.py','sleep.sh','extract.py','payload.json','validation.json','README.md']:
            shutil.copyfile(B/n,backup/n)
    payload=json.loads((backup/'payload.json').read_text())
    changes=['run.sh','onb.py','sleep.sh','extract.py','mirror.sh']
    for n,h in payload['runtime'].items():
        if n not in changes: assert sha(B/n)==h, 'combined input changed: '+n
    for n in changes:
        shutil.copyfile(HERE/n,B/n)
    for n in ['run.sh','onb.py']: (B/n).chmod(0o755)
    link=B/'vendor-fix4.img'
    if not link.exists(): link.symlink_to(result['vendor'])
    assert link.resolve()==Path(result['vendor']).resolve()
    payload.update(vendor=str(link),vendor_sha256=result['vendor_sha256'],phone_executed=False)
    payload['runtime'].update({n:sha(B/n) for n in changes})
    payload['fix4']=dict(report=str(OUT/'READY.txt'),build=str(OUT/'result.json'),stock_radio_commit='85e6578/8f4cfef',
                         kmsg_mirror=True,wakeup_deltas=True,phone_executed=False)
    (B/'payload.json').write_text(json.dumps(payload,indent=2)+'\n')
    validation=json.loads((backup/'validation.json').read_text())
    validation['fix4']=dict(host_only=True,smoke_groups=5,android_shell_qemu=True,
                            module_audit=result['module_audit'],phone_access=False,hardware_tested=False)
    (B/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    (B/'README.md').write_text((HERE/'README.md').read_text())
    (OUT/'runtime.json').write_text(json.dumps(payload,indent=2)+'\n')
    print('PASS staged FIX4 into combined fix3; XTRA/APK/credentials/return-A preserved')


def stage_fix5():
    out=OUT.parent/'fix5'
    checks=json.loads((out/'radio-smoke.json').read_text())
    assert len(checks['cases'])==5 and checks['android_state_reducer'] and checks['interrupt_preserved']
    payload=json.loads((B/'payload.json').read_text())
    assert sha(payload['vendor'])==payload['vendor_sha256']
    assert sha(payload['boot'])==payload['boot_sha256']
    for name,digest in payload['runtime'].items():
        if sha(B/name)==digest:continue
        # The coordinator's post-FIX4 archive-pull timeout repair is already
        # present in the current launcher. Preserve exactly those four edits.
        assert name=='b-try-outdoor.sh',name
        prior=(ROOT/'gnss-outdoor-fix2-20261023'/name).read_bytes().replace(
            b'gnss-outdoor-fix2-20261023/extract.py',b'gnss-outdoor-fix3-20261023/extract.py')
        assert hashlib.sha256(prior).hexdigest()==digest
        assert (B/name).read_bytes()==prior.replace(b'timeout 60 "$ADB" exec-out',b'timeout 600 "$ADB" exec-out')
        payload['runtime'][name]=sha(B/name)
    backup=out/'before-fix3'
    if not backup.exists():
        backup.mkdir()
        for name in ['onb.py','README.md','payload.json','validation.json']:
            shutil.copyfile(B/name,backup/name)
    shutil.copyfile(HERE/'onb.py',B/'onb.py');(B/'onb.py').chmod(0o755)
    shutil.copyfile(HERE/'README.md',B/'README.md')
    payload['runtime']['onb.py']=sha(B/'onb.py')
    payload['fix4'].update(phone_executed=True,rehearsal_result='boot62 optional radio check failed; returned A')
    payload['fix5']=dict(report=str(out/'READY.txt'),radio_optional=True,service_state_context='existing_native_helper',phone_executed=False)
    (B/'payload.json').write_text(json.dumps(payload,indent=2)+'\n')
    validation=json.loads((B/'validation.json').read_text());validation['fix5']=checks
    (B/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    (out/'runtime.json').write_text(json.dumps(payload,indent=2)+'\n')
    print('PASS staged FIX5 hook; all boot/vendor/XTRA/APK/logger/runner inputs retained')


def stage_fix6():
    out=ROOT/'integ-20261023-6'
    result=json.loads((out/'integrated-build.json').read_text())
    for name,digest in result['sha256'].items():assert sha(out/name)==digest,name
    stats=json.loads((out/'stats-smoke.json').read_text())
    radio=json.loads((out/'radio-smoke.json').read_text())
    assert stats['cases']['unmounted']['soc_sources']==2
    assert stats['cases']['mount_failed']['soc_sources']==0
    assert stats['boot_radio_gates'] and radio['interrupt_preserved']
    payload=json.loads((B/'payload.json').read_text())
    for name,digest in payload['runtime'].items():assert sha(B/name)==digest,name
    backup=out/'before-fix3'
    if not backup.exists():
        backup.mkdir()
        for name in ['run.sh','onb.py','sleep.sh','extract.py','README.md','payload.json','validation.json']:
            shutil.copyfile(B/name,backup/name)
    changes=['run.sh','onb.py','sleep.sh','extract.py']
    for name in changes:shutil.copyfile(HERE/name,B/name)
    for name in ['run.sh','onb.py']:(B/name).chmod(0o755)
    payload.update(boot=str(out/'boot.img'),vendor=str(out/'vendor.img'),boot_sha256=result['sha256']['boot.img'],vendor_sha256=result['sha256']['vendor.img'])
    payload['runtime'].update({name:sha(B/name) for name in changes})
    payload['fix5'].update(phone_executed=True,rehearsal_result='boot65 flow passed; stats unavailable and pre-hook phone ANRs remained')
    payload['fix6']=dict(report=str(out/'READY.txt'),debugfs_mount=True,radio_boot_start=True,phone_executed=False)
    (B/'payload.json').write_text(json.dumps(payload,indent=2)+'\n')
    validation=json.loads((B/'validation.json').read_text());validation['fix6']=dict(stats=stats,radio=radio,abl=result['abl'],fsck=result['fsck'])
    (B/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    (B/'README.md').write_text((HERE/'README.md').read_text())
    (out/'runtime.json').write_text(json.dumps(payload,indent=2)+'\n')
    print('PASS staged FIX6 integ6 images + stats logger + boot-radio observer')


def outdoor_runner(source):
    old='python3 "/home/user/A9-build/mainline/gnss-outdoor-fix3-20261023/extract.py" "$out/$latest"'
    assert source.count(old)==1
    assert source.count('[[ $hold == 2400 && -n $onb ]]')==1
    assert source.count('replug by +30 min.')==1
    assert source.count('timeout 600 "$ADB" exec-out')==4
    return source.replace(old,old+' --json "$out/outdoor-result.json"').replace(
        '[[ $hold == 2400 && -n $onb ]]','[[ $hold == 4200 && -n $onb ]]').replace(
        'replug by +30 min.','replug by +50 min.')


def stage_fix6_outing():
    out=ROOT/'integ-20261023-6';build=out/'outing-fix'
    result=json.loads((build/'result.json').read_text())
    checks=json.loads((build/'outing-smoke.json').read_text())
    assert checks['boot69_gnss_fix'] and checks['persistent_current_boot'] and checks['android_mksh_awk']
    assert result['abl']==result['fsck']=='PASS'
    for name,digest in result['sha256'].items():assert sha(build/name)==digest,name
    payload=json.loads((B/'payload.json').read_text())
    assert sha(out/'boot.img')==result['base_boot_sha256']==payload['boot_sha256']
    assert sha(out/'vendor.img')==result['base_vendor_sha256']==payload['vendor_sha256']
    for name,digest in payload['runtime'].items():assert sha(B/name)==digest,name
    runner=outdoor_runner((B/'b-try-outdoor.sh').read_text())
    backup=out/'before-boot69-addendum';backup.mkdir()
    runtime=backup/'launcher';runtime.mkdir()
    changes=['run.sh','onb.py','sleep.sh','extract.py','b-try-outdoor.sh']
    for name in changes+['payload.json','validation.json','README.md']:
        shutil.copyfile(B/name,runtime/name)
    for name in ['boot.img','vendor.img','integ.dtb','integrated-build.json','runtime.json','SHA256SUMS','READY.txt','abl-check.log','vendor-fsck.log']:
        (out/name).rename(backup/name)
    for name in ['boot.img','vendor.img','integ.dtb']:(out/name).symlink_to(build/name)
    for name in ['abl-check.log','vendor-fsck.log']:shutil.copyfile(build/name,out/name)
    for name in changes:
        if name=='b-try-outdoor.sh':(B/name).write_text(runner)
        else:shutil.copyfile(HERE/name,B/name)
    for name in ['run.sh','onb.py','b-try-outdoor.sh']:(B/name).chmod(0o755)
    prior=json.loads((backup/'integrated-build.json').read_text())
    prior.update(boot_byte_identical_to_integ5=False,rpm_dt_unchanged=True,uart_wake_falling_edge=True,
                 source_commit=result['source_commit'],
                 outing_addendum=result,changed=['etc/init/init.a9ml-stock-radio.rc','bin/a9ml-deadman.sh'])
    prior['sha256'].update({name:sha(out/name) for name in ['boot.img','vendor.img','integ.dtb']})
    (out/'integrated-build.json').write_text(json.dumps(prior,indent=2)+'\n')
    (out/'SHA256SUMS').write_text(''.join(d+'  '+n+'\n' for n,d in prior['sha256'].items()))
    payload.update(boot_sha256=sha(out/'boot.img'),vendor_sha256=sha(out/'vendor.img'))
    payload['runtime'].update({name:sha(B/name) for name in changes})
    payload['fix6'].update(outing_addendum=str(build/'result.json'),persistent_gnss=True,
                           named_wakeup_sources=True,uart_rx_falling_edge=True,
                           host_hold_seconds=4200,deadman_hold_seconds=5400,replug_seconds=1200)
    payload.update(host_hold_s=4200,vendor_postboot_deadman_s=5400)
    (B/'payload.json').write_text(json.dumps(payload,indent=2)+'\n')
    validation=json.loads((B/'validation.json').read_text());validation['fix6']['outing_addendum']=checks
    (B/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    shutil.copyfile(HERE/'README.md',B/'README.md')
    (out/'runtime.json').write_text(json.dumps(payload,indent=2)+'\n')
    print('PASS staged FIX6 outing addendum; previous images/launcher preserved; XTRA/radio/return-A retained')


if __name__=='__main__':
    main()
