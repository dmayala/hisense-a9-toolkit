"""One qualification before USB/reboot under the existing sweep owner. Imports are inert."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import time

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('surface_validation',HERE/'validation.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)


def run(s):
    if getattr(s,'hvenc_qualification_attempted',False):return
    s.hvenc_qualification_attempted=True
    evidence=dict(qualification=1,attempted=0,bundle_verified=0,slot_b=0,cleanup=0,
                  native_pass=0,host_mp4_ok=0,budget_ok=0,staged=0,remaining_ms=max(0,int((s.qualification_end-time.monotonic())*1000)),
                  skip_reason=0)
    remote=None;media=s.out/'hvenc-output.mp4'
    cpu_original=None;cpu_reset_interrupted=False
    cpu_property='debug.stagefright.a9.enc_cpu_read'
    try:
        if os.environ.get('A9_BTRY_IN_HOOK')!='1' or not s.hvenc_qualify or not s.touched:
            evidence['skip_reason']=1;return
        # Never inherit finally's extra cleanup time as a new test admission.
        if s.qualification_end-time.monotonic()<145:
            evidence['skip_reason']=3;return
        evidence['budget_ok']=1;s.command_end=min(s.qualification_end,time.monotonic()+140)
        image=Path(os.environ.get('A9_SWEEP_IMAGE',str(s.assets.parent)))
        bundle=image/'hvenc-qualification'
        v.verify_bundle(bundle,HERE.parents[1],image/'encoder-stage.json')
        evidence['bundle_verified']=1
        if media.exists() or (s.out/'hvenc-output.mp4.part').exists():return
        rc,text=s.shell('getprop ro.boot.slot_suffix; getprop sys.boot_completed',timeout=8)
        if rc or text.strip().splitlines()!=['_b','1']:return
        evidence['slot_b']=1
        # New components sample this at interface creation, before GBS allocates
        # a Surface buffer. Keep the original owner/deadline and restore value.
        cpu_choice=os.environ.get('A9_SWEEP_HVENC_CPU_READ','')
        if cpu_choice in ('0','1'):
            evidence.update(cpu_read_requested=1,cpu_read_value=int(cpu_choice))
            rc,text=s.shell('getprop '+cpu_property,timeout=3)
            if rc or text.strip() not in ('','0','1','false','true'):return
            cpu_original=text.strip()
            rc,text=s.shell('set -e\nsetprop '+cpu_property+' '+cpu_choice+'\n'
                '[ "$(getprop '+cpu_property+')" = '+cpu_choice+' ]\nprintf "HVENC_CPU_READ '+cpu_choice+'\\n"',timeout=3)
            evidence['cpu_read_applied']=int(rc==0 and text.strip()=='HVENC_CPU_READ '+cpu_choice)
            if not evidence['cpu_read_applied']:return
        rc,text=s.shell('mktemp -d /data/local/tmp/a9-hvenc.XXXXXX',timeout=5)
        if rc or not re.fullmatch(r'/data/local/tmp/a9-hvenc\.[A-Za-z0-9]{6,}\s*',text):return
        remote=text.strip();q=shlex.quote(remote)
        rc,_=s.run([s.adb,'push',bundle/'qualify',bundle/'avc.mp4',bundle/'hevc.mp4',remote+'/'],timeout=20)
        if rc:return
        rc,_=s.shell('chmod 700 '+shlex.quote(remote+'/qualify'),timeout=3)
        if rc:return
        evidence.update(staged=1,attempted=1)
        # Native alarm50, device TERM55/KILL57, host60 and the original owner
        # hold are independent bounds. No retry after a partial/hung encode.
        rc,text=s.shell('timeout -k 2 55 '+shlex.quote(remote+'/qualify')+' '+q+' 2>&1',
                        timeout=60,capture_partial=True)
        evidence.update(v.parse(text,rc));evidence['native_pass']=int(v.passed(evidence))
        # Only synthetic output is eligible for export. Bound it before pull.
        rc,size=s.shell('stat -c %s '+shlex.quote(remote+'/output.mp4')+' 2>/dev/null',timeout=5)
        if rc or not re.fullmatch(r'[0-9]{1,10}\s*',size) or not 0<int(size)<=4*1024**2:return
        evidence['mp4_bytes']=int(size)
        part=s.out/'hvenc-output.mp4.part'
        rc,_=s.run([s.adb,'pull',remote+'/output.mp4',part],timeout=15)
        if rc or not part.is_file() or part.is_symlink() or part.stat().st_size!=int(size):return
        part.chmod(0o600);part.replace(media)
        if s.command_end-time.monotonic()<22:return
        evidence.update(v.inspect(media))
    except InterruptedError:raise
    except (OSError,ValueError,KeyError,AssertionError):pass
    finally:
        if cpu_original is not None:
            try:
                rc,text=s.shell('set -e\nsetprop '+cpu_property+' '+shlex.quote(cpu_original)+'\n'
                    '[ "$(getprop '+cpu_property+')" = '+shlex.quote(cpu_original)+' ]\n'
                    'printf "HVENC_CPU_RESTORED 1\\n"',timeout=3)
            except InterruptedError:
                rc,text,cpu_reset_interrupted=1,'',True
            except (OSError,ValueError):rc,text=1,''
            evidence['cpu_read_restored']=int(rc==0 and text.strip()=='HVENC_CPU_RESTORED 1')
        if remote is not None:
            evidence['cleanup']=int(s.shell('rm -r -- '+shlex.quote(remote),timeout=5)[0]==0)
        p=s.out/'hvenc-qualification.json';p.write_text(json.dumps(evidence,sort_keys=True)+'\n');p.chmod(0o600)
        ok=evidence['native_pass']==1 and evidence['host_mp4_ok']==1 and evidence['cleanup']==1 and evidence.get('cpu_read_restored',1)==1
        s.put('18_hardware_encoder',ok,**evidence)
        if not ok and s.results['A_stability']['status']!='NOT_RUN':
            previous=dict(s.results['A_stability']['evidence'],hvenc_qualification_failed=1)
            s.put('A_stability',False,**previous)
        if cpu_reset_interrupted:raise InterruptedError()
