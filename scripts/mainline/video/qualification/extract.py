#!/usr/bin/env python3
"""Offline per-boot Venus qualification diagnostics. Raw splats stay in kernel.log/pstore."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('hvenc_validation',HERE/'validation.py')
v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
NUM=r'(?:-?[0-9]{1,20}|0x[0-9a-fA-F]{1,8})'
VENUS=(
    re.compile(r'A9_VENUS_ENC op=[A-Za-z0-9_.]{1,64} phase=(?:send|queued|response|begin|end|buffer\.end) rc='+NUM+r' state='+NUM+r' codec='+NUM),
    re.compile(r'A9_VENUS_ENC op=bufreq source=(?:platform|firmware) type='+NUM+r' size='+NUM+r' count='+NUM+r' min='+NUM),
    re.compile(r'A9_VENUS_ENC op=set_buffers type='+NUM+r' size='+NUM+r' count='+NUM),
    re.compile(r'A9_VENUS_ENC op=set_property type='+NUM),
    re.compile(r'A9_VENUS_ENC op=buffer.begin type='+NUM+r' alloc='+NUM+r' filled='+NUM+r' offset='+NUM+r' flags='+NUM+r' tag='+NUM),
    re.compile(r'A9_VENUS_ENC op=buffer.done type='+NUM+r' used='+NUM+r' offset='+NUM+r' flags='+NUM+r' hfi_flags='+NUM+r' tag='+NUM),
)
RECOVERY=re.compile(r'A9_VENUS_RECOVERY event=1 stage=0 rc=0 irq='+NUM)
C2=re.compile(r'A9_ENC_TRACE op=(?:input_usage|layout\.map|layout\.lock_ycbcr|converter\.(?:create|pool|allocate)|output_pool|convert\.(?:input_map|output_map)|qbuf\.input|streamon\.(?:capture|output)) phase=(?:begin|end)(?: (?:cpu_read|usage|status|present|type|roots|pool|count|width|height|slot|success)='+NUM+r'){0,4}')
SPLAT=re.compile(r'Kernel panic|\bOops:|\bBUG:|\bWARNING:|Call trace:|soft lockup|hard LOCKUP|blocked for more than|Unhandled context fault|SMMU fault')


def collect(boot):
    selected=[];seen=set();kernel=[];logcat=[]
    for name in ('kernel.early.log','kernel.log.prev','kernel.log','logcat.log.prev','logcat.log'):
        p=boot/name
        if not p.is_file() or p.is_symlink():continue
        with p.open(errors='replace') as f:
            for line in f:
                key=line.rstrip()
                if key in seen:continue
                seen.add(key)
                (logcat if name.startswith('logcat') else kernel).append(key)
    # Prefer direct kmsg, avoiding double-counting its logcat bridge. Older
    # captures may have a broken kmsg reader; retain bridged Venus records then.
    firmware=kernel if any('A9_VENUS_ENC ' in l or 'A9_VENUS_RECOVERY ' in l for l in kernel) else logcat
    response=timeouts=buffers=errors=0
    watchdogs=0
    for line in firmware:
        pos=line.find('A9_VENUS_RECOVERY ')
        if pos>=0 and RECOVERY.fullmatch(line[pos:]):
            selected.append(line[pos:]);watchdogs+=1
        start=line.find('A9_VENUS_ENC ')
        if start<0:continue
        public=line[start:]
        if not any(p.fullmatch(public) for p in VENUS):continue
        selected.append(public)
        response+=int(' phase=response ' in public)
        timeouts+=int(' rc=-110 ' in public)
        errors+=int(bool(re.search(r' rc=-[1-9][0-9]* ',public)))
        buffers+=int('op=buffer.done ' in public)
    for line in logcat:
        pos=line.find('A9_ENC_TRACE ')
        if pos>=0 and C2.fullmatch(line[pos:]):selected.append(line[pos:])
    generic_timeouts=sum(bool(re.search(r'(?:venus|hfi).*?(?:response timed out|session timed out|buffer.*timed out)',l,re.I)) for l in firmware)
    session_errors=sum(bool(re.search(r'(?:venus|hfi).*firmware session error',l,re.I)) for l in firmware)
    # The prior boot's pstore is retained by the owner, but cannot qualify or
    # fail this boot's firmware exchanges. It may belong to an older image.
    native=[]
    for line in logcat:
        for tag,pattern in (('HVENC_QUAL ',v.POINT),('HVENC_RESULT ',v.RESULT)):
            pos=line.find(tag)
            if pos>=0 and pattern.fullmatch(line[pos:]):native.append(line[pos:]);selected.append(line[pos:])
    result=v.parse('\n'.join(native),0)
    splats=sum(bool(SPLAT.search(l)) for l in kernel)
    # Whole-boot diagnostic scope is deliberate and separate from encode-only
    # counters; an unrelated earlier splat is visible rather than silently waived.
    summary=dict(probe_seen=int(bool(native)),native_pass=int(v.passed(result)),
        firmware_response_records=response,firmware_timeouts=timeouts+generic_timeouts,firmware_errors=errors+session_errors+watchdogs,buffer_completions=buffers,
        firmware_watchdog_records=watchdogs,
        kernel_splat_lines=splats,diagnostics_ok=int(response>0 and buffers>0 and timeouts+generic_timeouts==0 and errors+session_errors+watchdogs==0 and splats==0 and len(selected)<=8192),
        native=result,record_limit_reached=int(len(selected)>8192))
    return selected[:8192],summary


def private_write(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)
    path.chmod(0o600)


def save(boot,host_stage=None,media=None):
    records,summary=collect(boot);os.umask(0o077)
    for name,text in [('hvenc-diagnostics.log','\n'.join(records)+'\n'),
                      ('hvenc-diagnostics.json',json.dumps(summary,indent=2,sort_keys=True)+'\n')]:
        private_write(boot/name,text.encode())
    # These are host-produced numeric results and a synthetic MP4, never camera
    # media. Bind only to the final pulled boot containing the qualification tag.
    if summary['probe_seen'] and host_stage and host_stage.is_file() and not host_stage.is_symlink():
        data=json.loads(host_stage.read_text())
        if isinstance(data,dict) and all(isinstance(k,str) and type(x) is int for k,x in data.items()):
            private_write(boot/'hvenc-qualification.json',(json.dumps(data,sort_keys=True)+'\n').encode())
            if media and data.get('host_mp4_ok')==1 and media.is_file() and not media.is_symlink() and media.stat().st_size<=4*1024**2:
                private_write(boot/'hvenc-output.mp4',media.read_bytes())
    return summary


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--saved-boot',type=Path,required=True)
    ap.add_argument('--host-stage',type=Path);ap.add_argument('--media',type=Path)
    a=ap.parse_args();d=save(a.saved_boot,a.host_stage,a.media)
    print('HVENC_DIAGNOSTICS '+ ' '.join(f'{k}={d[k]}' for k in (
        'probe_seen','native_pass','firmware_response_records','firmware_timeouts',
        'buffer_completions','kernel_splat_lines','firmware_errors','diagnostics_ok')))


if __name__=='__main__':main()
