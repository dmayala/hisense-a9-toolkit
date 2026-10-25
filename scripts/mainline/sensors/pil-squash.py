#!/usr/bin/env python3
"""pil-squash.py <name.mdt> <out.mbn> -- join a split Qualcomm PIL image
(<name>.mdt + <name>.bNN) into one ELF (.mbn), as linaro's pil-squasher does:
ELF header + program headers from the .mdt, each segment N with p_filesz > 0
copied from <name>.bNN to its p_offset. Segments without a .bNN must have
p_filesz == 0 (else refused). Offline; mainline qcom_q6v5_pas loads the .mbn."""
import os, struct, sys
mdt, out = sys.argv[1:3]
base = mdt[:-4]
d = open(mdt, 'rb').read()
if d[:4] != b'\x7fELF': sys.exit('not an ELF .mdt')
cls = d[4]
if cls == 1:
    e_phoff, = struct.unpack_from('<I', d, 0x1c); e_phentsize, e_phnum = struct.unpack_from('<HH', d, 0x2a)
    fmt, = ('<IIIIIIII',)
else:
    e_phoff, = struct.unpack_from('<Q', d, 0x20); e_phentsize, e_phnum = struct.unpack_from('<HH', d, 0x36)
    fmt = '<IIQQQQQQ'
segs = []
for i in range(e_phnum):
    f = struct.unpack_from(fmt, d, e_phoff + i * e_phentsize)
    if cls == 1: p_type, p_off, p_va, p_pa, p_filesz, p_memsz, p_flags, p_align = f
    else: p_type, p_flags, p_off, p_va, p_pa, p_filesz, p_memsz, p_align = f
    segs.append((i, p_off, p_filesz))
size = max([len(d)] + [o + s for _, o, s in segs])
img = bytearray(size)
img[:len(d)] = d
for i, off, sz in segs:
    if sz == 0: continue
    p = f'{base}.b{i:02d}'
    if not os.path.exists(p):
        # segment 0 (ELF header) and 1 (hash) may live inside the .mdt itself
        if off + sz <= len(d): continue
        sys.exit(f'missing {p} for segment {i} ({sz} bytes)')
    b = open(p, 'rb').read()
    if len(b) != sz: sys.exit(f'{p}: {len(b)} bytes, phdr says {sz}')
    img[off:off + sz] = b
open(out, 'wb').write(img)
print(f'{out}: {len(img)} bytes, {sum(1 for s in segs if s[2])} loadable segments')
