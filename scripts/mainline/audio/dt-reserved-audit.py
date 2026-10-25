#!/usr/bin/env python3
"""dt-reserved-audit.py -- effective reserved-memory map of a built audio DTB
vs the stock base DTB (review 33 F1/F2). Offline, read-only. Derived from the
gpu branch's scripts/mainline/android/gpu/dt-reserved-audit.py (review 27).

  dt-reserved-audit.py <our.dtb|.dts> <stock base_0.dts|.dtb>
                       [--baseline <android dtb|dts>] [--adsp okay|disabled]

PASS requires: (1) every stock no-map region with a fixed reg is fully covered
by our no-map regions; (2) no two of our static regions overlap; (3) with
--baseline: every no-map byte of the baseline DTB is still no-map (audio may
only add reservations); (4) with --adsp (DTB input): /soc@0/remoteproc@a400000
has exactly that status, and pil_adsp_mem is the stock 0x53800000+0x1e00000.
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
adsp = None
if '--adsp' in args:
    k = args.index('--adsp'); adsp = args[k + 1]; del args[k:k + 2]
    if adsp not in ('okay', 'disabled'): sys.exit('--adsp okay|disabled')
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
if adsp:
    nd = fdt_nodes(args[0])
    r = nd.get('/soc@0/remoteproc@a400000')
    st = None if r is None else r.get('status', b'okay\0').rstrip(b'\0').decode()
    mem = [(b, s) for b, s, n, nm in ours if n == 'memory@53800000' and nm]
    good = st == adsp and mem == [(0x53800000, 0x1e00000)]
    print(f'  remoteproc@a400000 (ADSP) status {st} (want {adsp}), pil_adsp_mem {[(hex(b), hex(s)) for b, s in mem]}: {"ok" if good else "FAIL"}')
    ok &= good
print('dt-reserved-audit:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
