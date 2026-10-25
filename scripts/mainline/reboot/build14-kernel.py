#!/usr/bin/env python3
"""Host-only INTEG14 kernel: native incremental link, no shared-cache writes.

Replay the established FP6 core additions over retained FIX11 native objects,
using INTEG13's exact configuration and export table. Recompile the deadline
worker for EINKDIAG. All unchanged objects are immutable symlink inputs.
"""
from pathlib import Path
import argparse,gzip,hashlib,importlib.util,json,os,shutil,subprocess as sp
HERE=Path(__file__).resolve().parent
MAIN=HERE.parent
ROOT=Path('/home/user/A9-build/mainline')

def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--base',type=Path,default=ROOT/'integ-20261024-13');ap.add_argument('--out',type=Path,default=ROOT/'integ14-host/kernel');a=ap.parse_args()
 k=load('integ14_native',MAIN/'fingerprint/build-fp4-kernel.py')
 k.OUT=a.out.resolve();k.K=k.OUT/'kernel-source';k.OBJ=k.OUT/'obj';k.BASE=a.base.resolve()
 k.TARGET_CONFIG=(k.BASE/'kernel.config').read_bytes()
 assert b'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' in k.TARGET_CONFIG
 assert b'CONFIG_SCSI_UFSHCD_A9_QUERY=y' in k.TARGET_CONFIG
 assert b'# CONFIG_RPMB is not set' in k.TARGET_CONFIG
 k.EXTRA_OBJECTS=['drivers/firmware/qcom/qcom_tzmem.o','init/a9_boot_deadline.o']
 k.OUT.mkdir(parents=True,exist_ok=True)
 assert not k.OBJ.exists(),'preserve partial builds for inspection'
 # Existing native recipe needs host compiler and standard tools for link helpers.
 tool_dirs=[]
 for pattern in ('*gcc-wrapper-14.3.0/bin','*gnumake*/bin','*coreutils*/bin','*gnused*/bin','*gnugrep*/bin','*gawk*/bin','*findutils*/bin','*diffutils*/bin','*bash-5*/bin'):
  tool_dirs.extend(str(p) for p in Path('/nix/store').glob(pattern) if 'aarch64' not in str(p))
 k.ENV['PATH']+=':'+':'.join(tool_dirs)
 k.prepare();k.extra_headers()
 k.write(k.K/'init/a9_boot_deadline.c',(MAIN/'boot-deadline/a9_boot_deadline-wdt.c').read_bytes())
 k.write(k.K/'include/linux/a9_boot_deadline.h',(MAIN/'boot-deadline/a9_boot_deadline-wdt.h').read_bytes())
 # Recompile the UFS core with the RPMB read-only guard; do not reuse the
 # pre-guard object while copying only new source/headers into the image record.
 for p in (k.CLEAN/'drivers/ufs/core').iterdir():
  if p.is_file() and (p.suffix in ('.c','.h') or p.name in ('Kconfig','Makefile')):
   k.write(k.K/'drivers/ufs/core'/p.name,p.read_bytes())
 for patch in ('0001-ufs-query-hook.patch','0002-rpmb-readonly-guard.patch'):
  k.run(['patch','--batch','--fuzz=0','-p1','-d',k.K,'-i',MAIN/'ufs-rpmb'/patch],patch.removesuffix('.patch'))
 for p in (MAIN/'ufs-rpmb/driver').iterdir():
  if p.is_file():k.write(k.K/'drivers/ufs/core'/p.name,p.read_bytes())
 k.EXTRA_OBJECTS.append('drivers/ufs/core/ufshcd.o')
 # Match GKI's existing CAP_SYS_NICE migration permission. Detach this source
 # from the immutable cache and ensure the linked image consumes its object.
 cgroup_patch=MAIN/'kernel/patches/0064-cgroup-v1-cap-sys-nice.patch'
 k.write(k.K/'kernel/cgroup/cgroup-v1.c',(k.CLEAN/'kernel/cgroup/cgroup-v1.c').read_bytes())
 k.run(['patch','--batch','--fuzz=0','-p1','-d',k.K,'-i',cgroup_patch], 'cgroup-nice-patch')
 k.EXTRA_OBJECTS.append('kernel/cgroup/cgroup-v1.o')
 # Restore the published starvation API, then remove PG-only decisions.
 for rel in ('drivers/regulator/fp9929a.c','include/linux/regulator/fp9929a.h'):
  k.write(k.K/rel,(k.CLEAN/rel).read_bytes())
 for patch in ('0057-regulator-fp9929a-read-only-VCOM-readback.patch','0059-regulator-fp9929a-starvation-recovery.patch','0065-regulator-fp9929a-stock-pg-telemetry.patch'):
  k.run(['patch','--batch','--fuzz=0','-p1','-d',k.K,'-i',MAIN/'kernel/patches'/patch],patch.removesuffix('.patch'))
 k.EXTRA_OBJECTS.append('drivers/regulator/fp9929a.o')
 k.link()
 assert k.exports(k.OUT/'vmlinux.symvers')==k.exports(k.BASE/'vmlinux.symvers')
 assert (k.OUT/'kernel.config').read_bytes()==(k.BASE/'kernel.config').read_bytes()
 raw=gzip.decompress((k.OUT/'Image.gz').read_bytes())
 assert b'A9_BOOT_DEADLINE EXPIRED' in raw and b'kmsg_dump' in (k.OBJ/'System.map').read_bytes()
 for marker in (b'A9_RPMB_GUARD mode=2',b'A9_RPMB_REGISTER phase=',b'A9_QSEE_LOG phase=2',b'A9_RPMB_REJECT reason=',b'A9_RPMB_RESULT command=',b'A9_RPMB_MAP phase='):
  assert marker in raw, marker
 sources={str(p.relative_to(k.K)):k.sha(p) for p in k.K.rglob('*') if p.is_file() and not p.is_symlink()}
 commit=sp.check_output(['git','-C',HERE.parents[2],'rev-parse','HEAD'],text=True).strip()
 (k.OUT/'integ14-kernel.json').write_text(json.dumps(dict(parent=str(k.BASE),source_commit=commit,rpmb_read_only=False,rpmb_guard_mode=2,rpmb_write_scope='learned-gatekeeper-and-stock-single-kmtb-entry',qsee_log_capture=True,configuration_unchanged=True,all_export_crcs_identical=True,native_link=True,cgroup_nice_patch_sha256=k.sha(cgroup_patch),pg_stock_patch_sha256=k.sha(MAIN/'kernel/patches/0065-regulator-fp9929a-stock-pg-telemetry.patch'),source_sha256=sources,deadline_source_sha256=k.sha(MAIN/'boot-deadline/a9_boot_deadline-wdt.c'),host_only=True,phone_access=False),indent=2)+'\n')
 print('INTEG14 kernel PASS: exact parent exports/config; native linker',flush=True)
if __name__=='__main__':main()
