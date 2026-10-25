#!/usr/bin/env python3
"""Run the stock A9 composer's TCON code in isolation under Unicorn and compare it,
byte for byte, with the standalone model in a9tcon.py.

usage (host only, no phone):
  nix-shell -p 'python3.withPackages(p:[p.unicorn p.numpy p.pillow])' --run \
    'python3 -I scripts/mainline/tcon-oracle.py COMPOSER [IMAGE.png] [--quick]'

COMPOSER = vendor.qti.hardware.display.composer-service, sha256 8c89557b...
(live device copy). Nothing of the binary is written out; the report holds
hashes and counts only.

Stock code executed (vaddr == file offset):
  0x1a48c8, 0x1a49a4   grey lookup builders (EpdSetContrast knobs)
  0x1a7338..0x1a7b30   conditioning, dither, >>4, pack (+ sentinel), old:=new,
                       final type, temperature bin (EinkSwTconThreadHandler body)
  0x1d1690             lookUpTable, called until it returns 0 (one update)
  0x1a7d04..0x1a7df4   the one-time init flash frame generator
  (used by tcon/test-tcon-c.py, the C port's test, in addition:)
  0x1a5acc..0x1a5c80   handler prologue (rodata step vectors -> stack, v8/v15)
  0x1a6604..0x1a6ea0   mode-switch request builder + step index arrays, mode word
  0x1a7c78..0x1a7df4   per-frame dispatch: steps of a mode switch, then the final pass
  0x1a61b0..0x1a62fc   RGBA -> grey + rotation (832-stride portrait -> 1648x824)
  0x19ee98..0x19f054   HWCDisplay::SetPowerMode: the control-template builder (run once
                       in Stock(); writes the template to 0x1f29a8 = stock's null buffer,
                       sets the null/cache pointers 0x35d4a8 / 0x4c7fb0)

Whole 32-bit words: lookUpTable / the init flash write byte 0 of every word of the
caller's output buffer (a DRM ring-buffer slot) and nothing else; null frames are a
memcpy of the null buffer 0x1f29a8 (= the control template, 0x1d1b54..0x1d1b74), hold
frames a memcpy of the cache (a copy of frame 0). Bytes 1..3 of a drive frame are
therefore whatever the ring slot holds, and the 16 live stock ring buffers hold the
template there (MEASURED 2026-10-15, b-diag/live-dumps). So every output buffer here
starts as the template (Stock.template, built by stock's own builder above, not read
from a file), and frames are compared as whole words.
PLT stubs memcpy 0x1e6800, memset 0x1e6960 and the logger 0x1e6930 are
replaced by host implementations / no-ops; anything else unexpected faults.
"""
import hashlib
import json
import os
import struct
import sys
import time

import numpy as np
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                 UC_ARM64_REG_X10, UC_ARM64_REG_X19, UC_ARM64_REG_X20,
                                 UC_ARM64_REG_X25, UC_ARM64_REG_X26, UC_ARM64_REG_X29,
                                 UC_ARM64_REG_X30, UC_ARM64_REG_PC, UC_ARM64_REG_SP)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a9tcon  # noqa: E402
from a9tcon import N, W, H, FRAME_BYTES  # noqa: E402

MEMCPY, MEMSET, LOGGER = 0x1e6800, 0x1e6960, 0x1e6930
OBJ, IMAGE, OLD, PAIRS, OUT, SLOT, STACK, STOP = (
    0x10000000, 0x20000000, 0x20400000, 0x20800000, 0x20c00000, 0x21000000,
    0x21400000, 0x4ff000)
G_TYPE, G_BIN, G_COUNT, G_S, G_PAIRS = 0x1eee38, 0x4c86d8, 0x4c86d0, 0x4c86c8, 0x4c86c0
G_COUNTER, G_CACHEP, G_NULLP, G_INIT = 0x4c7fbc, 0x4c7fb0, 0x35d4a8, 0x4c7fb8
CACHE, NULLBUF = 0x35d4b0, 0x1f29a8
STEPS, RGBA, GREYTMP = 0x22000000, 0x24000000, 0x24800000   # S+0x30.. arrays, SF buffer
G_STEP = 0x4c86d4


