#!/usr/bin/env python3
"""Offline fixed-tag RPMB evidence only; no device commands or packet data."""
import argparse
from pathlib import Path
import re

S32=r'-?[0-9]{1,10}'
U32=r'[0-9]{1,10}'
REGISTER=re.compile(r'A9_RPMB_REGISTER (?:phase=[1-6] rc='+S32+
    r'|request='+U32+r' mapped='+U32+r' buf_page_aligned=[01] len_page_aligned=[01] cmd=6 arginfo=0x83'
    r'|check=scm_return cmd=[16] rc='+S32+r')')
SCM=re.compile(r'A9_RPMB_SCM (?:check=unknown_convention rc=-22'
    r'|check=(?:ce_clock|bandwidth|transport_return) rc='+S32+
    r'|check=missing_ext_pool nargs='+U32+r' rc=-22'
    r'|check=invoke fn=0x(?:32|72|b2|f2)00020[16] arginfo=0x83 nargs='+U32+
    r' size='+U32+r' buf_page_aligned=[01] len_page_aligned=[01]'
    r'|check=smc_return transport='+S32+r' status_valid=[01] secure_status=-?[0-9]{1,19} mapped_rc='+S32+r')')
DIAG=re.compile(r'A9_RPMB_DIAG startup=1 seq='+U32+r' overrun='+U32+r' truncated='+U32+
    r' submitted='+U32+r' queued='+U32+r' dropped='+U32+r' readerr='+U32)
GUARD=re.compile(r'A9_RPMB_GUARD mode=[12] allowed=[01] operation=[0-6] reason=[0-6]')
KMTB_WRITE=re.compile(r'A9_KMTB_WRITE phase=[1-3] allowed=[01] operation=[0-2] reason=[0-6] changed='+U32)
KMTB_READ=re.compile(r'A9_KMTB_READ phase=[1-3] valid=[01]')
BOOTSTRAP=re.compile(r'A9_RPMB_BOOTSTRAP phase=[1-6] rc='+S32)
MAP=re.compile(r'A9_RPMB_MAP phase=[12] valid=[01]')
REQUEST_TRACE=re.compile(r'A9_RPMB_REQUEST_TRACE command=[23] shape=[01] reason=[0-6] request=[0-5] frames='+U32+r' input_frames='+U32+r' offset='+U32+r' first='+U32+r' count='+U32+r' map_valid=[01] context='+U32+r' table_candidate=[01] table_valid=[01]')
WRITE_TRACE=re.compile(r'A9_RPMB_WRITE_TRACE allowed=[01] map_valid=[01] groups_valid=[01] partition=[0-5] first='+U32+r' offset='+U32+r' frames='+U32+r' context='+U32)
REJECT=re.compile(r'A9_RPMB_REJECT reason=[1-6] opcode=[0-2] direction=[0-3] bytes_valid=[01] length_match=[01] frames='+U32+r' frame_valid=[01] request=[0-5]')
RESULT=re.compile(r'A9_RPMB_RESULT command=[23] status_valid=[01] operation_rc='+S32)
QSEE=re.compile(r'A9_QSEE_LOG phase=[1-3] rc='+S32)
CAPTURE=re.compile(r'A9_QSEE_CAPTURE phase=[1-3] rc='+S32)
INIT=re.compile(r'A9_QSEE_INIT ready=1 whitelist=[01]')
ATTEST_FS=re.compile(r'A9_ATTEST_FS object=[1-4] op=[1-3] access=[0-3] errno=[0-9]{1,4} result=[01]')
ATTEST_LIMIT=re.compile(r'A9_ATTEST_FS_LIMIT object=[1-4] limit=7 dropped=1')
BACKEND=re.compile(r'A9_RPMB_BACKEND op=[12] ok=[01] errno=[0-9]{1,4} ufs=[01] mmc=[01] truncated=[01]')
BACKEND_LIMIT=re.compile(r'A9_RPMB_BACKEND_LIMIT op=[12] limit=7 dropped=1')
CALLBACK=re.compile(r'A9_RPMB_CALLBACK (?:phase=1 command=[0-3] size_valid=[01]'
    r'|phase=2 command=[0-3] rc='+S32+r' status_valid=[01] qsee_result=-?[0-9]{1,19})')
QUERY=re.compile(r'A9_RPMB_QUERY idn=[027] rpmb_lun=[01] rc='+S32+r' length_valid=[01]')
SFS_OPEN=re.compile(r'A9_SFS_OPEN root=[1-3] op=[1-3] access=[0-3] create=[01] errno=[0-9]{1,4} result=[01]')
PERSIST_RO=re.compile(r'A9_PERSIST_RO phase=[1-3] rc='+S32)
PATTERNS=(REGISTER,SCM,DIAG,GUARD,KMTB_WRITE,KMTB_READ,BOOTSTRAP,MAP,REQUEST_TRACE,WRITE_TRACE,REJECT,RESULT,QSEE,CAPTURE,INIT,ATTEST_FS,ATTEST_LIMIT,
          BACKEND,BACKEND_LIMIT,CALLBACK,QUERY,SFS_OPEN,PERSIST_RO)


