#!/usr/bin/env python3
"""Bounded attended D2 host supervisor (one earpiece tone); derived from d0-host.py.
Never discovers devices or stages files.

Execution requires an independently reviewed private admission receipt. Offline
tests use an explicit fake transport; this script is not called by init/builds.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shlex
import subprocess as sp
import time
import uuid

def command(argv, seconds, log, script=None):
    # Direct child PID only. No process groups, pkill, killall or adb-server kill.
    with log.open('wb') as f:
        p=sp.Popen(argv,stdin=sp.PIPE if script is not None else sp.DEVNULL,stdout=f,stderr=sp.STDOUT)
        try:
            p.communicate(script,timeout=seconds)
            return p.returncode
        except sp.TimeoutExpired: raise RuntimeError('transport-timeout')
        finally:
            if p.poll() is None:
                p.terminate()
                try: p.wait(timeout=2)
                except sp.TimeoutExpired: p.kill(); p.wait(timeout=2)

class Transport:
    """One selected adb transport; nc's exit status is never target status."""
    def __init__(self, adb, transport_id, serial, root_transport, boot):
        self.argv=[str(adb),'-t',transport_id]
        self.serial=serial
        self.root_transport=root_transport
        self.boot=boot

    def target(self, args, seconds, log):
        return self.execute(['/system/bin/sh','/data/local/tmp/adsp-d2/d2-target.sh',*args],seconds,log)

    def execute(self, argv, seconds, log):
        deadline=time.monotonic()+seconds
        identity=log.with_suffix('.serial.log')
        assert command(self.argv+['get-serialno'],min(5,seconds),identity)==0
        assert identity.read_text().strip()==self.serial, 'transport serial mismatch'
        nonce='D2-TRANSPORT-'+uuid.uuid4().hex
        # Feed only fixed, quoted commands. The target cannot consume protocol
        # input, and exit closes the listener even if nc does not half-close EOF.
        script=(
            'd0_uid=$(id -u) || exit 126\n'
            'd0_boot=$(cat /proc/sys/kernel/random/boot_id) || exit 126\n'
            f'printf "{nonce} ID %s %s\\n" "$d0_uid" "$d0_boot"\n'
            '[ "$d0_uid" = 0 ] || exit 126\n'
            f'[ "$d0_boot" = {shlex.quote(self.boot)} ] || exit 125\n'
            +shlex.join(argv)+' </dev/null 2>&1\n'
            'd0_rc=$?\n'
            f'printf "\\n{nonce} RC %s\\n" "$d0_rc"\n'
            'exit\n').encode()
        shell='nc 127.0.0.1 2324' if self.root_transport=='loopback' else '/system/bin/sh -s'
        raw=log.with_suffix('.transport.log')
        remaining=deadline-time.monotonic()
        if remaining<=0: raise RuntimeError('transport-timeout')
        rc=command(self.argv+['shell','-T',shell],remaining,raw,script)
        data=raw.read_bytes().replace(b'\r\n',b'\n')
        prefix=(nonce+' ID 0 '+self.boot+'\n').encode()
        match=re.fullmatch(re.escape(prefix)+rb'(.*)\n'+nonce.encode()+rb' RC ([0-9]{1,3})\n',data,re.S)
        if rc!=0 or match is None or int(match[2])>255:
            raise RuntimeError('transport identity/completion unverified: '+str(raw))
        log.write_bytes(match[1])
        return int(match[2])

