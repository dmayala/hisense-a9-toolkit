#!/usr/bin/env python3
"""Every syscall the shipping static AArch64 musl listener can make must be allowed by the production filter,
unless it is documented as pre-filter or unreachable. Static scan of the real binary (qemu-user cannot enforce
seccomp, which is how a missing writev(2) survived to the hardware run and silenced every listener diagnostic).

  test-s1-listener-syscalls.py --cc <aarch64-musl-gcc> --objdump <aarch64-objdump>"""
import argparse,glob,re,subprocess as sp,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; S=ROOT/'scripts/mainline/sensors'
ap=argparse.ArgumentParser(); ap.add_argument('--cc',required=True); ap.add_argument('--objdump',required=True); a=ap.parse_args()
hdr=glob.glob('/nix/store/*linux-headers*/include/asm-generic/unistd.h')
assert hdr,'asm-generic unistd.h needed to name syscalls'
names={}
for l in open(hdr[0]):
    m=re.match(r'#define __NR(?:3264)?_(\w+)\s+(\d+)',l)
    if m: names[m[1]]=int(m[2])
sandbox=(S/'s1-registry-sandbox.c').read_text()
alias={'newfstatat':'fstatat'}
allowed={names[alias.get(n,n)] for n in re.findall(r'OK\(SYS_(\w+)\)',sandbox)}
allowed|={names[n] for n in ('ioctl','openat','fcntl','clone','mmap','mprotect','writev')}
assert 'SYS_writev' in sandbox and names['writev'] in allowed
# Called before the seccomp filter is installed (sandbox/main), or not reachable from the listener's paths.
EXEMPT={'chdir':'sandbox, before seccomp','fchdir':'sandbox, before seccomp','chroot':'sandbox, before seccomp',
 'prctl':'sandbox, before seccomp','setrlimit':'sandbox, before seccomp','prlimit64':'musl setrlimit, before seccomp',
 'umask':'main, before seccomp','capset':'sandbox, before seccomp','seccomp':'the filter itself','close_range':'main, before seccomp',
 'statfs':'musl statvfs, unused','gettimeofday':'musl clock fallback, unused','tkill':'musl abort/raise, fatal path only',
 'ppoll':'musl, unused','sched_setscheduler':'pthread explicit scheduling attr, unused','io_setup':'scan noise (generic wrapper)'}
with tempfile.TemporaryDirectory() as td:
    out=Path(td)/'listener'
    srcs=['s1-listener-main.c','s1-rpc.c','s1-registry-fs.c','s1-registry-sandbox.c']
    sp.run([a.cc,'-std=c11','-static','-pthread','-O2','-Wall','-Wextra','-Werror',*[str(S/x) for x in srcs],'-o',str(out)],check=True)
    dis=sp.run([a.objdump,'-d',str(out)],capture_output=True,text=True,check=True).stdout.splitlines()
used={}
for i,l in enumerate(dis):
    if re.search(r'\bsvc\s+#0x0',l):
        for j in range(i-1,max(i-14,0),-1):
            m=re.search(r'mov\s+x8, #(0x[0-9a-f]+|\d+)',dis[j])
            if m: used.setdefault(int(m[1],0),0); used[int(m[1],0)]+=1; break
rev={v:k for k,v in names.items()}
bad=[(n,rev.get(n,'?')) for n in sorted(used) if n not in allowed and rev.get(n) not in EXEMPT]
assert not bad,('syscalls reachable in the listener but denied by the filter and not documented',bad)
assert names['writev'] in used and names['writev'] in allowed
print('S1 LISTENER SYSCALL ALLOWLIST TEST PASS',len(used),'syscalls scanned')
