#!/usr/bin/env python3
"""Regression (review 72 finding 1): the D1 bundle's live.sha256 must carry the D0
attempt-3 corrections. Usage: test-d1-live.py <d1 bundle> [<d0 fix4 bundle>]."""
import hashlib, sys
from pathlib import Path
EMPTY = hashlib.sha256(b'').hexdigest()
MASKED = ['/system/etc/permissions/android.hardware.bluetooth.xml',
          '/system/etc/permissions/android.hardware.bluetooth_le.xml',
          '/system/etc/permissions/android.hardware.fingerprint.xml']
MEM = '/sys/firmware/devicetree/base/memory@80000000/reg'
def load(b):
    rows = {}
    for l in (Path(b) / 'live.sha256').read_text().splitlines():
        h, p = l.split('  ', 1); assert p not in rows; rows[p] = h
    return rows
d1 = load(sys.argv[1])
for p in MASKED: assert d1[p] == EMPTY, ('masked file not the empty hash', p)
assert MEM not in d1, 'memory@80000000/reg must not be in the live manifest'
assert '/sys/firmware/devicetree/base/memory@80000000/device_type' in d1, 'sibling memory property must stay'
assert any(p.startswith('/sys/firmware/devicetree/base/reserved-memory/') for p in d1), 'reserved-memory checks must stay'
assert '/sys/kernel/notes' in d1
if len(sys.argv) > 2:
    d0 = load(sys.argv[2])
    only0 = sorted(set(d0) - set(d1)); only1 = sorted(set(d1) - set(d0))
    diff = sorted(p for p in set(d0) & set(d1) if d0[p] != d1[p])
    # firmware, kernel notes and the unchanged DT/system/vendor must agree with the working D0 fix4 list.
    # The ONLY legitimate list differences are the D1 DT delta itself (APR no longer disabled,
    # new /sound nodes), the kernel notes (new kernel) and nothing else.
    B = '/sys/firmware/devicetree/base'
    assert only0 == [B + '/soc@0/remoteproc@a400000/glink-edge/apr/status'], only0
    assert only1 == sorted(B + '/sound/' + x for x in ('compatible', 'mm1-dai-link/cpu/sound-dai',
        'mm1-dai-link/link-name', 'mm2-dai-link/cpu/sound-dai', 'mm2-dai-link/link-name')) or \
        only1 == sorted(B + '/sound/' + x for x in ('compatible', 'model', 'mm1-dai-link/cpu/sound-dai',
        'mm1-dai-link/link-name', 'mm2-dai-link/cpu/sound-dai', 'mm2-dai-link/link-name')), only1
    # dtc renumbers unreferenced-label phandles by +1 once q6asmdai is referenced (check-d1-dt.py
    # proves no referencing cell differs); the new kernel changes the notes.
    # New kernel build: its vendor modules, the thermal HAL pairing files and the notes change.
    rest = [p for p in diff if not p.endswith('/phandle') and not p.startswith('/vendor/lib/modules/')]
    assert rest == ['/sys/kernel/notes', '/vendor/bin/hw/android.hardware.thermal-service.a9',
                    '/vendor/etc/thermal-a9.kernel-image.sha256'] or sorted(rest) == sorted(
                    ['/sys/kernel/notes', '/vendor/bin/hw/android.hardware.thermal-service.a9',
                     '/vendor/etc/thermal-a9.kernel-image.sha256']), rest
    assert not any(p.startswith('/system/') for p in diff), 'system files must be identical to D0'
print('D1 live manifest corrections PASS:', len(d1), 'entries')