class Stock:
    def __init__(self, binary):
        self.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.mem_map(0, 0x500000)
        uc.mem_write(0, binary[:0x1ee000])                     # LOAD 0/1 (+ LOAD2 head)
        uc.mem_write(0x1ee000, bytes(0x500000 - 0x1ee000))     # bss starts zeroed
        uc.mem_write(0x1e8000, binary[0x1e8000:0x1e8000 + 24088])   # LOAD2 (r/w data)
        uc.mem_write(2027032, binary[2022936:2022936 + 36])    # LOAD3 .data (file->vaddr)
        uc.mem_map(OBJ, 0x2000000)
        for a in (IMAGE, OLD, PAIRS, OUT, SLOT):
            uc.mem_map(a, 0x400000)
        uc.mem_map(STACK, 0x10000)
        uc.mem_map(STEPS, 5 * 0x300000)       # the five step index arrays (zeroed, as stock init)
        uc.mem_map(RGBA, 0x800000)
        uc.mem_map(GREYTMP, 0x200000)
        self.S0 = OBJ + 0x19e7418
        for k in range(5):
            uc.mem_write(self.S0 + 0x30 + 8 * k, struct.pack('<Q', STEPS + k * 0x300000))
        uc.mem_write(OBJ + 0x688, struct.pack('<Q', RGBA))
        uc.mem_write(OBJ + 0x7f0, struct.pack('<Q', GREYTMP))
        self._stops = set()
        uc.mem_write(OBJ, struct.pack('<Q', 0x1ea950))          # vtable that passes CFI
        uc.mem_write(OBJ + 0x7e8, struct.pack('<Q', IMAGE))
        uc.mem_write(OBJ + 0x7d0, struct.pack('<Q', OLD))
        uc.mem_write(OBJ + 0x7d8, struct.pack('<Q', PAIRS))
        uc.mem_write(SLOT, struct.pack('<Q', OUT))
        self.S = OBJ + 0x19e7418
        for a, f in ((MEMCPY, self._memcpy), (MEMSET, self._memset), (LOGGER, self._ret)):
            uc.hook_add(UC_HOOK_CODE, f, begin=a, end=a)
        # the composer's TCON start (SetPowerMode 0x19ee98..0x19f054): null/cache
        # pointers, memset of the null buffer, then the control template into it
        # (row scratch 0x4c7fc0..0x4c86c0 is reused by nothing here)
        self.sp()
        uc.reg_write(UC_ARM64_REG_X19, OBJ)
        uc.reg_write(UC_ARM64_REG_X0, 0)
        self.run(0x19ee98, 0x19f054)
        assert struct.unpack('<Q', uc.mem_read(G_NULLP, 8))[0] == NULLBUF
        assert struct.unpack('<Q', uc.mem_read(G_CACHEP, 8))[0] == CACHE
        self.template = bytes(uc.mem_read(NULLBUF, FRAME_BYTES))
        self.template_np = np.frombuffer(self.template, np.uint8)

    def _ret(self, uc, *a):
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def _memcpy(self, uc, *a):
        d, s, n = (uc.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2))
        uc.mem_write(d, bytes(uc.mem_read(s, n)))
        self._ret(uc)

    def _memset(self, uc, *a):
        d, v, n = (uc.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2))
        uc.mem_write(d, bytes([v & 0xff]) * n)
        self._ret(uc)

    def run(self, start, end=STOP, also=()):
        """Run start..end; `also` = further addresses that end the run (code hooks)."""
        uc = self.uc
        hooks = [uc.hook_add(UC_HOOK_CODE, lambda u, *a: u.emu_stop(), begin=a, end=a)
                 for a in also]
        uc.reg_write(UC_ARM64_REG_X30, STOP)
        uc.emu_start(start, end, count=2_000_000_000)
        for hk in hooks:
            uc.hook_del(hk)
        pc = uc.reg_read(UC_ARM64_REG_PC)
        assert pc == end or pc in also, hex(pc)
        return pc

    def sp(self):
        self.uc.reg_write(UC_ARM64_REG_SP, STACK + 0x8000)
        return STACK + 0x8000

    def u32(self, a):
        return struct.unpack('<I', self.uc.mem_read(a, 4))[0]

    def builders(self, black, white, contrast):
        self.uc.mem_write(OBJ + 0x19e7208, struct.pack('<iii', black, white, contrast))
        for fn in (0x1a48c8, 0x1a49a4):
            self.sp()
            self.uc.reg_write(UC_ARM64_REG_X0, OBJ)
            self.run(fn)
        return (bytes(self.uc.mem_read(OBJ + 0x19e7214, 256)),
                bytes(self.uc.mem_read(OBJ + 0x19e7314, 256)))

    def prepare(self, old, grey, mode, clear, t1, t2, steps=0):
        """Conditioning .. bin, exactly as the engine thread runs it before the frame loop."""
        uc = self.uc
        uc.mem_write(IMAGE, bytes(grey))
        uc.mem_write(OLD, bytes(old))
        uc.mem_write(PAIRS, bytes(N * 2))
        uc.mem_write(OBJ + 0x800, bytes(N * 4 + 0x10000))
        sp = self.sp()
        for off, val in ((0, OBJ + 0x19e7314), (8, OBJ + 0x21c4), (0x10, OBJ + 0x21b8),
                         (0x18, OBJ + 0x800), (0x48, OBJ + 0x19e7214)):
            uc.mem_write(sp + off, struct.pack('<Q', val))
        fp = sp + 0x400
        uc.reg_write(UC_ARM64_REG_X29, fp)
        uc.mem_write(fp - 0xa0, struct.pack('<Q', self.S))
        uc.mem_write(fp - 0x68, struct.pack('<Q', self.S))
        uc.mem_write(fp - 0x58, struct.pack('<Q', OBJ + 0x498))
        if steps is not None:          # None: keep what the mode-switch builder left
            uc.mem_write(self.S + 0xc, struct.pack('<I', steps))
        uc.mem_write(OBJ + 0x7fc, struct.pack('<I', mode))
        uc.mem_write(OBJ + 0x6dc, struct.pack('<I', clear))
        uc.mem_write(OBJ + 0x6d8, struct.pack('<i', t1))
        uc.mem_write(OBJ + 0x6d4, struct.pack('<i', t2))
        uc.reg_write(UC_ARM64_REG_X19, OBJ)
        uc.reg_write(UC_ARM64_REG_X25, OLD)
        uc.reg_write(UC_ARM64_REG_X26, IMAGE)
        uc.reg_write(UC_ARM64_REG_X20, PAIRS)
        self.run(0x1a7338, 0x1a7b30)
        return {
            'levels': np.frombuffer(bytes(uc.mem_read(OLD, N)), np.uint8),   # old := new
            'pairs': np.frombuffer(bytes(uc.mem_read(PAIRS, 2 * N)), np.uint16),
            'type': self.u32(G_TYPE), 'bin': uc.mem_read(G_BIN, 1)[0],
            'count': self.u32(G_COUNT),
            'pairs_ptr': struct.unpack('<Q', uc.mem_read(G_PAIRS, 8))[0],
            'S_ptr': struct.unpack('<Q', uc.mem_read(G_S, 8))[0],
        }

    def lookup(self, wtype):
        uc = self.uc
        uc.mem_write(OUT, self.template)   # a ring slot: template in bytes 1..3
        self.sp()
        uc.reg_write(UC_ARM64_REG_X0, OUT)
        uc.reg_write(UC_ARM64_REG_X1, wtype)
        uc.reg_write(UC_ARM64_REG_X2, PAIRS)
        self.run(0x1d1690)
        return uc.reg_read(UC_ARM64_REG_X0) & 0xff, bytes(uc.mem_read(OUT, FRAME_BYTES))

    def init_frame(self):
        uc = self.uc
        uc.mem_write(OUT, self.template)   # a ring slot: template in bytes 1..3
        uc.reg_write(UC_ARM64_REG_X10, SLOT)
        self.sp()
        self.run(0x1a7d04, 0x1a7df4)
        return uc.reg_read(UC_ARM64_REG_X0) & 0xff, bytes(uc.mem_read(OUT, FRAME_BYTES))


    # ---- additions for the C port's test (tcon/test-tcon-c.py) -------------------
    def prologue(self):
        """Handler prologue 0x1a5acc..0x1a5c80: stack frame with the rodata step
        vectors, x29, v8 = (6,6), v15 = (9,9), v12..v14 = luma weights, x27."""
        uc = self.uc
        uc.reg_write(UC_ARM64_REG_SP, STACK + 0xc000)
        uc.reg_write(UC_ARM64_REG_X0, OBJ)
        self.run(0x1a5acc, 0x1a5c80)

    def build_request(self, pre, cur, clear, flag6c7=1):
        """Transition handler 0x1a6604..0x1a6ea0 on the current OLD buffer.
        Returns the request S, the mode word, the clear flags and the step arrays."""
        uc = self.uc
        self.prologue()
        uc.mem_write(OBJ + 0x6e4, struct.pack('<I', pre))
        uc.mem_write(OBJ + 0x6e8, struct.pack('<I', cur))
        uc.mem_write(OBJ + 0x6dc, struct.pack('<I', clear))
        uc.mem_write(OBJ + 0x6e0, b'\0')
        uc.mem_write(OBJ + 0x6c7, bytes([flag6c7]))
        self.run(0x1a6604, 0x1a6ea0)
        S = bytes(uc.mem_read(self.S0, 0x30))
        w = struct.unpack('<9I', S[:36])
        return {
            'kind': w[0], 'full': w[1], 'code': w[2], 'count': w[3], 'types': list(w[4:9]),
            'levels': list(S[0x24:0x29]),
            'mode': self.u32(OBJ + 0x7fc), 'clear': self.u32(OBJ + 0x6dc),
            'clear_reset': uc.mem_read(OBJ + 0x6e0, 1)[0],
            'old': np.frombuffer(bytes(uc.mem_read(OLD, N)), np.uint8),
        }

    def step_array(self, k):
        return np.frombuffer(bytes(self.uc.mem_read(STEPS + k * 0x300000, 2 * N)), np.uint16)

    def dispatch(self):
        """One frame of the engine's dispatch 0x1a7c78..0x1a7df4 (steps, then final)."""
        uc = self.uc
        uc.mem_write(OUT, self.template)   # a ring slot: template in bytes 1..3
        uc.reg_write(UC_ARM64_REG_X10, SLOT)
        self.sp()
        self.run(0x1a7c78, 0x1a7df4)
        return uc.reg_read(UC_ARM64_REG_X0) & 0xff, bytes(uc.mem_read(OUT, FRAME_BYTES))

    def rgba_to_grey(self, rgba):
        """0x1a61b0..0x1a62fc: SF's RGBA (832 x 1648) -> grey -> rotated into IMAGE."""
        uc = self.uc
        self.prologue()
        uc.mem_write(RGBA, bytes(rgba))
        uc.mem_write(IMAGE, bytes(N))
        self.sp()
        uc.reg_write(UC_ARM64_REG_X19, OBJ)
        self.run(0x1a61b0, 0x1a62fc)
        return np.frombuffer(bytes(uc.mem_read(IMAGE, N)), np.uint8)