def main():
    def interrupted(signum, frame): raise RuntimeError('host-interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGHUP,interrupted)
    ap=argparse.ArgumentParser()
    ap.add_argument('--adb',required=True,type=Path)
    ap.add_argument('--serial',required=True)
    ap.add_argument('--transport-id',required=True)
    ap.add_argument('--root-transport',required=True,choices=('adbd','loopback'))
    ap.add_argument('--boot-id',required=True)
    ap.add_argument('--admission',required=True,type=Path)
    ap.add_argument('--manifest',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--ledger',required=True,type=Path)
    ap.add_argument('--rehearse',action='store_true')
    ap.add_argument('--stop',action='store_true',help='request stop through the existing target guardian; never dispatch playback')
    a=ap.parse_args()
    receipt=json.loads(a.admission.read_text())
    t=receipt['tuple']
    assert re.fullmatch('[a-f0-9]{64}',t)
    assert hashlib.sha256(a.manifest.read_bytes()).hexdigest()==t, 'frozen manifest mismatch'
    assert receipt['independent_review'] == 'ADMITTED-ATTENDED-D2'
    # D2 builds on a passed D1 on the SAME tuple family: the receipt must cite it.
    assert receipt['stage'] == 'D2'
    d1=receipt['d1_pass_evidence']
    assert isinstance(d1,str) and 'PASS-ATTENDED-D1' in d1 and len(d1)>=40
    assert receipt['operator_present'] is True
    assert receipt['output_path'] == 'internal earpiece; headphone jack empty'
    assert receipt['tone'] == {'rate':48000,'channels':1,'format':'S16_LE','hz':1000,'frames':48000,'peak':1036}

    owner=receipt['fallback_owner']
    assert isinstance(owner,str) and owner.strip() and owner.strip().upper()!='PENDING'
    assert receipt['fallback'] == 'attended PON2/Power then golden restore'
    assert receipt['service_admission'] == 'PASS' and receipt['frozen_manifest_sha256'] == t
    assert receipt['root_transport'] == a.root_transport, 'root transport not admitted'
    assert re.fullmatch('[1-9][0-9]*',a.transport_id)
    assert re.fullmatch('[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}',a.boot_id)
    assert a.adb.is_absolute() and a.adb.is_file()
    assert a.rehearse == bool(receipt.get('rehearsal',False))
    if a.rehearse: assert a.adb.name == 'fake-adb'
    out=a.output.expanduser().resolve()
    assert 'a9-assets' not in str(out) and not out.exists()
    out.mkdir(parents=True)
    transport=Transport(a.adb,a.transport_id,a.serial,a.root_transport,a.boot_id)
    if a.stop:
        rc=transport.target(['stop',t,a.boot_id],18,out/'stop.log')
        safe=rc == 0 and (out/'stop.log').read_text().rstrip().endswith('D2-CONTAINED-OFFLINE')
        (out/'result').write_text('STOPPED-OFFLINE\n' if safe else 'CONTAINMENT-UNVERIFIED\n')
        assert safe, 'stop completion unverified; attended Power/PON2 fallback required; no retry'
        print('STOPPED-OFFLINE'); return
    # Read-only boot check precedes durable attempt consumption. No retry loop.
    assert transport.target(['boot-id',t],5,out/'boot-id.log') == 0
    boot=(out/'boot-id.log').read_text().strip()
    assert boot == a.boot_id, 'ambiguous boot identity'
    ledger=a.ledger.expanduser().resolve()
    assert 'a9-assets' not in str(ledger)
    ledger.mkdir(parents=True,exist_ok=True)
    with (ledger/boot).open('x') as f:
        f.write(t+'\n'); f.flush(); os.fsync(f.fileno())
    fd=os.open(ledger,os.O_RDONLY); os.fsync(fd); os.close(fd)
    ok=False
    try:
        # Independent host wall clock bounds the complete target transaction.
        rc=transport.target(['run',t,boot],340 if not a.rehearse else 2,out/'run.log')
        log=(out/'run.log').read_text(errors='replace')
        ok=rc == 0 and log.rstrip().endswith('D2-PASS-ONE-TONE-CLOSED-RESET-OFFLINE')
        ok=ok and ('ADSP-D2-'+boot+'-'+t) in log
        if not ok: raise RuntimeError('target-failed-or-incomplete')
        assert transport.target(['boot-id',t],5,out/'boot-after.log') == 0
        assert (out/'boot-after.log').read_text().strip() == boot, 'SoC reset'
    except BaseException:
        # One bounded containment call, never a second start/load/recover. Even
        # confirmed OFFLINE is a failed, consumed run. Reset/disconnect stays red.
        try:
            rc=transport.target(['contain',t,boot],18,out/'contain.log')
            completed=(out/'run.log').read_text(errors='replace')
            contained=rc == 0 and 'D2-FAILED-OFFLINE' in completed and (out/'contain.log').read_text().rstrip().endswith('D2-CONTAINED-OFFLINE')
        except BaseException: contained=False
        (out/'result').write_text('FAILED-OFFLINE\n' if contained else 'CONTAINMENT-UNVERIFIED\n')
        if not contained:
            (out/'fallback').write_text('REQUIRED: '+owner.strip()+' owns '+receipt['fallback']+'; completion unverified; no retry\n')
        raise
    (out/'result').write_text('PASS-ATTENDED-D2\n')
    print('PASS-ATTENDED-D2')

if __name__ == '__main__': main()
