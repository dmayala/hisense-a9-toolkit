#!/usr/bin/env python3
"""eink-compare.py <screencap.png> <grey.bin> <outdir>

Compare what SurfaceFlinger composed (adb screencap, 824x1648 RGBA portrait)
with what the a9_eink driver holds as its last update's grey
(/sys/kernel/debug/a9_eink/grey: 1648x824 bytes, landscape). The screencap is
put through the driver's own grey stage (a9e_fb_to_grey in
drivers/gpu/drm/a9_eink/a9_eink_engine.c, the stock composer's luma + rotation):
  v = (76*R + 150*G + 30*B) >> 8      (AB24/XB24: bytes R,G,B,X)
  grey[(823 - c) * 1648 + r] = v(fb row r, column c)
i.e. a 90 degree counter-clockwise rotation. Prints exact-match share, mean
absolute difference and PSNR, writes ref-grey.png, driver-grey.png, diff.png and
side-by-side.png (8-bit greyscale PNGs, landscape) into <outdir>.
Pure Python (zlib only): no Pillow needed.
"""
import struct
import sys
import zlib

FB_W, FB_H = 824, 1648          # portrait framebuffer
GW, GH = FB_H, FB_W             # landscape grey: 1648 wide, 824 high


def png_read(path):
    d = open(path, 'rb').read()
    assert d[:8] == b'\x89PNG\r\n\x1a\n', 'not a PNG'
    i, idat, w = 8, b'', None
    while i < len(d):
        n, t = struct.unpack('>I4s', d[i:i + 8]); c = d[i + 8:i + 8 + n]; i += 12 + n
        if t == b'IHDR':
            w, h, bd, ct, _, _, il = struct.unpack('>IIBBBBB', c)
            assert bd == 8 and ct in (2, 6) and il == 0, f'unsupported PNG {bd} {ct} {il}'
            bpp = 4 if ct == 6 else 3
        elif t == b'IDAT':
            idat += c
        elif t == b'IEND':
            break
    raw = zlib.decompress(idat)
    stride = w * bpp
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]; line = bytearray(raw[p + 1:p + 1 + stride]); p += 1 + stride
        if f == 1:
            for x in range(bpp, stride): line[x] = (line[x] + line[x - bpp]) & 255
        elif f == 2:
            for x in range(stride): line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]; c = prev[x - bpp] if x >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, bpp, out


def png_write_grey(path, w, h, pix):
    raw = b''.join(b'\x00' + bytes(pix[y * w:(y + 1) * w]) for y in range(h))
    def chunk(t, c): return struct.pack('>I', len(c)) + t + c + struct.pack('>I', zlib.crc32(t + c) & 0xffffffff)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def main():
    cap, greyf, out = sys.argv[1:4]
    w, h, bpp, px = png_read(cap)
    if (w, h) != (FB_W, FB_H):
        sys.exit(f'screencap is {w}x{h}, expected {FB_W}x{FB_H} portrait')
    ref = bytearray(GW * GH)
    for r in range(FB_H):
        row = r * w * bpp
        for c in range(FB_W):
            o = row + c * bpp
            ref[(FB_W - 1 - c) * GW + r] = (76 * px[o] + 150 * px[o + 1] + 30 * px[o + 2]) >> 8
    drv = open(greyf, 'rb').read()
    if len(drv) != GW * GH:
        sys.exit(f'grey is {len(drv)} bytes, expected {GW * GH}')
    n = GW * GH
    eq = sum(1 for a, b in zip(ref, drv) if a == b)
    sad = sum(abs(a - b) for a, b in zip(ref, drv))
    sse = sum((a - b) * (a - b) for a, b in zip(ref, drv))
    import math
    psnr = float('inf') if sse == 0 else 10 * math.log10(255 * 255 * n / sse)
    diff = bytearray(min(255, abs(a - b) * 4) for a, b in zip(ref, drv))
    side = bytearray()
    for y in range(GH):
        side += ref[y * GW:(y + 1) * GW] + b'\x80' * 8 + drv[y * GW:(y + 1) * GW]
    png_write_grey(f'{out}/ref-grey.png', GW, GH, ref)
    png_write_grey(f'{out}/driver-grey.png', GW, GH, drv)
    png_write_grey(f'{out}/diff.png', GW, GH, diff)
    png_write_grey(f'{out}/side-by-side.png', 2 * GW + 8, GH, side)
    blank = sum(1 for b in drv if b) == 0
    refnz = sum(1 for b in ref if b)
    print(f'exact {100 * eq / n:.2f} %  mean |d| {sad / n:.2f}  PSNR {psnr:.1f} dB'
          f'{"  (driver grey is ALL ZERO)" if blank else ""}  [screencap non-black pixels {100 * refnz / n:.2f} %]')
    print(f'PNGs: {out}/side-by-side.png (left: screencap through the driver grey stage, right: driver grey)')
    # verdict: identical, near (dithering/timing), different
    # exit 0: identical, or close (PSNR >= 30 dB) with real content in the driver
    # grey; 3: different, or an all-zero driver grey against a non-black frame
    if blank and refnz:
        sys.exit(3)
    sys.exit(0 if eq == n or psnr >= 30 else 3)


if __name__ == '__main__':
    main()
