#!/usr/bin/env python3
"""Minimal Android DTBO table writer (the mkdtboimg format the A9 ABL reads):
usage: python3 -I mkdtbo.py out.img overlay1.dtbo [overlay2.dtbo ...]
Header and entries big-endian, magic 0xd7b7ab1e, page size 4096, entry id/rev 0."""
import struct, sys

out, dts = sys.argv[1], [open(p, 'rb').read() for p in sys.argv[2:]]
HDR, ENT = 32, 32
off = HDR + ENT * len(dts)
entries, blobs = b'', b''
for d in dts:
    entries += struct.pack('>8I', len(d), off + len(blobs), 0, 0, 0, 0, 0, 0)
    blobs += d
total = off + len(blobs)
hdr = struct.pack('>8I', 0xd7b7ab1e, total, HDR, ENT, len(dts), HDR, 4096, 0)
open(out, 'wb').write(hdr + entries + blobs)
