#!/usr/bin/env python3
"""Synthetic public-tag/redaction fixtures, no device tools or real key data."""
from pathlib import Path
import importlib.util,tempfile,subprocess,sys
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('rpmb',HERE/'rpmb-evidence.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
with tempfile.TemporaryDirectory(prefix='rpmb-evidence-test-') as d:
 p=Path(d)
 (p/'logcat.log').write_text('rpmb_init succeeded! with ret = 0\nError::Register listener ioctl failed for lstnr ID 8192 & ret=-1\nrpmb_start ioctl failed! with ret = -1\nSYNTHETIC_SECRET\n')
 out=m.collect(p)
 assert 'init_ok=1 register_failed=1 start_failed=1 stor_read_failed=0 key_id_missing=0 ta_log_present=0' in out
 (p/'qsee_log').write_text('qsee_stor_read_sectors() failed\nkey id not present SYNTHETIC_SECRET\n')
 (p/'kernel.log').write_text('prefix A9_RPMB_REGISTER phase=3 rc=-22\nprefix A9_RPMB_GUARD mode=1 allowed=0 operation=0 reason=4\nA9_RPMB_REGISTER phase=3 rc=-22 secret=SYNTHETIC_SECRET\n')
 out=m.collect(p)
 assert 'stor_read_failed=1 key_id_missing=1 ta_log_present=1' in out
 assert out.count('A9_RPMB_REGISTER')==1 and 'A9_RPMB_GUARD' in out and 'SYNTHETIC' not in out
 (p/'qsee_log.prev').write_text('qsee_stor_read_sectors() failed\n')
 (p/'qsee_log').write_text('')
 (p/'kernel.log').write_text('A9_QSEE_LOG phase=2 rc=0\nA9_QSEE_CAPTURE phase=2 rc=0\nA9_QSEE_LOG phase=2 rc=0 secret=SYNTHETIC_SECRET\n')
 out=m.collect(p)
 assert 'ta_log_present=1' in out and out.count('A9_QSEE_LOG')==1
 assert 'A9_QSEE_CAPTURE phase=2 rc=0' in out and 'SYNTHETIC' not in out
 # Full registration discriminator survives logcat alone if kernel persistence dies.
 records=[
  'A9_RPMB_REGISTER request=25600 mapped=28672 buf_page_aligned=1 len_page_aligned=1 cmd=6 arginfo=0x83',
  'A9_RPMB_SCM check=ce_clock rc=0',
  'A9_RPMB_SCM check=bandwidth rc=0',
  'A9_RPMB_SCM check=invoke fn=0x72000206 arginfo=0x83 nargs=3 size=28672 buf_page_aligned=1 len_page_aligned=1',
  'A9_RPMB_SCM check=smc_return transport=0 status_valid=1 secure_status=-2 mapped_rc=-22',
  'A9_RPMB_REGISTER check=scm_return cmd=6 rc=-22',
  'A9_RPMB_REGISTER phase=4 rc=-22',
  'A9_RPMB_DIAG startup=1 seq=1 overrun=0 truncated=0 submitted=7 queued=0 dropped=0 readerr=0',
  'A9_QSEE_INIT ready=1 whitelist=1',
  'A9_ATTEST_FS object=3 op=1 access=0 errno=2 result=0',
  'A9_ATTEST_FS object=4 op=2 access=3 errno=22 result=0',
  'A9_ATTEST_FS_LIMIT object=3 limit=7 dropped=1',
  'A9_RPMB_BACKEND op=2 ok=1 errno=0 ufs=1 mmc=0 truncated=0',
  'A9_RPMB_BACKEND_LIMIT op=2 limit=7 dropped=1',
  'A9_RPMB_CALLBACK phase=1 command=2 size_valid=1',
  'A9_RPMB_CALLBACK phase=2 command=2 rc=0 status_valid=1 qsee_result=0',
  'A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=3',
  'A9_RPMB_RESULT command=3 status_valid=1 operation_rc=-1',
  'A9_KMTB_WRITE phase=1 allowed=1 operation=1 reason=0 changed=1',
  'A9_KMTB_WRITE phase=2 allowed=0 operation=0 reason=3 changed=0',
  'A9_KMTB_WRITE phase=3 allowed=1 operation=2 reason=0 changed=1',
  'A9_KMTB_READ phase=2 valid=1',
  'A9_RPMB_MAP phase=2 valid=1',
  'A9_RPMB_BOOTSTRAP phase=5 rc=0',
  'A9_RPMB_REQUEST_TRACE command=3 shape=1 reason=0 request=3 frames=34 input_frames=34 offset=24 first=2050 count=34 map_valid=0 context=10 table_candidate=0 table_valid=0',
  'A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=34 context=10',
  'A9_RPMB_GUARD mode=2 allowed=1 operation=5 reason=0',
  'A9_RPMB_GUARD mode=2 allowed=1 operation=6 reason=0',
  'A9_RPMB_QUERY idn=7 rpmb_lun=1 rc=-22 length_valid=0',
  'A9_SFS_OPEN root=1 op=1 access=1 create=1 errno=30 result=0',
  'A9_SFS_OPEN root=2 op=2 access=0 create=0 errno=0 result=1',
  'A9_SFS_OPEN root=3 op=1 access=2 create=1 errno=13 result=0',
  'A9_PERSIST_RO phase=3 rc=0',
 ]
 for name in ('kernel.log','logcat.log','qsee_log.prev','qsee_log'):(p/name).unlink(missing_ok=True)
 (p/'logcat.log').write_text(''.join('public-prefix A9_RPMB_DIAG: '+s+'\n' for s in records))
 out=m.collect(p)
 assert all(s in out for s in records)
 assert 'register_records=3 scm_records=4 bridge_records=1' in out
 # Early saved records and mirrored copies deduplicate without losing raw status.
 (p/'kernel.early.log').write_text('\n'.join(records)+'\n')
 assert m.collect(p)==out
 # Equal open outcomes remain separate logcat records; kernel mirrors do not
 # manufacture extra calls. Rotated log overlap still cannot prove uniqueness.
 sfs=next(s for s in records if s.startswith('A9_SFS_OPEN '))
 with (p/'logcat.log').open('a') as f:f.write(sfs+'\n'+sfs+'\n')
 assert m.collect(p).count(sfs)==3
 for status in ('4294967294','-9223372036854775808','9223372036854775807'):
  s='A9_RPMB_SCM check=smc_return transport=0 status_valid=1 secure_status='+status+' mapped_rc=-22'
  assert m.public_record('prefix '+s+'\n')==s
 for bad in (records[0]+' address=0x12345678', records[4]+' secret=SYNTHETIC_SECRET',
             records[3].replace('0x72000206','0x12345678'),
             records[3].replace('nargs=3','nargs=11'),
             records[4].replace('secure_status=-2','secure_status=9223372036854775808'),
             records[0].replace('mapped=28672','mapped=1048577'),
             records[1].replace('rc=0','rc=2147483648'),
             'A9_ATTEST_FS object=3 op=1 access=0 errno=2 result=0 path=SYNTHETIC_SECRET',
             'A9_ATTEST_FS object=3 op=1 access=0 errno=4096 result=0',
             'A9_RPMB_CALLBACK phase=1 command=258 size_valid=1',
             'A9_RPMB_MAP phase=2 valid=2',
             'A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=65536 offset=0 frames=34 context=10',
             'A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=50 context=10',
             'A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=34 context=11',
             'A9_RPMB_GUARD mode=2 allowed=1 operation=7 reason=0',
             'A9_RPMB_REJECT reason=3 opcode=3 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=3',
             'A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=4294967296 frame_valid=1 request=3',
             'A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=6',
             'A9_RPMB_RESULT command=1 status_valid=1 operation_rc=0',
             'A9_RPMB_RESULT command=3 status_valid=1 operation_rc=2147483648',
             'A9_RPMB_RESULT command=3 status_valid=1 operation_rc=0 sector=123',
             'A9_RPMB_QUERY idn=3 rpmb_lun=1 rc=0 length_valid=1',
             'A9_SFS_OPEN root=4 op=1 access=0 create=0 errno=2 result=0',
             'A9_SFS_OPEN root=1 op=1 access=0 create=0 errno=4096 result=0',
             'A9_SFS_OPEN root=1 op=1 access=0 create=0 errno=2 result=0 path=SYNTHETIC_SECRET',
             'A9_PERSIST_RO phase=4 rc=0',
             'A9_RPMB_BACKEND op=2 ok=1 errno=0 ufs=1 mmc=0 truncated=0 path=SYNTHETIC_SECRET'):
  assert m.public_record(bad) is None,bad
 # The adjacent private daemon extract must retain the bridge's underscore tag.
 dest=p/'extracted-public.log';raw=p/'daemon-private.log'
 subprocess.run([sys.executable,str(HERE/'rpmb-evidence.py'),'--saved-boot',str(p),
                 '--out',str(dest),'--qseecomd-out',str(raw)],check=True,capture_output=True)
 assert all(s in raw.read_text() for s in records)
 assert dest.stat().st_mode&0o777==0o600 and raw.stat().st_mode&0o777==0o600
 with tempfile.TemporaryDirectory() as empty:
  assert 'init_ok=0 register_failed=0' in m.collect(Path(empty))
  assert 'register_records=0 scm_records=0 bridge_records=0' in m.collect(Path(empty))
 print('PASS RPMB evidence: legacy/TA counts, logcat-only SCM chain, early mirror dedupe, signed64/redaction, private daemon export')
