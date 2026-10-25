#!/usr/bin/env python3
"""Private MSM rebuild retaining published DPU fixes plus EINKDIAG trace."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess as sp
HERE=Path(__file__).resolve().parent;MAIN=HERE.parent;ROOT=Path('/home/user/A9-build/mainline')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--base',type=Path,default=ROOT/'integ-20261024-13');ap.add_argument('--kernel',type=Path,default=ROOT/'integ14-host/kernel');ap.add_argument('--out',type=Path,default=ROOT/'integ14-host/msm');a=ap.parse_args();O=a.out.resolve();O.mkdir(parents=True,exist_ok=True)
 S=O/'source';M=S/'drivers/gpu/drm/msm';J=O/'obj';SRC=ROOT/'linux-7.2.10-gpu';C=ROOT/'integrated-20261021n-d/msm-build2/source/drivers/gpu/drm/msm';B=ROOT/'6r3-host/q6/obj'
 assert not M.exists() and not J.exists(),'preserve partial outputs'
 env=dict(os.environ);paths=[]
 for pattern in ('*gcc-wrapper-14.3.0/bin','*gnumake*/bin','*coreutils*/bin','*gnused*/bin','*gnugrep*/bin','*gawk*/bin','*findutils*/bin','*diffutils*/bin','*bash-5*/bin','*python3-3.13*/bin','*binutils*/bin'):
  paths.extend(str(p) for p in Path('/nix/store').glob(pattern) if 'aarch64' not in str(p) or 'wrapper' in str(p))
 env['PATH']=':'.join(paths)+':'+env['PATH']
 def run(cmd,n):
  with (O/(n+'.log')).open('w') as f:sp.run(list(map(str,cmd)),env=env,stdout=f,stderr=sp.STDOUT,check=True)
  print('INTEG14_MSM '+n,flush=True)
 shutil.copytree(SRC/'drivers/gpu/drm/msm',M)
 # Preserve the published module's materialized fixes; dangling cache aliases
 # refer to the same earlier MSM tree, retained as linux-7.2.10-gpu.
 overlays={}
 for p in C.rglob('*'):
  if p.is_file() and not p.is_symlink() and p.suffix in ('.c','.h') and 'generated' not in p.relative_to(C).parts and not p.name.endswith('.mod.c'):
   rel=p.relative_to(C);shutil.copyfile(p,M/rel);overlays[str(rel)]=sha(p)
 for h in (SRC/'drivers/gpu/drm').glob('*.h'):(M.parent/h.name).symlink_to(h)
 for patch in ('0060-drm-msm-latch-DPU-completion-errors.patch','0059-drm-msm-dpu-observe-flush-without-IRQ-wakeup.patch'):
  cmd=['patch','--batch','--fuzz=0','-d',S,'-p1','-i',MAIN/'kernel/patches'/patch]
  reverse=sp.run(list(map(str,cmd+['--dry-run','-R'])),capture_output=True,env=env)
  if reverse.returncode:run(cmd,patch)
 run(['patch','--batch','--fuzz=0','-d',S,'-p1','-i',MAIN/'kernel/patches/0063-drm-msm-a9-boot-probe-trace.patch'],'trace-patch')
 J.mkdir()
 for d in ('include','scripts','arch/arm64/include'):shutil.copytree(B/d,J/d,symlinks=True)
 for n in ('.config','Makefile'):shutil.copyfile(B/n,J/n)
 assert (J/'.config').read_bytes()==(a.base/'kernel.config').read_bytes()
 shutil.copyfile(a.kernel/'Module.symvers',J/'Module.symvers');(J/'source').symlink_to(SRC)
 shutil.copytree(C/'generated',M/'generated',ignore=shutil.ignore_patterns('*.o','*.cmd','*.d'))
 run(['make','-C',SRC,'O='+str(J),'M='+str(M),'ARCH=arm64','CROSS_COMPILE=aarch64-unknown-linux-gnu-','HOSTCC=gcc','LOCALVERSION=+','-j4','modules'],'build')
 shutil.copyfile(M/'msm.ko',O/'msm.ko')
 run(['aarch64-unknown-linux-gnu-strip','--strip-debug',O/'msm.ko'],'strip')
 expected={a[1]:a[0] for l in (a.base/'Module.symvers').read_text().splitlines() if len(a:=l.split())>=4 and Path(a[2]).name=='msm'}
 actual={a[1]:a[0] for l in (M/'Module.symvers').read_text().splitlines() if len(a:=l.split())>=4}
 assert expected and actual==expected,'MSM exports changed'
 (O/'build.json').write_text(json.dumps(dict(module_sha256=sha(O/'msm.ko'),source_tree=str(SRC),published_materialized_overlays=overlays,MSM_export_crcs_identical=True,trace_patch_sha256=sha(MAIN/'kernel/patches/0063-drm-msm-a9-boot-probe-trace.patch')),indent=2)+'\n')
if __name__=='__main__':main()
