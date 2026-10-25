#!/usr/bin/env python3
"""Byte-exact offline tests for s2-sns / s2-sns-probe; never opens a socket to a phone.

An independent encoder (this file, written from the layout table in
scripts/mainline/sensors/s2-sns-layouts.md, not from the C code) produces every
frame the probe must send and every SSC reply. The C harness fails on the first
byte that differs. Native and, with --qemu, static AArch64 musl.
"""
import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'scripts/mainline/sensors'
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--cc', default=shutil.which('cc'))
p.add_argument('--qemu', type=Path)
a = p.parse_args()

# ---- independent protobuf (proto2 wire) ----
def varint(v):
    out = b''
    while True:
        c = v & 0x7f; v >>= 7
        out += bytes([c | (0x80 if v else 0)])
        if not v: return out
def key(f, w): return varint(f << 3 | w)
def fx32(f, v): return key(f, 5) + struct.pack('<I', v)
def fx64(f, v): return key(f, 1) + struct.pack('<Q', v)
def sfx64(f, v): return key(f, 1) + struct.pack('<q', v)
def flt(f, v): return key(f, 5) + struct.pack('<f', v)
def vint(f, v): return key(f, 0) + varint(v)
def ld(f, b): return key(f, 2) + varint(len(b)) + b

LOOKUP = (0xabababababababab, 0xabababababababab)
def suid_msg(s): return fx64(1, s[0]) + fx64(2, s[1])
def client_request(suid, msg_id, delivery, payload=None, batching=None):
    req = b''
    if batching is not None: req += ld(1, vint(1, batching))
    if payload is not None: req += ld(2, payload)
    return ld(1, suid_msg(suid)) + fx32(2, msg_id) + ld(3, vint(1, 1) + vint(2, delivery)) + ld(4, req)
def suid_req(dt, default_only=True): return ld(1, dt.encode()) + vint(2, 1) + vint(3, 1 if default_only else 0)

# ---- independent QMI ----
def tlv(t, v): return bytes([t]) + struct.pack('<H', len(v)) + v
def qmi(kind, txn, msg, body): return bytes([kind]) + struct.pack('<HHH', txn, msg, len(body)) + body
def q_req(txn, pb): return qmi(0, txn, 0x20, tlv(1, struct.pack('<H', len(pb)) + pb) + tlv(0x10, b'\x01'))
def q_resp(txn, cid, result=0, error=0): return qmi(2, txn, 0x20, tlv(2, struct.pack('<HH', result, error)) + tlv(0x10, struct.pack('<Q', cid)))
def q_ind(cid, pb, jumbo=False): return qmi(4, 0, 0x22 if jumbo else 0x21, tlv(1, struct.pack('<Q', cid)) + tlv(2, struct.pack('<H', len(pb)) + pb))
def event_msg(suid, events):
    return ld(1, suid_msg(suid)) + b''.join(ld(2, fx32(1, m) + fx64(2, ts) + ld(3, pl)) for m, ts, pl in events)
def suid_event(dt, suids): return ld(1, dt.encode()) + b''.join(ld(2, suid_msg(s)) for s in suids)
def attr(i, data): return ld(1, vint(1, i) + ld(2, b''.join(ld(1, d) for d in data)))
def attr_event(name, vendor, typ, stream_type, rates=(), nul=False):
    z = b'\x00' if nul else b''   # F1: the SSC sends C strings with their terminator (S2a: "stk_stk3b6x?")
    return (attr(0, [ld(2, name.encode() + z)]) + attr(1, [ld(2, vendor.encode() + z)]) + attr(2, [ld(2, typ.encode() + z)]) +
            attr(3, [vint(5, 1)]) + attr(16, [sfx64(4, stream_type)]) + (attr(6, [flt(3, r) for r in rates]) if rates else b''))
def sensor_event(vals, status=3): return b''.join(flt(1, v) for v in vals) + vint(2, status)

ALS = (0x1111111111111111, 0x2222222222222222)
ACC = (0x3333333333333333, 0x4444444444444444)
PROX = (0x5555555555555555, 0x6666666666666666)

def lookup(dt, suids, cid, early=False, error=None, mismatch=False, late=False, big=False):
    send = [('SEND', q_req(1, client_request(LOOKUP, 512, 0, suid_req(dt))))]
    pad = [(9999, 999, b'\x00' * 3000)] if big else []   # review 79j N2: a jumbo early indication (> 1100 bytes)
    ind = q_ind(cid + (1 if mismatch else 0), event_msg(LOOKUP, pad + [(768, 1000, suid_event(dt, suids))]), jumbo=big)
    if error: return send + [('RECV', q_resp(1, cid, *error))]
    if late: return send + [('RECV', q_resp(1, cid)), ('RECV', q_ind(cid, event_msg(LOOKUP, [(768, 999, suid_event(dt, []))]))), ('RECV', ind)]
    return send + ([('RECV', ind), ('RECV', q_resp(1, cid))] if early else [('RECV', q_resp(1, cid)), ('RECV', ind)])
def attrs(s, cid, ev):
    return [('SEND', q_req(1, client_request(s, 1, 1, b''))), ('RECV', q_resp(1, cid)),
            ('RECV', q_ind(cid, event_msg(s, [(128, 2000, ev)])))]
