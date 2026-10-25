#!/usr/bin/env python3
"""Host-only namespaces/seccomp probes; never executes an ARM64 daemon."""
from pathlib import Path
import os
import resource
import shutil
import socket
import subprocess
import tempfile
import argparse

REPO=Path(__file__).resolve().parents[2]
HERE=REPO/'scripts/mainline/wifi'
NDK=Path(os.environ.get('ANDROID_NDK_HOME','/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973'))
cc=NDK/'toolchains/llvm/prebuilt/linux-x86_64/bin/x86_64-linux-android30-clang'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--evidence',type=Path)
args=parser.parse_args()
if os.geteuid()!=0:
    raise SystemExit('This host-only test requires root for an actual noswap tmpfs: Linux refuses noswap in user namespaces. No weaker mount fallback is permitted.')
with tempfile.TemporaryDirectory(prefix='a9-wifi-sandbox-') as d:
    root=Path(d);jail=root/'jail';jail.mkdir(mode=0o700)
    helper=root/'helper';probe=root/'probe'
    common=[str(cc),'-static','-O2','-Wall','-Wextra','-Werror','-DA9_SANDBOX_HOST_TEST','-I'+str(HERE)]
    subprocess.run(common+[str(HERE/'sandbox.c'),'-o',str(helper)],check=True)
    subprocess.run(common+[str(Path(__file__).with_name('sandbox-probe.c')),str(HERE/'postexec.c'),'-o',str(probe)],check=True)
    # No source or device descriptor: inject a harmless own directory at150,
    # then lower the soft limit so the historical cleanup bug would leak it.
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    os.dup2(fd,150);os.close(fd)
    def limits():resource.setrlimit(resource.RLIMIT_NOFILE,(80,resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
    cmd=['unshare','--mount',str(helper),'--test',str(jail),str(probe)]
    trace=root/'trace'
    trace_tool=shutil.which('strace')
    run=(['strace','-f','-o',str(trace),'-e','trace=%file,%desc,prctl,capset,mount,umount2,pivot_root']+cmd) if trace_tool else cmd
    r=subprocess.run(run,pass_fds=(150,),preexec_fn=limits,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    os.close(150)
    if r.returncode:
        tail='\n'.join(trace.read_text().splitlines()[-45:]) if trace.exists() else ''
        raise RuntimeError(f'sandbox probe failed ({r.returncode}):\n'+r.stdout+r.stderr+'\n'+tail)
    print(r.stdout.strip())
    if trace_tool:
        text=trace.read_text()
        assert 'pivot_root(' in text and 'PR_SET_SECCOMP' in text
        assert 'fake-modemst1' in text and 'EPERM' in text
        if args.evidence:
            args.evidence.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(trace,args.evidence/'sandbox.strace')
            (args.evidence/'sandbox.stdout').write_text(r.stdout)
        print('strace: private root and actual default-deny syscall results recorded')
    a,b=socket.socketpair()
    bad=subprocess.run(cmd,stdout=b.fileno(),stderr=subprocess.PIPE,text=True)
    a.close();b.close()
    assert bad.returncode!=0 and 'logs must be write-only pipes' in bad.stderr
    print('sandbox: inherited Unix log socket rejected before exec')
