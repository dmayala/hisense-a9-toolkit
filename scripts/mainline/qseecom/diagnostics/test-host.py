#!/usr/bin/env python3
"""Production C parser/queue plus actual arm64 logdw datagrams under QEMU."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import socket
import struct
import subprocess as sp
import time
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('build_diag', HERE / 'build.py')
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
QEMU = '/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
p = argparse.ArgumentParser(description=__doc__); p.add_argument('--out', required=True, type=Path)
a = p.parse_args(); out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
elf = out / 'test-diag'
sp.run(list(map(str, [b.CC, '-O2', '-Wall', '-Wextra', '-Werror', '-static', HERE/'test-diag.c',
                     HERE/'diag.c', HERE/'transport.c', '-o', elf])), check=True)
r = sp.run([QEMU, str(elf)], capture_output=True, text=True, timeout=10)
(out / 'parser-queue.log').write_text(r.stdout+r.stderr)
assert r.returncode == 0, r.stdout+r.stderr
records = [f'A9_KMTB_READ phase=2 valid=1' if i % 4 == 0 else
           f'A9_KMTB_WRITE phase=2 allowed=1 operation=1 reason=0 changed=1' if i % 4 == 1 else
           f'A9_RPMB_RESULT command=3 status_valid=1 operation_rc={-i}' if i % 4 == 2 else
           f'A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames={i} frame_valid=1 request=3'
           for i in range(64)]
records[0]='A9_RPMB_REQUEST_TRACE command=3 shape=1 reason=0 request=3 frames=34 input_frames=34 offset=24 first=2050 count=34 map_valid=0 context=10 table_candidate=0 table_valid=0'
records[2]='A9_RPMB_BOOTSTRAP phase=5 rc=-74'
records[1]='A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=34 context=10'
input_path = out / 'synthetic-kmsg.txt'
input_path.write_text(''.join(f'6,{i+1},{i+1},-;{line}\n' for i,line in enumerate(records)))
sockpath = out / 'logdw.sock'
assert not sockpath.exists()
child = sp.Popen([QEMU, str(elf), str(input_path), str(sockpath)], stdout=sp.PIPE, stderr=sp.PIPE, text=True)
# Delay logd, then fill its queue before draining. Both paths retain queue head.
time.sleep(0.4)
sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); sock.bind(str(sockpath)); sock.settimeout(5)
time.sleep(0.4)
received = []
try:
    for _ in records:
        data = sock.recv(1024)
        ident, tid, sec, nsec = struct.unpack('<BHII', data[:11])
        assert ident == 0 and tid and sec and nsec < 1_000_000_000
        assert data[11] == 5
        tag, text, empty = data[12:].split(b'\0')
        assert tag == b'A9_RPMB_DIAG' and not empty
        received.append(text.decode('ascii'))
    stdout, stderr = child.communicate(timeout=10)
finally:
    sock.close(); sockpath.unlink(missing_ok=True)
    if child.poll() is None:
        child.kill(); child.wait()
(out / 'replay.log').write_text(stdout+stderr)
assert child.returncode == 0, stdout+stderr
assert received == records
# Compare production C against the coordinator extractor's public fixtures.
extractor_path = HERE.parents[1] / 'reboot/rpmb-evidence.py'
spec = importlib.util.spec_from_file_location('rpmb_evidence', extractor_path)
extractor = importlib.util.module_from_spec(spec); spec.loader.exec_module(extractor)
tree = ast.parse((extractor_path.parent / 'test-rpmb-evidence.py').read_text())
fixtures = next(ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'records' for t in n.targets))
userspace = [v for v in fixtures if v.startswith(('A9_RPMB_DIAG ', 'A9_QSEE_INIT ',
                                                'A9_ATTEST_FS', 'A9_RPMB_BACKEND', 'A9_SFS_OPEN '))]
fixtures = [v for v in fixtures if v not in userspace]
fixtures += ['A9_RPMB_SCM check=smc_return transport=0 status_valid=1 secure_status='+v+' mapped_rc=-22'
             for v in ('4294967294', '-9223372036854775808', '9223372036854775807')]
fixtures += [fixtures[0]+' secret=hidden', fixtures[1].replace('rc=0','rc=2147483648'),
             'A9_RPMB_SCM check=unknown_convention rc=0',
             'A9_RPMB_SCM check=missing_ext_pool nargs=3 rc=0']
raw = ''.join('6,1,1,-;'+v+'\n' for v in fixtures)
r = sp.run([QEMU, str(elf), '--filter'], input=raw, capture_output=True, text=True, timeout=10)
assert r.returncode == 0
assert r.stdout.splitlines() == [extractor.public_record(v) or 'REJECT' for v in fixtures]
# Already-userspace observations are retained by the offline collector, not
# accepted as new kernel input. Keep the bridge's producer boundary explicit.
raw = ''.join('6,1,1,-;'+v+'\n' for v in userspace)
r = sp.run([QEMU, str(elf), '--filter'], input=raw, capture_output=True, text=True, timeout=10)
assert r.returncode == 0 and r.stdout.splitlines() == ['REJECT'] * len(userspace)
assert all(extractor.public_record(v) for v in userspace)
# Production main must back off on EOF/EINVAL/EIO instead of polling a
# persistently readable/broken descriptor in a busy loop.
loop = out / 'test-loop'
sp.run(list(map(str, [b.CC, '-O2', '-Wall', '-Wextra', '-Werror', '-static',
                     HERE/'test-loop.c', HERE/'diag.c', '-o', loop])), check=True)
for mode in ('0', '1', '2'):
    check = sp.run([QEMU, str(loop), mode], capture_output=True, text=True, timeout=10)
    assert check.returncode == 0, check.stdout+check.stderr
(out / 'loop.log').write_text('PASS EOF/EINVAL/EIO: three bounded500ms sleeps, cursor retained\n')
result = dict(production_main_fault_backoff='PASS', c_python_schema_agreement=len(fixtures), production_parser='PASS' , bounded_queue_retry_order_no_duplicates='PASS',
              arm64_real_datagram_replay='PASS', datagrams=len(received), wire_header_bytes=11,
              delayed_logd=True, socket_backpressure=True)
(out / 'tests.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
