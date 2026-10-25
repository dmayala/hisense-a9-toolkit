#!/usr/bin/env python3
"""Host-only actual-production helper tests. Never opens a real QoS/trace node."""
import json, os, signal, subprocess as sp, sys, tempfile, time
from pathlib import Path
SRC=Path(__file__).resolve().parent
OUT=Path(sys.argv[1]).resolve()
QEMU='/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
CC='/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin/gcc'
CROSS='/nix/store/4vpsm867635vxx8cxyp8kxr1jpisqqr5-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc'
results=[]
def record(name):
 results.append(name); print('PASS',name,flush=True)
def line(t,event,payload='',cpu=0):
 return f'         a9_eink-41 [{cpu:03d}] d..2 {t:.9f}: {event}: {payload}\n'
def phase(start,age):
 return ''.join([line(start-0.1,'msm_atomic_commit_tail_start'),line(start-0.09,'msm_atomic_wait_flush_start'),line(start-0.04,'msm_atomic_wait_flush_finish'),line(start-0.001,'msm_atomic_flush_commit'),line(start,'msm_atomic_wait_flush_start'),line(start+age,'msm_atomic_wait_flush_finish')])
with tempfile.TemporaryDirectory(prefix='a9-phase5-test-') as tmp:
 root=Path(tmp)
 # Real parser under both native libc and static ARM64 musl/QEMU.
 fixtures={'phase3-only-and-fast-phase5':(phase(10,0.012),False,0),
 'phase5-63ms':(phase(10,0.063),True,63000),
 'threshold-30ms':(phase(10,0.030),True,30000),
 'below-threshold':(phase(10,0.0299),False,0),
 'crosscpu-short-end-cancels':(phase(10,0.012).replace(line(10.012,'msm_atomic_wait_flush_finish'),line(10.050,'irq_handler_entry',cpu=1)+line(10.012,'msm_atomic_wait_flush_finish')),False,0),
 'backlog-fast-repeated':(''.join(phase(10+i,0.01) for i in range(100)),False,0),
 'preserve-flush-irq-sched':(phase(10,0.063).replace(line(10.063,'msm_atomic_wait_flush_finish'),line(10.020,'a9p5_9_flush_value','value=0x1')+line(10.035,'a9p5_9_vsync_in')+line(10.040,'sched_switch','prev_pid=41 next_pid=9')+line(10.062,'a9p5_9_flush_value','value=0x0')+line(10.063,'msm_atomic_wait_flush_finish')),True,63000)}
 for arch,prefix in [('native',[]),('arm64',[QEMU])]:
  exe=OUT/'host/phase5-watch' if arch=='native' else OUT/'phase5-watch'
  for name,(trace,trigger,age) in fixtures.items():
   f=root/(name+'.txt');f.write_text(trace);dest=root/(arch+'-'+name);dest.mkdir()
   run=sp.run([*prefix,str(exe),'replay',str(f),str(dest),'1','30000'],capture_output=True,text=True,timeout=10)
   assert run.returncode==0,(name,run.stderr)
   r=json.loads((dest/'trigger.json').read_text());assert r['triggered']==trigger,(name,r)
   assert r['completed_age_us']==age,(name,r)
   if name=='preserve-flush-irq-sched': assert 'value=0x0' in (dest/'trace-private.txt').read_text()
  record(arch+': seven event-timestamp phase/threshold/backlog/context fixtures')
 # Wrap the actual product IO at link time. No alternate production test path.
 shim=root/'qos-shim.c';shim.write_text(r'''
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
static const int fake=91;
static const char *mode(void){const char *p=getenv("QOS_CASE");return p?p:"ok";}
int __real_open(const char *,int,...);
ssize_t __real_write(int,const void *,size_t);
ssize_t __real_read(int,void *,size_t);
int __real_close(int);
static void logop(const char *s){const char *p=getenv("QOS_LOG");if(!p)_exit(80);int fd=__real_open(p,O_WRONLY|O_APPEND|O_CREAT,0600);if(fd<0)_exit(81);__real_write(fd,s,strlen(s));__real_close(fd);}
int __wrap_open(const char *p,int flags,...){if(strcmp(p,"/dev/cpu_dma_latency"))_exit(82);if((flags&(O_CLOEXEC|O_NOFOLLOW|O_RDWR))!=(O_CLOEXEC|O_NOFOLLOW|O_RDWR))_exit(83);if(!strcmp(mode(),"denied")){errno=EACCES;return -1;}logop("open\n");return fake;}
int __wrap_fstat(int fd,struct stat *st){if(fd!=fake)_exit(84);memset(st,0,sizeof(*st));st->st_mode=!strcmp(mode(),"nonchar")?S_IFREG:S_IFCHR;return 0;}
ssize_t __wrap_write(int fd,const void *p,size_t n){if(fd!=fake)return __real_write(fd,p,n);if(n!=4||*(const int32_t *)p!=0)_exit(85);logop("write-four-binary-zero\n");return !strcmp(mode(),"shortwrite")?2:4;}
ssize_t __wrap_read(int fd,void *p,size_t n){if(fd!=fake)return __real_read(fd,p,n);if(n!=4)_exit(86);*(int32_t *)p=!strcmp(mode(),"badreadback")?100:0;logop("read-aggregate\n");return 4;}
int __wrap_close(int fd){if(fd!=fake)return __real_close(fd);logop("close\n");return 0;}
''')
 for arch,cc,prefix,flags in [('native',CC,[],[]),('arm64',CROSS,[QEMU],['-static'])]:
  exe=root/('qos-'+arch);cmd=[cc,'-O2','-Wall','-Wextra','-Werror',*flags,str(SRC/'latency-hold.c'),str(shim),*[f'-Wl,--wrap={s}' for s in ['open','fstat','write','read','close']],'-o',str(exe)]
  sp.run(cmd,check=True,capture_output=True)
  for case in ['ok','denied','nonchar','shortwrite','badreadback']:
   log=root/(arch+case+'.log');env=dict(os.environ,QOS_CASE=case,QOS_LOG=str(log))
   run=sp.run([*prefix,str(exe),'1'],env=env,capture_output=True,text=True,timeout=5)
   assert run.returncode==(0 if case=='ok' else 1),(arch,case,run.stderr)
   text=log.read_text() if log.exists() else ''
   assert ('close\n' in text)==(case!='denied'),(case,text)
   if case=='ok':assert 'READY' in run.stdout and 'RELEASED' in run.stdout and text=='open\nwrite-four-binary-zero\nread-aggregate\nclose\n'
  log=root/(arch+'signal.log');env=dict(os.environ,QOS_CASE='ok',QOS_LOG=str(log))
  p=sp.Popen([*prefix,str(exe),'60'],env=env,stdout=sp.PIPE,stderr=sp.PIPE,text=True)
  assert 'READY' in p.stdout.readline();assert 'close' not in log.read_text();p.send_signal(signal.SIGTERM);stdout,stderr=p.communicate(timeout=5);assert p.returncode==0 and 'RELEASED' in stdout and log.read_text().endswith('close\n')
  actual=OUT/'host/latency-hold' if arch=='native' else OUT/'latency-hold'
  for arg in ['0','901','abc','1x','-1']:
   r=sp.run([*prefix,str(actual),arg],capture_output=True,text=True);assert r.returncode==2
  record(arch+': actual QoS expiry/signal/FD ownership, denied/nonchar/short IO/readback and invalid argv')
 # Exercise actual nonblocking capture/stop path through a fixture FIFO.
 for mode in ['slow','signal']:
  inst=root/('live-'+mode);inst.mkdir();(inst/'tracing_on').write_text('0\n');os.mkfifo(inst/'trace_pipe')
  pipe=os.open(inst/'trace_pipe',os.O_RDWR|os.O_NONBLOCK);dest=root/('live-out-'+mode);dest.mkdir()
  p=sp.Popen([str(OUT/'host/phase5-watch'),'capture',str(inst),str(dest),'2','30000'],stdout=sp.PIPE,stderr=sp.PIPE,text=True)
  assert 'READY' in p.stdout.readline()
  if mode=='slow':
   # Feed old fast phases to model reader backlog, then a real slow phase.
   os.write(pipe,phase(1,.012).encode());now=time.monotonic()
   os.write(pipe,(line(now-.001,'msm_atomic_commit_tail_start')+line(now-.0005,'msm_atomic_flush_commit')+line(now,'msm_atomic_wait_flush_start')).encode())
   time.sleep(.04);os.write(pipe,line(now+.04,'a9p5_3_flush_value','value=0x1').encode())
   time.sleep(.025);os.write(pipe,line(now+.063,'msm_atomic_wait_flush_finish').encode())
  else:p.send_signal(signal.SIGTERM)
  stdout,stderr=p.communicate(timeout=4);os.close(pipe)
  assert p.returncode==0,(mode,stderr)
  assert (inst/'tracing_on').read_text().splitlines()[-1]=='0'
  r=json.loads((dest/'trigger.json').read_text());assert r['triggered']==(mode=='slow'),r
 record('native: real FIFO capture delayed-reader immunity, slow trigger and SIGTERM stop')
 # Bounded ring retains newest complete lines, never more than8MiB.
 f=root/'large.txt';f.write_text(line(1,'ignored','x'*200)*45000)
 d=root/'large';d.mkdir();sp.run([str(OUT/'host/phase5-watch'),'replay',str(f),str(d),'1','30000'],check=True,capture_output=True)
 r=json.loads((d/'trigger.json').read_text());assert r['recorder_overwritten_bytes']>0 and (d/'trace-private.txt').stat().st_size<=8*1024*1024
 record('native: 8MiB recording bound/ring wrap')
(OUT/'native-arm64-tests.json').write_text(json.dumps({'checks':results,'real_phone_access':False},indent=2)+'\n')
