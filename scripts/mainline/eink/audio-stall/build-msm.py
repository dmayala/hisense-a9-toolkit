#!/usr/bin/env python3
"""Host-only N-d MSM rebuild with flush observation and existing hard-error latch.

No kernel Image rebuild; shared source/objects are read-only inputs. Borrow the
prepared ancestor's generated headers, and check imports/exports against N.
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess as sp
R=Path(__file__).resolve().parents[4]
B=Path('/home/user/A9-build/mainline/suspend-pmtest-kernel-20261020')
N=Path('/home/user/A9-build/mainline/integrated-20261021n')
ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();O=a.out.resolve();O.mkdir(parents=True,exist_ok=True)
os.umask(0o077)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
env=dict(os.environ);env['PATH']=':'.join(json.loads((N.parent/'demo-integrated-20261019/source.json').read_text())['tools'])+':'+env['PATH']
def run(args,name):
 with (O/(name+'.log')).open('w') as f:p=sp.run(list(map(str,args)),env=env,stdout=f,stderr=sp.STDOUT)
 (O/(name+'.rc')).write_text(str(p.returncode)+'\n');assert p.returncode==0,name
assert not sp.check_output(['git','-C',str(R),'status','--porcelain']).strip()
assert shutil.disk_usage(O).free>1024**3 # module-only, bounded private staging
before={str(B/'obj'/n):sha(B/'obj'/n) for n in ('.config','Module.symvers','arch/arm64/boot/Image.gz')}
S=O/'source';M=S/'drivers/gpu/drm/msm';J=O/'obj';M.parent.mkdir(parents=True,exist_ok=True);assert not M.exists() and not J.exists();J.mkdir()
run(['cp','-as',B/'kernel/drivers/gpu/drm/msm',M],'source-copy')
# MSM snapshot headers include DRM-private headers relative to this subtree.
for header in (B/'kernel/drivers/gpu/drm').glob('*.h'):
 (M.parent/header.name).symlink_to(header.resolve())
for rel in ('msm_atomic.c','msm_kms.h','disp/dpu1/dpu_kms.c','disp/dpu1/dpu_encoder_phys_vid.c'):
 p=M/rel;d=p.read_bytes();p.unlink();p.write_bytes(d)
for patch in ('0060-drm-msm-latch-DPU-completion-errors.patch','0059-drm-msm-dpu-observe-flush-without-IRQ-wakeup.patch'):
 run(['patch','--batch','--fuzz=0','-d',M,'-p5','-i',R/'scripts/mainline/kernel/patches'/patch],patch)
for d in ('include','scripts','arch/arm64/include'):shutil.copytree(B/'obj'/d,J/d,symlinks=True)
for n in ('.config','Module.symvers','Makefile'):shutil.copyfile(B/'obj'/n,J/n)
(J/'source').symlink_to(B/'kernel')
# Generated register headers are cached inputs; build can regenerate privately.
shutil.copytree(B/'obj/drivers/gpu/drm/msm/generated',M/'generated',symlinks=False)
run(['make','-C',B/'kernel','O='+str(J),'M='+str(M),'ARCH=arm64','CROSS_COMPILE=aarch64-unknown-linux-gnu-','HOSTCC=gcc','LOCALVERSION=+','-j4','modules'],'msm-build')
module=O/'msm.ko';shutil.copyfile(M/'msm.ko',module)
run(['aarch64-unknown-linux-gnu-strip','--strip-debug',module],'strip')
# Existing N MSM export declarations and every imported CRC must match.
def symbols(p):return {s.split()[1]:s.split()[0] for s in p.read_text().splitlines() if len(s.split())>=4}
old={s.split()[1]:s.split()[0] for s in (N/'Module.symvers').read_text().splitlines() if len(s.split())>=4 and s.split()[2].endswith('/msm')}
new=symbols(M/'Module.symvers');assert old and new==old,('MSM exports changed',old.keys()^new.keys())
def versions(p):
 text=sp.check_output(['modprobe','--dump-modversions',str(p)],env=env,text=True)
 return {n:int(c,16) for c,n in (s.split() for s in text.splitlines())}
ref=N/'msm.ko';assert versions(module)==versions(ref),'MSM imported CRC set changed'
vm=lambda p:sp.check_output(['modinfo','-F','vermagic',str(p)],env=env,text=True).strip()
assert vm(module)==vm(ref)
assert all(sha(Path(p))==h for p,h in before.items())
run(['python3','-I',R/'scripts/mainline/eink/stall-recovery/test-msm-latch.py',S],'msm-latch-test')
run(['python3','-I',R/'scripts/mainline/eink/audio-stall/test-path.py','--kernel',B/'kernel','--patched',S,'--out',O/'host-tests'],'path-test')
result={'source_commit':sp.check_output(['git','-C',str(R),'rev-parse','HEAD'],text=True).strip(),'module_sha256':sha(module),'module_bytes':module.stat().st_size,'vermagic':vm(module),'all_import_crcs_identical_to_N':len(versions(module)),'all_export_crcs_identical_to_N':len(old),'borrowed_inputs_unchanged':before,'boot_rebuild_required':False,'poll_predicate':'CTL flush register == 0','poll_sleep_us':1000,'DPU_timeout_us':50000,'A9_deadline_unchanged':True}
(O/'build-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
