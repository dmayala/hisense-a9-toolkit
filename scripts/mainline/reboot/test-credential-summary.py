#!/usr/bin/env python3
"""Focused offline summary fixtures; no phone, real credentials or subprocess logs."""
import json
from pathlib import Path
import re
import subprocess as sp
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SECRET = 'SYNTHETIC_PRIVATE_PAYLOAD'


def run(path, rc=0):
    r = sp.run([sys.executable, '-B', str(HERE / 'credential-summary.py'), str(path)],
               capture_output=True, text=True, timeout=5)
    assert r.returncode == rc, r.stderr
    assert r.stderr == '' and SECRET not in r.stdout
    for line in r.stdout.splitlines():
        assert re.fullmatch(r'A9_[A-Z_]+(?: [a-z][a-z0-9_]*=-?[0-9]+)+', line), line
    return r.stdout


with tempfile.TemporaryDirectory(prefix='credential-summary-') as tmp:
    base = Path(tmp)
    # Complete PIN row, two actual boots and private/unrelated fields.
    root = base / 'sweep with spaces'
    first = root / 'boot-0000000120'
    second = root / 'boot-0000000121'
    first.mkdir(parents=True)
    second.mkdir()
    (root / 'boot-probe.log').write_text(SECRET)
    stages = {k: 1 for k in ('attempted', 'set', 'verified', 'locked', 'unlocked',
                             'removed', 'finally_unsecured', 'cleanup_ok')}
    stages['private'] = SECRET
    (root / 'acceptance.json').write_text(json.dumps({'29_pin_lifecycle': {'status': 'PASS', 'evidence': stages}, 'private': SECRET}))
    (first / 'kernel.log').write_text("init: starting service 'vendor.gatekeeper-1-0'...\nA9_RPMB_REGISTER phase=4 rc=0\nA9_PERSIST_RO phase=3 rc=0\n")
    (first / 'logcat.log').write_text('hwservicemanager: Registered android.hardware.gatekeeper@1.0::IGatekeeper/default\n'
        'A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=3\n'
        'A9_RPMB_RESULT command=3 status_valid=1 operation_rc=-1\n'
        'A9_KMTB_WRITE phase=2 allowed=1 operation=1 reason=0 changed=1\n'
        'A9_KMTB_READ phase=2 valid=1\n'
        'A9_RPMB_MAP phase=2 valid=1\nA9_RPMB_BOOTSTRAP phase=5 rc=0\n'
        'A9_RPMB_REQUEST_TRACE command=3 shape=1 reason=0 request=3 frames=34 input_frames=34 offset=24 first=2050 count=34 map_valid=0 context=10 table_candidate=0 table_valid=0\n'
        'A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=34 context=10\n'
        'A9_RPMB_GUARD mode=2 allowed=1 operation=5 reason=0\n'
        'A9_RPMB_MAP phase=2 valid=1 private='+SECRET+'\n'
        'A9_RPMB_RESULT command=2 status_valid=1 operation_rc=2147483648\n'
        'A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=3 private='+SECRET+'\n'
        'A9_SFS_OPEN root=1 op=1 access=0 create=0 errno=0 result=1\n'
        'A9_SFS_OPEN root=1 op=1 access=1 create=1 errno=30 result=0\n'
        'A9_SFS_OPEN root=2 op=2 access=0 create=0 errno=2 result=0\n'
        'A9_SFS_OPEN root=3 op=1 access=2 create=1 errno=0 result=1\n'
        'A9_SFS_OPEN root=3 op=1 access=2 create=1 errno=13 result=0\n'
        'A9_SFS_OPEN root=3 op=1 access=2 create=1 errno=13 result=0 private='+SECRET+'\n'
        'keystore2: Error::Km(-10003) '+SECRET+'\nkeystore2: Error::Km(-67)\n'
        'vold: Falling back to non-rollback resistant key\n')
    (second / 'logcat.log').write_text('linker: CANNOT LINK EXECUTABLE "/vendor/bin/hw/android.hardware.gatekeeper@1.0-service-qti": library not found '+SECRET+'\n')
    (first / 'qsee_log').write_text('[01]<8>keymaster64: km_add_key_id:122:-56\r\n'
        '[02]<8>keymaster64: km_add_key_id:101:-8\r\n'
        '[03]<8>keymaster64: addkeyId:219:-56\n' # wrapper is not a second scan
        '[04]<8>other_function:122:-56\n'
        '[05]<8>keymaster64: km_add_key_id:122:-56 private='+SECRET+'\n')
    out = run(root)
    assert 'A9_PIN verdict=1 source=1' in out and 'verified=1 locked=1 unlocked=1 removed=1' in out
    assert 'boot=1 start=1 link=1' in out and 'boot=2 start=-1 link=0' in out
    assert 'root=1 errno=30 count=1 failed=1 reads=0 writes=1 creates=1' in out
    assert 'root=3 errno=0 count=1 failed=0 reads=0 writes=1 creates=1' in out
    assert 'root=3 errno=13 count=1 failed=1 reads=0 writes=1 creates=1' in out
    assert 'km_m10003=1 km_m67=1 rollback_fallback=1' in out
    assert 'ta_key_id_failed=2 ta_key_id_full=1' in out
    assert 'valid=1 boots=2 read_errors=0' in out and '0120' not in out
    assert 'A9_RPMB_RESULT boot=1 command=3 status_valid=1 operation_rc=-1' in out
    assert 'A9_RPMB_REJECT boot=1 reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=3' in out
    assert out.count('A9_RPMB_RESULT') == out.count('A9_RPMB_REJECT') == 1
    assert 'A9_KMTB_WRITE boot=1 phase=2 allowed=1 operation=1 reason=0 changed=1' in out
    assert out.count('A9_RPMB_MAP') == 1 and 'A9_RPMB_MAP boot=1 phase=2 valid=1' in out
    assert 'A9_RPMB_REQUEST_TRACE boot=1 command=3 shape=1 reason=0 request=3 frames=34 input_frames=34 offset=24 first=2050 count=34 map_valid=0 context=10 table_candidate=0 table_valid=0' in out
    assert 'A9_RPMB_WRITE_TRACE boot=1 allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=34 context=10' in out
    assert 'A9_RPMB_BOOTSTRAP boot=1 phase=5 rc=0' in out
    assert 'A9_RPMB_GUARD boot=1 mode=2 allowed=1 operation=5 reason=0' in out
    print('PASS complete verdict, boot/status distinction, numeric-only privacy')

    # Derived exports cannot double counts; identical raw calls stay separate.
    sfs = 'A9_SFS_OPEN root=1 op=1 access=1 create=1 errno=30 result=0\n'
    with (first / 'logcat.log').open('a') as f:
        f.write(sfs + sfs)
    (first / 'qseecomd-logcat.log').write_text('Error::Km(-67)\n' * 100)
    (first / 'kernel.early.log').write_text((first / 'kernel.log').read_text() + sfs)
    out = run(root)
    assert 'root=1 errno=30 count=3 failed=3' in out
    assert out.count('A9_PERSIST_RO boot=1 phase=3 rc=0') == 1
    assert 'km_m67=1 ' in out
    print('PASS raw-source precedence, kernel mirror dedupe and repeated opens')

    # Owner stdout fallback and authoritative JSON precedence, no secret echoes.
    (root / 'acceptance.json').unlink()
    adjacent = root.parent / (root.name + '.out')
    adjacent.write_text('29_pin_lifecycle FAIL '+json.dumps({'verified': 0, 'cleanup_ok': 0, 'private': SECRET})+'\n')
    assert 'A9_PIN verdict=0 source=2' in run(root)
    (root / 'acceptance.json').write_text(json.dumps({'29_pin_lifecycle': {'status': 'NOT_RUN', 'evidence': {}}}))
    assert 'A9_PIN verdict=2 source=1' in run(root)
    print('PASS PIN fallback and stored-verdict precedence')

    # Missing/malformed evidence stays unknown; no path or JSON traceback.
    empty = base / 'empty'
    empty.mkdir()
    assert 'A9_PIN verdict=-1 source=0' in run(empty)
    (empty / 'acceptance.json').write_text(SECRET)
    assert 'read_errors=1' in run(empty)
    (empty / 'acceptance.json').write_text(json.dumps({'29_pin_lifecycle': {'status': [], 'evidence': []}}))
    assert 'verdict=-1' in run(empty)
    assert 'valid=0 boots=0 read_errors=1' in run(base / SECRET, rc=2)
    print('PASS absent/malformed evidence and numeric-only failure')

    # Manual layout, strict public bounds and refusal of linked log sources.
    manual = base / 'manual-sweep' / 'manual'
    manual.mkdir(parents=True)
    (manual / 'logcat.txt').write_text('A9_SFS_OPEN root=1 op=1 access=0 create=0 errno=4096 result=0\n'
        'A9_PERSIST_RO phase=3 rc=0 private='+SECRET+'\nError::Km(-67)\n')
    (manual / 'kernel.log').symlink_to(first / 'kernel.log')
    out = run(manual.parent)
    assert 'kernel_files=0' in out and 'root=1 errno=-1 count=0' in out
    assert 'A9_PERSIST_RO boot=1 records=0' in out and 'km_m67=1' in out
    print('PASS manual layout, strict diagnostic records and symlink exclusion')
