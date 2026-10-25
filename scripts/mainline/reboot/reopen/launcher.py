#!/usr/bin/env python3
"""One owned mainline-B -> existing stock-A same-key TEE control. No raw output."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import shlex
import signal
import struct
import subprocess as sp
import sys
import tempfile

HERE=Path(__file__).resolve().parent
IMAGE=Path(os.environ.get('A9_REOPEN_IMAGE',HERE.parents[2])).resolve()
NAMES=('version','keymaster_key_blob','secdiscardable','encrypted_key','stretching')
TEE=re.compile(r'A9_REOPEN_TEE tee=[01] chars_rc=-?[0-9]{1,8} begin_rc=-?[0-9]{1,8} finish_rc=-?[0-9]{1,8} export_rc=-?[0-9]{1,8} wrapped_bytes=[0-9]{1,5}')
RESULT=re.compile(r'(?:PASS|FAIL) stock_reopen=[01] wrapped_bytes=[0-9]{1,5}')


def adb(*args,input=None,timeout=30):
    return sp.run([os.environ['ADB'],*args],input=input,stdout=sp.PIPE,stderr=sp.PIPE,timeout=timeout)


def encode(files):
    if files['version']!=b'1' or files.get('stretching',b'nopassword') not in (b'nopassword',b'none'):
        raise ValueError('version_or_stretching')
    k,s,e=(files[n] for n in NAMES[1:4])
    if not k.startswith(b'pKMblob\0') or not 8<len(k)<=65536 or len(s)!=16384 or not 28<len(e)<=65536:
        raise ValueError('input_format')
    return struct.pack('<4I',0x31524246,len(k),len(s),len(e))+k+s+e


def extract(text):
    # This private channel is never saved in the log directory or printed.
    files={}
    pattern=r'^A9_REOPEN_BEGIN ([a-z_]+)\n([A-Za-z0-9+/=\n]+?)\nA9_REOPEN_END\n'
    for match in re.finditer(pattern,text.replace('\r',''),re.M):
        name=match[1]
        if name not in NAMES or name in files:raise ValueError('unexpected_input')
        files[name]=base64.b64decode(match[2].replace('\n',''),validate=True)
    encode(files)
    return files


def shell_read(directory):
    # Fixed paths and file names only; no private values enter the script text.
    return '''
set -e
for name in version keymaster_key_blob secdiscardable encrypted_key stretching; do
    file="'''+directory+'''/$name"
    if [ "$name" = stretching ] && [ ! -e "$file" ]; then continue; fi
    [ -f "$file" ] && [ ! -L "$file" ]
    size=$(stat -c %s "$file")
    [ "$size" -le 65536 ]
    printf 'A9_REOPEN_BEGIN %s\\n' "$name"
    base64 "$file"
    printf '\\nA9_REOPEN_END\\n'
done
'''


def capture(before):
    ram=Path(os.environ['A9_REOPEN_RAM'])
    if not ram.is_dir() or ram.stat().st_mode & 0o077:return 2
    if before:
        if os.environ.get('A9_BTRY_IN_SETUP')!='1':return 2
        if adb('get-state').stdout.strip()!=b'recovery':return 2
        source='''exec 2>/dev/null
set -e
[ "$(id -u)" = 0 ]
[ "$(getprop ro.boot.slot_suffix)" = _a ]
mkdir /tmp/a9-reopen-metadata
trap 'umount /tmp/a9-reopen-metadata >/dev/null 2>&1; rmdir /tmp/a9-reopen-metadata' EXIT
mount -t ext4 -o ro,noload /dev/block/by-name/metadata /tmp/a9-reopen-metadata
'''+shell_read('/tmp/a9-reopen-metadata/vold/metadata_encryption/key')
        r=adb('exec-out','sh -s',input=source.encode(),timeout=35)
        if r.returncode:return 2
        files=extract(r.stdout.decode('ascii'))
        packet=encode(files)
        (ram/'before.bin').write_bytes(packet);(ram/'before.bin').chmod(0o600)
        print('PASS reopen_snapshot=1 metadata_readonly=1',flush=True)
    else:
        if os.environ.get('A9_BTRY_IN_HOOK')!='1':return 2
        helper=HERE.parent.parent/'boot-deadline/onb-root.py'
        r=sp.run([sys.executable,'-B',helper],input=shell_read('/metadata/vold/metadata_encryption/key'),text=True,
                 stdout=sp.PIPE,stderr=sp.PIPE,timeout=80)
        if r.returncode:return 2
        packet=encode(extract(r.stdout))
        unchanged=packet==(ram/'before.bin').read_bytes()
        (ram/'same').write_text('1' if unchanged else '0')
        print(f'{"PASS" if unchanged else "FAIL"} same_key_inputs={int(unchanged)}',flush=True)
        # Useful regular INTEG13 diagnostics; fixed numeric protocol only.
        sp.run([sys.executable,'-B',HERE.parent/'fbe-onb.py'],check=False)
        return 0 if unchanged else 1
    return 0


def clean_a(remote):
    # Remove only this invocation's newly created staging directory.
    try:adb('shell','su -c '+shlex.quote('rm -rf '+remote),timeout=15)
    except (OSError,sp.TimeoutExpired):pass


def stock_probe(packet,log):
    state=adb('shell','getprop ro.boot.slot_suffix; getprop sys.boot_completed; uname -r')
    lines=state.stdout.replace(b'\r',b'').splitlines()
    if state.returncode or len(lines)!=3 or lines[:2]!=[b'_a',b'1'] or not lines[2].startswith(b'4.19.'):
        return ['FAIL stock_context=0'],2
    check='id -u; sha256sum /vendor/lib64/libqtikeymaster4.so'
    checked=adb('shell','su -c '+shlex.quote(check))
    fields=checked.stdout.replace(b'\r',b'').splitlines()
    want=b'beb923d3e09579bbf54360a55fa19af3b4a8ee5be9446d9798199a909d46b274'
    if checked.returncode or len(fields)!=2 or fields[0]!=b'0' or fields[1].split()[:1]!=[want]:
        return ['FAIL stock_context=0'],2
    remote='/data/local/tmp/a9-reopen-control'
    made=adb('shell','umask 077; mkdir '+remote)
    if made.returncode:return ['FAIL stock_staging=0'],2
    try:
        pushed=adb('push',str(IMAGE/'reopen-client')+'/.',remote,timeout=60)
        if pushed.returncode:return ['FAIL stock_staging=0'],2
        # Recheck every bundled executable/library before execution, without
        # printing its device path, checksum or any private transaction output.
        command='cd '+remote+' && sha256sum -c SHA256SUMS >/dev/null && chmod 700 a9-fbe-reopen && exec /system/bin/timeout -s KILL 20 /system/bin/env LD_LIBRARY_PATH='+remote+'/lib64 ./a9-fbe-reopen'
        run=adb('exec-out','su -c '+shlex.quote(command),input=packet,timeout=30)
        public=[s for s in run.stdout.decode(errors='replace').replace('\r','').splitlines() if TEE.fullmatch(s) or RESULT.fullmatch(s) or s=='FAIL reopen_input=0']
        log.write_text('\n'.join(public)+'\n');log.chmod(0o600)
        # A transport failure/missing record never becomes a crypto result.
        if len([s for s in public if TEE.fullmatch(s)])!=1 or len([s for s in public if RESULT.fullmatch(s)])!=1:
            return public+['FAIL stock_transport=0'],2
        passed=run.returncode==0 and 'PASS stock_reopen=1 wrapped_bytes=68' in public
        return public,0 if passed else 1
    finally:clean_a(remote)


def main():
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    if len(sys.argv)==2 and sys.argv[1] in ('before-b','on-b'):
        try:return capture(sys.argv[1]=='before-b')
        except (OSError,ValueError,KeyError,UnicodeError,sp.TimeoutExpired):
            print('FAIL reopen_snapshot=0',flush=True);return 2
    if len(sys.argv)!=1:return 2
    # The shell launcher resolves the registry privately and owns the USB lock.
    if os.environ.get('A9_USB_LOCKED')!='1':return 2
    start=adb('shell','getprop ro.boot.slot_suffix; getprop sys.boot_completed')
    if start.returncode or start.stdout.replace(b'\r',b'').strip()!=b'_a\n1':
        print('FAIL start_slot_A=0');return 2
    manifest=json.loads((IMAGE/'reopen-manifest.json').read_text())
    for name,digest in manifest['sha256'].items():
        with (IMAGE/name).open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=digest:print('FAIL bundled_inputs=0');return 2
    out=Path(os.environ.get('A9_REOPEN_LOGS','/home/user/A9-build/mainline/reopen-run-'+__import__('time').strftime('%Y%m%dT%H%M%SZ',__import__('time').gmtime())))
    out.mkdir(mode=0o700,exist_ok=False)
    # Retained encrypted inputs/secdiscardable live only in a private host RAM
    # directory and the probe's stdin/memory. They never enter the log directory.
    with tempfile.TemporaryDirectory(prefix='a9-reopen-',dir='/dev/shm') as temporary:
        env=dict(os.environ,A9_REOPEN_RAM=temporary,A9_REOPEN_IMAGE=str(IMAGE),A9_BTRY_LOGS=str(out),
                 A9_SECILC=str(IMAGE/'runtime/tools/bin/secilc'),A9_BTRY_FORMAT='0',A9_BTRY_HOLD='90',
                 A9_BTRY_ONB=str(IMAGE/'reopen-on-b.sh'),A9_BTRY_BEFORE_B=str(IMAGE/'reopen-before-b.sh'))
        with (out/'b-try.log').open('wb') as log:
            child=sp.Popen([str(IMAGE/'runtime/boot-deadline/b-try.sh'),str(IMAGE/'boot.img'),str(IMAGE/'vendor.img')],env=env,stdout=log,stderr=sp.STDOUT)
            interrupted=False
            def stop(signum,frame):
                nonlocal interrupted
                if interrupted:return
                interrupted=True
                # Let the existing owner's EXIT trap return A and finish; never
                # kill its cleanup group or launch a competing return operation.
                child.send_signal(signal.SIGTERM)
            old_int=signal.signal(signal.SIGINT,stop);old_term=signal.signal(signal.SIGTERM,stop)
            try:child.wait()
            finally:signal.signal(signal.SIGINT,old_int);signal.signal(signal.SIGTERM,old_term)
        same=Path(temporary,'same')
        b_same=same.is_file() and same.read_text()=='1'
        repaired=(out/'return-a-repair.log').exists()
        a=adb('shell','getprop ro.boot.slot_suffix; getprop sys.boot_completed')
        returned=a.returncode==0 and a.stdout.replace(b'\r',b'').strip()==b'_a\n1'
        public=[f'{"PASS" if returned else "FAIL"} slot_A={int(returned)}',f'{"PASS" if b_same else "FAIL"} same_key_inputs={int(b_same)}']
        rc=2
        if returned and b_same and not repaired and not interrupted:
            try:probed,rc=stock_probe(Path(temporary,'before.bin').read_bytes(),out/'stock-reopen.log')
            except (OSError,ValueError,sp.TimeoutExpired):probed,rc=['FAIL stock_transport=0'],2
            public+=probed
        else:public.append(f'FAIL stock_control_tested=0 repair_before_probe={int(repaired)}')
        # b-try already owns return and any allowed data repair. No new reboot.
        (out/'result.log').write_text('\n'.join(public)+'\n')
        print('\n'.join(public),flush=True)
        return rc

if __name__=='__main__':
    try:sys.exit(main())
    except (OSError,ValueError,KeyError,UnicodeError,sp.TimeoutExpired):
        print('FAIL reopen_launcher=0',flush=True);sys.exit(2)
