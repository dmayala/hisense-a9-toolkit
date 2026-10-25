#!/usr/bin/env python3
"""Production logger identity vs actual Android-shell crypto lookup, host only.

Usage: HOSTCC=cc python3 -I test-kernel-log-identity.py OUTPUT_DIR
"""
from pathlib import Path
import os,json,subprocess as sp,tempfile,importlib.util,re,sys
repo=Path(__file__).resolve().parents[2];out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
env=dict(os.environ)
cc=os.environ.get('HOSTCC','gcc')
source=out/'header.c';source.write_text('#define main logger_main\n#include "'+str(repo/'scripts/mainline/boot-deadline/logger.c')+'"\n#undef main\nint main(void){early_len=123;early_overruns=0;write_info_header(1,"boot-0000000001");return 0;}\n')
sp.run([cc,'-O2','-Wall','-Wextra','-Wno-unused-parameter','-Werror',str(source),'-o',str(out/'header')],env=env,check=True)
header=sp.check_output([out/'header'],text=True)
pid=re.search(r'pid=(\d+)',header)[1]
spec=importlib.util.spec_from_file_location('onb',repo/'scripts/mainline/acceptance/onb.py');onb=importlib.util.module_from_spec(spec);spec.loader.exec_module(onb)
markers='ICE: wrapped keys selected\nAndroid wrappedkey_v0 add raw_size=68\nAES-256-XTS using blk-crypto (native)\n'
sh='/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sh'
results=[]
for mode in ['old-extended','fixed','wrong-boot']:
 with tempfile.TemporaryDirectory(prefix='fbe29r-header-') as tmp:
  d=Path(tmp);cmd=d/'bin';cmd.mkdir();proc=d/'proc'
  private=proc/pid/'root/kdebuginfo/mainline';boot=private/'boot-0000000001';boot.mkdir(parents=True)
  (private/'counter').write_text('1\n');(boot/'kernel.log').write_text(markers)
  h=header if mode!='old-extended' else header.splitlines()[0]+' early_bytes=123 early_overruns=0\n'
  (boot/'info.txt').write_text(h+'boot_id='+('wrong' if mode=='wrong-boot' else 'fixture-current')+'\n')
  bid=proc/'sys/kernel/random/boot_id';bid.parent.mkdir(parents=True);bid.write_text('fixture-current\n')
  for n,body in {'pidof':'echo '+pid,'dmesg':'exit 0','timeout':'shift; exec "$@"'}.items():
   f=cmd/n;f.write_text('#!/bin/sh\n'+body+'\n');f.chmod(0o755)
  text=onb.KERNEL_LOG.replace('/proc/',str(proc)+'/')
  text=text.replace('cat /kdebuginfo/mainline/counter','cat '+str(d/'absent/counter')).replace("printf '/kdebuginfo/mainline/boot-%010d'", "printf '"+str(d)+"/absent/boot-%010d'")
  p=sp.run([sh,'-c',text+'\ncurrent_kernel\n'],env=dict(env,PATH=str(cmd)+':'+env['PATH']),capture_output=True,text=True,timeout=10)
  assert p.returncode==0,p.stderr
  if mode=='fixed':assert p.stdout==markers,(mode,p.stdout,p.stderr)
  else:assert p.stdout=='',(mode,p.stdout,p.stderr)
  results.append({'case':mode,'retained_crypto_markers':bool(p.stdout)})
(out/'header-replay.json').write_text(json.dumps({'production_logger_header':True,'actual_android_shell':True,'cases':results,'phone_access':False},indent=2)+'\n')
print('PASS: actual Android shell reproduces lost crypto with old extended header; fixed production header restores all3; wrong-boot refuses')