def stream(s, cid, msg, payload, events, jumbo=False):
    out = [('SEND', q_req(1, client_request(s, msg, 1, payload, batching=0))), ('RECV', q_resp(1, cid))]
    for chunk in events:
        out.append(('RECV', q_ind(cid, event_msg(s, chunk), jumbo)))
    return out + [('SEND', q_req(2, client_request(s, 10, 1))), ('RECV', q_resp(2, cid))]

def scenario(*, als_suids=(ALS,), early=False, jumbo=False, accel=(0.12, -0.05, 9.79), acc_error=False,
             lookup_error=False, mismatch=False, late=False, big=False, accel_name='kxtj3', nul=False):
    steps = lookup('ambient_light', als_suids, 0x10, early=early, late=late, big=big, error=(1, 0x30) if lookup_error else None, mismatch=mismatch)
    if lookup_error or mismatch: return steps
    steps += lookup('accel', [ACC], 0x20) + lookup('proximity', [PROX], 0x30)
    if not als_suids: return steps
    steps += attrs(ALS, 0x40, attr_event('stk3b6x', 'sensortek', 'ambient_light', 1, nul=nul))
    steps += attrs(ACC, 0x50, attr_event(accel_name, 'kionix', 'accel', 0, (25.0, 50.0, 100.0), nul=nul))
    steps += attrs(PROX, 0x60, attr_event('stk3b6x', 'sensortek', 'proximity', 1, nul=nul))
    if accel_name not in ('kxtj3', 'stk8baxx'): return steps   # N1: probe stops before any enable
    steps += stream(ALS, 0x70, 514, b'', [[(768, 3000, b'\x08\x01'), (1025, 3001, sensor_event([123.5]))]])
    acc_chunks = [[(1025, 4000 + i, sensor_event(list(accel)))] for i in range(12)]
    if acc_error: acc_chunks.insert(3, [(130, 4100, vint(1, 5))])
    steps += stream(ACC, 0x80, 513, flt(1, 50.0), acc_chunks, jumbo)
    if acc_error:   # the probe stops reading at the error event, then disables
        steps = steps[:steps.index(('RECV', q_ind(0x80, event_msg(ACC, acc_chunks[3]), jumbo))) + 1] + steps[-2:]
    return steps

CASES = {
    'pass': (scenario(), 'S2-PROBE-PASS'),
    'pass-early-indication': (scenario(early=True), 'S2-PROBE-PASS'),
    'pass-jumbo-indications': (scenario(jumbo=True), 'S2-PROBE-PASS'),
    'pass-late-suid': (scenario(late=True), 'S2-PROBE-PASS'),
    'pass-early-jumbo-indication': (scenario(early=True, big=True), 'S2-PROBE-PASS'),
    'pass-nul-terminated-attrs': (scenario(nul=True), 'S2-PROBE-PASS'),
    'unexpected-part': (scenario(accel_name='bmi160'), 'S2-PROBE-FAIL unexpected-part dt=accel'),
    'qmi-error': (scenario(lookup_error=True), 'S2-PROBE-FAIL qmi-error suid-req result=1 error=48 sns=0'),
    'client-id-mismatch': (scenario(mismatch=True), 'S2-PROBE-FAIL suid-event ambient_light'),
    'missing-als': (scenario(als_suids=()), 'S2-PROBE-FAIL missing-sensor als=0 accel=1'),
    'accel-out-of-range': (scenario(accel=(0.1, 0.1, 2.0)), 'S2-PROBE-FAIL criteria als=1 accel=0'),
    'sns-error-event': (scenario(acc_error=True), 'S2-PROBE-FAIL sns-error dt=accel error=5'),
}

def build(td, cc, qemu):
    out = Path(td) / ('t-arm64' if qemu else 't-native')
    argv = [cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-D_GNU_SOURCE'] + (['-static'] if qemu else []) + \
           [str(ROOT / 'tests/mainline-sensors/test-s2-sns.c'), '-lm', '-o', str(out)]
    subprocess.run(argv, check=True)
    return ([str(qemu)] if qemu else []) + [str(out)]

with tempfile.TemporaryDirectory(prefix='s2-sns-') as td:
    run = build(td, a.cc, a.qemu)
    r = subprocess.run(run + ['--units'], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    print(r.stdout.strip())
    for name, (steps, marker) in CASES.items():
        path = Path(td) / (name + '.txt')
        path.write_text(''.join(f'{k} {v.hex()}\n' for k, v in steps))
        r = subprocess.run(run + [str(path), marker], capture_output=True, text=True)
        assert r.returncode == 0, (name, r.stdout, r.stderr)
        if name.startswith('pass'):   # F1: no '?' from a trailing NUL, names exactly as sent
            attrs_out = [l for l in r.stdout.splitlines() if l.startswith('S2-ATTR')]
            assert len(attrs_out) == 3 and not any('?' in l for l in attrs_out), (name, attrs_out)
            assert 'name="stk3b6x" vendor="sensortek" type="ambient_light"' in attrs_out[0], attrs_out[0]
        print('PASS s2-sns case', name, '->', marker)
        if name == 'pass':
            for line in r.stdout.splitlines():
                if line.startswith(('S2-SUID', 'S2-ATTR', 'S2-PROBE-RESULT', 'S2-STREAM-END')): print('   ', line)
print('S2-SNS OFFLINE TEST PASS', 'aarch64-qemu' if a.qemu else 'native')
