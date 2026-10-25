#!/usr/bin/env python3
"""dt-reserved-audit.py -- effective reserved-memory map of a built DTB vs the
stock base DTB (review 27 item 2). Offline.

  dt-reserved-audit.py <our.dtb|.dts> <stock base_0.dts|.dtb> [--baseline <other dtb|dts>] [--gpu-okay]

--gpu-okay additionally requires (DTB input) /soc@0/gpu@5900000 status "okay"
and its zap-shader memory-region to be the reserved node at 0x55615000.

PASS requires: (1) every stock no-map region with a fixed reg is fully covered
by our no-map regions; (2) no two of our static regions overlap; (3) with
--baseline: every no-map byte of the baseline DTB is still no-map (the GPU
DTB may only add reservations).
Prints the full effective map."""
import re, sys

def fdt_nodes(path):
    """minimal flattened-device-tree reader: {node path: {prop: bytes}}"""
    import struct
    d = open(path, 'rb').read()
    magic, _, off_struct, off_strings = struct.unpack_from('>4I', d, 0)
    assert magic == 0xd00dfeed, f'{path}: not a DTB'
    nodes, stack, i = {}, [], off_struct
    while True:
        tok = struct.unpack_from('>I', d, i)[0]; i += 4
        if tok == 1:
            e = d.index(b'\0', i); name = d[i:e].decode(); i = (e + 4) & ~3
            stack.append(name); nodes['/'.join(stack) or '/'] = {}
        elif tok == 2: stack.pop()
        elif tok == 3:
            ln, no = struct.unpack_from('>2I', d, i); i += 8
            pn = d[off_strings + no:d.index(b'\0', off_strings + no)].decode()
            nodes['/'.join(stack) or '/'][pn] = d[i:i + ln]; i = (i + ln + 3) & ~3
        elif tok == 4: continue
        elif tok == 9: break
    return nodes

def regions(path):
    if open(path, 'rb').read(4) == b'\xd0\x0d\xfe\xed':
        import struct
        out = []
        for k, pr in fdt_nodes(path).items():
            if k.startswith('/reserved-memory/') and k.count('/') == 2 and 'reg' in pr and len(pr['reg']) == 16:
                a, b, c, e = struct.unpack('>4I', pr['reg'])
                out.append(((a << 32) | b, (c << 32) | e, k.split('/')[-1], 'no-map' in pr))
        return sorted(out)
    txt = open(path).read()
    i = txt.find('reserved-memory {')
    if i < 0: sys.exit(f'{path}: no reserved-memory')
    depth, j = 0, i
    while True:
        c = txt[j]
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: break
        j += 1
    body = txt[i:j]
    out = []
    for m in re.finditer(r'\n\t\t([\w,@.-]+) \{(.*?)\n\t\t\};', body, re.S):
        name, b = m.group(1), m.group(2)
        r = re.search(r'\breg = <([^>]*)>', b)
        if not r: continue
        v = [int(x, 16) for x in r.group(1).split()]
        base, size = (v[0] << 32 | v[1]), (v[2] << 32 | v[3])
        out.append((base, size, name, 'no-map' in b))
    return sorted(out)

def covered(lo, hi, regs):
    pos = lo
    for b, s, _, nm in regs:
        if not nm: continue
        if b <= pos < b + s: pos = b + s
        if pos >= hi: return True
    return pos >= hi

args = sys.argv[1:]
base_path = None
gpu_okay = '--gpu-okay' in args
args = [a for a in args if a != '--gpu-okay']
if '--baseline' in args:
    k = args.index('--baseline'); base_path = args[k + 1]; del args[k:k + 2]
ours, stock = regions(args[0]), regions(args[1])
ok = True
print('effective reserved-memory (ours):')
for b, s, n, nm in ours:
    print(f'  {b:#012x}-{b + s:#012x} {s:#10x} {"no-map" if nm else "mapped"} {n}')
for x, y in zip(ours, ours[1:]):
    if x[0] + x[1] > y[0]:
        ok = False; print(f'  OVERLAP {x[2]} {x[0]:#x}+{x[1]:#x} with {y[2]} {y[0]:#x}')
for b, s, n, nm in stock:
    if not nm: continue
    c = covered(b, b + s, sorted(ours))
    print(f'  stock {n:38s} {b:#012x}+{s:#9x} {"covered" if c else "NOT COVERED"}')
    ok &= c
if base_path:
    for b, s, n, nm in regions(base_path):
        if nm and not covered(b, b + s, ours):
            ok = False; print(f'  baseline {n} {b:#x}+{s:#x} is no longer fully reserved: FAIL')
    if ok: print('  every baseline no-map byte is still reserved')
if gpu_okay:
    import struct
    nd = fdt_nodes(args[0])
    g = nd.get('/soc@0/gpu@5900000', {})
    st = g.get('status', b'okay\0').rstrip(b'\0').decode()
    z = nd.get('/soc@0/gpu@5900000/zap-shader', {})
    ph = struct.unpack('>I', z['memory-region'])[0] if 'memory-region' in z else None
    tgt = [k for k, pr in nd.items() if 'phandle' in pr and struct.unpack('>I', pr['phandle'])[0] == ph]
    fw = z.get('firmware-name', b'').rstrip(b'\0').decode()
    good = st == 'okay' and tgt == ['/reserved-memory/memory@55615000'] and fw == 'qcom/sm6115/hisense/hlte556n/a610_zap.mbn'
    print(f'  gpu@5900000 status {st}, zap region {tgt}, zap firmware {fw}: {"ok" if good else "FAIL"}')
    ok &= good
print('dt-reserved-audit:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