def h(b):
    return hashlib.sha256(bytes(b)).hexdigest()[:16]


def test_image(path):
    """1648x824 grey: the given picture (scaled, letterboxed) or a synthetic chart."""
    yy, xx = np.mgrid[0:H, 0:W]
    synth = ((xx * 255) // (W - 1)).astype(np.uint8)            # horizontal ramp
    synth[: H // 4] = ((xx[: H // 4] // 103) * 17).astype(np.uint8)   # 16 grey steps
    synth[((yy // 8 + xx // 8) % 2 == 0) & (yy > 3 * H // 4)] = 0     # checker strip
    if not path:
        return synth.ravel(), 'synthetic'
    from PIL import Image
    im = Image.open(path).convert('L')
    im.thumbnail((W, H))
    canvas = Image.new('L', (W, H), 255)
    canvas.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
    return np.asarray(canvas, np.uint8).ravel(), os.path.basename(path)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    quick = '--quick' in sys.argv
    binary = open(args[0], 'rb').read()
    model = a9tcon.Tcon(binary)
    stock = Stock(binary)
    report = {'composer_sha256': h(binary), 'checks': []}
    fails = 0
    # the model writes byte 0 only, like stock: give it stock's template buffers
    tmpl = stock.template_np
    model.null = tmpl.copy()
    model.cache = tmpl.copy()
    report['template_sha256'] = hashlib.sha256(stock.template).hexdigest()

    def check(name, ok, **kw):
        nonlocal fails
        fails += 0 if ok else 1
        report['checks'].append(dict(name=name, match=bool(ok), **kw))
        print(('OK   ' if ok else 'FAIL ') + name, kw if kw else '', flush=True)

    # 0. the control template stock builds at TCON start
    w = tmpl.reshape(-1, 4)
    check('control template: byte 0 = 0, byte 2 = 0, byte 3 = 0xff',
          bool((w[:, 0] == 0).all() and (w[:, 2] == 0).all() and (w[:, 3] == 0xff).all()),
          sha256=report['template_sha256'][:16])

    # 1. grey lookup builders
    for ctl in ((30, 30, 0), (0, 100, 100), (101, -1, 50)):
        g2, g16 = stock.builders(*ctl)
        model.set_controls(*ctl)
        check(f'builders {ctl}', g2 == bytes(model.g2) and g16 == bytes(model.g16))
    stock.builders(30, 30, 0)
    model.set_controls(30, 30, 0)

    # 2. temperature -> bin, across the clamp quirks
    bad = []
    for t1 in list(range(-5, 60)) + [127, 128, 200, 255, 256]:
        for t2 in (-1, 0, 20, 33, 49, 50, 51, 128):
            stock.uc.mem_write(OBJ + 0x6d8, struct.pack('<i', t1))
            if model.temp_bin(t1, t2) != stock_bin(stock, t1, t2):
                bad.append((t1, t2))
    check('temperature bin map (73 x 8 pairs)', not bad, mismatches=bad[:5])

    # 3. the one-time init flash, bins 0, 9, 13
    for b in ((0, 0), (28, 34), (50, 50)):
        stock.uc.mem_write(G_INIT, b'\0')
        stock.uc.mem_write(G_COUNTER, bytes(4))
        st = stock_bin(stock, *b)
        stock.uc.mem_write(G_BIN, bytes([st]))
        model.counter, n, same = 0, 0, True
        while True:
            rs, fs = stock.init_frame()
            out = tmpl.copy()
            rm = model.init_frame(st, out)
            same &= (rs == rm) and fs == out.tobytes()
            n += 1
            if not rs or not rm or n > 200:
                break
        check(f'init flash bin {st}', same and rs == rm == 0, frames=n)
    stock.uc.mem_write(G_INIT, b'\1')

    # 4. full updates: image -> levels -> pairs -> every drive frame
    grey, name = test_image(args[1] if len(args) > 1 else None)
    report['image'] = name
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 256, N, dtype=np.uint8)
    cases = [  # (mode, clear, t1, t2, old-levels source)
        (0x201, 0, 28, 34, 'white'), (0x206, 0, 27, 33, 'white'),
        (0x209, 0, 10, 12, 'white'), (0x203, 0, 28, 33, 'prev'),
        (0x201, 0, 28, 34, 'prev'), (0x203, 0, 28, 33, 'inverse'),
        (0x203, 1, 28, 33, 'prev'), (0x201, 1, 0, 0, 'prev'),
        (0x401, 0, 45, 45, 'white'), (0x303, 0, 20, 20, 'prev'),
        (0x202, 0, 33, 30, 'white'), (0x208, 0, 5, 5, 'prev'),
    ]
    if quick:
        cases = cases[:5]
    prev = np.full(N, 15, np.uint8)
    for mode, clear, t1, t2, src in cases:
        old = {'white': np.full(N, 15, np.uint8), 'prev': prev,
               'inverse': (15 - prev).astype(np.uint8)}[src]
        pic = grey if mode != 0x208 else noise
        t0 = time.time()
        st = stock.prepare(old, pic, mode, clear, t1, t2)
        t_stock = time.time() - t0
        t0 = time.time()
        lv = model.condition(pic, mode)
        pr = model.pack(old, lv, mode, clear)
        t_model = time.time() - t0
        mt, mb = model.final_type(mode, clear), model.temp_bin(t1, t2)
        check(f'prepare mode {mode:#x} clear {clear}',
              np.array_equal(st['levels'], lv) and np.array_equal(st['pairs'], pr)
              and st['type'] == mt and st['bin'] == mb and st['pairs_ptr'] == PAIRS,
              type=st['type'], bin=int(st['bin']), levels=h(lv), pairs=h(pr),
              sentinels=int((pr == 256).sum()), secs_stock=round(t_stock, 1),
              secs_model=round(t_model, 1))
        flags = (0, 1) if mt == 1 else (0,)
        for flag in flags:
            stock.uc.mem_write(stock.S + 4, struct.pack('<I', flag))
            model.balanced_full = flag
            stock.uc.mem_write(G_COUNTER, bytes(4))
            model.counter = 0
            n, same, digest = 0, True, hashlib.sha256()
            out = tmpl.copy()
            while True:
                rs, fs = stock.lookup(st['type'])
                out[:] = tmpl
                rm = model.lookup(mt, mb, pr, out)
                ok = rs == rm and fs == out.tobytes()
                if not ok and same:
                    diff = np.nonzero(np.frombuffer(fs, np.uint8) != out)[0]
                    print('   first diff at frame', n, 'byte', diff[:4], rs, rm)
                same &= ok
                digest.update(fs)
                n += 1
                if not rs or not rm or n > 300:
                    break
            nz = int(np.count_nonzero(np.frombuffer(fs, np.uint8)[0::4]))
            check(f'  frames mode {mode:#x} clear {clear} type {mt} bin {mb}'
                  + (f' S+4={flag}' if mt == 1 else ''),
                  same and rs == rm == 0 and fs == stock.template, frames=n,
                  frames_sha256=digest.hexdigest()[:16], last_frame_drive_bytes=nz)
        prev = lv
    report['failures'] = fails
    print(json.dumps(report, indent=1))
    sys.exit(1 if fails else 0)


def stock_bin(stock, t1, t2):
    """Run only the bin computation of the prologue (0x1a7a94-0x1a7b28) for t1, t2."""
    uc = stock.uc
    uc.mem_write(OBJ + 0x6d8, struct.pack('<i', t1))
    uc.mem_write(OBJ + 0x6d4, struct.pack('<i', t2))
    sp = stock.sp()
    fp = sp + 0x400
    uc.reg_write(UC_ARM64_REG_X29, fp)
    uc.mem_write(fp - 0xa0, struct.pack('<Q', stock.S))
    uc.mem_write(fp - 0x68, struct.pack('<Q', stock.S))
    uc.reg_write(UC_ARM64_REG_X19, OBJ)
    stock.run(0x1a7a48, 0x1a7b30)
    return uc.mem_read(G_BIN, 1)[0]


if __name__ == '__main__':
    main()