def public_record(line):
    """Keep only whole numeric diagnostic messages, never raw kernel suffixes."""
    for pattern in PATTERNS:
        tag=pattern.pattern.split(' ',1)[0]+' '
        start=line.find(tag)
        if start<0:continue
        public=line[start:].rstrip('\r\n')
        if not pattern.fullmatch(public):continue
        for key,value in re.findall(r'(?:^| )([a-z_]+)=(-?[0-9]+)(?= |$)',public):
            n=int(value)
            if key in ('secure_status','qsee_result'):bounds=(-2**63,2**63-1)
            elif key in ('rc','transport','mapped_rc','operation_rc'):bounds=(-2**31,2**31-1)
            elif key in ('request','mapped','size'):bounds=(0,1048576)
            elif key=='nargs':bounds=(0,10)
            elif key=='errno':bounds=(0,4095)
            else:bounds=(0,2**32-1)
            if pattern in (REQUEST_TRACE,WRITE_TRACE):
                if key in ('first','count') or (key=='offset' and pattern is WRITE_TRACE):bounds=(0,65535)
                elif key in ('frames','input_frames'):bounds=(0,49)
                elif key=='context':bounds=(0,10)
                elif key=='offset':bounds=(0,1048576)
            if not bounds[0]<=n<=bounds[1]:break
        else:return public
    return None


def daemon_record(line):
    return bool(re.search(r'\b(?:qseecomd|QSEECOMAPI|RPMB)\b|A9_(?:KMTB_(?:WRITE|READ)|RPMB_(?:REGISTER|SCM|GUARD|BOOTSTRAP|MAP|REQUEST_TRACE|WRITE_TRACE|REJECT|RESULT|DIAG|BACKEND(?:_LIMIT)?|CALLBACK|QUERY)|QSEE_(?:INIT|LOG|CAPTURE)|ATTEST_FS(?:_LIMIT)?|SFS_OPEN|PERSIST_RO)\b',line,re.I))


def collect(boot):
    counts=dict(init_ok=0,register_failed=0,start_failed=0,stor_read_failed=0,key_id_missing=0,ta_log_present=0)
    selected=[]
    sfs_selected=[]
    for name in ('kernel.early.log','kernel.log.prev','kernel.log','logcat.log.prev','logcat.log','qsee_log.prev','qsee_log','tz_log'):
        p=boot/name
        if not p.is_file() or p.is_symlink():continue
        if name in ('qsee_log.prev','qsee_log','tz_log'):counts['ta_log_present']|=int(p.stat().st_size>0)
        with p.open(errors='replace') as source:
            for line in source:
                counts['init_ok']+=int('rpmb_init succeeded!' in line and 'ret = 0' in line)
                counts['register_failed']+=int('Register listener ioctl failed for lstnr ID 8192' in line)
                counts['start_failed']+=int('rpmb_start ioctl failed' in line)
                counts['stor_read_failed']+=int('qsee_stor_read_sectors' in line and 'failed' in line)
                counts['key_id_missing']+=int('key id not present' in line)
                public=public_record(line)
                if public and public.startswith('A9_SFS_OPEN '):
                    # Each actual open attempts direct logcat submission. Keep
                    # repeated outcomes, rather than collapsing equal calls.
                    # Kernel mirrors are not a second SFS event source.
                    if name in ('logcat.log.prev','logcat.log'):sfs_selected.append(public)
                elif public and public not in selected:selected.append(public)
    selected.extend(sfs_selected)
    counts.update(kmtb_write_records=sum(s.startswith('A9_KMTB_WRITE ') for s in selected),
                  kmtb_read_records=sum(s.startswith('A9_KMTB_READ ') for s in selected),
                  register_records=sum(s.startswith('A9_RPMB_REGISTER ') for s in selected),
                  scm_records=sum(s.startswith('A9_RPMB_SCM ') for s in selected),
                  bridge_records=sum(s.startswith('A9_RPMB_DIAG ') for s in selected),
                  attest_fs_records=sum(s.startswith('A9_ATTEST_FS ') for s in selected),
                  backend_records=sum(s.startswith('A9_RPMB_BACKEND ') for s in selected),
                  callback_records=sum(s.startswith('A9_RPMB_CALLBACK ') for s in selected),
                  reject_records=sum(s.startswith('A9_RPMB_REJECT ') for s in selected),
                  bootstrap_records=sum(s.startswith('A9_RPMB_BOOTSTRAP ') for s in selected),
                  map_records=sum(s.startswith('A9_RPMB_MAP ') for s in selected),
                  request_trace_records=sum(s.startswith('A9_RPMB_REQUEST_TRACE ') for s in selected),
                  write_trace_records=sum(s.startswith('A9_RPMB_WRITE_TRACE ') for s in selected),
                  result_records=sum(s.startswith('A9_RPMB_RESULT ') for s in selected),
                  sfs_open_records=sum(s.startswith('A9_SFS_OPEN ') for s in selected),
                  persist_ro_records=sum(s.startswith('A9_PERSIST_RO ') for s in selected),
                  query_records=sum(s.startswith('A9_RPMB_QUERY ') for s in selected))
    selected.append('A9_RPMB_EVIDENCE '+' '.join(k+'='+str(v) for k,v in counts.items()))
    return '\n'.join(selected)+'\n'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--saved-boot',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--qseecomd-out',type=Path)
    a=ap.parse_args();text=collect(a.saved_boot)
    a.out.write_text(text);a.out.chmod(0o600)
    if a.qseecomd_out:
        # Retain private raw daemon/API text in a separate file, never stdout.
        # Full logcat remains in the adjacent saved boot for other tags.
        with a.qseecomd_out.open('w') as dest:
            a.qseecomd_out.chmod(0o600)
            for name in ('logcat.log.prev','logcat.log'):
                p=a.saved_boot/name
                if p.is_file() and not p.is_symlink():
                    with p.open(errors='replace') as source:
                        for line in source:
                            if daemon_record(line):dest.write(line)
    print(text.splitlines()[-1])
if __name__=='__main__':main()
